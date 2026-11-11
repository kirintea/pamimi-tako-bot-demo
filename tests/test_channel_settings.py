# -*- coding: utf-8 -*-
"""渠道管理设置测试 — configs/channel.yaml 加载/热重载 + push_session_id 门控

约定：
- push_session_id=true（默认）：新会话照常推「本次对话会话ID: …」
- push_session_id=false：首次创建不推；空闲轮换仅发确认语（不含会话 ID）；
  指令回执前置的轮换提示同样去 ID
- 文件缺失 / 坏 yaml / 非法值一律回退安全默认，不阻断启动
"""

from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from agentscope.app.message_bus import MessageBusKeys
from core.channels.events import InboundMessage
from core.channels.manager import ChannelManager
from core.channels.settings import ChannelSettings
from core.message_bus import InMemoryMessageBus

pytestmark = pytest.mark.asyncio(loop_scope="session")


# ============================================================
# ChannelSettings 加载 / 重载
# ============================================================


async def test_load_missing_file_returns_defaults(tmp_path: Path):
    s = ChannelSettings.load(str(tmp_path / "not-exists.yaml"))
    assert s.push_session_id is True
    assert s.path is not None


async def test_load_parses_false_and_true(tmp_path: Path):
    p = tmp_path / "channel.yaml"
    p.write_text("push_session_id: false\n", encoding="utf-8")
    assert ChannelSettings.load(str(p)).push_session_id is False
    p.write_text("push_session_id: true\n", encoding="utf-8")
    assert ChannelSettings.load(str(p)).push_session_id is True


async def test_load_invalid_value_falls_back_to_default(tmp_path: Path):
    p = tmp_path / "channel.yaml"
    p.write_text('push_session_id: "banana"\n', encoding="utf-8")
    s = ChannelSettings.load(str(p))
    assert s.push_session_id is True  # 非法值不翻转行为


async def test_load_broken_yaml_keeps_default_without_raise(tmp_path: Path):
    p = tmp_path / "channel.yaml"
    p.write_text("push_session_id: [unclosed\n", encoding="utf-8")
    s = ChannelSettings.load(str(p))
    assert s.push_session_id is True
    # 后续 refresh 不再重复解析坏文件（mtime 已记录），也不抛异常
    s.refresh()
    s.refresh()
    assert s.push_session_id is True


async def test_refresh_picks_up_file_change(tmp_path: Path):
    p = tmp_path / "channel.yaml"
    p.write_text("push_session_id: true\n", encoding="utf-8")
    s = ChannelSettings.load(str(p))
    assert s.push_session_id is True

    p.write_text("push_session_id: false\n", encoding="utf-8")
    # 显式前移 mtime，规避文件系统时间精度差异
    st = p.stat()
    os.utime(p, (st.st_atime + 5, st.st_mtime + 5))
    s.refresh()
    assert s.push_session_id is False


async def test_refresh_noop_when_unchanged(tmp_path: Path):
    p = tmp_path / "channel.yaml"
    p.write_text("push_session_id: false\n", encoding="utf-8")
    s = ChannelSettings.load(str(p))
    s.refresh()
    s.refresh()
    assert s.push_session_id is False


async def test_injected_settings_not_file_tracked():
    s = ChannelSettings(push_session_id=False)  # path=None（测试注入）
    assert s.refresh().push_session_id is False  # 不读文件、不报错


async def test_env_placeholder_resolved(tmp_path: Path, monkeypatch):
    p = tmp_path / "channel.yaml"
    p.write_text(
        "push_session_id: ${TEST_CHANNEL_PUSH_SID:-false}\n", encoding="utf-8",
    )
    monkeypatch.delenv("TEST_CHANNEL_PUSH_SID", raising=False)
    assert ChannelSettings.load(str(p)).push_session_id is False
    monkeypatch.setenv("TEST_CHANNEL_PUSH_SID", "true")
    assert ChannelSettings.load(str(p)).push_session_id is True


# ============================================================
# ChannelManager 门控行为
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
    def __init__(self, exists: bool) -> None:
        self.exists = exists
        self.calls: list[tuple] = []

    async def session_exists(self, user_id, session_id) -> bool:
        self.calls.append((user_id, session_id))
        return self.exists


class _MapStub:
    """resolve_or_rotate 打桩：rotated_from 非空表示发生了空闲轮换"""

    def __init__(self, rotated_from: str | None = None) -> None:
        self._rotated = rotated_from

    async def resolve_or_rotate(
        self, user_id, channel, chat_id, *, default, idle_hours, stale_probe=None,
    ) -> tuple[str, str | None]:
        if self._rotated:
            return f"{default}_new", self._rotated
        return default, None


