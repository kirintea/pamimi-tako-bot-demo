# -*- coding: utf-8 -*-

"""观测台 turns 路由测试 —— 最小 FastAPI app + FakeDB/FakeUserService

覆盖 api/observe.py：
- root 可访问，返回 turns DTO
- 非 root → 403
- DB 未初始化 → 503
- limit 只保留最近 N 轮
"""

from __future__ import annotations

from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.observe import router as observe_router


class FakeUserService:
    def __init__(self, role: str = "root"):
        self._role = role

    async def get_role(self, uid):
        # 对齐 core/user_service.UserService.get_role：未知/缺省 uid 一律 normal
        if not uid:
            return "normal"
        return self._role


class FakeDB:
    is_initialized = True

    def __init__(self, rows: list[dict]):
        self._rows = rows
        self.last_limit = None
        self.last_include_archived = None

    async def get_conversation_history(
        self, user_id, session_id, before_id=None, limit=50, include_archived=False
    ):
        self.last_limit = limit
        self.last_include_archived = include_archived
        return {"messages": list(self._rows), "has_more": False, "oldest_id": None}

    async def fetchval(self, sql, *args):
        return "wecom"


class FailingChannelDB(FakeDB):
    async def fetchval(self, sql, *args):
        raise RuntimeError("no such column")


def _rows() -> list[dict]:
    return [
        {"id": 1, "role": "user", "content": "查设备",
         "metadata": None, "created_at": "2026-10-15T10:16:01+08:00",
         "turn_id": "t1", "turn_seq": 0},
        {"id": 2, "role": "assistant", "content": "查到了",
         "metadata": {"type": "text"}, "created_at": "2026-10-15T10:16:02+08:00",
         "turn_id": "t1", "turn_seq": 1},
        {"id": 3, "role": "user", "content": "再查一台",
         "metadata": None, "created_at": "2026-10-15T10:17:01+08:00",
         "turn_id": "t2", "turn_seq": 0},
    ]


def _make_app(role: str = "root", rows=None, db=None) -> FastAPI:
    app = FastAPI()
    app.include_router(observe_router)
    app.state.user_service = FakeUserService(role)
    app.state.database_manager = db if db is not None else FakeDB(_rows())
    app.state.config = SimpleNamespace()
    return app


def test_turns_returns_grouped_turns():
    client = TestClient(_make_app())
    resp = client.get("/admin/observe/sessions/u1/s1/turns?viewer=root")
    assert resp.status_code == 200
    body = resp.json()
    assert body["user_id"] == "u1"
    assert body["session_id"] == "s1"
    assert body["channel"] == "wecom"
    assert len(body["turns"]) == 2
    first = body["turns"][0]
    assert first["turn_id"] == "t1"
    assert first["user_message"] == "查设备"
    assert [n["kind"] for n in first["nodes"]] == ["user", "text"]
    assert first["tool_summary"] == {"count": 0, "path": []}


def test_turns_forbidden_for_normal_user():
    client = TestClient(_make_app(role="normal"))
    resp = client.get("/admin/observe/sessions/u1/s1/turns?viewer=bob")
    assert resp.status_code == 403


def test_turns_503_when_db_missing():
    app = _make_app()
    app.state.database_manager = None
    client = TestClient(app)
    resp = client.get("/admin/observe/sessions/u1/s1/turns?viewer=root")
    assert resp.status_code == 503


def test_turns_limit_keeps_recent():
    client = TestClient(_make_app())
    resp = client.get("/admin/observe/sessions/u1/s1/turns?viewer=root&limit=1")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["turns"]) == 1
    assert body["turns"][0]["turn_id"] == "t2"


def test_turns_limit_clamped_and_scales_fetch_window():
    db = FakeDB(_rows())
    client = TestClient(_make_app(db=db))
    # limit=0 → 钳到 1；取行窗口 = max(500, 1*8) = 500
    resp = client.get("/admin/observe/sessions/u1/s1/turns?viewer=root&limit=0")
    assert resp.status_code == 200
    assert len(resp.json()["turns"]) == 1
    assert db.last_limit == 500

    # 默认 limit=50 → 取行窗口 = max(500, 50*8) = 500（下限兜住）
    resp = client.get("/admin/observe/sessions/u1/s1/turns?viewer=root")
    assert resp.status_code == 200
    assert db.last_limit == 500

    # limit=500 → 钳到 200；取行窗口 = max(500, 200*8) = 1600
    resp = client.get("/admin/observe/sessions/u1/s1/turns?viewer=root&limit=500")
    assert resp.status_code == 200
    assert db.last_limit == 1600


def test_turns_reads_archived_internal_rows():
    """观测台必须看见被「轮内压缩」归档的 thinking/tool_call 行

    chat_service 会把 thinking/tool_call 内部行软删成 status='archived'，
    使其退出 UI 回放与 PG 回填。观测台恰恰要观测这些中间链路，故取历史时
    必须显式打开 include_archived，否则整条链只剩 user + text。
    """
    db = FakeDB(_rows())
    client = TestClient(_make_app(db=db))
    resp = client.get("/admin/observe/sessions/u1/s1/turns?viewer=root")
    assert resp.status_code == 200
    assert db.last_include_archived is True


def test_turns_channel_lookup_failure_degrades_to_empty():
    client = TestClient(_make_app(db=FailingChannelDB(_rows())))
    resp = client.get("/admin/observe/sessions/u1/s1/turns?viewer=root")
    assert resp.status_code == 200
    assert resp.json()["channel"] == ""
    assert len(resp.json()["turns"]) == 2


def test_turns_empty_session():
    client = TestClient(_make_app(db=FakeDB([])))
    resp = client.get("/admin/observe/sessions/u1/s1/turns?viewer=root")
    assert resp.status_code == 200
    assert resp.json()["turns"] == []


def test_turns_forbidden_without_viewer():
    client = TestClient(_make_app())
    resp = client.get("/admin/observe/sessions/u1/s1/turns")
    assert resp.status_code == 403
