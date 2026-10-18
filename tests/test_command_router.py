# -*- coding: utf-8 -*-
"""core/command/router.py 单测 — 归一化 / 分发 / close-match / 放行规则"""

from __future__ import annotations

import pytest

from core.command.router import (
    CommandContext,
    CommandResult,
    CommandRouter,
    normalize_command_text,
)


async def _ok(ctx: CommandContext) -> CommandResult:
    return CommandResult(text=f"hit:{ctx.raw}:{ctx.args}")


def _router() -> CommandRouter:
    r = CommandRouter()
    r.exact("/new", _ok)
    r.exact("/help", _ok)
    r.prefix("/model ", _ok)
    return r


class TestNormalize:
    def test_strips_bot_suffix(self):
        assert normalize_command_text("/new@mybot") == "/new"

    def test_keeps_args_with_bot_suffix(self):
        assert normalize_command_text("/model@bot gpt-5") == "/model gpt-5"

    def test_fullwidth_slash(self):
        assert normalize_command_text("／new") == "/new"

    def test_plain_text_untouched(self):
        assert normalize_command_text("  hello  ") == "hello"

    def test_non_bot_at_suffix_kept(self):
        # 不像 bot 名的后缀（含中文/标点）不做剥离
        assert normalize_command_text("/new@助手") == "/new@助手"


class TestDispatch:
    @pytest.mark.asyncio(loop_scope="session")
    async def test_exact_match(self):
        res = await _router().dispatch(CommandContext(raw="/new"))
        assert res is not None and res.text == "hit:/new:"

    @pytest.mark.asyncio(loop_scope="session")
    async def test_case_insensitive(self):
        res = await _router().dispatch(CommandContext(raw="/HELP"))
        assert res is not None and res.text == "hit:/HELP:"

    @pytest.mark.asyncio(loop_scope="session")
    async def test_prefix_args(self):
        res = await _router().dispatch(CommandContext(raw="/model gpt-5"))
        assert res is not None and res.text == "hit:/model gpt-5:gpt-5"

    @pytest.mark.asyncio(loop_scope="session")
    async def test_exact_with_args_rejected(self):
        res = await _router().dispatch(CommandContext(raw="/new please"))
        assert res is not None
        assert "不接受参数" in res.text

    @pytest.mark.asyncio(loop_scope="session")
    async def test_unknown_slash_close_match_hint(self):
        res = await _router().dispatch(CommandContext(raw="/nwe"))
        assert res is not None
        assert "/new" in res.text

    @pytest.mark.asyncio(loop_scope="session")
    async def test_path_like_text_passes_to_llm(self):
        # /etc/hosts 与任何命令都不接近 → 放行给 LLM（D3）
        res = await _router().dispatch(CommandContext(raw="/etc/hosts 是什么"))
        assert res is None

    @pytest.mark.asyncio(loop_scope="session")
    async def test_plain_text_never_dispatched(self):
        res = await _router().dispatch(CommandContext(raw="帮我新开一个会话"))
        assert res is None