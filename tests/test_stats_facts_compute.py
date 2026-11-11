# -*- coding: utf-8 -*-

"""facts 后端 compute_stats 口径 —— 对照设计文档人次口径"""

from __future__ import annotations

from datetime import date

import pytest

from core.stats import compute_stats, range_window

TZ = "Asia/Shanghai"


class FakeFactsDB:
    def __init__(self, series_rows, bc_rows):
        self.series_rows = series_rows
        self.bc_rows = bc_rows
        self.calls = []
        self._series_calls = 0

    async def fetch_named(self, name, *args):
        self.calls.append((name, args))
        if name == "stats_daily_series":
            # 第一次 = 当前窗；第二次 = 环比前窗（返回空）
            self._series_calls += 1
            return list(self.series_rows) if self._series_calls == 1 else []
        if name == "stats_daily_by_channel":
            return list(self.bc_rows)
        return []


def _day_rows(*rows):
    return [
        {"stat_day": d, "uv": u, "sessions_active": a, "turns": t, "sessions_new": n}
        for d, u, a, t, n in rows
    ]


@pytest.mark.asyncio(loop_scope="session")
async def test_facts_totals_sum_series_and_prev():
    db = FakeFactsDB(
        series_rows=_day_rows(
            (date(2026, 10, 20), 1, 2, 3, 1),
            (date(2026, 10, 21), 2, 1, 4, 0),
        ),
        bc_rows=[],
    )
    start, end = range_window(TZ, "7d", today=date(2026, 10, 22))
    out = await compute_stats(db, start=start, end=end, tz=TZ, granularity="day",
                              channel=None, range_key="7d", backend="facts")
    assert out["totals"] == {"uv": 3, "sessions_active": 3, "sessions_new": 1, "turns": 7}
    assert out["prev_totals"] == {"uv": 0, "sessions_active": 0, "sessions_new": 0, "turns": 0}


@pytest.mark.asyncio(loop_scope="session")
async def test_facts_series_week_bucket_sums_days():
    db = FakeFactsDB(
        series_rows=_day_rows(
            (date(2026, 10, 19), 1, 0, 0, 0),  # 周一
            (date(2026, 10, 20), 2, 0, 0, 0),  # 周二（同周）
        ),
        bc_rows=[],
    )
    start, end = range_window(TZ, "7d", today=date(2026, 10, 22))
    out = await compute_stats(db, start=start, end=end, tz=TZ, granularity="week",
                              channel=None, range_key="7d", backend="facts")
    series = [s for s in out["series"] if s["uv"]]
    assert series == [{"bucket": "2026-10-19", "uv": 3, "sessions_active": 0,
                       "sessions_new": 0, "turns": 0}]


@pytest.mark.asyncio(loop_scope="session")
async def test_facts_channels_sum_across_days_with_share():
    db = FakeFactsDB(
        series_rows=[],
        bc_rows=[
            {"stat_day": date(2026, 10, 20), "channel": "web", "uv": 3, "sessions_active": 1, "turns": 5, "sessions_new": 1},
            {"stat_day": date(2026, 10, 21), "channel": "web", "uv": 1, "sessions_active": 1, "turns": 2, "sessions_new": 0},
            {"stat_day": date(2026, 10, 20), "channel": "wecom", "uv": 2, "sessions_active": 1, "turns": 1, "sessions_new": 1},
        ],
    )
    start, end = range_window(TZ, "7d", today=date(2026, 10, 22))
    out = await compute_stats(db, start=start, end=end, tz=TZ, granularity="day",
                              channel=None, range_key="7d", backend="facts")
    chans = {c["channel"]: c for c in out["channels"]}
    assert chans["web"]["uv"] == 4  # 3 + 1 跨天相加
    assert chans["web"]["turns"] == 7
    assert chans["wecom"]["uv"] == 2
    assert out["channel_options"] == [
        {"value": "web", "label": "Web"},
        {"value": "wecom", "label": "企业微信"},
    ]
