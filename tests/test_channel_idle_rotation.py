# -*- coding: utf-8 -*-
"""渠道会话空闲自动新开 — ChannelManager.handle_inbound 轮换闭环

约定：
    入站解析指针时判定空闲超时（N 按 msg.channel_id 对应的渠道条目解析）
    → 轮换新会话 → 旧会话 KV 清理（PG 历史保留）
    → 新会话推送文案带「自动开启新会话」说明（复用 §3.4 session_id 推送）。
"""

from __future__ import annotations

import json
import time
from types import SimpleNamespace

import pytest

from agentscope.app.message_bus import MessageBusKeys
from core.channels.config_store import DEFAULT_SESSION_IDLE_HOURS
from core.channels.events import InboundMessage
from core.channels.manager import ChannelManager
from core.channels.session_map import ChannelSessionMap
from core.kv.base import KVStore
from core.message_bus import InMemoryMessageBus

pytestmark = pytest.mark.asyncio(loop_scope="session")

PREFIX = "dmx_agent_redis:session:"


class FakeKV(KVStore):
    """实现 KVStore（ABC）全部抽象方法，仅本测试用"""

    def __init__(self) -> None:
        self.data: dict[str, str] = {}

    async def ping(self) -> None:
        return None

    async def get(self, key: str):
        return self.data.get(key)

    async def set(self, key: str, value: str, ex: int | None = None) -> None:
        self.data[key] = value

    async def delete(self, *keys: str) -> int:
        return sum(1 for k in keys if self.data.pop(k, None) is not None)

    async def scan_keys(self, match: str) -> list[str]:
        return list(self.data.keys())

    async def aclose(self) -> None:
        return None


class _FakeInst:
    name = "wecom"
    _running = True

    def __init__(self) -> None:
        self.sent: list[tuple] = []

    async def send_delta(self, chat_id, delta, *, stream_id, stream_end=False):
        self.sent.append((chat_id, delta, stream_end))


class _RecordingChatService:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    async def run(self, user_id, session_id, content, *a, **kw):
        self.calls.append((user_id, session_id, content))


class _SessionMgrStub:
    def __init__(self, exists: bool = False) -> None:
        self.exists = exists
        self.deleted: list[tuple] = []

    async def session_exists(self, user_id, session_id) -> bool:
        return self.exists

    async def delete_session(self, user_id, session_id) -> bool:
        self.deleted.append((user_id, session_id))
        return True


def _config_store(idle_by_channel: dict[str, float | None]):
    """按渠道 id 配不同 N 的 config_store 桩（缺 id → 默认 24，与实现同语义）"""

    def get_by_id(channel_id: str):
        if channel_id in idle_by_channel:
            return SimpleNamespace(session_idle_hours=idle_by_channel[channel_id])
        return None

    return SimpleNamespace(get_by_id=get_by_id)


def _make_manager(session_map, session_mgr, idle_by_channel) -> ChannelManager:
    return ChannelManager(
        config_store=_config_store(idle_by_channel),
        bus=InMemoryMessageBus(),
        chat_service=_RecordingChatService(),
        object_storage=None,
        db=None,
        session_mgr=session_mgr,
        session_map=session_map,
    )


def _inbound(channel_id: str = "c1") -> InboundMessage:
    return InboundMessage(
        channel="wecom",
        channel_id=channel_id,
        user_id="u1",
        chat_id="chat1",
        sender_id="s1",
        content="在吗",
    )


async def _drive(mgr: ChannelManager, inst: _FakeInst, channel_id: str = "c1") -> None:
    mgr._instances[channel_id] = inst

    async def fake_safe_run(user_id, session_id, content, media, stream_id, chat_id,
                            inst, display_name=None, channel="web"):
        await mgr._bus.publish(
            MessageBusKeys.session_events(session_id), {"type": "run_end"},
        )

    mgr._safe_run = fake_safe_run
    await mgr.handle_inbound(_inbound(channel_id))


def _seed_pointer(smap: ChannelSessionMap, session_id: str, last_active: float | None) -> None:
    key = smap._key("u1", "wecom", "chat1")
    payload = {"session_id": session_id}
    if last_active is not None:
        payload["last_active"] = last_active
    smap._kv.data[key] = json.dumps(payload)


