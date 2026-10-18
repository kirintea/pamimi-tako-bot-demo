# -*- coding: utf-8 -*-
"""斜杠指令层 — 入站前拦截分发（Web / 渠道共用）"""

from core.command.router import (
    CommandContext,
    CommandResult,
    CommandRouter,
    normalize_command_text,
)
from core.command.service import CommandService, get_command_service

__all__ = [
    "CommandContext",
    "CommandResult",
    "CommandRouter",
    "CommandService",
    "get_command_service",
    "normalize_command_text",
]