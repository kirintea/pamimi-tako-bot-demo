# -*- coding: utf-8 -*-

"""Offloader 用户域绑定回归测试

背景：AgentScope 2.0.7 的 Offloader 协议（agentscope/workspace/_offload_protocol.py）
调用 offload_context / offload_tool_result 时只传 session_id，不带 user_id
（见 agentscope/agent/_agent.py 的两处调用点）。而卸载目录按用户域隔离
要求 user_id，缺失时 LocalWorkspaceManager 直接抛 ValueError ——
框架截断工具结果 / 压缩上下文时即触发，中断整个回复。

覆盖：
- 框架调用形态（无 user_id 参数）经 UserScopedOffloader 绑定后正常落盘；
- 落盘路径位于 user_spaces/{user_id}/sessions/{session_id}/；
- 未绑定 user_id 的裸管理器仍抛 ValueError（用户域隔离保证不放松）。

运行方式：
    .venv/Scripts/python.exe -m pytest tests/test_workspace_offload.py -v
"""

from __future__ import annotations

import asyncio
import os
import tempfile
from pathlib import Path

import pytest

from agentscope.message import Msg, TextBlock, ToolResultBlock

from core.workspace import LocalWorkspaceManager, UserScopedOffloader


def _tool_block() -> ToolResultBlock:
    """构造一个被截断卸载的工具结果块"""
    return ToolResultBlock(id="tool-1", name="read", output="x" * 100)


def _user_msg() -> Msg:
    """构造一条被压缩卸载的对话消息"""
    return Msg(name="user", role="user", content=[TextBlock(text="hello")])


class TestUserScopedOffloader:
    """UserScopedOffloader — 把框架协议调用绑定到固定 user_id"""

    def test_offload_tool_result_protocol_call_binds_user(self):
        """框架调用形态 offload_tool_result(session_id, block) 不带 user_id，
        绑定后应写入用户私有域，不再抛 ValueError。"""
        with tempfile.TemporaryDirectory() as root:
            manager = LocalWorkspaceManager(base_dir=os.path.join(root, "ws"))
            offloader = UserScopedOffloader(manager, user_id="u1")

            # 与 agentscope/agent/_agent.py 的调用形态一致：仅传 session_id + block
            path = asyncio.run(offloader.offload_tool_result("s1", _tool_block()))

            parts = Path(path).parts
            assert "user_spaces" in parts
            assert "u1" in parts
            assert "s1" in parts
            assert Path(path).name == "tool_result-tool-1.txt"
            assert Path(path).is_file()

    def test_offload_context_protocol_call_binds_user(self):
        """框架调用形态 offload_context(session_id, msgs=...) 不带 user_id，
        绑定后应写入用户私有域（上下文压缩路径，同一缺陷）。"""
        with tempfile.TemporaryDirectory() as root:
            manager = LocalWorkspaceManager(base_dir=os.path.join(root, "ws"))
            offloader = UserScopedOffloader(manager, user_id="u1")

            # 与 agentscope/agent/_agent.py 的调用形态一致：msgs 走关键字参数
            path = asyncio.run(offloader.offload_context("s1", msgs=[_user_msg()]))

            parts = Path(path).parts
            assert "user_spaces" in parts
            assert "u1" in parts
            assert "s1" in parts
            assert Path(path).name == "context.jsonl"
            assert Path(path).is_file()

    def test_offload_coerces_invalid_ids(self):
        """绑定的 user_id / 框架传入的 session_id 均应被 coerce_id 净化"""
        with tempfile.TemporaryDirectory() as root:
            manager = LocalWorkspaceManager(base_dir=os.path.join(root, "ws"))
            offloader = UserScopedOffloader(manager, user_id="..")

            path = asyncio.run(offloader.offload_tool_result("../evil", _tool_block()))

            parts = Path(path).parts
            assert "anonymous" in parts
            assert "evil" not in str(Path(path).parent)


class TestBareManagerStillIsolated:
    """裸 LocalWorkspaceManager 不绑定 user_id 时仍拒绝卸载（隔离不放松）"""

    def test_offload_tool_result_without_user_id_raises(self):
        with tempfile.TemporaryDirectory() as root:
            manager = LocalWorkspaceManager(base_dir=os.path.join(root, "ws"))
            with pytest.raises(ValueError, match="user_id"):
                asyncio.run(manager.offload_tool_result("s1", _tool_block()))

    def test_offload_context_without_user_id_raises(self):
        with tempfile.TemporaryDirectory() as root:
            manager = LocalWorkspaceManager(base_dir=os.path.join(root, "ws"))
            with pytest.raises(ValueError, match="user_id"):
                asyncio.run(manager.offload_context("s1", msgs=[_user_msg()]))
