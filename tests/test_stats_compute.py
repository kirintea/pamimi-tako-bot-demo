# -*- coding: utf-8 -*-

"""运营统计口径对账 —— 对照 .draft/2026-10-22-ops-stats-dashboard.md 的对账示例"""

from __future__ import annotations

from datetime import date, datetime

import pytest

from core.stats import compute_stats, prev_window, range_window

TZ = "Asia/Shanghai"


class FakeStatsDB:
    """按语句名返回预置行的 DB 替身"""

    def __init__(self, rows_by_name: dict[str, list[dict]]):
        self.rows = rows_by_name
        self.calls: list[tuple[str, tuple]] = []
        self.dialect = "postgres"

    async def fetch_named(self, name: str, *args):
        self.calls.append((name, args))
        return list(self.rows.get(name, []))


def _window():
    return range_window(TZ, "7d", today=date(2026, 10, 22))


@pytest.mark.asyncio(loop_scope="session")
async def test_totals_use_turn_id_distinct_plus_legacy_fallback():
    """turns = DISTINCT turn_id + turn_id IS NULL 且 role='user' 的行数"""
    db = FakeStatsDB({
        "stats_conv_total": [{"uv": 2, "turns_v3": 3, "turns_legacy": 4}],
        "stats_active_total": [{"sessions_active": 1}],
        "stats_new_total": [{"sessions_new": 2}],
    })
    start, end = _window()
    out = await compute_stats(db, start=start, end=end, tz=TZ, granularity="day",
                              channel=None, range_key="7d")
    assert out["totals"] == {
        "uv": 2, "sessions_active": 1, "sessions_new": 2, "turns": 7,
    }


@pytest.mark.asyncio(loop_scope="session")
async def test_null_aggregates_become_zero():
    """SUM/COUNT 空集返回 None → 必须落 0，不能 None"""
    db = FakeStatsDB({
        "stats_conv_total": [{"uv": None, "turns_v3": None, "turns_legacy": None}],
        "stats_active_total": [{"sessions_active": None}],
        "stats_new_total": [{"sessions_new": None}],
    })
    start, end = _window()
    out = await compute_stats(db, start=start, end=end, tz=TZ, granularity="day",
                              channel=None, range_key="7d")
    assert out["totals"] == {"uv": 0, "sessions_active": 0, "sessions_new": 0, "turns": 0}


@pytest.mark.asyncio(loop_scope="session")
async def test_series_zero_fills_missing_buckets():
    """无数据的桶补 0，保证趋势图 X 轴连续"""
    db = FakeStatsDB({
        "stats_conv_series": [
            {"bucket": datetime(2026, 10, 16), "uv": 1, "turns_v3": 2, "turns_legacy": 0},
            {"bucket": datetime(2026, 10, 22), "uv": 3, "turns_v3": 4, "turns_legacy": 1},
        ],
        "stats_active_series": [
            {"bucket": datetime(2026, 10, 16), "sessions_active": 5},
        ],
        "stats_new_series": [],
    })
    start, end = _window()
    out = await compute_stats(db, start=start, end=end, tz=TZ, granularity="day",
                              channel=None, range_key="7d")
    series = out["series"]
    assert [s["bucket"] for s in series] == [
        "2026-10-16", "2026-10-17", "2026-10-18", "2026-10-19",
        "2026-10-20", "2026-10-21", "2026-10-22",
    ]
    assert series[0] == {
        "bucket": "2026-10-16", "uv": 1, "sessions_active": 5,
        "sessions_new": 0, "turns": 2,
    }
    assert series[1]["uv"] == 0
    assert series[6] == {
        "bucket": "2026-10-22", "uv": 3, "sessions_active": 0,
        "sessions_new": 0, "turns": 5,
    }


@pytest.mark.asyncio(loop_scope="session")
async def test_cross_bucket_sessions_counted_in_each_active_day():
    """对账示例：S1 两天活跃 → 活跃口径两天各计 1；新增只计创建日"""
    db = FakeStatsDB({
        "stats_conv_series": [],
        "stats_active_series": [
            {"bucket": datetime(2026, 10, 20), "sessions_active": 1},
            {"bucket": datetime(2026, 10, 25), "sessions_active": 2},
        ],
        "stats_new_series": [
            {"bucket": datetime(2026, 10, 20), "sessions_new": 1},
            {"bucket": datetime(2026, 10, 25), "sessions_new": 1},
        ],
    })
    start, end = range_window(TZ, "30d", today=date(2026, 10, 25))
    out = await compute_stats(db, start=start, end=end, tz=TZ, granularity="day",
                              channel=None, range_key="30d")
    by_day = {s["bucket"]: s for s in out["series"]}
    assert by_day["2026-10-20"]["sessions_active"] == 1
    assert by_day["2026-10-20"]["sessions_new"] == 1
    assert by_day["2026-10-25"]["sessions_active"] == 2
    assert by_day["2026-10-25"]["sessions_new"] == 1
    # 活跃各桶相加(3) > 区间总数(2)，属正常
    assert sum(s["sessions_active"] for s in out["series"]) == 3


