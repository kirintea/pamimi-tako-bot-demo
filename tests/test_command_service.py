# -*- coding: utf-8 -*-
"""CommandService 共享入口单测 — 前置过滤（D4）与上下文注入"""

from __future__ import annotations

import pytest

from core.command.service import CommandService


@pytest.mark.asyncio(loop_scope="session")
class TestTryHandle:
    async def test_plain_text_returns_none(self):
        svc = CommandService()
        assert await svc.try_handle(text="你好", user_id="u", session_id="s") is None

    async def test_help_returns_result(self):
        svc = CommandService()
        res = await svc.try_handle(text="/help", user_id="u", session_id="s")
        assert res is not None and "/new" in res.text

    async def test_fullwidth_slash_dispatched(self):
        svc = CommandService()
        res = await svc.try_handle(text="／help", user_id="u", session_id="s")
        assert res is not None and "/new" in res.text

    async def test_whitespace_stripped(self):
        svc = CommandService()
        res = await svc.try_handle(text="  /help  ", user_id="u", session_id="s")
        assert res is not None

    async def test_blank_text_returns_none(self):
        svc = CommandService()
        assert await svc.try_handle(text="", user_id="u", session_id="s") is None
        assert await svc.try_handle(text="   ", user_id="u", session_id="s") is None

    async def test_context_fields_passed(self):
        svc = CommandService()
        res = await svc.try_handle(
            text="/new", user_id="u1", session_id="s1",
            transport="wecom", chat_id="chat9",
        )
        # web 无 session_map → 仍返回 new_session_id（未旋转指针，仅铸造）
        assert res is not None and res.new_session_id and res.new_session_id != "s1"