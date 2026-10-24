# -*- coding: utf-8 -*-

"""运营统计聚合

职责：时间窗切分、分桶键生成、统计 SQL 调用与响应组装。
SQL 本体在 core/db/statements.py 注册表（stats_* 前缀）。

口径要点：
- 行过滤 status='active'（排除软删 deleted 与轮内归档 archived）
- 对话轮次 = COUNT(DISTINCT turn_id) + 旧数据(turn_id IS NULL)按 role='user' 行数
- 会话双口径：活跃（按活动日，跨天各计一次）/ 新增（按创建日，每会话一次）
- 会话渠道 = conversations.channel（sessions.channel 不可信）
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

GRAN_DAY = "day"
GRAN_WEEK = "week"
GRAN_MONTH = "month"
GRANULARITIES: tuple[str, ...] = (GRAN_DAY, GRAN_WEEK, GRAN_MONTH)

RANGE_DAYS: dict[str, int] = {"7d": 7, "30d": 30, "90d": 90}

CHANNEL_ALL = "all"


def range_window(tz: str, range_key: str, today: date | None = None) -> tuple[datetime, datetime]:
    """近 N 天窗口（含今天），左闭右开 [start, end)

    边界按 tz 的本地日 00:00 切。返回 aware datetime；写入 SQL 前由
    bounds_for_dialect() 按方言剥 tzinfo（MySQL naive 墙钟）。
    """
    if range_key not in RANGE_DAYS:
        raise ValueError(f"未知时间窗: {range_key!r}")
    try:
        zone = ZoneInfo(tz)
    except Exception as e:
        raise ValueError(f"未知时区: {tz!r}") from e
    base = today or datetime.now(zone).date()
    start_d = base - timedelta(days=RANGE_DAYS[range_key] - 1)
    end_d = base + timedelta(days=1)
    return (
        datetime(start_d.year, start_d.month, start_d.day, tzinfo=zone),
        datetime(end_d.year, end_d.month, end_d.day, tzinfo=zone),
    )


MAX_CUSTOM_DAYS = 366


def custom_window(tz: str, start_d: date, end_d: date) -> tuple[datetime, datetime]:
    """自定义区间 [start_d, end_d]（两端含）→ 左闭右开 [start 00:00, end+1d 00:00)

    长度（含首尾）限制 1–366 天；end < start / 未知时区抛 ValueError。
    """
    if end_d < start_d:
        raise ValueError(f"end 早于 start: {start_d} ~ {end_d}")
    span_days = (end_d - start_d).days + 1
    if span_days > MAX_CUSTOM_DAYS:
        raise ValueError(f"自定义区间最长 {MAX_CUSTOM_DAYS} 天，收到 {span_days} 天")
    try:
        zone = ZoneInfo(tz)
    except Exception as e:
        raise ValueError(f"未知时区: {tz!r}") from e
    end_next = end_d + timedelta(days=1)
    return (
        datetime(start_d.year, start_d.month, start_d.day, tzinfo=zone),
        datetime(end_next.year, end_next.month, end_next.day, tzinfo=zone),
    )


def prev_window(start: datetime, end: datetime) -> tuple[datetime, datetime]:
    """环比前窗：等长紧邻 [start-(end-start), start)，右开左闭同主窗"""
    span = end - start
    return start - span, start


def bucket_keys(start: datetime, end: datetime, granularity: str) -> list[str]:
    """窗口内全部桶的首日（升序 ISO 日期），无数据补 0 用

    桶边界按 start/end 已是本地日，直接在本地日期轴上切。
    周桶取 ISO 周一，月桶取自然月首日。
    """
    if granularity not in GRANULARITIES:
        raise ValueError(f"未知粒度: {granularity!r}")
    first = start.date()
    last = (end - timedelta(microseconds=1)).date()
    days: list[date] = []
    d = first
    while d <= last:
        days.append(d)
        d += timedelta(days=1)
    if granularity == GRAN_DAY:
        return [d.isoformat() for d in days]
    keys: list[date] = []
    for d in days:
        b = d - timedelta(days=d.weekday()) if granularity == GRAN_WEEK else d.replace(day=1)
        if not keys or keys[-1] != b:
            keys.append(b)
    return [k.isoformat() for k in keys]


def bounds_for_dialect(dialect: str, start: datetime, end: datetime) -> tuple[datetime, datetime]:
    """按方言调整时间列传参形状

    PG TIMESTAMPTZ 用 aware；MySQL DATETIME 用 naive 墙钟（同 DatabaseManager.now()）。
    """
    if dialect == "mysql":
        return start.replace(tzinfo=None), end.replace(tzinfo=None)
    return start, end


def bucket_key(value: Any) -> str:
    """SQL 返回的桶值 → ISO 日期串

    PG date_trunc 回 datetime / date；MySQL CASE 回 date / datetime。
    """
    if isinstance(value, datetime):
        value = value.date()
    return value.isoformat()


CHANNEL_LABELS: dict[str, str] = {"web": "Web", "wecom": "企业微信", "": "未知"}


def _norm_int(value: Any) -> int:
    """SQL 聚合值 → int（None / 空集 SUM 落 0）"""
    return int(value or 0)


def _channel_arg(channel: str | None) -> str | None:
    """前端 'all' / None → SQL NULL；具体值原样"""
    if not channel or channel == CHANNEL_ALL:
        return None
    return channel


def _row_map(rows: list[dict], key: str = "bucket") -> dict[str, dict]:
    return {bucket_key(r[key]): r for r in rows if r.get(key) is not None}


async def compute_stats(
    db,
    *,
    start: datetime,
    end: datetime,
    tz: str,
    granularity: str,
    channel: str | None,
    range_key: str,
) -> dict[str, Any]:
    """按口径组装看板响应

    Args:
        db: 提供 fetch_named(name, *args) 的对象（DatabaseManager 或测试替身）
        start/end: 已按 tz 切好的窗口边界（aware）
        tz: database.timezone
        granularity: day / week / month
        channel: None 或 'all' 表示全部，否则具体 channel 值
        range_key: 7d / 30d / 90d / custom（透传给前端）
    """
    b_start, b_end = bounds_for_dialect(getattr(db, "dialect", "postgres"), start, end)
    ch = _channel_arg(channel)
    gran = granularity

    conv_series = await db.fetch_named(
        "stats_conv_series", gran, tz, b_start, b_end, ch,
    )
    conv_total = await db.fetch_named("stats_conv_total", b_start, b_end, ch)
    active_series = await db.fetch_named(
        "stats_active_series", gran, tz, b_start, b_end, ch,
    )
    active_total = await db.fetch_named("stats_active_total", b_start, b_end, ch)
    new_series = await db.fetch_named(
        "stats_new_series", gran, tz, b_start, b_end, ch,
    )
    new_total = await db.fetch_named("stats_new_total", b_start, b_end, ch)

    conv_by = _row_map(conv_series)
    active_by = _row_map(active_series)
    new_by = _row_map(new_series)

    series: list[dict[str, Any]] = []
    for key in bucket_keys(start, end, gran):
        c = conv_by.get(key, {})
        a = active_by.get(key, {})
        n = new_by.get(key, {})
        series.append({
            "bucket": key,
            "uv": _norm_int(c.get("uv")),
            "sessions_active": _norm_int(a.get("sessions_active")),
            "sessions_new": _norm_int(n.get("sessions_new")),
            "turns": _norm_int(c.get("turns_v3")) + _norm_int(c.get("turns_legacy")),
        })

    def _totals_from_rows(conv_rows, active_rows, new_rows) -> dict[str, int]:
        ct = conv_rows[0] if conv_rows else {}
        at = active_rows[0] if active_rows else {}
        nt = new_rows[0] if new_rows else {}
        return {
            "uv": _norm_int(ct.get("uv")),
            "sessions_active": _norm_int(at.get("sessions_active")),
            "sessions_new": _norm_int(nt.get("sessions_new")),
            "turns": _norm_int(ct.get("turns_v3")) + _norm_int(ct.get("turns_legacy")),
        }

    totals = _totals_from_rows(conv_total, active_total, new_total)

    # 环比前窗：等长紧邻 [start-span, start)，与本窗同 channel 过滤
    p_start, p_end = prev_window(start, end)
    pb_start, pb_end = bounds_for_dialect(getattr(db, "dialect", "postgres"), p_start, p_end)
    prev_conv_total = await db.fetch_named("stats_conv_total", pb_start, pb_end, ch)
    prev_active_total = await db.fetch_named("stats_active_total", pb_start, pb_end, ch)
    prev_new_total = await db.fetch_named("stats_new_total", pb_start, pb_end, ch)
    prev_totals = _totals_from_rows(prev_conv_total, prev_active_total, prev_new_total)

    ch_conv = await db.fetch_named("stats_channel_conv", b_start, b_end)
    ch_active = await db.fetch_named("stats_channel_active", b_start, b_end)
    ch_new = await db.fetch_named("stats_channel_new", b_start, b_end)

    merged: dict[str, dict[str, Any]] = {}
    for rows, field in (
        (ch_conv, "conv"),
        (ch_active, "active"),
        (ch_new, "new"),
    ):
        for r in rows:
            key = r.get("channel") or ""
            entry = merged.setdefault(key, {
                "channel": key, "uv": 0, "sessions_active": 0,
                "sessions_new": 0, "turns": 0,
            })
            if field == "conv":
                entry["uv"] = _norm_int(r.get("uv"))
                entry["turns"] = _norm_int(r.get("turns_v3")) + _norm_int(r.get("turns_legacy"))
            elif field == "active":
                entry["sessions_active"] = _norm_int(r.get("sessions_active"))
            else:
                entry["sessions_new"] = _norm_int(r.get("sessions_new"))

    total_uv = sum(m["uv"] for m in merged.values())
    channels = []
    for m in merged.values():
        m["label"] = CHANNEL_LABELS.get(m["channel"], m["channel"])
        m["uv_share"] = round(m["uv"] / total_uv, 4) if total_uv else 0.0
        channels.append(m)
    channels.sort(key=lambda m: (-m["turns"], m["channel"]))

    options_rows = await db.fetch_named("stats_channel_options", b_start, b_end)
    channel_options = [
        {"value": r["channel"], "label": CHANNEL_LABELS.get(r["channel"], r["channel"])}
        for r in options_rows
        if r.get("channel")
    ]

    # 按天 × 渠道（明细表第二张；口径同 *_series，GROUP BY 再加 channel）
    bc_conv = await db.fetch_named("stats_bucket_channel_conv", gran, tz, b_start, b_end, ch)
    bc_active = await db.fetch_named("stats_bucket_channel_active", gran, tz, b_start, b_end, ch)
    bc_new = await db.fetch_named("stats_bucket_channel_new", gran, tz, b_start, b_end, ch)

    bc_merged: dict[tuple[str, str], dict[str, Any]] = {}
    for rows, field in (
        (bc_conv, "conv"),
        (bc_active, "active"),
        (bc_new, "new"),
    ):
        for r in rows:
            if r.get("bucket") is None:
                continue
            key = (bucket_key(r["bucket"]), r.get("channel") or "")
            entry = bc_merged.setdefault(key, {
                "bucket": key[0], "channel": key[1],
                "uv": 0, "sessions_active": 0, "sessions_new": 0, "turns": 0,
            })
            if field == "conv":
                entry["uv"] = _norm_int(r.get("uv"))
                entry["turns"] = _norm_int(r.get("turns_v3")) + _norm_int(r.get("turns_legacy"))
            elif field == "active":
                entry["sessions_active"] = _norm_int(r.get("sessions_active"))
            else:
                entry["sessions_new"] = _norm_int(r.get("sessions_new"))
    series_by_channel = sorted(
        bc_merged.values(), key=lambda e: (e["bucket"], e["channel"]),
    )

    return {
        "range": {
            "start": start.isoformat(),
            "end": end.isoformat(),
            "granularity": gran,
            "range_key": range_key,
            "channel": channel or CHANNEL_ALL,
            "timezone": tz,
        },
        "totals": totals,
        "prev_totals": prev_totals,
        "series": series,
        "series_by_channel": series_by_channel,
        "channels": channels,
        "channel_options": channel_options,
    }
