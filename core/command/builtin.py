# -*- coding: utf-8 -*-
"""内置斜杠指令 — /new /stop /compact /help（v1 最小集，D8）。

handler 语义依赖 CommandContext 注入的平台组件（无 loop 引用，对齐无状态范式）。
回执均为 CommandResult（side-channel，不落库不进 AgentState，D5）。
"""

from __future__ import annotations

from dataclasses import dataclass

from core.command.router import CommandContext, CommandResult, CommandRouter


@dataclass(frozen=True)
class CommandSpec:
    command: str
    title: str
    description: str
    arg_hint: str = ""
    accepts_args: bool = False


BUILTIN_COMMAND_SPECS: tuple[CommandSpec, ...] = (
    CommandSpec("/new", "新会话", "结束当前上下文，开启全新会话（历史保留）"),
    CommandSpec("/stop", "停止", "请求停止当前正在生成的回复"),
    CommandSpec("/compact", "压缩上下文", "将当前会话历史压缩为摘要，减轻上下文负担"),
    CommandSpec("/help", "帮助", "列出可用指令"),
)


def build_help_text() -> str:
    lines = ["📋 可用指令："]
    for spec in BUILTIN_COMMAND_SPECS:
        cmd = f"{spec.command} {spec.arg_hint}" if spec.arg_hint else spec.command
        lines.append(f"{cmd} — {spec.description}")
    return "\n".join(lines)


async def cmd_new(ctx: CommandContext) -> CommandResult:
    """开启全新会话：渠道旋转会话指针；web 返回新 id 由前端切换（§3.3）。"""
    new_sid = ctx.mint_session_id()
    if ctx.session_map is not None and ctx.chat_id:
        await ctx.session_map.bind(ctx.user_id, ctx.transport, ctx.chat_id, new_sid)
    return CommandResult(text="✅ 已开启新会话，之前的历史不再携带。", new_session_id=new_sid)


async def cmd_stop(ctx: CommandContext) -> CommandResult:
    if ctx.status_tracker is None:
        return CommandResult(text="当前无进行中的任务。")
    ok = await ctx.status_tracker.request_cancel(ctx.session_id, f"command:{ctx.transport}")
    return CommandResult(
        text="⏹ 已请求停止当前回复。" if ok else "当前无进行中的任务。",
    )


async def cmd_compact(ctx: CommandContext) -> CommandResult:
    if ctx.session_mgr is None:
        return CommandResult(text="会话服务不可用，压缩失败。")
    try:
        agent = await ctx.session_mgr.get_or_create(ctx.user_id, ctx.session_id)
        result = await agent.compress_context()
        await ctx.session_mgr.save(ctx.user_id, ctx.session_id)
        return CommandResult(text=(
            f"🗜 已压缩上下文：tokens {getattr(result, 'tokens_before', '?')}"
            f" → {getattr(result, 'tokens_after', '?')}"
            f"（压缩 {getattr(result, 'messages_compressed', 0)} 条消息）。"
        ))
    except Exception as e:  # noqa: BLE001 — 指令层必须给出可读回执，不向上抛
        return CommandResult(text=f"压缩失败：{e}")


async def cmd_help(ctx: CommandContext) -> CommandResult:
    return CommandResult(text=build_help_text())


def register_builtin_commands(router: CommandRouter) -> None:
    router.exact("/new", cmd_new)
    router.exact("/stop", cmd_stop)
    router.exact("/compact", cmd_compact)
    router.exact("/help", cmd_help)