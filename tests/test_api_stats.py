# -*- coding: utf-8 -*-

"""运营统计路由测试 —— root 门控 / 503 / 响应形状"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.stats import router as stats_router


class FakeUserService:
    def __init__(self, role: str = "root"):
        self._role = role

    async def get_role(self, uid):
        if not uid:
            return "normal"
        return self._role


class FakeDB:
    is_initialized = True
    dialect = "postgres"

    def __init__(self):
        self.names: list[str] = []

    async def fetch_named(self, name, *args):
        self.names.append(name)
        if name == "stats_conv_series":
            return []
        if name == "stats_conv_total":
            return [{"uv": 2, "turns_v3": 3, "turns_legacy": 4}]
        if name == "stats_active_series":
            return []
        if name == "stats_active_total":
            return [{"sessions_active": 1}]
        if name == "stats_new_series":
            return []
        if name == "stats_new_total":
            return [{"sessions_new": 2}]
        if name == "stats_channel_conv":
            return [{"channel": "web", "uv": 2, "turns_v3": 3, "turns_legacy": 4}]
        if name == "stats_channel_active":
            return [{"channel": "web", "sessions_active": 1}]
        if name == "stats_channel_new":
            return [{"channel": "web", "sessions_new": 2}]
        if name == "stats_channel_options":
            return [{"channel": "web"}]
        return []


def _app(role: str = "root", db=None) -> FastAPI:
    app = FastAPI()
    app.include_router(stats_router)
    app.state.database_manager = db if db is not None else FakeDB()
    app.state.user_service = FakeUserService(role)
    return app


def test_root_can_read_overview():
    client = TestClient(_app())
    res = client.get("/admin/stats", params={"viewer": "root1"})
    assert res.status_code == 200
    body = res.json()
    assert body["totals"] == {
        "uv": 2, "sessions_active": 1, "sessions_new": 2, "turns": 7,
    }
    assert body["range"]["range_key"] == "30d"
    assert body["range"]["granularity"] == "day"
    assert len(body["series"]) == 30


def test_non_root_gets_403():
    client = TestClient(_app(role="normal"))
    res = client.get("/admin/stats", params={"viewer": "u1"})
    assert res.status_code == 403
    assert "root" in res.json()["detail"]


def test_missing_viewer_gets_403():
    client = TestClient(_app())
    res = client.get("/admin/stats")
    assert res.status_code == 403


def test_db_not_initialized_503():
    class Down:
        is_initialized = False

    client = TestClient(_app(db=Down()))
    res = client.get("/admin/stats", params={"viewer": "root1"})
    assert res.status_code == 503


def test_rejects_bad_range_and_granularity():
    client = TestClient(_app())
    assert client.get("/admin/stats",
                      params={"viewer": "root1", "range": "1y"}).status_code == 422
    assert client.get("/admin/stats",
                      params={"viewer": "root1", "granularity": "hour"}).status_code == 422


def test_channel_all_maps_to_null_arg():
    client = TestClient(_app())
    res = client.get("/admin/stats", params={"viewer": "root1", "channel": "all"})
    assert res.status_code == 200
    assert res.json()["range"]["channel"] == "all"


def test_export_csv_has_bom_and_expected_columns():
    client = TestClient(_app())
    res = client.get("/admin/stats/export.csv",
                     params={"viewer": "root1", "range": "7d", "granularity": "day"})
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("text/csv")
    assert "attachment" in res.headers["content-disposition"]
    raw = res.content
    assert raw.startswith(b"\xef\xbb\xbf"), "CSV 必须带 UTF-8 BOM"
    text = raw.decode("utf-8-sig")
    lines = text.strip().splitlines()
    assert lines[0] == "日期,使用人数,活跃会话,新增会话,对话轮次"
    assert len(lines) == 10  # 表头 + 7 行 + 空行 + 第二段表头（FakeDB 无 bucket_channel 行）
    assert lines[8] == ""
    assert lines[9] == "日期,渠道,使用人数,活跃会话,新增会话,对话轮次"


def test_export_csv_filename_reflects_filters():
    client = TestClient(_app())
    res = client.get("/admin/stats/export.csv",
                     params={"viewer": "root1", "range": "30d",
                             "granularity": "week", "channel": "wecom"})
    assert "ops-stats-30d-week-wecom.csv" in res.headers["content-disposition"]


def test_export_csv_requires_root():
    client = TestClient(_app(role="normal"))
    res = client.get("/admin/stats/export.csv", params={"viewer": "u1"})
    assert res.status_code == 403


def test_start_end_custom_range_ok():
    client = TestClient(_app())
    res = client.get("/admin/stats", params={
        "viewer": "root1", "start": "2026-10-16", "end": "2026-10-22",
    })
    assert res.status_code == 200
    body = res.json()
    assert body["range"]["range_key"] == "custom"
    assert len(body["series"]) == 7  # 含首尾 7 天


def test_range_and_start_end_mutually_exclusive():
    client = TestClient(_app())
    res = client.get("/admin/stats", params={
        "viewer": "root1", "range": "7d", "start": "2026-10-16", "end": "2026-10-22",
    })
    assert res.status_code == 422
    assert "range" in res.json()["detail"]


def test_start_without_end_rejected():
    client = TestClient(_app())
    assert client.get("/admin/stats", params={
        "viewer": "root1", "start": "2026-10-16",
    }).status_code == 422


def test_custom_range_span_limits():
    client = TestClient(_app())
    # 367 天 → 422
    res = client.get("/admin/stats", params={
        "viewer": "root1", "start": "2026-01-01", "end": "2027-01-02",
    })
    assert res.status_code == 422
    # end < start → 422
    res = client.get("/admin/stats", params={
        "viewer": "root1", "start": "2026-10-23", "end": "2026-10-17",
    })
    assert res.status_code == 422


def test_response_has_prev_totals_and_series_by_channel():
    client = TestClient(_app())
    res = client.get("/admin/stats", params={"viewer": "root1"})
    body = res.json()
    assert body["prev_totals"] == {
        "uv": 2, "sessions_active": 1, "sessions_new": 2, "turns": 7,
    }  # FakeDB 前窗复用同行
    assert body["series_by_channel"] == []  # FakeDB 未预置 bucket_channel 行


def test_export_csv_second_section_day_by_channel():
    class FakeDB2(FakeDB):
        async def fetch_named(self, name, *args):
            if name == "stats_bucket_channel_conv":
                return [{"bucket": __import__("datetime").date(2026, 10, 16),
                         "channel": "wecom", "uv": 1, "turns_v3": 2, "turns_legacy": 0}]
            return await super().fetch_named(name, *args)

    client = TestClient(_app(db=FakeDB2()))
    res = client.get("/admin/stats/export.csv", params={
        "viewer": "root1", "range": "7d", "granularity": "day",
    })
    assert res.status_code == 200
    text = res.content.decode("utf-8-sig")
    lines = text.strip().splitlines()
    # 第一段：表头 + 近7天 7 行；空行；第二段：表头 + 1 行
    assert lines[0] == "日期,使用人数,活跃会话,新增会话,对话轮次"
    assert lines[8] == ""
    assert lines[9] == "日期,渠道,使用人数,活跃会话,新增会话,对话轮次"
    assert lines[10] == "2026-10-16,企业微信,1,0,0,2"
    assert len(lines) == 11


def test_export_csv_custom_range_filename():
    client = TestClient(_app())
    res = client.get("/admin/stats/export.csv", params={
        "viewer": "root1", "start": "2026-10-16", "end": "2026-10-22",
    })
    assert res.status_code == 200
    assert "ops-stats-custom-2026-10-16-2026-10-22-day-all.csv" in res.headers["content-disposition"]
