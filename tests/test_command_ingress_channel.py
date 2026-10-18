# -*- coding: utf-8 -*-
"""渠道入站指令拦截测试 — 命中指令直接回执，不进 ChatService.run（企微路径）"""

from __future__ import annotations

import asyncio

import pytest

from core.channels.events import InboundMessage
from core.channels.manager import ChannelManager
from core.channels.session_map import ChannelSessionMap
from core.command.service import CommandService
from tests.test_channel_session_map import FakeKV, PREFIX


class FakeChannel:
    name = "wecom"
    _running = True

    def __init__(self) -> None:
        self.sent: list[tuple[str, str, bool]] = []

    async def send_delta(self, chat_id, delta, *, stream_id, stream_end=False):
        self.sent.append((chat_id, delta, stream_end))


class FakeSubscription:
    """模拟事件总线订阅 — yield run_end 以终止 handle_inbound 的事件循环。"""

    def __init__(self) -> None:
        self._events: asyncio.Queue = asyncio.Queue()

    async def put(self, event: dict) -> None:
        await self._events.put(event)

    def __aiter__(self):
        return self

    async def __anext__(self):
        event = await self._events.get()
        if event is None:
            raise StopAsyncIteration
        return event

    async def aclose(self) -> None:
        pass


class FakeBus:
    def __init__(self) -> None:
        self._subs: dict[str, FakeSubscription] = {}

    def subscribe(self, key) -> FakeSubscription:
        sub = FakeSubscription()
        self._subs[key] = sub
        return sub


class FakeChatService:
    def __init__(self, bus: FakeBus) -> None:
        self.runs: list[tuple[str, str, str]] = []
        self._bus = bus

    async def run(self, user_id, session_id, message, **kw):
        self.runs.append((user_id, session_id, message))
        # 模拟内核事件：向总线发送 run_end 以终止 handle_inbound 的事件循环
        from agentscope.app.message_bus import MessageBusKeys
        key = MessageBusKeys.session_events(session_id)
        sub = self._bus._subs.get(key)
        if sub:
            await sub.put({"type": "run_end"})


def _manager() -> tuple[ChannelManager, FakeChannel, FakeChatService, FakeBus, FakeKV]:
    kv = FakeKV()
    bus = FakeBus()
    chat = FakeChatService(bus)
    inst = FakeChannel()
    mgr = ChannelManager(
        config_store=None, bus=bus, chat_service=chat,
        object_storage=None, db=None,
        session_map=ChannelSessionMap(kv, PREFIX),
        commands=CommandService(),
    )
    mgr._instances["cid1"] = inst  # noqa: SLF001 — 测试直接注入运行态实例
    return mgr, inst, chat, bus, kv


def _msg(content: str, media=None) -> InboundMessage:
    return InboundMessage(
        channel="wecom", channel_id="cid1", user_id="u1",
        chat_id="chat1", sender_id="u1", content=content, media=media,
    )


@pytest.mark.asyncio(loop_scope="session")
class TestChannelCommandIngress:
    async def test_help_replies_without_llm(self):
        mgr, inst, chat, _, _ = _manager()
        await mgr.handle_inbound(_msg("/help"))
        assert chat.runs == []  # 未进 LLM
        assert len(inst.sent) == 1
        chat_id, text, end = inst.sent[0]
        assert chat_id == "chat1" and "/new" in text and end is True

    async def test_new_rotates_pointer_then_next_message_uses_new_session(self):
        mgr, inst, chat, _, kv = _manager()
        await mgr.handle_inbound(_msg("/new"))
        assert chat.runs == []
        assert "新会话" in inst.sent[0][1]
        # 第二条普通消息应进入旋转后的新会话
        await mgr.handle_inbound(_msg("你好"))
        assert len(chat.runs) == 1
        _, session_id, _ = chat.runs[0]
        assert session_id.startswith("ch-")
        assert session_id != __import__(
            "core.channels.session_map", fromlist=["derive_session_id"],
        ).derive_session_id("u1", "wecom", "chat1")

    async def test_plain_text_still_runs_llm(self):
        mgr, _, chat, _, _ = _manager()
        await mgr.handle_inbound(_msg("你好"))
        assert len(chat.runs) == 1

    async def test_media_message_never_dispatched(self):
        mgr, _, chat, _, _ = _manager()
        await mgr.handle_inbound(_msg("/help", media=["/tmp/a.png"]))
        assert len(chat.runs) == 1  # 带媒体 → 按普通消息进 LLM（D4）