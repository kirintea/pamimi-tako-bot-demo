# -*- coding: utf-8 -*-
"""channel 入参 wiring 测试 — 三路对话接口可选 channel（默认 web）

子计划：.draft/2026-11-11-user-info-and-channel-param.md（父调研 §7 缺口 3 + §10）。
覆盖：请求模型（Task 5）/ _persist_conversation 去硬编码（Task 6）/
      路线 B（Task 7）/ 路线 C（Task 8）/ 路线 A WS（Task 9）。
"""

from __future__ import annotations

import threading
from types import SimpleNamespace

import pytest
from fastapi import WebSocketDisconnect
from fastapi.testclient import TestClient
from pydantic import ValidationError

from agentscope.event import EventType

from api.chat import ChatRequest, ChatTriggerRequest


# ============================================================
# 请求模型（Task 5）
# ============================================================


def test_chat_request_channel_defaults_web():
    assert ChatRequest(message="hi").channel == "web"


def test_chat_request_accepts_custom_channel():
    assert ChatRequest(message="hi", channel="partner_a").channel == "partner_a"


def test_chat_request_rejects_invalid_channel():
    with pytest.raises(ValidationError):
        ChatRequest(message="hi", channel="bad channel!")
    with pytest.raises(ValidationError):
        ChatRequest(message="hi", channel="x" * 17)


def test_chat_trigger_request_channel_default_and_valid():
    assert ChatTriggerRequest(message="hi").channel == "web"
    assert ChatTriggerRequest(message="hi", channel="wecom").channel == "wecom"


def test_chat_trigger_request_rejects_invalid_channel():
    with pytest.raises(ValidationError):
        ChatTriggerRequest(message="hi", channel="bad!")


# ============================================================
# _persist_conversation — 去硬编码（Task 6）
# ============================================================


class _PersistFakeDB:
    is_initialized = True

    def __init__(self) -> None:
        self.inserts: list[dict] = []
        self.titles: list[str] = []

    async def insert_conversation(
        self, user_id, session_id, role, content,
        metadata=None, channel="web", turn_id=None, turn_seq=None,
    ):
        self.inserts.append({"role": role, "channel": channel})
        return len(self.inserts)

    async def get_session_title(self, user_id, session_id):
        return None  # 触发 upsert_session_title 分支

    async def upsert_session_title(self, user_id, session_id, title,
                                   channel="web"):
        self.titles.append(channel)


@pytest.mark.asyncio(loop_scope="session")
async def test_persist_conversation_chat_passes_channel():
    from api.chat import _persist_conversation

    db = _PersistFakeDB()
    await _persist_conversation(
        None, db, "u1", "s1", "你好", "回复", channel="partner_a",
    )
    assert db.inserts and {r["channel"] for r in db.inserts} == {"partner_a"}
    assert db.titles == ["partner_a"]


@pytest.mark.asyncio(loop_scope="session")
async def test_persist_conversation_chat_defaults_web():
    from api.chat import _persist_conversation

    db = _PersistFakeDB()
    await _persist_conversation(None, db, "u1", "s1", "你好", "回复")
    assert {r["channel"] for r in db.inserts} == {"web"}
    assert db.titles == ["web"]


@pytest.mark.asyncio(loop_scope="session")
async def test_persist_conversation_ws_passes_channel():
    from api.ws_chat import _persist_conversation

    db = _PersistFakeDB()
    await _persist_conversation(
        None, db, "u1", "s1", "你好", "回复", channel="partner_a",
    )
    assert {r["channel"] for r in db.inserts} == {"partner_a"}
    assert db.titles == ["partner_a"]


# ============================================================
# 路线 B：POST /chat/stream（Task 7）
# ============================================================


class _StreamAgent:
    """有限事件流的假 Agent — 流必须会结束，TestClient 才不会挂死
    （httpx ASGITransport 要等应用跑完才返回，见 test_route_c_sse_format 注释）"""

    def __init__(self) -> None:
        self.__tracing_context__ = None

    async def reply_stream(self, msg):
        yield SimpleNamespace(type=EventType.TEXT_BLOCK_DELTA, delta="你好")
        yield SimpleNamespace(type=EventType.REPLY_END, finished_reason="stop")


class _RecordingSessionMgr:
    """只实现三条路由用到的方法，记录 get_or_create 的 channel 入参"""

    def __init__(self, agent) -> None:
        self._agent = agent
        self.get_or_create_kwargs: list[dict] = []

    async def get_or_create(self, user_id, session_id, display_name=None,
                            channel="web"):
        self.get_or_create_kwargs.append(
            {"display_name": display_name, "channel": channel},
        )
        return self._agent

    async def refresh_state(self, user_id, session_id) -> None:
        pass

    async def save(self, user_id, session_id) -> None:
        pass

    async def load_session_meta(self, user_id, session_id):
        return None


def _make_chat_app():
    """最小应用：只挂 chat_router，状态全用替身（无 DB/Redis/LLM）"""
    from fastapi import FastAPI

    from api.chat import router as chat_router

    app = FastAPI()
    app.include_router(chat_router)
    agent = _StreamAgent()
    mgr = _RecordingSessionMgr(agent)
    app.state.session_manager = mgr
    app.state.database_manager = None
    app.state.message_bus = None
    app.state.session_status_tracker = None
    app.state.object_storage = None
    app.state.config = SimpleNamespace(
        llm=SimpleNamespace(timeout=5),
        auth=SimpleNamespace(required=False),
    )
    return app, mgr, agent


