# -*- coding: utf-8 -*-

"""本地工作区管理器 — 双根工作路径布局

布局（sandbox_dir 默认 ./workspaces）：
    workspaces/
    ├── agent_space/                      # 【Agent 共享域】普通用户只读、root 可写
    │   ├── skills/  mcp/  prompts/  runtime/
    │   ├── soul.md   agent.md
    └── user_spaces/
        └── {user_id}/                    # 【用户私有域】仅该用户可写
            ├── memory/                   # 长期记忆（AgenticMemory 落盘）
            ├── sessions/{session_id}/    # 会话工作区
            ├── uploads/                  # 用户上传文件
            └── scratch/                  # 文件修改/执行私有区

参考 AgentScope 的 LocalWorkspaceManager，简化为平台所需功能。

使用方式：
    manager = LocalWorkspaceManager(base_dir="./workspaces")
    workspace = await manager.get_workspace(user_id, session_id)
    # workspace.workdir == workspaces/user_spaces/{user_id}/sessions/{session_id}
"""

from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from loguru import logger

from core.validators import coerce_id


# agent_space 脚手架子目录与占位文件
_AGENT_SPACE_SUBDIRS = ("skills", "mcp", "prompts", "runtime")
_AGENT_SPACE_PLACEHOLDERS = {
    "soul.md": (
        "# Agent Soul\n\n"
        "本文件在 agent_space/ 下，作为 Agent 的人设（OpenClaw 风格）。\n"
        "普通用户只读，root 用户可编辑。\n"
    ),
    "agent.md": (
        "# Agent 说明\n\n"
        "本文件描述 Agent 的系统提示基线与行为规范。\n"
        "普通用户只读，root 用户可编辑。\n"
    ),
}


@dataclass
class Workspace:
    """工作区实例"""
    workdir: str
    """工作区根目录的绝对路径"""

    user_id: str
    """所属用户 ID"""

    session_id: str
    """所属会话 ID"""

    def resolve_path(self, relative_path: str) -> str:
        """将相对路径解析为绝对路径，确保不越界沙箱

        Args:
            relative_path: 相对于工作区根目录的路径

        Returns:
            绝对路径

        Raises:
            ValueError: 路径越界（超出工作区根目录）
        """
        # 使用 realpath 解析符号链接，防止通过软链接逃逸沙箱（fix #1）
        target = os.path.realpath(os.path.join(self.workdir, relative_path))
        workdir_norm = os.path.realpath(self.workdir)
        if not (target == workdir_norm or target.startswith(workdir_norm + os.sep)):
            raise ValueError(
                f"路径越界: '{relative_path}' 超出工作区范围 ({self.workdir})"
            )
        return target

    def list_files(self, sub_path: str = ".") -> list[dict]:
        """列出工作区中的文件

        Args:
            sub_path: 子目录路径（相对于工作区根目录）

        Returns:
            文件信息列表 [{"name": ..., "path": ..., "is_dir": ..., "size": ...}]
        """
        target = self.resolve_path(sub_path)
        if not os.path.isdir(target):
            return []

        result = []
        try:
            for entry in os.scandir(target):
                stat = entry.stat()
                result.append({
                    "name": entry.name,
                    "path": os.path.relpath(entry.path, self.workdir),
                    "is_dir": entry.is_dir(),
                    "size": stat.st_size if entry.is_file() else 0,
                    "modified": stat.st_mtime,
                })
        except PermissionError:
            pass

        result.sort(key=lambda x: (not x["is_dir"], x["name"]))
        return result

    def file_exists(self, relative_path: str) -> bool:
        """检查文件是否存在"""
        return os.path.isfile(self.resolve_path(relative_path))

    def dir_exists(self, relative_path: str) -> bool:
        """检查目录是否存在"""
        return os.path.isdir(self.resolve_path(relative_path))


