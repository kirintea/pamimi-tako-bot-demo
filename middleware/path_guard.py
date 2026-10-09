# -*- coding: utf-8 -*-

"""沙箱路径守卫中间件（多域读写）

拦截文件操作类工具的路径参数，校验是否在允许的工作域（前缀）内，并按
该域的读写标志（ro / rw）放行或拒绝。

双域模型（本平台）：
- ``user_spaces/{user_id}/``：该用户可读写（rw）。
- ``agent_space/``：普通用户只读（ro），root 可读写（rw）。
- 其它任何路径（含他人 ``user_spaces/{other}/``）均不在允许域内 → 拒绝。

与 tool_guard / command_guard 并列：
  - tool_guard   → 工具名级 (Bash, Read, Write ...)
  - command_guard → 命令内容级 (rm -rf, curl|bash ...)
  - path_guard   → 路径级（多域读写隔离）

构造方式（两种，向后兼容）：
  - 旧：``PathGuardMiddleware(sandbox_dir="workspaces")`` → 等价于单条 rw 规则。
  - 新：``PathGuardMiddleware(rules=build_sandbox_rules(user_space, agent_space, is_root))``。

工具路径参数映射：
  - Read:    file_path
  - Write:   file_path
  - Edit:    file_path
  - Glob:    path
  - Grep:    path
  - Bash:    command（解析命令中的文件路径，按命令读写语义校验）
  - 其他:    放行（无路径概念的工具不受限）
"""

from __future__ import annotations

import json
import os
import re
import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from loguru import logger

from agentscope.middleware import MiddlewareBase
from agentscope.message import TextBlock, ToolResultState
from agentscope.tool import ToolResponse

# 工具名 → 路径参数字段
_TOOL_PATH_KEYS: dict[str, str] = {
    "Read": "file_path",
    "Write": "file_path",
    "Edit": "file_path",
    "Glob": "path",
    "Grep": "path",
}

# Bash 命令中需要检查路径的子命令（文件操作类）
_BASH_FILE_CMDS: set[str] = {
    "cat", "head", "tail", "less", "more",
    "ls", "dir", "tree", "find", "locate",
    "cp", "copy", "mv", "move", "rm", "del", "rmdir",
    "mkdir", "md", "touch", "ln",
    "chmod", "chown", "chgrp",
    "grep", "egrep", "fgrep", "rg",
    "sed", "awk", "tee",
    "wc", "sort", "uniq", "cut", "tr",
    "diff", "cmp", "comm",
    "tar", "zip", "unzip", "gzip", "gunzip",
    "dd", "truncate",
    "stat", "file", "du", "df",
    "readlink", "realpath",
    "source", ".",
}

# 写操作命令基名（用于推断 Bash 命令是否为写入）
_WRITE_CMD_BASES: set[str] = {
    "cp", "copy", "mv", "move", "rm", "del", "rmdir",
    "mkdir", "md", "touch", "ln", "dd", "truncate", "tee",
    "chmod", "chown", "chgrp", "mkfs", "mount", "install",
    "scp", "rsync",
}

# 重定向符号后跟的路径也需要检查（覆盖 > / >> / < / << / <<< / 2> / 1>> 等）
_REDIRECT_RE = re.compile(r'\d*[<>]{1,3}\s*([^\s;|&]+)')


@dataclass
class SandboxRule:
    """单条沙箱域规则

    Attributes:
        prefix: 允许域的绝对路径前缀。
        mode: ``"ro"`` 只读（读允许、写拒绝）/ ``"rw"`` 读写。
    """

    prefix: str
    mode: str  # "ro" | "rw"


def build_sandbox_rules(
    user_space_dir: str,
    agent_space_dir: str,
    is_root: bool = False,
) -> list[SandboxRule]:
    """构造标准双域规则集

    - 用户域 ``user_spaces/{user_id}``：始终 ``rw``。
    - Agent 域 ``agent_space``：普通用户 ``ro``，root ``rw``。

    Args:
        user_space_dir: 用户私有域绝对路径（如 workspaces/user_spaces/{user_id}）。
        agent_space_dir: Agent 共享域绝对路径（如 workspaces/agent_space）。
        is_root: 当前用户是否为 root（决定 agent_space 读写）。

    Returns:
        SandboxRule 列表。
    """
    return [
        SandboxRule(prefix=os.path.abspath(user_space_dir), mode="rw"),
        SandboxRule(
            prefix=os.path.abspath(agent_space_dir),
            mode="rw" if is_root else "ro",
        ),
    ]


