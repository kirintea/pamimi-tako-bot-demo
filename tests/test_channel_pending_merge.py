# -*- coding: utf-8 -*-
"""渠道忙时排队 + 合并续跑 — ChannelManager 公共入站路径

约定（2026-11-11 方案）：
    同 session 上一轮生成中，后续入站静默入队（上限 10，超限丢最旧）；
    上一轮结束后排空积压，多条合并为一轮（文本 \\n 连接、media 顺序拼接）。
    指令（/new /stop 等）不入队；逻辑只在 ChannelManager，适配器零接入。
"""

from __future__ import annotations

import asyncio

import pytest

from agentscope.app.message_bus import MessageBusKeys

from core.channels.events import InboundMessage
from core.channels.manager import PENDING_MAX_PER_SESSION, ChannelManager
from core.session_status import SessionBusyError, SessionState

pytestmark = pytest.mark.asyncio(loop_scope="session")


# ============================================================
# Fakes
# ============================================================


class FakeChannel:
    name = "wecom"
    _running = True

    def __init__(self) -> None:
        self.sent: list[tuple[str, str, bool]] = []

    async def send_delta(self, chat_id, delta, *, stream_id, stream_end=False):
        self.sent.append((chat_id, delta, stream_end))


class FakeSubscription:
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
        self._seq = 0

    def subscribe(self, key) -> FakeSubscription:
        sub = FakeSubscription()
        self._subs[key] = sub
        return sub

    async def log_append(self, key, payload, *, ttl_secs=None, max_len=None) -> str:
        self._seq += 1
        return f"{self._seq}-0"

    async def publish(self, key, payload) -> None:
        sub = self._subs.get(key)
        if sub:
            await sub.put(payload)


class GateChatService:
    """第一条 run 阻塞在 gate 上，便于并发入队；其后立即完成并 publish run_end。"""

    def __init__(self, bus: FakeBus) -> None:
        self.runs: list = []
        self._bus = bus
        self._gate = asyncio.Event()
        self._entered = asyncio.Event()

    async def run(self, user_id, session_id, message, **kw):
        self.runs.append(message)
        self._entered.set()
        if len(self.runs) == 1:
            await self._gate.wait()
        key = MessageBusKeys.session_events(session_id)
        sub = self._bus._subs.get(key)
        if sub:
            await sub.put({"type": "run_end"})

    def release(self) -> None:
        self._gate.set()

    async def wait_entered(self) -> None:
        await asyncio.wait_for(self._entered.wait(), timeout=2)


class BusyOnceChatService:
    """第一次 run 抛 SessionBusyError（Q4 重试路径），其后成功。"""

    def __init__(self, bus: FakeBus) -> None:
        self.calls = 0
        self.runs: list = []
        self._bus = bus

    async def run(self, user_id, session_id, message, **kw):
        self.calls += 1
        if self.calls == 1:
            key = MessageBusKeys.session_events(session_id)
            sub = self._bus._subs.get(key)
            if sub:
                await sub.put({"type": "busy", "owner": "web"})
                await sub.put({"type": "run_end"})
            raise SessionBusyError(owner="web", started_at=0.0)
        self.runs.append(message)
        key = MessageBusKeys.session_events(session_id)
        sub = self._bus._subs.get(key)
        if sub:
            await sub.put({"type": "run_end"})


class AlwaysBusyChatService:
    def __init__(self, bus: FakeBus) -> None:
        self.calls = 0
        self._bus = bus

    async def run(self, user_id, session_id, message, **kw):
        self.calls += 1
        key = MessageBusKeys.session_events(session_id)
        sub = self._bus._subs.get(key)
        if sub:
            await sub.put({"type": "busy", "owner": "web"})
            await sub.put({"type": "run_end"})
        raise SessionBusyError(owner="web", started_at=0.0)


def _mgr_bound(chat_factory) -> tuple[ChannelManager, FakeChannel, object]:
    """chat_factory(bus) -> service，保证 bus 与 manager 一致"""
    bus = FakeBus()
    chat = chat_factory(bus)
    inst = FakeChannel()
    mgr = ChannelManager(
        config_store=None,
        bus=bus,
        chat_service=chat,
        object_storage=None,
        db=None,
    )
    mgr._instances["cid1"] = inst  # noqa: SLF001
    return mgr, inst, chat


def _msg(content: str, media=None, msg_id: str | None = None) -> InboundMessage:
    metadata = {"message_id": msg_id} if msg_id else {}
    return InboundMessage(
        channel="wecom",
        channel_id="cid1",
        user_id="u1",
        chat_id="chat1",
        sender_id="u1",
        content=content,
        media=media,
        metadata=metadata,
    )


