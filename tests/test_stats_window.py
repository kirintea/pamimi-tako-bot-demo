# -*- coding: utf-8 -*-

"""运营统计 —— 时间窗与分桶键纯函数"""

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from core.stats import bucket_keys, custom_window, prev_window, range_window

TZ = "Asia/Shanghai"


def test_range_window_30d_includes_today_left_closed_right_open():
    start, end = range_window(TZ, "30d", today=date(2026, 10, 22))
    assert start == datetime(2026, 9, 23, tzinfo=ZoneInfo(TZ))
    assert end == datetime(2026, 10, 23, tzinfo=ZoneInfo(TZ))


def test_range_window_7d_and_90d():
    s7, e7 = range_window(TZ, "7d", today=date(2026, 10, 22))
    assert (s7.date(), e7.date()) == (date(2026, 10, 16), date(2026, 10, 23))
    s90, _ = range_window(TZ, "90d", today=date(2026, 10, 22))
    assert s90.date() == date(2026, 7, 25)


def test_range_window_rejects_unknown():
    with pytest.raises(ValueError):
        range_window(TZ, "1y", today=date(2026, 10, 22))
    with pytest.raises(ValueError):
        range_window("Not/AZone", "7d", today=date(2026, 10, 22))


def test_bucket_keys_day_is_every_local_day():
    start, end = range_window(TZ, "7d", today=date(2026, 10, 22))
    keys = bucket_keys(start, end, "day")
    assert keys[0] == "2026-10-16"
    assert keys[-1] == "2026-10-22"
    assert len(keys) == 7


def test_bucket_keys_week_starts_monday():
    # 2026-10-22 是周四；窗口 10-16(周五) ~ 10-22 → 两个 ISO 周
    start, end = range_window(TZ, "7d", today=date(2026, 10, 22))
    keys = bucket_keys(start, end, "week")
    assert keys == ["2026-10-12", "2026-10-19"]


def test_bucket_keys_month():
    start, end = range_window(TZ, "30d", today=date(2026, 10, 22))
    keys = bucket_keys(start, end, "month")
    assert keys == ["2026-09-01", "2026-10-01"]


def test_bucket_keys_respects_timezone_day_boundary():
    """23:30 的消息按 Asia/Shanghai 落当天，不漏到次日"""
    start, end = range_window(TZ, "7d", today=date(2026, 10, 22))
    keys = bucket_keys(start, end, "day")
    assert "2026-10-22" in keys
    assert len(keys) == 7


def test_custom_window_inclusive_end_left_closed_right_open():
    start, end = custom_window(TZ, date(2026, 10, 17), date(2026, 10, 23))
    assert start == datetime(2026, 10, 17, tzinfo=ZoneInfo(TZ))
    assert end == datetime(2026, 10, 24, tzinfo=ZoneInfo(TZ))  # end+1 天 00:00，含 10-23


def test_custom_window_span_limits():
    # 单天合法
    s, e = custom_window(TZ, date(2026, 10, 17), date(2026, 10, 17))
    assert (e - s).days == 1
    # 366 天合法
    custom_window(TZ, date(2026, 1, 1), date(2026, 12, 31))
    # 367 天拒绝
    with pytest.raises(ValueError):
        custom_window(TZ, date(2026, 1, 1), date(2027, 1, 2))
    # end < start 拒绝
    with pytest.raises(ValueError):
        custom_window(TZ, date(2026, 10, 23), date(2026, 10, 17))
    # 未知时区拒绝
    with pytest.raises(ValueError):
        custom_window("Not/AZone", date(2026, 10, 17), date(2026, 10, 23))


def test_prev_window_equal_length_before_start():
    start, end = range_window(TZ, "7d", today=date(2026, 10, 22))
    assert (start.date(), end.date()) == (date(2026, 10, 16), date(2026, 10, 23))
    p_start, p_end = prev_window(start, end)
    assert p_end == start
    assert (p_start.date(), p_end.date()) == (date(2026, 10, 9), date(2026, 10, 16))
    assert (p_end - p_start) == (end - start)  # 等长
