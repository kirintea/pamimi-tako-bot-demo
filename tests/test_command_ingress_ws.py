# -*- coding: utf-8 -*-
"""WS 入口指令拦截测试 — chat 帧命中指令直接回执；生成中也能发 /stop（D6）"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from main import app

UID = "u_cmd_ws"
SID = "s_cmd_ws"


@pytest.fixture
def client():
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


def test_ws_help_command(client):
    with client.websocket_connect(f"/ws/chat?user_id={UID}&session_id={SID}") as ws:
        assert ws.receive_json()["type"] == "connected"
        ws.send_json({"type": "chat", "payload": {"message": "/help"}})
        types = []
        end = {}
        while True:
            msg = ws.receive_json()
            types.append(msg["type"])
            if msg["type"] == "reply_end":
                end = msg["payload"] if "payload" in msg else msg
                break
        assert "text_delta" in types
        assert end.get("command") is True
        assert "/new" in end.get("text", "")


def test_ws_new_command_carries_new_session(client):
    with client.websocket_connect(f"/ws/chat?user_id={UID}&session_id={SID}") as ws:
        assert ws.receive_json()["type"] == "connected"
        ws.send_json({"type": "chat", "payload": {"message": "/new"}})
        while True:
            msg = ws.receive_json()
            if msg["type"] == "reply_end":
                payload = msg.get("payload", msg)
                assert payload.get("command") is True
                assert payload.get("new_session_id") != SID
                break