# ============================================================
# 排队 + 合并
# ============================================================


async def test_t2_t3_queue_then_merge_after_t1():
    """T1 生成中入队 T2/T3；T1 结束后合并为一轮（\\n 连接）"""
    mgr, inst, chat = _mgr_bound(GateChatService)

    t1_task = asyncio.ensure_future(mgr.handle_inbound(_msg("T1")))
    await chat.wait_entered()

    await mgr.handle_inbound(_msg("T2"))
    await mgr.handle_inbound(_msg("T3"))
    assert len(chat.runs) == 1  # 仍在 T1 生成中

    chat.release()
    await asyncio.wait_for(t1_task, timeout=2)

    assert len(chat.runs) == 2
    assert chat.runs[0] == "T1"
    assert chat.runs[1] == "T2\nT3"


async def test_pending_cap_drops_oldest():
    """积压上限 10：超限丢最旧，保留最新 10 条参与合并"""
    mgr, inst, chat = _mgr_bound(GateChatService)

    t1_task = asyncio.ensure_future(mgr.handle_inbound(_msg("T0")))
    await chat.wait_entered()

    for i in range(1, 13):  # 12 条 → 上限 10 → 丢最旧 2 条（T1、T2）
        await mgr.handle_inbound(_msg(f"T{i}"))

    assert PENDING_MAX_PER_SESSION == 10
    pending_lens = [len(q) for q in mgr._pending.values()]  # noqa: SLF001
    assert pending_lens == [PENDING_MAX_PER_SESSION]

    chat.release()
    await asyncio.wait_for(t1_task, timeout=2)

    assert len(chat.runs) == 2
    merged = chat.runs[1]
    assert not merged.startswith("T1") and "\nT1\n" not in merged
    assert merged.startswith("T3\n")
    assert merged.endswith("T12")
    assert merged.count("\n") == 9  # 10 条


async def test_merge_content_with_media_kw():
    """media 不在无 storage 的 message 字符串里，但合并 content 正确"""
    mgr, inst, chat = _mgr_bound(GateChatService)

    t1_task = asyncio.ensure_future(mgr.handle_inbound(_msg("T1")))
    await chat.wait_entered()

    await mgr.handle_inbound(_msg("T2", media=["/tmp/a.png"]))
    await mgr.handle_inbound(_msg("T3", media=["/tmp/b.png"]))

    chat.release()
    await asyncio.wait_for(t1_task, timeout=2)

    assert len(chat.runs) == 2
    assert chat.runs[1] == "T2\nT3"


async def test_merge_jobs_unit():
    """_merge_jobs：\\n 连接、media 顺序拼接、merged_from 记录"""
    from core.channels.manager import _merge_jobs

    def _job(content, media=None, mid=None):
        return {
            "msg": InboundMessage(
                channel="wecom", channel_id="c1", user_id="u1",
                chat_id="ch", sender_id="u1", content=content, media=media,
                metadata={"message_id": mid} if mid else {},
            ),
            "session_id": "s1",
            "inst": None,
            "display_name": None,
        }

    single = _merge_jobs([_job("only")])
    assert single["msg"].content == "only"

    merged = _merge_jobs([
        _job("T2", media=["/tmp/a.png"], mid="m2"),
        _job("T3", media=["/tmp/b.png"], mid="m3"),
    ])
    assert merged["msg"].content == "T2\nT3"
    assert merged["msg"].media == ["/tmp/a.png", "/tmp/b.png"]
    assert merged["msg"].metadata["merged_from"] == ["m2", "m3"]
    assert merged["msg"].chat_id == "ch"


async def test_sequential_single_message_unchanged():
    """空闲时单条入站行为不变：一轮 run + 占位 + run_end 收尾"""
    mgr, inst, chat = _mgr_bound(GateChatService)
    # Gate 第一条会阻塞——用 release 前先放行：改用非 gate 路径
    # 直接跑会卡在 gate；先 release 再发
    chat.release()
    await mgr.handle_inbound(_msg("hello"))
    assert chat.runs == ["hello"]
    # 占位 + run_end 后的收尾
    assert inst.sent[0][1] == "…"


# ============================================================
# 指令不入队 / busy 重试 / 日志级别
# ============================================================