class LocalWorkspaceManager:
    """本地工作区管理器（双根：agent_space / user_spaces）

    为每个 (user_id, session_id) 提供隔离的会话工作目录，并提供
    读 agent_space（Agent 共享资产）与写 user_spaces/{user_id}（用户私有）的
    路径解析能力。

    Args:
        base_dir: 工作区根目录（即 sandbox_dir，如 ./workspaces），其下包含
            agent_space/ 与 user_spaces/ 两个域。
        agent_space_name: Agent 共享域名（默认 agent_space）。
        user_spaces_name: 用户私有域父目录名（默认 user_spaces）。
    """

    def __init__(
        self,
        base_dir: str = "./workspaces",
        agent_space_name: str = "agent_space",
        user_spaces_name: str = "user_spaces",
    ) -> None:
        self._base_dir = os.path.abspath(base_dir)
        self._agent_space_name = agent_space_name
        self._user_spaces_name = user_spaces_name
        self._agent_space_dir = os.path.join(self._base_dir, agent_space_name)
        self._user_spaces_dir = os.path.join(self._base_dir, user_spaces_name)

        os.makedirs(self._agent_space_dir, exist_ok=True)
        os.makedirs(self._user_spaces_dir, exist_ok=True)
        self._scaffold_agent_space()
        self._warn_legacy_layout()

        logger.info(
            "工作区管理器已初始化（双根）: base={}, agent_space={}, user_spaces={}",
            self._base_dir, self._agent_space_dir, self._user_spaces_dir,
        )

    # ------------------------------------------------------------------
    # 双根访问器
    # ------------------------------------------------------------------

    @property
    def base_dir(self) -> str:
        return self._base_dir

    @property
    def agent_space_dir(self) -> str:
        """Agent 共享域根目录（普通用户只读、root 可写）"""
        return self._agent_space_dir

    @property
    def user_spaces_dir(self) -> str:
        """用户私有域父目录"""
        return self._user_spaces_dir

    def get_agent_space_dir(self) -> str:
        """返回 Agent 共享域根目录（供 Agent 运行时只读读取 skills/mcp/soul 等）"""
        return self._agent_space_dir

    def get_user_space_dir(self, user_id: str) -> str:
        """返回指定用户的私有域根目录 user_spaces/{user_id}"""
        return os.path.join(self._user_spaces_dir, coerce_id(user_id))

    # ------------------------------------------------------------------
    # 脚手架与旧数据告警
    # ------------------------------------------------------------------

    def _scaffold_agent_space(self) -> None:
        """创建 agent_space 子目录与占位文件（已存在则跳过，不覆盖用户编辑）"""
        for sub in _AGENT_SPACE_SUBDIRS:
            os.makedirs(os.path.join(self._agent_space_dir, sub), exist_ok=True)
        for name, content in _AGENT_SPACE_PLACEHOLDERS.items():
            path = os.path.join(self._agent_space_dir, name)
            if not os.path.exists(path):
                try:
                    with open(path, "w", encoding="utf-8") as f:
                        f.write(content)
                except OSError:
                    pass

    def _warn_legacy_layout(self) -> None:
        """demo 阶段：发现旧的扁平 workspaces/{user_id} 结构仅告警，不读取/不迁移。

        新布局为 workspaces/user_spaces/{user_id}/...，旧的 workspaces/{user_id}、
        workspaces/sessions/、workspaces/uploads/ 等直接丢弃（见方案 §8.5）。
        """
        try:
            for name in os.listdir(self._base_dir):
                full = os.path.join(self._base_dir, name)
                if not os.path.isdir(full):
                    continue
                if name in (self._agent_space_name, self._user_spaces_name):
                    continue
                # 其他顶层目录视为旧扁平结构残留
                logger.warning(
                    "发现旧版工作区顶层目录（demo 阶段直接丢弃，不迁移）: {}",
                    full,
                )
        except OSError:
            pass

    # ------------------------------------------------------------------
    # 工作区生命周期
    # ------------------------------------------------------------------

    async def get_workspace(
        self,
        user_id: str,
        session_id: str,
    ) -> Workspace:
        """获取或创建工作区（会话工作区归用户域）

        Args:
            user_id: 用户 ID
            session_id: 会话 ID

        Returns:
            Workspace 实例（workdir = user_spaces/{user_id}/sessions/{session_id}）
        """
        user_id = coerce_id(user_id)
        session_id = coerce_id(session_id)
        workdir = self._get_workdir(user_id, session_id)
        os.makedirs(workdir, exist_ok=True)
        return Workspace(
            workdir=workdir,
            user_id=user_id,
            session_id=session_id,
        )

    async def delete_workspace(
        self,
        user_id: str,
        session_id: str,
    ) -> bool:
        """删除工作区（会话工作区归用户域）"""
        user_id = coerce_id(user_id)
        session_id = coerce_id(session_id)
        workdir = self._get_workdir(user_id, session_id)
        deleted = os.path.isdir(workdir)
        if deleted:
            shutil.rmtree(workdir, ignore_errors=True)
            logger.info("已删除工作区: {}", workdir)
        # 同时清理该 session 的 offload 卸载目录（fix #3），缺失不报错
        self._cleanup_offload_dir(user_id, session_id)
        return deleted

    async def workspace_exists(
        self,
        user_id: str,
        session_id: str,
    ) -> bool:
        """检查工作区是否存在"""
        workdir = self._get_workdir(user_id, session_id)
        return os.path.isdir(workdir)

    def list_workspaces(self, user_id: str) -> list[str]:
        """列出用户的所有会话 session ID（位于 user_spaces/{user_id}/sessions/）"""
        user_dir = self.get_user_space_dir(user_id)
        sessions_dir = os.path.join(user_dir, "sessions")
        if not os.path.isdir(sessions_dir):
            return []
        return [
            name for name in os.listdir(sessions_dir)
            if os.path.isdir(os.path.join(sessions_dir, name))
        ]

    def _get_workdir(self, user_id: str, session_id: str) -> str:
        """获取会话工作区目录路径

        布局：user_spaces/{user_id}/sessions/{session_id}
        对 user_id / session_id 进行标识符规范化，防止 ``"/"``、``"../"``
        等被拼接到文件系统路径造成越界（fix #4）。
        """
        return os.path.join(
            self._user_spaces_dir,
            coerce_id(user_id),
            "sessions",
            coerce_id(session_id),
        )

    # ------------------------------------------------------------------
    # Offloader 卸载目录布局（fix #2 / fix #3）
    #
    # 卸载文件（上下文 / 工具结果）落在 user_spaces/{user_id}/sessions/{session_id}
    # 下，与 _get_workdir 完全一致，确保 api/chat.py 读取卸载文件时路径匹配。
    # ------------------------------------------------------------------

    def _offload_dir(
        self,
        session_id: str,
        user_id: str | None = None,
    ) -> Path:
        """返回某 session 的卸载目录 = user_spaces/{user_id}/sessions/{session_id}

        Args:
            session_id: 会话 ID（会被 coerce_id 规范化）。
            user_id: 必填。按用户隔离卸载目录（用户域）。
        """
        if not user_id:
            raise ValueError("offload 目录需要 user_id（用户域隔离）")
        sid = coerce_id(session_id)
        uid = coerce_id(user_id)
        return Path(self._user_spaces_dir) / uid / "sessions" / sid

    def _cleanup_offload_dir(self, user_id: str, session_id: str) -> None:
        """删除某 session 的卸载目录（缺失时不报错）"""
        d = self._offload_dir(session_id, user_id)
        if d.is_dir():
            shutil.rmtree(d, ignore_errors=True)
            logger.info("已删除 offload 目录: {}", d)

    # ------------------------------------------------------------------
    # Offloader 协议实现 — 上下文卸载
    # ------------------------------------------------------------------

    async def offload_context(
        self,
        session_id: str,
        msgs: list,
        user_id: str | None = None,
        **kwargs,
    ) -> str:
        """持久化被压缩的消息到 JSONL 文件（Offloader 协议）

        压缩时由 AgentScope 框架自动调用，将被移除的消息写入文件。
        Agent 后续可通过 Read/Grep 工具回查被卸载的内容。

        Args:
            session_id: 会话 ID
            msgs: 被压缩移除的消息列表
            user_id: 必填。传入时对用户域按用户隔离卸载目录。

        Returns:
            写入的文件路径
        """
        if not user_id:
            raise ValueError("offload_context 需要 user_id（用户域隔离）")
        session_dir = self._offload_dir(session_id, user_id=user_id)
        session_dir.mkdir(parents=True, exist_ok=True)

        filepath = session_dir / "context.jsonl"
        with open(filepath, "a", encoding="utf-8") as f:
            for msg in msgs:
                content = getattr(msg, "content", [])
                record = {
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "role": getattr(msg, "role", "unknown"),
                    "name": getattr(msg, "name", ""),
                    "content": [
                        block.model_dump() if hasattr(block, "model_dump")
                        else {"type": "text", "text": str(block)}
                        for block in (content if isinstance(content, list) else [content])
                    ],
                }
                f.write(json.dumps(record, ensure_ascii=False) + "\n")

        logger.info("Offloaded {} messages to {}", len(msgs), filepath)
        return str(filepath)

    async def offload_tool_result(
        self,
        session_id: str,
        tool_result,
        user_id: str | None = None,
        **kwargs,
    ) -> str:
        """持久化被截断的工具结果到独立文件（Offloader 协议）

        截断时由 AgentScope 框架自动调用，保留完整工具结果。
        截断标记会包含文件路径提示，Agent 可按需读取完整内容。

        Args:
            session_id: 会话 ID
            tool_result: 被截断的工具结果
            user_id: 必填。传入时对用户域按用户隔离卸载目录。

        Returns:
            写入的文件路径
        """
        if not user_id:
            raise ValueError("offload_tool_result 需要 user_id（用户域隔离）")
        session_dir = self._offload_dir(session_id, user_id=user_id)
        session_dir.mkdir(parents=True, exist_ok=True)

        tool_id = getattr(tool_result, "id", "unknown")
        filepath = session_dir / f"tool_result-{tool_id}.txt"
        content = getattr(tool_result, "content", "")
        if not isinstance(content, str):
            content = str(content)
        filepath.write_text(content, encoding="utf-8")

        logger.info("Offloaded tool result {} to {}", tool_id, filepath)
        return str(filepath)