async def test_rotates_when_idle_exceeded_and_drops_old_kv():
    kv = FakeKV()
    smap = ChannelSessionMap(kv, PREFIX)
    _seed_pointer(smap, "ch-old", time.time() - 25 * 3600)
    session_mgr = _SessionMgrStub(exists=False)
    mgr = _make_manager(smap, session_mgr, {"c1": 24.0})
    inst = _FakeInst()
    await _drive(mgr, inst)

    # 旧会话 KV 被清（PG 历史不动）
    assert session_mgr.deleted == [("u1", "ch-old")]
    # 指针已指向新会话
    new_sid = await smap.resolve("u1", "wecom", "chat1")
    assert new_sid != "ch-old" and new_sid.startswith("ch-")
    # 首条消息：过期说明 + 新会话ID（N=24 的措辞锁定，防配置二读不一致）
    first_text = inst.sent[0][1]
    assert "自动开启新会话" in first_text
    assert "超过 24 小时" in first_text
    assert new_sid in first_text
    await mgr._bus.aclose()


async def test_no_rotation_when_fresh():
    kv = FakeKV()
    smap = ChannelSessionMap(kv, PREFIX)
    _seed_pointer(smap, "ch-cur", time.time() - 60)
    session_mgr = _SessionMgrStub(exists=True)
    mgr = _make_manager(smap, session_mgr, {"c1": 24.0})
    inst = _FakeInst()
    await _drive(mgr, inst)

    assert session_mgr.deleted == []
    assert await smap.resolve("u1", "wecom", "chat1") == "ch-cur"
    assert all("自动开启新会话" not in text for _, text, _ in inst.sent)
    await mgr._bus.aclose()


async def test_per_channel_idle_hours():
    """不同渠道不同 N —— 同样 25h 未活跃：N=24 的轮换，N=100 的不轮换"""
    kv = FakeKV()
    smap = ChannelSessionMap(kv, PREFIX)
    _seed_pointer(smap, "ch-old", time.time() - 25 * 3600)
    session_mgr = _SessionMgrStub(exists=True)
    mgr = _make_manager(smap, session_mgr, {"c1": 24.0, "c2": 100.0})

    inst1 = _FakeInst()
    await _drive(mgr, inst1, channel_id="c1")
    new_sid = await smap.resolve("u1", "wecom", "chat1")
    assert new_sid != "ch-old"

    # 重置指针到 25h 未活跃，换 c2（N=100）投递 → 不轮换
    _seed_pointer(smap, "ch-old", time.time() - 25 * 3600)
    inst2 = _FakeInst()
    await _drive(mgr, inst2, channel_id="c2")
    assert await smap.resolve("u1", "wecom", "chat1") == "ch-old"
    await mgr._bus.aclose()


async def test_zero_disables_rotation():
    kv = FakeKV()
    smap = ChannelSessionMap(kv, PREFIX)
    _seed_pointer(smap, "ch-cur", time.time() - 10**7)
    session_mgr = _SessionMgrStub(exists=True)
    mgr = _make_manager(smap, session_mgr, {"c1": 0})
    inst = _FakeInst()
    await _drive(mgr, inst)

    assert session_mgr.deleted == []
    assert await smap.resolve("u1", "wecom", "chat1") == "ch-cur"
    await mgr._bus.aclose()


async def test_missing_entry_uses_default_24h():
    """条目未配置 N → 默认 24h；25h 未活跃照样轮换"""
    kv = FakeKV()
    smap = ChannelSessionMap(kv, PREFIX)
    _seed_pointer(smap, "ch-old", time.time() - 25 * 3600)
    session_mgr = _SessionMgrStub(exists=False)
    mgr = _make_manager(smap, session_mgr, {"c1": None})  # None = 未配置
    inst = _FakeInst()
    await _drive(mgr, inst)

    new_sid = await smap.resolve("u1", "wecom", "chat1")
    assert new_sid != "ch-old"
    assert DEFAULT_SESSION_IDLE_HOURS == 24.0
    await mgr._bus.aclose()


async def test_first_contact_derives_stable_id_no_rotation():
    kv = FakeKV()
    smap = ChannelSessionMap(kv, PREFIX)
    session_mgr = _SessionMgrStub(exists=False)
    mgr = _make_manager(smap, session_mgr, {"c1": 24.0})
    inst = _FakeInst()
    await _drive(mgr, inst)

    expected = ChannelManager.derive_session_id("u1", "wecom", "chat1")
    assert await smap.resolve("u1", "wecom", "chat1") == expected
    assert session_mgr.deleted == []
    # 首条为 session_id 推送（无过期说明），其次占位
    assert expected in inst.sent[0][1]
    assert "自动开启新会话" not in inst.sent[0][1]
    await mgr._bus.aclose()


