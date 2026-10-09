# -*- coding: utf-8 -*-

"""存储层数据模型 — 定义仍落库资源的 Pydantic 模型

参考 AgentScope 的 storage._model，简化为平台所需的子集。
所有记录共用 _RecordBase（id + created_at + updated_at）。
注：Agent / Schedule / Channel / MCP / Skill 的记录模型均已删除（对应表已从 DDL 移除，
配置改由 JSON 文件驱动），当前仅保留 Session 相关模型。
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


# ============================================================
# 基础模型
# ============================================================

def _generate_id() -> str:
    """生成短唯一 ID"""
    return uuid.uuid4().hex[:16]


class _RecordBase(BaseModel):
    """所有记录的基类"""

    id: str = Field(
        default_factory=_generate_id,
        description="唯一标识",
    )
    created_at: datetime = Field(
        default_factory=datetime.now,
        description="创建时间",
    )
    updated_at: datetime = Field(
        default_factory=datetime.now,
        description="更新时间",
    )


# ============================================================
# Session 记录
# ============================================================

class SessionSource(str, Enum):
    """会话来源"""
    USER = "user"
    SCHEDULE = "schedule"
    CHANNEL = "channel"
    FORK = "fork"


class SessionConfig(BaseModel):
    """会话配置"""

    workspace_id: str = Field(
        default_factory=_generate_id,
        description="工作区 ID",
    )
    name: str = Field(
        default_factory=lambda: datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        description="会话显示名称",
    )
    cwd: str | None = Field(default=None, description="当前工作目录")
    chat_model_config: dict | None = Field(
        default=None,
        description="聊天模型配置",
    )


class SessionRecord(_RecordBase):
    """会话持久化记录"""

    user_id: str = Field(description="所属用户 ID")
    agent_id: str = Field(description="所属 Agent ID")
    source: SessionSource = Field(
        default=SessionSource.USER,
        description="会话来源",
    )
    team_id: str | None = Field(default=None, description="所属团队 ID")
    config: SessionConfig = Field(description="会话配置")
    state_json: str = Field(
        default="",
        description="AgentState 序列化 JSON（由 chat_service 管理）",
    )
    parent_session_id: str | None = Field(
        default=None,
        description="父会话 ID（Fork 血缘），根会话为 None",
    )
    depth: int = Field(
        default=0,
        description="Fork 深度，根会话=0，每 fork 一次 +1",
    )


# ============================================================
# MCP / Skill 记录 — 已从 storage_models 移除
# ============================================================
# MCP 配置改由 configs/mcps.json（MCPConfigStore）文件驱动，运行时单一真源；
# Skill 元数据改由 configs/skills.json（SkillConfigStore）文件驱动（管理平面）。
# 二者均不再落库，故对应 Pydantic 记录模型已删除（见 core/mcp/config_store.py、
# core/skill/config_store.py 的 *ConfigEntry）。
