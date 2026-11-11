# -*- coding: utf-8 -*-
"""路线 C SSE 输出结构对齐路线 B — GET /sessions/{session_id}/stream

背景：路线 B（POST /chat/stream）输出 ``event: {type}\\ndata: {payload}\\n\\n``；
路线 C 此前只有 ``data:`` 行且无空行分块，EventSource / 共享解析器无法复用。
子计划：.draft/2026-11-10-route-c-sse-align-route-b.md（父调研 §7 缺口 5）。

不走 TestClient/HTTP：httpx ASGITransport 会把 ASGI 应用运行到完成才返回
（httpx/_transports/asgi.py 的 ``await self.app(...)``），无限 SSE 流在
HTTP 层必然挂死——路线 B 能过仅因其流会结束。故直接驱动端点函数返回的
``StreamingResponse.body_iterator``；HTTP 层 content-type 由
tests/test_ws_openapi.py::test_session_stream_declares_event_stream 守卫。
"""

from __future__ import annotations

import asyncio
import json
import uuid
from types import SimpleNamespace

import pytest

from agentscope.app.message_bus import MessageBusKeys
from api.chat import _sse_from_bus_event, session_event_stream
from core.message_bus import InMemoryMessageBus

# 每次跑用新 sid（InMemoryMessageBus 每测新建，固定值亦可，保留防串味）
UID = "u_route_c_sse"
SID = f"s_route_c_{uuid.uuid4().hex[:10]}"


def _parse_sse(text: str) -> list[tuple[str, dict]]:
    """按路线 B 格式解析（event: 行 + data 行 + 空行分块）——
    与 tests/test_command_ingress_api.py 的解析器同构。"""
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


def _fake_request(bus: InMemoryMessageBus) -> SimpleNamespace:
    """最小 Request 替身 — 端点读 message_bus；归属校验（缺口 7）走配对命中放行"""

    class _Mgr:
        async def session_exists(self, uid, sid):
            return True  # 本人配对命中 → _authorize_session_access 直接放行，不落 PG

    return SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(message_bus=bus, session_manager=_Mgr()),
        ),
    )


async def _collect(iterator, n: int, timeout: float = 5.0) -> list[str]:
    """从（无限）SSE 迭代器取 n 块后关闭，防止死等心跳"""
    out: list[str] = []
    try:
        for _ in range(n):
            out.append(await asyncio.wait_for(iterator.__anext__(), timeout))
    finally:
        await iterator.aclose()
    return out


# ----------------------------------------------------------------
# 单元：格式化函数（回放与实时两条 yield 路径共用）
# ----------------------------------------------------------------

def test_sse_from_bus_event_matches_route_b_framing():
    """type 移入 event: 行；载荷不含 type/_entry_id，与 B 的 data 形状一致"""
    out = _sse_from_bus_event({
        "type": "text_delta",
        "delta": "你好",
        "_entry_id": "12-0",
    })
    assert out == 'event: text_delta\ndata: {"delta": "你好"}\n\n'


def test_sse_from_bus_event_defaults_type_and_keeps_other_fields():
    """缺 type 回退 message；其余字段原样保留"""
    out = _sse_from_bus_event({"delta": "x"})
    assert out.startswith("event: message\n")
    assert 'data: {"delta": "x"}' in out


# ----------------------------------------------------------------
# 集成：真实生产者形状（/chat/ 指令分支的两个事件）→ 总线日志 → 路线 C 回放
# ----------------------------------------------------------------

@pytest.mark.asyncio(loop_scope="session")
async def test_route_c_replay_uses_event_line_format():
    """回放段事件带 event: 行，data 载荷与路线 B 同形（无 type 键）"""
    bus = InMemoryMessageBus()
    key = MessageBusKeys.session_events(SID)
    # 与 chat_trigger 指令分支发布形状一致（type 在 JSON 内）
    await bus.log_append(key, {"type": "text_delta", "delta": "你好"}, max_len=1000)
    await bus.log_append(
        key,
        {
            "type": "reply_end",
            "text": "你好",
            "finished": True,
            "command": True,
            "new_session_id": None,
        },
        max_len=1000,
    )

    resp = await session_event_stream(_fake_request(bus), SID, "u1")
    assert "text/event-stream" in resp.headers["content-type"]

    # 回放恰为 2 块；取满即关（第 3 块起进入订阅态，会等 30s 心跳）
    chunks = await _collect(resp.body_iterator, 2)
    text = "".join(chunks)

    # 原始块精确帧格式（与 _sse_event 一致）
    assert chunks[0] == 'event: text_delta\ndata: {"delta": "你好"}\n\n'

    # 解析出的事件序列与载荷形状
    events = _parse_sse(text)
    assert [t for t, _ in events] == ["text_delta", "reply_end"]
    for etype, data in events:
        assert "type" not in data, f"{etype} 的 data 不应再含 type 键: {data}"
    end = dict(events)["reply_end"]
    assert end.get("command") is True


@pytest.mark.asyncio(loop_scope="session")
async def test_route_c_live_push_uses_event_line_format():
    """实时订阅路径同样输出 event: 帧（事件发布于订阅建立之后）"""
    bus = InMemoryMessageBus()
    key = MessageBusKeys.session_events(SID)

    resp = await session_event_stream(_fake_request(bus), SID, "u1")
    # 日志为空 → 首块只能来自实时订阅：先起取块任务，再等订阅注册后发布
    collect_task = asyncio.create_task(_collect(resp.body_iterator, 1))
    for _ in range(200):  # InMemoryMessageBus.subscribe 首次运行时入 _subscribers
        if bus._subscribers.get(key):
            break
        await asyncio.sleep(0.01)
    else:
        collect_task.cancel()
        raise AssertionError("订阅未建立")

    await bus.log_append(key, {"type": "text_delta", "delta": "x"}, max_len=1000)
    await bus.publish(key, {"type": "text_delta", "delta": "x", "_entry_id": "1-0"})

    chunks = await collect_task
    assert chunks == ['event: text_delta\ndata: {"delta": "x"}\n\n']
