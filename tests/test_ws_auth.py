# -*- coding: utf-8 -*-

"""WS 首帧认证测试 — D8 与 HTTP 中间件同源 + 凭证表校验 + channel 覆盖（缺口 2）

覆盖：
  - 仅 YAML 开认证（env 未设）→ WS 也要求首帧（D8 漂移修复，旧行为宽开）
  - 主 key（env / YAML 双源）→ connected（D8 比对源修复）
  - 凭证 key 命中 → connected 且 channel 被凭证覆盖（D2）
  - 凭证未命中 → error 帧 + close 4401
  - 凭证库不可用（未配置/异常）→ error 帧 + close 4503（D3）
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

import api.ws_chat as ws_chat_mod
from api.ws_chat import router

UID = "u_ws_auth"


class _SessionMgr:
    async def load_session_meta(self, user_id, session_id):
        return None


class _FakeDB:
    is_initialized = True

    def __init__(self, row=None, exc=None):
        self.row, self.exc = row, exc

    async def fetch_named(self, name, *args):
        if self.exc:
            raise self.exc
        return self.row


class _FakeLogger:
    """捕获 loguru 调用（monkeypatch api.ws_chat.logger 用）"""

    def __init__(self):
        self.calls = []

    def info(self, *a, **k):
        self.calls.append(a)

    def debug(self, *a, **k):
        self.calls.append(a)

    def warning(self, *a, **k):
        self.calls.append(a)

    def exception(self, *a, **k):
        self.calls.append(a)


def _app(config=None, db=None) -> FastAPI:
    """最小 app：仅含 /ws/chat，state 用假对象（不依赖活服务）"""
    app = FastAPI()
    app.include_router(router)
    app.state.config = config
    app.state.session_manager = _SessionMgr()
    app.state.database_manager = db
    app.state.message_bus = None
    app.state.session_status_tracker = None
    return app


def _cfg(required: bool = True, api_key: str = "") -> SimpleNamespace:
    return SimpleNamespace(auth=SimpleNamespace(required=required, api_key=api_key))


def _err_then_close(ws) -> int:
    """断言先收 error 帧、随后连接被关闭，返回 close code"""
    msg = ws.receive_json()
    assert msg["type"] == "error", msg
    with pytest.raises(WebSocketDisconnect) as ei:
        ws.receive_json()
    return ei.value.code


# ----------------------------------------------------------------
# D8：认证开关与主 key 比对源同 HTTP 中间件
# ----------------------------------------------------------------

def test_ws_auth_yaml_only_enables_protection(monkeypatch):
    """仅 YAML 开认证（env AUTH_REQUIRED 未设）→ WS 也要求首帧（旧实现 env-only 宽开 → 红）"""
    monkeypatch.delenv("AUTH_REQUIRED", raising=False)
    monkeypatch.delenv("API_KEY", raising=False)
    app = _app(config=_cfg(required=True))
    with TestClient(app) as client:
        with client.websocket_connect(f"/ws/chat?user_id={UID}") as ws:
            # 不发 auth 帧，先发 chat 帧 → error + 4401
            ws.send_json({"type": "chat", "payload": {"message": "hi"}})
            assert _err_then_close(ws) == 4401


def test_ws_auth_env_master_key_passes(monkeypatch):
    monkeypatch.setenv("AUTH_REQUIRED", "true")
    monkeypatch.setenv("API_KEY", "master123")
    app = _app(config=None)
    with TestClient(app) as client:
        with client.websocket_connect(f"/ws/chat?user_id={UID}") as ws:
            ws.send_json({"type": "auth", "payload": {"api_key": "master123"}})
            assert ws.receive_json()["type"] == "connected"


def test_ws_auth_yaml_master_key_passes(monkeypatch):
    """主 key 比对读 env > YAML 双源（旧实现只读 env → YAML 主 key 被 4401 → 红）"""
    monkeypatch.delenv("AUTH_REQUIRED", raising=False)
    monkeypatch.delenv("API_KEY", raising=False)
    app = _app(config=_cfg(required=True, api_key="yaml-key"))
    with TestClient(app) as client:
        with client.websocket_connect(f"/ws/chat?user_id={UID}") as ws:
            ws.send_json({"type": "auth", "payload": {"api_key": "yaml-key"}})
            assert ws.receive_json()["type"] == "connected"


# ----------------------------------------------------------------
# 凭证表校验（缺口 2）+ channel 覆盖（D2）
# ----------------------------------------------------------------

def test_ws_auth_credential_hit_and_channel_override(monkeypatch):
    monkeypatch.setenv("AUTH_REQUIRED", "true")
    monkeypatch.delenv("API_KEY", raising=False)
    fake_logger = _FakeLogger()
    monkeypatch.setattr(ws_chat_mod, "logger", fake_logger)
    db = _FakeDB(row={"id": 1, "cred_type": "api_key", "app_id": "acme_app",
                      "channel": "acme", "scopes": "chat"})
    app = _app(config=None, db=db)
    with TestClient(app) as client:
        with client.websocket_connect(f"/ws/chat?user_id={UID}&channel=web") as ws:
            ws.send_json({"type": "auth", "payload": {"api_key": "ak_cred"}})
            assert ws.receive_json()["type"] == "connected"
    # query channel=web 与凭证 channel=acme 不一致 → 覆盖并打 INFO
    assert any("凭证覆盖" in str(call) for call in fake_logger.calls), fake_logger.calls


def test_ws_auth_credential_miss_4401(monkeypatch):
    monkeypatch.setenv("AUTH_REQUIRED", "true")
    monkeypatch.delenv("API_KEY", raising=False)
    app = _app(config=None, db=_FakeDB(row=None))
    with TestClient(app) as client:
        with client.websocket_connect(f"/ws/chat?user_id={UID}") as ws:
            ws.send_json({"type": "auth", "payload": {"api_key": "ak_bad"}})
            assert _err_then_close(ws) == 4401


def test_ws_auth_db_down_4503(monkeypatch):
    monkeypatch.setenv("AUTH_REQUIRED", "true")
    monkeypatch.delenv("API_KEY", raising=False)

    # DB 未配置（database_manager 缺失）→ 4503
    app = _app(config=None, db=None)
    with TestClient(app) as client:
        with client.websocket_connect(f"/ws/chat?user_id={UID}") as ws:
            ws.send_json({"type": "auth", "payload": {"api_key": "ak_x"}})
            assert _err_then_close(ws) == 4503

    # 查询异常 → 4503
    app2 = _app(config=None, db=_FakeDB(exc=RuntimeError("db down")))
    with TestClient(app2) as client:
        with client.websocket_connect(f"/ws/chat?user_id={UID}") as ws:
            ws.send_json({"type": "auth", "payload": {"api_key": "ak_x"}})
            assert _err_then_close(ws) == 4503