class PathGuardMiddleware(MiddlewareBase):
    """沙箱路径守卫 — 多域读写隔离"""

    def __init__(
        self,
        sandbox_dir: str | None = None,
        rules: list[SandboxRule] | None = None,
    ) -> None:
        super().__init__()
        # 向后兼容：仅传 sandbox_dir 时等价于单条 rw 规则
        if rules is None:
            if sandbox_dir is None:
                raise ValueError("PathGuardMiddleware 需要 sandbox_dir 或 rules 之一")
            rules = [SandboxRule(prefix=os.path.abspath(sandbox_dir), mode="rw")]

        # 规范化为 (绝对前缀, 模式)，并记录日志
        self._rules: list[tuple[str, str]] = []
        for r in rules:
            abs_prefix = os.path.abspath(r.prefix)
            self._rules.append((abs_prefix, r.mode))
        # 主前缀 = 第一条 rw 规则前缀（用于相对路径解析的 CWD）
        self._primary_prefix = next(
            (p for p, m in self._rules if m == "rw"), self._rules[0][0]
        )
        logger.info(
            "PathGuard 已初始化（多域）: rules={}",
            [(p, m) for p, m in self._rules],
        )

    # ------------------------------------------------------------------
    # 系统提示词注入 — 让 Agent 第一步就知道允许的工作域
    # ------------------------------------------------------------------

    async def on_system_prompt(
        self,
        agent: Any,
        current_prompt: str,
    ) -> str:
        """在系统提示词末尾追加允许的工作域信息"""
        lines = ["\n\n## 沙箱路径（必读）", "所有文件操作必须在以下允许域内："]
        for prefix, mode in self._rules:
            lines.append(f"- `{prefix}` （{'只读' if mode == 'ro' else '读写'}）")
        lines.append(
            "创建、读取、编辑文件时，必须使用上述目录内的绝对路径。"
            "禁止访问任何允许域之外的文件（含他人 user_spaces、系统目录）。"
        )
        return current_prompt + "\n".join(lines)

    # ------------------------------------------------------------------
    # MiddlewareBase 钩子
    # ------------------------------------------------------------------

    async def on_acting(
        self,
        agent: Any,
        input_kwargs: dict,
        next_handler: Any,
    ):
        """拦截工具执行 — 检查路径参数是否在允许域内并按读写标志放行"""
        tool_call = input_kwargs["tool_call"]
        tool_name: str = tool_call.name

        # --- Bash/PowerShell: 解析命令中的文件路径 ---
        if tool_name in ("Bash", "PowerShell"):
            ok, bad_path = self._check_bash_command(tool_call.input)
            if ok:
                async for item in next_handler(**input_kwargs):
                    yield item
                return
            logger.warning(
                "PathGuard 拦截 {}: 路径 '{}' 越出允许域（rules={}）",
                tool_name, bad_path, self._rules,
            )
            yield ToolResponse(
                id=tool_call.id,
                content=[TextBlock(
                    text=(
                        f"[PathGuard] 命令被拒绝: 包含允许域外的路径 '{bad_path}'。"
                        f"所有文件操作必须在允许域内: {[p for p, _ in self._rules]}"
                    ),
                )],
                state=ToolResultState.DENIED,
            )
            return

        # --- Read/Write/Edit/Glob/Grep: 检查路径参数 ---
        path_key = _TOOL_PATH_KEYS.get(tool_name)
        if path_key is None:
            # 无路径映射的工具直接放行
            async for item in next_handler(**input_kwargs):
                yield item
            return

        path_value = self._extract_path(tool_call.input, path_key)
        if not path_value:
            # 无路径值（如 Glob 不传 path 参数时默认 "."），放行
            async for item in next_handler(**input_kwargs):
                yield item
            return

        is_write = tool_name in ("Write", "Edit")
        if self._evaluate_path(path_value, is_write):
            async for item in next_handler(**input_kwargs):
                yield item
            return

        # 路径越界或只读域写操作 — 拦截
        logger.warning(
            "PathGuard 拦截 {}: 路径 '{}' 越出允许域/只读（rules={}）",
            tool_name, path_value, self._rules,
        )
        yield ToolResponse(
            id=tool_call.id,
            content=[TextBlock(
                text=(
                    f"[PathGuard] 路径被拒绝: '{path_value}' 不在允许域内"
                    f"{'（只读域禁止写入）' if is_write else ''}。"
                    f"所有文件操作必须在允许域内: {[p for p, _ in self._rules]}"
                ),
            )],
            state=ToolResultState.DENIED,
        )

    # ------------------------------------------------------------------
    # 路径评估核心
    # ------------------------------------------------------------------

    def _resolve(self, path_str: str) -> str:
        """将路径解析为绝对路径（相对路径基于主前缀 / CWD）"""
        if not path_str:
            return ""
        # 去除 curl -d @file 这类本地路径前置的 @
        if path_str.startswith("@"):
            path_str = path_str[1:]
        expanded = os.path.expanduser(os.path.expandvars(path_str))
        if not expanded:
            return ""
        target = Path(expanded)
        if not target.is_absolute():
            target = (Path(self._primary_prefix) / target).resolve()
        else:
            target = target.resolve()
        return str(target)

    @staticmethod
    def _under(target: str, prefix: str) -> bool:
        """target 是否落在 prefix 内（含 prefix 自身）"""
        if not target:
            return False
        if target == prefix:
            return True
        return target.startswith(prefix + os.sep)

    def _evaluate_path(self, path_str: str, is_write: bool) -> bool:
        """评估单个路径是否允许

        - 命中某 rw 域 → 允许（读/写均可）。
        - 命中某 ro 域且为读 → 允许；为写 → 拒绝。
        - 不命中任何域 → 拒绝。
        """
        target = self._resolve(path_str)
        if not target:
            return False
        for prefix, mode in self._rules:
            if self._under(target, prefix):
                if is_write and mode == "ro":
                    return False
                return True
        return False

    def _command_is_write(self, command: str) -> bool:
        """推断 Bash 命令是否包含写操作（输出重定向到文件 / 写命令基名）"""
        try:
            tokens = shlex.split(command)
        except ValueError:
            tokens = command.split()
        for i, tok in enumerate(tokens):
            if tok in (">", ">>"):
                nxt = tokens[i + 1] if i + 1 < len(tokens) else ""
                # 排除 2>&1 / 1>&2 这类文件描述符重定向（非文件写）
                if nxt not in ("&1", "&2"):
                    return True
        if tokens:
            base = os.path.basename(tokens[0])
            if base in _WRITE_CMD_BASES:
                return True
        return False

    def _check_bash_command(
        self,
        tool_input: str,
    ) -> tuple[bool, str | None]:
        """检查 Bash 命令中是否包含允许域外的路径

        Returns:
            (True, None) 表示通过，(False, 问题路径) 表示拦截
        """
        command = self._extract_command(tool_input)
        if not command:
            return True, None

        is_write = self._command_is_write(command)
        paths = self._extract_paths_from_command(command)

        for p in paths:
            if not self._evaluate_path(p, is_write):
                return False, p

        return True, None

    def _extract_paths_from_command(self, command: str) -> list[str]:
        """从 Bash 命令中提取文件路径

        策略:
        1. 用 shlex 分词
        2. 识别文件操作子命令，提取其后的路径参数
        3. 检查重定向目标路径
        """
        paths: list[str] = []

        # 1. 提取重定向目标
        for m in _REDIRECT_RE.finditer(command):
            redir_path = m.group(1)
            if redir_path and redir_path != "/dev/null":
                paths.append(redir_path)

        # 2. 分词并提取子命令参数
        try:
            tokens = shlex.split(command)
        except ValueError:
            tokens = command.split()

        i = 0
        while i < len(tokens):
            tok = tokens[i]

            # 跳过选项标志
            if tok.startswith("-") and len(tok) > 1:
                if tok in ("-i", "-o", "--input", "--output") and i + 1 < len(tokens):
                    paths.append(tokens[i + 1])
                    i += 2
                    continue
                i += 1
                continue

            # 识别文件操作子命令
            base_cmd = os.path.basename(tok)
            if base_cmd in _BASH_FILE_CMDS:
                i += 1
                skip_next = False
                while i < len(tokens):
                    arg = tokens[i]
                    if skip_next:
                        skip_next = False
                        i += 1
                        continue
                    if arg.startswith("-") and len(arg) > 1:
                        if arg in ("-n", "-r", "-R", "-d", "-e", "-w", "-x",
                                   "-c", "-m", "-t", "-p", "--max-depth",
                                   "--include", "--exclude", "--pattern"):
                            skip_next = True
                        i += 1
                        continue
                    if arg and arg not in ("|", "&&", "||", ";", ">"):
                        paths.append(arg)
                    i += 1
                continue

            if base_cmd in ("python", "python3", "pip", "pip3"):
                for opt in ("-c", "-e"):
                    if opt in tokens[i + 1:]:
                        idx = tokens.index(opt, i + 1)
                        if idx + 1 < len(tokens):
                            code = tokens[idx + 1]
                            code_paths = re.findall(r'''['"]([^'"]+)['"]''', code)
                            for s in code_paths:
                                if (s.startswith("/") or s.startswith("~")
                                        or s.startswith(".") or ".." in s):
                                    paths.append(s)
                j = i + 1
                while j < len(tokens):
                    arg = tokens[j]
                    if self._looks_like_local_path(arg):
                        paths.append(arg)
                    j += 1
                i += 1
                continue

            # 未识别命令：fail-closed，扫描后续参数中的本地路径型 token
            i += 1
            while i < len(tokens):
                arg = tokens[i]
                if self._looks_like_local_path(arg):
                    paths.append(arg)
                i += 1
            continue

        return paths

    @staticmethod
    def _extract_command(tool_input: str) -> str:
        """从 tool_call.input JSON 中提取 command 字段"""
        if not tool_input:
            return ""
        try:
            data = json.loads(tool_input)
        except (json.JSONDecodeError, TypeError):
            return tool_input if isinstance(tool_input, str) else ""
        if not isinstance(data, dict):
            return ""
        cmd = data.get("command")
        return cmd if isinstance(cmd, str) else ""

    # ------------------------------------------------------------------
    # 路径提取（Read/Write/Edit/Glob/Grep）
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_path(tool_input: str, path_key: str) -> str | None:
        """从 tool_call.input JSON 中提取路径字段值

        Args:
            tool_input: 工具输入 JSON 字符串
            path_key: 路径字段名（如 "file_path", "path"）

        Returns:
            路径字符串，未找到则返回 None
        """
        if not tool_input:
            return None

        try:
            data = json.loads(tool_input)
        except (json.JSONDecodeError, TypeError):
            return None

        if not isinstance(data, dict):
            return None

        value = data.get(path_key)
        if isinstance(value, str) and value.strip():
            return value.strip()
        return None

    # ------------------------------------------------------------------
    # 本地路径识别（fail-closed 扫描用）
    # ------------------------------------------------------------------

    @staticmethod
    def _looks_like_local_path(tok: str) -> bool:
        """判断 token 是否可能是本地文件路径（用于未识别命令的 fail-closed 扫描）

        仅对看起来像本地路径的 token 做沙箱校验，避免误伤普通参数。
        远端写法（含 @ 或 host:path / scheme://）直接排除。
        """
        if not tok:
            return False
        if "@" in tok:
            if (":" in tok or "." in tok) and not tok.startswith("@"):
                return False  # user@host[:path] 远端写法 (scp/rsync)
            return True  # @/path 或 @file 均为本地路径, 必须校验
        if ":" in tok and not tok.startswith("/") and not tok.startswith("."):
            return False  # scheme:// 或 host:path 远端写法
        t = os.path.expanduser(os.path.expandvars(tok))
        if t.startswith("-"):
            return False  # 选项标志
        if t.isdigit():
            return False  # 纯数字（如 -m 644 的模式）
        if "/" in t or t.startswith("~") or t.startswith(".") or ".." in t:
            return True
        return False
