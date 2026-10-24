# -*- coding: utf-8 -*-
"""斜杠命令路由 — 移植自 nanobot command/router.py，适配本平台无状态形态。

与 nanobot 的差异：
- OutboundMessage → CommandResult（平台侧结果对象，不绑定渠道协议）
- CommandContext 不持有 AgentLoop；session_mgr / session_map / status_tracker
  由各入站口按需注入（请求驱动、多实例无状态）
- 未注册的 "/xxx" 仅在与已注册命令接近时提示（close-match），
  否则放行给 LLM —— 渠道用户可能发送 "/etc/hosts" 这类路径文本（D3）
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from difflib import get_close_matches
from typing import Any, Awaitable, Callable

_BOT_SUFFIX_RE = re.compile(r"^[A-Za-z0-9_]+$")
# 命令名形态：/new、/new-session（首个空白分隔 token）
_COMMAND_TOKEN_RE = re.compile(r"^/[a-z][a-z0-9-]*$", re.IGNORECASE)


def normalize_command_text(text: str) -> str:
    """归一化指令文本：去首尾空白、全角斜杠、Telegram/Discord 风格的 /cmd@bot。"""
    stripped = (text or "").strip()
    if stripped.startswith("／"):  # 全角斜杠（中文输入法常见）
        stripped = "/" + stripped[1:]
    if not stripped.startswith("/"):
        return stripped
    first, sep, rest = stripped.partition(" ")
    if "@" in first:
        command, suffix = first.rsplit("@", 1)
        if command and suffix and _BOT_SUFFIX_RE.fullmatch(suffix):
            return f"{command}{sep}{rest}" if sep else command
    return stripped


@dataclass
class CommandResult:
    """指令执行结果 — 入站层据此渲染回执，不进入 LLM（D5：不落库不进上下文）。"""

    text: str
    new_session_id: str | None = None  # /new 等换会话指令携带（web 前端据此切换）
    handled: bool = True


@dataclass
class CommandContext:
    """指令 handler 的唯一入参 — 依赖由入站层注入。"""

    raw: str
    args: str = ""
    user_id: str = ""
    session_id: str = ""
    transport: str = "web"        # web / wecom / ...（= InboundMessage.channel）
    chat_id: str | None = None    # 渠道侧 chat_id（web 为 None）
    session_mgr: Any = None       # core.session.SessionManager
    session_map: Any = None       # core.channels.session_map.ChannelSessionMap
    status_tracker: Any = None    # core.session_status.SessionStatusTracker
    metadata: dict[str, Any] = field(default_factory=dict)

    def mint_session_id(self) -> str:
        """铸造新会话 id：渠道沿用 ch- 前缀（derive 同款约束，≤64 字符）。"""
        if self.transport != "web":
            return f"ch-{uuid.uuid4().hex[:24]}"
        return str(uuid.uuid4())


Handler = Callable[[CommandContext], Awaitable["CommandResult | None"]]


class CommandRouter:
    """纯字典分发：exact 精确匹配 → prefix 最长前缀 → 未命中策略（D3）。

    取消了 nanobot 的 priority 档：本平台指令拦截发生在入站口 busy 检查之前
    （D6），所有指令天然可抢占，无需单独一档。
    """

    def __init__(self) -> None:
        self._exact: dict[str, Handler] = {}
        self._prefix: list[tuple[str, Handler]] = []

    def exact(self, cmd: str, handler: Handler) -> None:
        self._exact[cmd.lower()] = handler

    def prefix(self, pfx: str, handler: Handler) -> None:
        self._prefix.append((pfx.lower(), handler))
        self._prefix.sort(key=lambda p: len(p[0]), reverse=True)

    def registered_commands(self) -> dict[str, str]:
        """lower → 规范名（close-match 提示用）。"""
        names = [*self._exact, *(pfx.rstrip() for pfx, _ in self._prefix)]
        return {n.lower(): n for n in names if n}

    async def dispatch(self, ctx: CommandContext) -> CommandResult | None:
        """命中返回 CommandResult；非指令文本返回 None（放行给 LLM）。"""
        ctx.raw = normalize_command_text(ctx.raw)
        lowered = ctx.raw.lower()

        if handler := self._exact.get(lowered):
            return await handler(ctx)

        for pfx, handler in self._prefix:
            if lowered.startswith(pfx):
                ctx.args = ctx.raw[len(pfx):]
                return await handler(ctx)

        return self._unmatched_response(ctx)

    def _unmatched_response(self, ctx: CommandContext) -> CommandResult | None:
        entered = ctx.raw.split(maxsplit=1)[0].lower()
        if not _COMMAND_TOKEN_RE.match(entered):
            return None  # 形态不像指令（如 "/etc/hosts"）→ 放行给 LLM

        names = self.registered_commands()
        if entered in names:
            accepts_args = any(
                pfx.rstrip() == entered for pfx, _ in self._prefix
            )
            if accepts_args:
                text = f'指令 "{names[entered]}" 缺少参数。发送 /help 查看用法。'
            else:
                text = f'指令 "{names[entered]}" 不接受参数。发送 /help 查看用法。'
            return CommandResult(text=text)

        matches = get_close_matches(
            entered.lstrip("/"), [n.lstrip("/") for n in names.values()], n=1, cutoff=0.6,
        )
        if matches:
            return CommandResult(
                text=f'未知指令 "{entered}"。你是想用 "/{matches[0]}" 吗？',
            )
        return None  # 与任何指令都不接近 → 放行给 LLM