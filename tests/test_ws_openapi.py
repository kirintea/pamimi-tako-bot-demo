# -*- coding: utf-8 -*-

"""非 HTTP 接口进 OpenAPI 文档测试（方案 A）

背景：OpenAPI 3.x 无 WebSocket 通道概念，FastAPI 的 @router.websocket 不会
写入 schema，导致 /ws/chat 不出现在 /docs；SSE 端点按普通 JSON 接口展示，
未标注 text/event-stream。
方案与决策记录：.draft/2026-11-10-ws-in-openapi-docs.md（人工已批准方案 A）。

验收标准：
  - openapi.json 含 /ws/chat（get 操作 + x-websocket 扩展标记）
  - 描述覆盖协议要点：query 参数、首帧认证、入站/出站帧、close code、
    Try it out 不可用提示
  - SSE 端点 200 响应声明 text/event-stream
  - 既有 HTTP 路径不受影响

若 server.py 因环境缺少依赖无法导入, 整体跳过并给出说明（与 test_server_auth 一致）。
"""

import pytest

try:
    from core.config import ConfigManager
    from server import create_app
    _HAVE_SERVER = True
    _IMPORT_ERROR = None
except Exception as exc:  # pragma: no cover
    _HAVE_SERVER = False
    _IMPORT_ERROR = exc


@pytest.fixture(autouse=True)
def _require_server():
    if not _HAVE_SERVER:
        pytest.skip(f"server.py 不可导入, 跳过 openapi 扩展测试: {_IMPORT_ERROR}")


@pytest.fixture
def client(monkeypatch):
    from fastapi.testclient import TestClient

    monkeypatch.setenv("LLM_API_KEY", "test-key")
    monkeypatch.setenv("LLM_BASE_URL", "http://localhost:1/v1")

    ConfigManager._instance = None
    config = ConfigManager.get_instance().load()
    app = create_app(config)
    return TestClient(app)


@pytest.fixture
def schema(client):
    resp = client.get("/openapi.json")
    assert resp.status_code == 200
    return resp.json()


# ----------------------------------------------------------------
# /ws/chat 注入
# ----------------------------------------------------------------

def test_openapi_contains_ws_chat(schema):
    assert "/ws/chat" in schema["paths"], "openapi.json 应包含 /ws/chat"
    op = schema["paths"]["/ws/chat"]["get"]
    assert op.get("x-websocket") is True, "应携带 x-websocket: true 扩展标记"
    assert op["operationId"] == "websocket_chat"


def test_ws_chat_query_parameters(schema):
    op = schema["paths"]["/ws/chat"]["get"]
    params = {p["name"]: p for p in op.get("parameters", []) if p["in"] == "query"}
    assert set(params) >= {"user_id", "session_id", "device_id", "channel"}


def test_ws_chat_description_covers_protocol(schema):
    op = schema["paths"]["/ws/chat"]["get"]
    desc = op["description"]

    # 连接方式 + Try it out 不可用提示（点了会 GET → 404）
    assert "wss://" in desc
    assert "404" in desc

    # 认证首帧与 close code
    assert '"type":"auth"' in desc.replace(" ", "")
    assert "4401" in desc
    assert "4400" in desc

    # 入站帧（5 种）
    for frame in ("auth", "chat", "cancel", "interrupt", "ping"):
        assert frame in desc, f"入站帧缺失: {frame}"

    # 出站帧（14 种）
    for frame in (
        "connected", "pong", "busy", "pending_reply",
        "generation_in_progress", "other_device_active",
        "text_delta", "thinking_delta", "tool_call", "tool_result",
        "reply_end", "interrupted", "interrupt_requested", "error",
    ):
        assert frame in desc, f"出站帧缺失: {frame}"

    # 多模态 message 结构
    assert "ContentPart" in desc
    assert "images/upload" in desc


def test_existing_paths_unaffected(schema):
    """注入不得破坏既有 HTTP 路径。"""
    assert len(schema["paths"]) >= 40  # 39 既有 + /ws/chat
    assert "/chat/stream" in schema["paths"]
    assert "/mcp" in schema["paths"]


# ----------------------------------------------------------------
# SSE 端点标注 text/event-stream
# ----------------------------------------------------------------

def test_chat_stream_declares_event_stream(schema):
    op = schema["paths"]["/chat/stream"]["post"]
    content = op["responses"]["200"]["content"]
    assert "text/event-stream" in content, "/chat/stream 应声明 SSE 响应类型"


def test_session_stream_declares_event_stream(schema):
    op = schema["paths"]["/sessions/{session_id}/stream"]["get"]
    content = op["responses"]["200"]["content"]
    assert "text/event-stream" in content, "会话事件流应声明 SSE 响应类型"


# ----------------------------------------------------------------
# 与已修复的 /docs 本地化共存
# ----------------------------------------------------------------

def test_docs_page_still_local_after_injection(client):
    resp = client.get("/docs")
    assert resp.status_code == 200
    assert "cdn.jsdelivr.net" not in resp.text
