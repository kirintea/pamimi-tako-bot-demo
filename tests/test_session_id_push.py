# -*- coding: utf-8 -*-

"""新会话推送 session_id 测试 — 渠道入口仅在会话首次创建时推送一次

约定（§3.4）：
    ChannelManager.handle_inbound 解析 session_id 后、发送占位前，
    经 SessionManager.session_exists（内存 / KV / PG 任一命中即已存在）
    判定新会话：先发「本次对话会话ID: …」，再进入正常对话流程。
    仅会话创建时推送一次（含 /new 轮换后的首条消息），失败不阻断对话。
"""

from types import SimpleNamespace

import pytest

from agentscope.app.message_bus import MessageBusKeys
from core.channels.events import InboundMessage
from core.channels.manager import ChannelManager
from core.config import ConfigManager
from core.kv.jsonl_kv import JsonlKVStore
from core.message_bus import InMemoryMessageBus
from core.session import SessionManager

pytestmark = pytest.mark.asyncio(loop_scope="session")


# ============================================================
# SessionManager.session_exists
# ============================================================


@pytest.fixture
def cfg(tmp_path):
    """深拷贝真实配置并切到 jsonl 后端（无需 Redis）"""
    cfg = ConfigManager.get_instance().load().model_copy(deep=True)
    cfg.kv.backend = "jsonl"
    cfg.kv.jsonl_path = str(tmp_path / "kv")
    return cfg


async def test_session_exists_false_when_empty(cfg):
    mgr = SessionManager(cfg, session_ttl=1800, max_sessions=10)
    await mgr.initialize()
    try:
        assert await mgr.session_exists("u1", "s_none") is False
    finally:
        await mgr.shutdown()


async def test_session_exists_true_on_memory_entry(cfg):
    mgr = SessionManager(cfg, session_ttl=1800, max_sessions=10)
    await mgr.initialize()
    try:
        mgr._sessions[("u_mem", "s_mem")] = SimpleNamespace()
        assert await mgr.session_exists("u_mem", "s_mem") is True
        mgr._sessions.clear()  # 假 entry 无 agent，避开 shutdown 时的 MCP 清理
    finally:
        await mgr.shutdown()


async def test_session_exists_true_on_kv_meta(cfg):
    mgr = SessionManager(cfg, session_ttl=1800, max_sessions=10)
    await mgr.initialize()
    try:
        await mgr.save_session_meta("u_meta", "s_meta", title="标题", message_count=1)
        assert await mgr.session_exists("u_meta", "s_meta") is True
    finally:
        await mgr.shutdown()


async def test_session_exists_true_on_kv_state(cfg):
    mgr = SessionManager(cfg, session_ttl=1800, max_sessions=10)
    await mgr.initialize()
    try:
        assert isinstance(mgr._kv, JsonlKVStore)
        await mgr._kv.set(mgr._redis_key("u_state", "s_state"), "{}", ex=1800)
        assert await mgr.session_exists("u_state", "s_state") is True
    finally:
        await mgr.shutdown()


async def test_session_exists_true_on_pg_history(cfg):
    """KV 全部过期但 PG conversations 有行 → 视为已存在（续聊不重复推送）"""

    class _DB:
        is_initialized = True

        async def get_conversation_history(self, user_id, session_id, limit=20):
            return {"messages": [{"role": "user", "content": "在吗"}]}

    mgr = SessionManager(cfg, session_ttl=1800, max_sessions=10, db=_DB())
    await mgr.initialize()
    try:
        assert await mgr.session_exists("u_pg", "s_pg") is True
    finally:
        await mgr.shutdown()


async def test_session_exists_true_on_pg_empty_history_not(cfg):
    """PG 可用但无历史行 → 仍视为新会话"""

    class _DB:
        is_initialized = True

        async def get_conversation_history(self, user_id, session_id, limit=20):
            return {"messages": []}

    mgr = SessionManager(cfg, session_ttl=1800, max_sessions=10, db=_DB())
    await mgr.initialize()
    try:
        assert await mgr.session_exists("u_pg2", "s_pg2") is False
    finally:
        await mgr.shutdown()