async def test_command_not_queued_during_generation():
    """/help 在生成中立即回执，不进 pending、不进 LLM"""
    from core.command.service import CommandService

    mgr, inst, chat = _mgr_bound(GateChatService)
    mgr._commands = CommandService()  # noqa: SLF001

    t1_task = asyncio.ensure_future(mgr.handle_inbound(_msg("T1")))
    await chat.wait_entered()

    await mgr.handle_inbound(_msg("/help"))
    assert all(len(q) == 0 for q in mgr._pending.values())  # noqa: SLF001
    assert any("new" in s[1].lower() or "会话" in s[1] for s in inst.sent)

    chat.release()
    await asyncio.wait_for(t1_task, timeout=2)
    assert chat.runs[0] == "T1"
    # /help 不进 LLM
    assert not any(r == "/help" for r in chat.runs)


async def test_busy_requeues_and_retries_once():
    """SessionBusyError → 重入队，下一轮成功（Q4）"""
    mgr, inst, chat = _mgr_bound(BusyOnceChatService)

    await mgr.handle_inbound(_msg("hello"))

    assert chat.calls >= 2
    assert chat.runs == ["hello"]


async def test_busy_gives_up_after_one_retry():
    """连续 busy：立即重试一次后回队，不在 handle_inbound 死循环"""
    mgr, inst, chat = _mgr_bound(AlwaysBusyChatService)

    await mgr.handle_inbound(_msg("hello"))

    assert chat.calls == 2  # 首跑 + 1 次重试
    # 消息仍在 pending（不丢）
    assert any(len(q) == 1 for q in mgr._pending.values())  # noqa: SLF001


async def test_safe_run_busy_is_warning():
    """_safe_run 捕获 SessionBusyError → False + warning，非 ERROR 全栈（P1）"""
    from loguru import logger as loguru_logger

    class _BusyChat:
        async def run(self, *a, **kw):
            raise SessionBusyError(owner="channel:wecom", started_at=1.0)

    mgr = ChannelManager(
        config_store=None, bus=FakeBus(), chat_service=_BusyChat(),
        object_storage=None, db=None,
    )
    inst = FakeChannel()

    records: list[tuple[str, str]] = []
    sink_id = loguru_logger.add(
        lambda m: records.append((m.record["level"].name, str(m))),
        level="WARNING",
    )
    try:
        result = await mgr._safe_run(
            "u1", "s1", "hi", None, "sid", "chat1", inst,
            display_name=None, channel="wecom",
        )
    finally:
        loguru_logger.remove(sink_id)

    assert result is False
    assert any(lvl == "WARNING" and "忙" in msg for lvl, msg in records)
    # P1：不得出现 ERROR 级别（logger.exception 全栈）
    assert not any(lvl == "ERROR" for lvl, _ in records)


async def test_safe_run_other_exception_returns_true():
    """非 busy 异常仍 logger.exception 并返回 True（不重试，防死循环）"""

    class _BoomChat:
        async def run(self, *a, **kw):
            raise RuntimeError("boom")

    mgr = ChannelManager(
        config_store=None, bus=FakeBus(), chat_service=_BoomChat(),
        object_storage=None, db=None,
    )
    inst = FakeChannel()
    result = await mgr._safe_run(
        "u1", "s1", "hi", None, "sid", "chat1", inst,
        display_name=None, channel="wecom",
    )
    assert result is True


# ============================================================
# 内核 busy 补 run_end（Q6）
# ============================================================


async def test_chat_service_busy_publishes_run_end():
    """busy 分支：busy + run_end 都上总线后再 raise（防订阅挂起）"""

    class _Tracker:
        async def get_status(self, session_id):
            return {
                "state": SessionState.GENERATING,
                "owner": "web",
                "started_at": 1.0,
            }

        async def set_generating(self, *a, **kw):
            return None

        async def set_idle(self, session_id):
            return None

        async def should_cancel(self, session_id):
            return False

    from core.chat_service import ChatService

    bus = FakeBus()
    svc = ChatService(object(), bus, status_tracker=_Tracker())

    key = MessageBusKeys.session_events("s_busy")
    sub = bus.subscribe(key)
    events: list[dict] = []

    async def _collect():
        async for ev in sub:
            events.append(ev)
            if ev.get("type") == "run_end":
                break

    collector = asyncio.ensure_future(_collect())
    # 给订阅者一点时间挂上
    await asyncio.sleep(0)

    with pytest.raises(SessionBusyError):
        await svc.run("u1", "s_busy", "hi")

    await asyncio.wait_for(collector, timeout=2)
    types = [e["type"] for e in events]
    assert "busy" in types
    assert "run_end" in types
    assert types.index("run_end") > types.index("busy")