def test_chat_stream_forwards_channel_to_session_and_tracing():
    app, mgr, agent = _make_chat_app()
    client = TestClient(app)
    resp = client.post(
        "/chat/stream", json={"message": "你好", "channel": "partner_a"},
    )
    assert resp.status_code == 200, resp.text
    assert mgr.get_or_create_kwargs, "应调用 get_or_create"
    assert mgr.get_or_create_kwargs[-1]["channel"] == "partner_a"
    assert agent.__tracing_context__["agentscope.channel"] == "partner_a"
    assert agent.__tracing_context__["agentscope.user.id"]  # 既有键不回归


def test_chat_stream_channel_defaults_web():
    app, mgr, agent = _make_chat_app()
    client = TestClient(app)
    resp = client.post("/chat/stream", json={"message": "你好"})
    assert resp.status_code == 200, resp.text
    assert mgr.get_or_create_kwargs[-1]["channel"] == "web"
    assert agent.__tracing_context__["agentscope.channel"] == "web"


def test_chat_stream_rejects_invalid_channel_422():
    app, _, _ = _make_chat_app()
    client = TestClient(app)
    resp = client.post(
        "/chat/stream", json={"message": "hi", "channel": "bad channel!"},
    )
    assert resp.status_code == 422


# ============================================================
# 路线 C：POST /chat/（Task 8）
# ============================================================


class _RecordingChatService:
    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.done = threading.Event()

    async def run(self, *args, **kwargs):
        self.calls.append({"args": args, "kwargs": kwargs})
        self.done.set()


def _make_trigger_app():
    from fastapi import FastAPI

    from api.chat import router as chat_router

    app = FastAPI()
    app.include_router(chat_router)
    svc = _RecordingChatService()
    app.state.chat_service = svc
    app.state.session_manager = _RecordingSessionMgr(_StreamAgent())
    app.state.message_bus = None
    app.state.database_manager = None
    app.state.session_status_tracker = None
    app.state.object_storage = None
    app.state.config = SimpleNamespace(
        llm=SimpleNamespace(timeout=5),
        auth=SimpleNamespace(required=False),
    )
    return app, svc


def test_chat_trigger_forwards_channel_to_run():
    app, svc = _make_trigger_app()
    client = TestClient(app)
    resp = client.post("/chat/", json={"message": "你好", "channel": "partner_a"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "started"
    assert svc.done.wait(timeout=3), "chat_service.run 应被后台触发"
    assert svc.calls[0]["kwargs"].get("channel") == "partner_a"


def test_chat_trigger_channel_defaults_web():
    app, svc = _make_trigger_app()
    client = TestClient(app)
    resp = client.post("/chat/", json={"message": "你好"})
    assert resp.status_code == 200, resp.text
    assert svc.done.wait(timeout=3)
    assert svc.calls[0]["kwargs"].get("channel") == "web"


def test_chat_trigger_rejects_invalid_channel_422():
    app, _ = _make_trigger_app()
    client = TestClient(app)
    resp = client.post("/chat/", json={"message": "hi", "channel": "bad!"})
    assert resp.status_code == 422


# ============================================================
# 路线 A：WS /ws/chat query channel（Task 9）
# ============================================================


def _make_ws_app():
    from fastapi import FastAPI

    from api.ws_chat import router as ws_router

    app = FastAPI()
    app.include_router(ws_router)
    agent = _StreamAgent()
    mgr = _RecordingSessionMgr(agent)
    app.state.session_manager = mgr
    app.state.database_manager = None
    app.state.message_bus = None
    app.state.session_status_tracker = None
    app.state.object_storage = None
    app.state.config = SimpleNamespace(
        llm=SimpleNamespace(timeout=5),
        auth=SimpleNamespace(required=False),
    )
    return app, mgr, agent


def _drain_until_reply_end(ws) -> None:
    while True:
        msg = ws.receive_json()
        if msg["type"] == "reply_end":
            return


def test_ws_chat_forwards_channel_query_param():
    app, mgr, agent = _make_ws_app()
    client = TestClient(app)
    with client.websocket_connect(
        "/ws/chat?user_id=u1&session_id=s1&channel=partner_a",
    ) as ws:
        assert ws.receive_json()["type"] == "connected"
        ws.send_json({"type": "chat", "payload": {"message": "你好"}})
        _drain_until_reply_end(ws)
    assert mgr.get_or_create_kwargs[-1]["channel"] == "partner_a"
    assert agent.__tracing_context__["agentscope.channel"] == "partner_a"


def test_ws_chat_channel_defaults_web():
    app, mgr, agent = _make_ws_app()
    client = TestClient(app)
    with client.websocket_connect(
        "/ws/chat?user_id=u1&session_id=s2",
    ) as ws:
        assert ws.receive_json()["type"] == "connected"
        ws.send_json({"type": "chat", "payload": {"message": "你好"}})
        _drain_until_reply_end(ws)
    assert mgr.get_or_create_kwargs[-1]["channel"] == "web"
    assert agent.__tracing_context__["agentscope.channel"] == "web"


def test_ws_chat_invalid_channel_error_then_4400():
    """非法 channel：error 帧 + close 4400（D5，对齐 user_id 处理）"""
    app, _, _ = _make_ws_app()
    client = TestClient(app)
    with client.websocket_connect(
        "/ws/chat?user_id=u1&channel=bad!",
    ) as ws:
        first = ws.receive_json()
        assert first["type"] == "error"
        # starlette TestSession：receive() 返回原始 ASGI 消息，仅
        # receive_text/json 经 _raise_on_close 抛 WebSocketDisconnect
        with pytest.raises(WebSocketDisconnect) as exc:
            ws.receive_json()
        assert exc.value.code == 4400