@pytest.mark.asyncio(loop_scope="session")
async def test_channel_filter_passed_as_none_for_all():
    """channel=None → 传 SQL NULL，不传具体值"""
    db = FakeStatsDB({
        "stats_conv_total": [{"uv": 0, "turns_v3": 0, "turns_legacy": 0}],
        "stats_active_total": [{"sessions_active": 0}],
        "stats_new_total": [{"sessions_new": 0}],
    })
    start, end = _window()
    await compute_stats(db, start=start, end=end, tz=TZ, granularity="day",
                        channel=None, range_key="7d")
    _, args = db.calls[0]
    assert args[-1] is None


@pytest.mark.asyncio(loop_scope="session")
async def test_channel_table_share_is_uv_based():
    """占比 = 该渠道 UV ÷ 全渠道 UV（原型数字反推：720/1284≈56%）"""
    db = FakeStatsDB({
        "stats_conv_total": [{"uv": 1284, "turns_v3": 18650, "turns_legacy": 0}],
        "stats_active_total": [{"sessions_active": 3920}],
        "stats_new_total": [{"sessions_new": 1200}],
        "stats_channel_conv": [
            {"channel": "web", "uv": 720, "turns_v3": 9800, "turns_legacy": 0},
            {"channel": "wecom", "uv": 420, "turns_v3": 6200, "turns_legacy": 0},
            {"channel": "api", "uv": 144, "turns_v3": 2650, "turns_legacy": 0},
        ],
        "stats_channel_active": [
            {"channel": "web", "sessions_active": 2100},
            {"channel": "wecom", "sessions_active": 1280},
            {"channel": "api", "sessions_active": 540},
        ],
        "stats_channel_new": [
            {"channel": "web", "sessions_new": 540},
            {"channel": "wecom", "sessions_new": 420},
            {"channel": "api", "sessions_new": 240},
        ],
        "stats_channel_options": [
            {"channel": "api"}, {"channel": "web"}, {"channel": "wecom"},
        ],
    })
    start, end = _window()
    out = await compute_stats(db, start=start, end=end, tz=TZ, granularity="day",
                              channel=None, range_key="7d")
    chans = out["channels"]
    assert chans[0]["channel"] == "web"
    assert round(chans[0]["uv_share"], 2) == 0.56
    assert round(chans[1]["uv_share"], 2) == 0.33
    assert round(chans[2]["uv_share"], 2) == 0.11
    # 按 turns 降序
    assert [c["channel"] for c in chans] == ["web", "wecom", "api"]


@pytest.mark.asyncio(loop_scope="session")
async def test_unknown_channel_bucket_labeled_empty_string():
    """无 conversations 行的会话派生渠道为空串 → 归入 ''，前端显示『未知』"""
    db = FakeStatsDB({
        "stats_conv_total": [{"uv": 0, "turns_v3": 0, "turns_legacy": 0}],
        "stats_active_total": [{"sessions_active": 0}],
        "stats_new_total": [{"sessions_new": 0}],
        "stats_channel_conv": [],
        "stats_channel_active": [],
        "stats_channel_new": [{"channel": "", "sessions_new": 3}],
        "stats_channel_options": [],
    })
    start, end = _window()
    out = await compute_stats(db, start=start, end=end, tz=TZ, granularity="day",
                              channel=None, range_key="7d")
    assert out["channels"][0]["channel"] == ""
    assert out["channels"][0]["label"] == "未知"


