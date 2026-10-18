# -*- coding: utf-8 -*-
"""Web 入口指令拦截集成测试 — /chat/stream 与 /chat/ 不进 LLM 直接回执"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from main import app

UID = "u_cmd_api"
SID = "s_cmd_api"


@pytest.fixture
def client():
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


def _parse_sse(text: str) -> list[tuple[str, dict]]:
    events = []
    for block in text.split("\n\n"):
        etype, data = "", {}
        for line in block.splitlines():
            if line.startswith("event: "):
                etype = line[len("event: "):]
            elif line.startswith("data: "):
                data = json.loads(line[len("data: "):])
        if etype:
            events.append((etype, data))
    return events


def test_chat_stream_help_no_llm(client):
    with client.stream("POST", "/chat/stream", json={
        "user_id": UID, "session_id": SID, "message": "/help",
    }) as r:
        assert r.status_code == 200
        events = _parse_sse(r.read().decode("utf-8"))
    types = [e for e, _ in events]
    assert "text_delta" in types and "reply_end" in types
    end = dict(events)["reply_end"]
    assert end.get("command") is True
    assert "/new" in end.get("text", "")


def test_chat_stream_new_returns_new_session_id(client):
    with client.stream("POST", "/chat/stream", json={
        "user_id": UID, "session_id": SID, "message": "/new",
    }) as r:
        events = _parse_sse(r.read().decode("utf-8"))
    end = dict(events)["reply_end"]
    assert end.get("command") is True
    assert end.get("new_session_id") and end["new_session_id"] != SID


def test_chat_trigger_help(client):
    r = client.post("/chat/", json={
        "user_id": UID, "session_id": SID, "message": "/help",
    })
    assert r.status_code == 200
    assert r.json()["status"] == "command"


def test_plain_message_not_treated_as_command(client):
    """普通消息不产生 command 标记（走正常 LLM 链路，事件序列无 command 字段）。"""
    with client.stream("POST", "/chat/stream", json={
        "user_id": UID, "session_id": SID, "message": "你好",
    }) as r:
        events = _parse_sse(r.read().decode("utf-8"))
    for etype, data in events:
        if etype == "reply_end":
            assert not data.get("command")