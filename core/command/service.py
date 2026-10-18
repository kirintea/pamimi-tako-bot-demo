# -*- coding: utf-8 -*-
"""CommandService — 三个入站口（渠道 / SSE / WS）共享的指令分发入口。

职责：廉价前置过滤（非 "/" 开头直接放行）→ 构造 CommandContext → router.dispatch。
服务本身无状态；依赖经参数注入，可单测。
"""

from __future__ import annotations

from typing import Any

from core.command.builtin import register_builtin_commands
from core.command.router import CommandContext, CommandResult, CommandRouter


class CommandService:
    def __init__(self) -> None:
        self._router = CommandRouter()
        register_builtin_commands(self._router)

    @property
    def router(self) -> CommandRouter:
        return self._router

    async def try_handle(
        self,
        *,
        text: str,
        user_id: str,
        session_id: str,
        transport: str = "web",
        chat_id: str | None = None,
        session_mgr: Any = None,
        session_map: Any = None,
        status_tracker: Any = None,
        metadata: dict[str, Any] | None = None,
    ) -> CommandResult | None:
        """命中指令返回 CommandResult；否则 None（调用方继续走 LLM 链路）。

        前置规则（D4/D7）：仅纯文本、以 "/"（或全角 "／"）开头的消息参与分发。
        """
        if not text or not text.strip():
            return None
        stripped = text.strip()
        if not (stripped.startswith("/") or stripped.startswith("／")):
            return None

        ctx = CommandContext(
            raw=stripped,
            user_id=user_id,
            session_id=session_id,
            transport=transport,
            chat_id=chat_id,
            session_mgr=session_mgr,
            session_map=session_map,
            status_tracker=status_tracker,
            metadata=dict(metadata or {}),
        )
        return await self._router.dispatch(ctx)


_service: CommandService | None = None


def get_command_service() -> CommandService:
    """进程内单例（路由器注册表构建一次）。"""
    global _service
    if _service is None:
        _service = CommandService()
    return _service