def _make_manager(
    session_mgr,
    *,
    push_session_id: bool,
    session_map=None,
    commands=None,
) -> ChannelManager:
    return ChannelManager(
        config_store=None,
        bus=InMemoryMessageBus(),
        chat_service=_RecordingChatService(),
        object_storage=None,
        db=None,
        session_mgr=session_mgr,
        session_map=session_map,
        commands=commands,
        channel_settings=ChannelSettings(push_session_id=push_session_id),
    )


def _inbound(content: str = "在吗") -> InboundMessage:
    return InboundMessage(
        channel="wecom",
        channel_id="c1",
        user_id="u1",
        chat_id="chat1",
        sender_id="s1",
        content=content,
    )


async def _drive(
    mgr: ChannelManager, inst: _FakeInst, msg: InboundMessage | None = None,
) -> None:
    mgr._instances["c1"] = inst

    async def fake_safe_run(user_id, session_id, content, media, stream_id, chat_id,
                            inst, display_name=None, channel="web"):
        await mgr._bus.publish(
            MessageBusKeys.session_events(session_id), {"type": "run_end"},
        )

    mgr._safe_run = fake_safe_run
    await mgr.handle_inbound(msg or _inbound())


async def test_push_disabled_skips_first_session_id():
    """push=false：首条消息直接是占位，不推会话ID，也不做存在性探测"""
    mgr = _make_manager(_SessionMgrStub(exists=False), push_session_id=False)
    inst = _FakeInst()
    await _drive(mgr, inst)

    assert inst.sent and inst.sent[0][1] == "…"  # 占位先行
    assert all("会话ID" not in text for _, text, _ in inst.sent)
    # push 关闭时跳过 session_exists 探测（省一次 KV/PG 查询）
    assert mgr._session_mgr.calls == []
    await mgr._bus.aclose()


async def test_push_enabled_default_still_pushes():
    """push=true（默认行为不变）：新会话先推会话ID，再发占位"""
    mgr = _make_manager(_SessionMgrStub(exists=False), push_session_id=True)
    inst = _FakeInst()
    await _drive(mgr, inst)

    expected_sid = ChannelManager.derive_session_id("u1", "wecom", "chat1")
    assert inst.sent[0][1].startswith("本次对话会话ID")
    assert expected_sid in inst.sent[0][1]
    assert inst.sent[1][1] == "…"
    await mgr._bus.aclose()


async def test_push_disabled_idle_rotation_confirms_without_id():
    """push=false：空闲轮换仍发确认语（用户需知历史已重置），但不含会话ID"""
    mgr = _make_manager(
        None, push_session_id=False, session_map=_MapStub(rotated_from="old_sid"),
    )
    inst = _FakeInst()
    await _drive(mgr, inst)

    first = inst.sent[0][1]
    assert "已自动开启新会话" in first
    assert "会话ID" not in first
    assert inst.sent[1][1] == "…"
    await mgr._bus.aclose()


async def test_push_enabled_idle_rotation_keeps_id():
    """push=true：空闲轮换确认语 + 会话ID（原行为）"""
    mgr = _make_manager(
        None, push_session_id=True, session_map=_MapStub(rotated_from="old_sid"),
    )
    inst = _FakeInst()
    await _drive(mgr, inst)

    first = inst.sent[0][1]
    assert "已自动开启新会话" in first
    assert "本次对话会话ID" in first
    await mgr._bus.aclose()


async def test_command_receipt_rotation_notice_respects_push_flag():
    """push=false：指令回执前置的轮换提示只留确认语、不带会话ID"""
    cmd = SimpleNamespace(
        try_handle=lambda **kw: _async_value(SimpleNamespace(text="回执", new_session_id=None)),
    )
    mgr = _make_manager(
        None,
        push_session_id=False,
        session_map=_MapStub(rotated_from="old_sid"),
        commands=cmd,
    )
    inst = _FakeInst()
    await _drive(mgr, inst)

    assert len(inst.sent) == 1  # 指令命中即回执，不进对话流
    text = inst.sent[0][1]
    assert text.endswith("回执")
    assert "已自动开启新会话" in text
    assert "会话ID" not in text
    await mgr._bus.aclose()


def _async_value(v):
    async def _f(**kwargs):
        return v
    return _f()


async def test_new_session_notice_include_id_variants():
    """_new_session_notice 文案矩阵：include_id × 有无轮换确认语"""
    full = ChannelManager._new_session_notice("s1", None)
    assert "本次对话会话ID: s1" in full

    no_id_first = ChannelManager._new_session_notice("s1", None, include_id=False)
    assert no_id_first == ""  # 空串 → 调用方跳过发送

    no_id_rotated = ChannelManager._new_session_notice("s1", 24.0, include_id=False)
    assert "已自动开启新会话" in no_id_rotated
    assert "会话ID" not in no_id_rotated
