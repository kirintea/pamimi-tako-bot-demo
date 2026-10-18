# -*- coding: utf-8 -*-
"""内置指令单测 — /new（指针旋转 / web 返回新 id）/ /stop / /compact / /help"""

from __future__ import annotations

import pytest

from core.command.builtin import BUILTIN_COMMAND_SPECS, build_help_text, register_builtin_commands
from core.command.router import CommandContext, CommandRouter


class FakeSessionMap:
    def __init__(self) -> None:
        self.bound: dict[tuple[str, str, str], str] = {}

    async def bind(self, user_id, channel, chat_id, session_id) -> None:
        self.bound[(user_id, channel, chat_id)] = session_id


class FakeStatusTracker:
    def __init__(self) -> None:
        self.cancelled: list[tuple[str, str]] = []

    async def request_cancel(self, session_id: str, from_device: str) -> bool:
        self.cancelled.append((session_id, from_device))
        return True


class FakeAgent:
    def __init__(self) -> None:
        self.compressed = False

    async def compress_context(self):
        self.compressed = True

        class _R:
            messages_compressed = 3
            tokens_before = 9000
            tokens_after = 1200

        return _R()


class FakeSessionMgr:
    def __init__(self) -> None:
        self.agent = FakeAgent()
        self.saved: list[tuple[str, str]] = []

    async def get_or_create(self, user_id, session_id, display_name=None):
        return self.agent

    async def save(self, user_id, session_id) -> None:
        self.saved.append((user_id, session_id))


def _router() -> CommandRouter:
    r = CommandRouter()
    register_builtin_commands(r)
    return r


@pytest.mark.asyncio(loop_scope="session")
class TestCmdNew:
    async def test_channel_rotates_pointer(self):
        smap = FakeSessionMap()
        ctx = CommandContext(
            raw="/new", user_id="u1", session_id="ch-old",
            transport="wecom", chat_id="chat1", session_map=smap,
        )
        res = await _router().dispatch(ctx)
        assert res is not None and res.new_session_id
        assert res.new_session_id != "ch-old"
        assert res.new_session_id.startswith("ch-")
        assert smap.bound[("u1", "wecom", "chat1")] == res.new_session_id

    async def test_web_returns_new_id_without_map(self):
        ctx = CommandContext(raw="/new", user_id="u1", session_id="s1", transport="web")
        res = await _router().dispatch(ctx)
        assert res is not None and res.new_session_id
        assert res.new_session_id != "s1"
        assert not res.new_session_id.startswith("ch-")  # web 侧 uuid4


@pytest.mark.asyncio(loop_scope="session")
class TestCmdStopCompactHelp:
    async def test_stop_requests_cancel(self):
        st = FakeStatusTracker()
        ctx = CommandContext(
            raw="/stop", user_id="u1", session_id="s1",
            transport="web", status_tracker=st,
        )
        res = await _router().dispatch(ctx)
        assert res is not None and st.cancelled == [("s1", "command:web")]

    async def test_stop_without_tracker(self):
        res = await _router().dispatch(CommandContext(raw="/stop", user_id="u1", session_id="s1"))
        assert res is not None and "无进行中" in res.text

    async def test_compact_compresses_and_saves(self):
        mgr = FakeSessionMgr()
        ctx = CommandContext(
            raw="/compact", user_id="u1", session_id="s1",
            transport="web", session_mgr=mgr,
        )
        res = await _router().dispatch(ctx)
        assert res is not None and mgr.agent.compressed and mgr.saved == [("u1", "s1")]
        assert "9000" in res.text and "1200" in res.text

    async def test_help_lists_all_commands(self):
        res = await _router().dispatch(CommandContext(raw="/help", user_id="u1", session_id="s1"))
        assert res is not None
        for spec in BUILTIN_COMMAND_SPECS:
            assert spec.command in res.text
        assert build_help_text() == res.text