# ============================================================
# ChannelManager.handle_inbound — 新会话推送
# ============================================================


class _RecordingChatService:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def run(self, *args, **kwargs):
        self.calls.append(kwargs)


class _FakeInst:
    name = "wecom"
    _running = True

    def __init__(self) -> None:
        self.sent: list[tuple] = []

    async def send_delta(self, chat_id, delta, *, stream_id, stream_end=False):
        self.sent.append((chat_id, delta, stream_end))


class _SessionMgrStub:
    """只实现新会话检测用到的 session_exists"""

    def __init__(self, exists: bool) -> None:
        self.exists = exists
        self.calls: list[tuple] = []

    async def session_exists(self, user_id, session_id) -> bool:
        self.calls.append((user_id, session_id))
        return self.exists


def _make_manager(chat_service, session_mgr) -> ChannelManager:
    return ChannelManager(
        config_store=None,
        bus=InMemoryMessageBus(),
        chat_service=chat_service,
        object_storage=None,
        db=None,
        session_mgr=session_mgr,
    )


def _inbound() -> InboundMessage:
    return InboundMessage(
        channel="wecom",
        channel_id="c1",
        user_id="u1",
        chat_id="chat1",
        sender_id="s1",
        content="在吗",
    )


async def _drive(mgr: ChannelManager, inst: _FakeInst) -> None:
    """打桩 _safe_run（发布 run_end 收尾）后投递一条入站消息"""
    mgr._instances["c1"] = inst

    async def fake_safe_run(user_id, session_id, content, media, stream_id, chat_id,
                            inst, display_name=None, channel="web"):
        await mgr._bus.publish(
            MessageBusKeys.session_events(session_id), {"type": "run_end"},
        )

    mgr._safe_run = fake_safe_run
    await mgr.handle_inbound(_inbound())


async def test_handle_inbound_pushes_session_id_for_new_session():
    """新会话：先推 session_id，再发占位，且只推一次"""
    svc = _RecordingChatService()
    session_mgr = _SessionMgrStub(exists=False)
    mgr = _make_manager(svc, session_mgr)
    inst = _FakeInst()
    await _drive(mgr, inst)

    expected_sid = ChannelManager.derive_session_id("u1", "wecom", "chat1")
    assert session_mgr.calls == [("u1", expected_sid)]
    # 首条是 session_id 提示，其次才是占位「…」
    assert len(inst.sent) >= 2
    chat_id, first_text, first_end = inst.sent[0]
    assert chat_id == "chat1"
    assert expected_sid in first_text
    assert first_end is True
    assert inst.sent[1][1] == "…"
    await mgr._bus.aclose()


async def test_handle_inbound_no_push_for_existing_session():
    """已存在会话：不重复推送 session_id"""
    svc = _RecordingChatService()
    mgr = _make_manager(svc, _SessionMgrStub(exists=True))
    inst = _FakeInst()
    await _drive(mgr, inst)

    assert all("会话ID" not in text for _, text, _ in inst.sent)
    assert inst.sent and inst.sent[0][1] == "…"
    await mgr._bus.aclose()


async def test_push_failure_does_not_block_run():
    """session_id 推送失败不影响正常对话流程"""

    class _FailFirstInst(_FakeInst):
        async def send_delta(self, chat_id, delta, *, stream_id, stream_end=False):
            if "会话ID" in delta:
                raise RuntimeError("send failed")
            await super().send_delta(chat_id, delta, stream_id=stream_id, stream_end=stream_end)

    svc = _RecordingChatService()
    mgr = _make_manager(svc, _SessionMgrStub(exists=False))
    inst = _FailFirstInst()
    await _drive(mgr, inst)

    # 占位已发、run 已触发 → 对话未被阻断
    assert inst.sent and inst.sent[0][1] == "…"
    await mgr._bus.aclose()


async def test_no_push_when_session_mgr_missing():
    """未注入 session_mgr（如纯 web 部署）：跳过推送，不报错"""
    svc = _RecordingChatService()
    mgr = _make_manager(svc, None)
    inst = _FakeInst()
    await _drive(mgr, inst)

    assert all("会话ID" not in text for _, text, _ in inst.sent)
    await mgr._bus.aclose()