@pytest.mark.asyncio(loop_scope="session")
async def test_prev_totals_queried_with_shifted_equal_length_window():
    """prev_totals = 同一套 *_total 在等长前移窗上重跑"""
    db = FakeStatsDB({
        "stats_conv_total": [{"uv": 2, "turns_v3": 3, "turns_legacy": 4}],
        "stats_active_total": [{"sessions_active": 1}],
        "stats_new_total": [{"sessions_new": 2}],
    })
    start, end = _window()  # 2026-10-16 ~ 2026-10-23
    out = await compute_stats(db, start=start, end=end, tz=TZ, granularity="day",
                              channel=None, range_key="7d")
    total_calls = [c for c in db.calls if c[0].endswith("_total")]
    # 6 次 total 调用：3 条本窗 + 3 条前窗
    assert len(total_calls) == 6
    p_start, p_end = prev_window(start, end)
    for name, args in total_calls[3:]:
        assert args[0] == p_start and args[1] == p_end, name
    # FakeStatsDB 同名复用同一行 → 前窗值同本窗（此处只验窗口平移）
    assert out["prev_totals"] == out["totals"]


@pytest.mark.asyncio(loop_scope="session")
async def test_prev_totals_zero_when_prior_window_empty():
    """前窗无数据（空行集）→ prev_totals 全 0，前端据此显示『新增』"""
    class SeqDB(FakeStatsDB):
        """stats_*_total 首调返回本窗行，后续调用返回空"""

        def __init__(self, rows_by_name):
            super().__init__(rows_by_name)
            self._total_seen: set[str] = set()

        async def fetch_named(self, name, *args):
            self.calls.append((name, args))
            if name.endswith("_total") and name in self._total_seen:
                return []
            if name.endswith("_total"):
                self._total_seen.add(name)
            return list(self.rows.get(name, []))

    db = SeqDB({
        "stats_conv_total": [{"uv": 2, "turns_v3": 3, "turns_legacy": 4}],
        "stats_active_total": [{"sessions_active": 1}],
        "stats_new_total": [{"sessions_new": 2}],
    })
    start, end = _window()
    out = await compute_stats(db, start=start, end=end, tz=TZ, granularity="day",
                              channel=None, range_key="7d")
    assert out["totals"] == {"uv": 2, "sessions_active": 1, "sessions_new": 2, "turns": 7}
    assert out["prev_totals"] == {
        "uv": 0, "sessions_active": 0, "sessions_new": 0, "turns": 0,
    }


@pytest.mark.asyncio(loop_scope="session")
async def test_series_by_channel_merges_three_rowsets():
    """series_by_channel = bucket×channel 三行集合并，缺失指标补 0，按 (bucket, channel) 排序"""
    db = FakeStatsDB({
        "stats_bucket_channel_conv": [
            {"bucket": datetime(2026, 10, 16), "channel": "web",
             "uv": 1, "turns_v3": 2, "turns_legacy": 1},
            {"bucket": datetime(2026, 10, 17), "channel": "wecom",
             "uv": 3, "turns_v3": 0, "turns_legacy": 0},
        ],
        "stats_bucket_channel_active": [
            {"bucket": datetime(2026, 10, 16), "channel": "web", "sessions_active": 4},
        ],
        "stats_bucket_channel_new": [
            {"bucket": datetime(2026, 10, 17), "channel": "wecom", "sessions_new": 2},
            {"bucket": datetime(2026, 10, 16), "channel": "web", "sessions_new": 0},
        ],
    })
    start, end = _window()
    out = await compute_stats(db, start=start, end=end, tz=TZ, granularity="day",
                              channel=None, range_key="7d")
    sbc = out["series_by_channel"]
    assert [(r["bucket"], r["channel"]) for r in sbc] == [
        ("2026-10-16", "web"), ("2026-10-17", "wecom"),
    ]
    assert sbc[0] == {
        "bucket": "2026-10-16", "channel": "web",
        "uv": 1, "sessions_active": 4, "sessions_new": 0, "turns": 3,
    }
    assert sbc[1] == {
        "bucket": "2026-10-17", "channel": "wecom",
        "uv": 3, "sessions_active": 0, "sessions_new": 2, "turns": 0,
    }
    # bucket_channel 语句是 series 式参数：$1=gran $2=tz $3=start $4=end $5=channel
    bc_call = next(c for c in db.calls if c[0] == "stats_bucket_channel_conv")
    assert bc_call[1] == ("day", TZ, start, end, None)


@pytest.mark.asyncio(loop_scope="session")
async def test_wecom_label_is_enterprise_wechat():
    """后端 label 兜底：wecom → 企业微信（中文 CSV 用；前端 i18n 优先）"""
    db = FakeStatsDB({
        "stats_channel_conv": [
            {"channel": "wecom", "uv": 1, "turns_v3": 1, "turns_legacy": 0},
        ],
    })
    start, end = _window()
    out = await compute_stats(db, start=start, end=end, tz=TZ, granularity="day",
                              channel=None, range_key="7d")
    assert out["channels"][0]["label"] == "企业微信"