async def test_command_first_after_idle_still_notice():
    """空闲过期后首条为斜杠指令：自动新开提示 + 会话ID 不得因指令提前 return 丢失"""
    kv = FakeKV()
    smap = ChannelSessionMap(kv, PREFIX)
    _seed_pointer(smap, "ch-old", time.time() - 25 * 3600)
    session_mgr = _SessionMgrStub(exists=False)
    mgr = _make_manager(smap, session_mgr, {"c1": 24.0})
    inst = _FakeInst()
    mgr._instances["c1"] = inst
    await mgr.handle_inbound(
        InboundMessage(
            channel="wecom", channel_id="c1", user_id="u1", chat_id="chat1",
            sender_id="s1", content="/help",
        )
    )
    new_sid = await smap.resolve("u1", "wecom", "chat1")
    assert new_sid != "ch-old"
    # 指令回执合并了自动新开确认 + 会话ID
    assert len(inst.sent) == 1
    assert "自动开启新会话" in inst.sent[0][1]
    assert new_sid in inst.sent[0][1]
    assert "可用指令" in inst.sent[0][1]  # /help 回执仍在
    await mgr._bus.aclose()


async def test_new_command_first_after_idle_uses_new_receipt():
    """首条为 /new：以 /new 自身回执为准（不再追加自动轮换提示，避免双会话ID）"""
    kv = FakeKV()
    smap = ChannelSessionMap(kv, PREFIX)
    _seed_pointer(smap, "ch-old", time.time() - 25 * 3600)
    session_mgr = _SessionMgrStub(exists=False)
    mgr = _make_manager(smap, session_mgr, {"c1": 24.0})
    inst = _FakeInst()
    mgr._instances["c1"] = inst
    await mgr.handle_inbound(
        InboundMessage(
            channel="wecom", channel_id="c1", user_id="u1", chat_id="chat1",
            sender_id="s1", content="/new",
        )
    )
    # /new 又轮换了一次：指针最终是 /new 铸的 id（非自动轮换铸的那个）
    final_sid = await smap.resolve("u1", "wecom", "chat1")
    assert final_sid != "ch-old" and final_sid.startswith("ch-")
    assert len(inst.sent) == 1
    assert "已开启新会话" in inst.sent[0][1]
    assert "自动开启新会话" not in inst.sent[0][1]
    await mgr._bus.aclose()


async def test_delete_failure_does_not_block():
    """过期会话 KV 清理抛异常：不断流，轮换与后续流程照常"""
    kv = FakeKV()
    smap = ChannelSessionMap(kv, PREFIX)
    _seed_pointer(smap, "ch-old", time.time() - 25 * 3600)

    class _ExplodingSessionMgr(_SessionMgrStub):
        async def delete_session(self, user_id, session_id) -> bool:
            raise RuntimeError("kv down")

    session_mgr = _ExplodingSessionMgr(exists=False)
    mgr = _make_manager(smap, session_mgr, {"c1": 24.0})
    inst = _FakeInst()
    await _drive(mgr, inst)

    new_sid = await smap.resolve("u1", "wecom", "chat1")
    assert new_sid != "ch-old"
    assert any("自动开启新会话" in text for _, text, _ in inst.sent)
    await mgr._bus.aclose()


async def test_rotation_notice_even_if_session_exists():
    """PG 抖动等场景下 session_exists 保守返回 True 也不得吞掉自动新开提示（用户要求：有提示有会话ID）"""
    kv = FakeKV()
    smap = ChannelSessionMap(kv, PREFIX)
    _seed_pointer(smap, "ch-old", time.time() - 25 * 3600)
    session_mgr = _SessionMgrStub(exists=True)  # 保守判定「已存在」
    mgr = _make_manager(smap, session_mgr, {"c1": 24.0})
    inst = _FakeInst()
    await _drive(mgr, inst)

    new_sid = await smap.resolve("u1", "wecom", "chat1")
    assert new_sid != "ch-old"
    first_text = inst.sent[0][1]
    assert "自动开启新会话" in first_text
    assert new_sid in first_text
    await mgr._bus.aclose()
