# -*- coding: utf-8 -*-

"""Skill 管理 API 路由

端点：
- GET    /skill               — 列出已安装 Skill
- POST   /skill               — 添加 Skill
- GET    /skill/{skill_id}    — 获取单个 Skill
- PATCH  /skill/{skill_id}    — 更新 Skill（启用/禁用、改名）
- DELETE /skill/{skill_id}    — 删除 Skill

配置来源：configs/skills.json（JSON 文件，管理平面元数据），由 SkillConfigStore 读写。
注意：运行时 Skill 由 agent_space/skills 目录（文件系统，LocalSkillLoader 扫描）加载，
本文件仅作为管理/展示用的元数据目录（enabled 等标记用于 UI 状态展示）。
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field

from core.skill.config_store import SkillConfigEntry
from core.validators import is_auth_enabled, require_user_id

from loguru import logger

router = APIRouter(prefix="/skill", tags=["skill"])


# ============================================================
# 请求 / 响应 Schema
# ============================================================

class CreateSkillRequest(BaseModel):
    """添加 Skill 请求"""

    name: str = Field(description="Skill 名称（唯一）")
    display_name: str | None = Field(default=None, description="显示名称")
    description: str = Field(default="", description="描述")
    markdown: str = Field(default="", description="SKILL.md 内容")
    tags: list[str] = Field(default_factory=list, description="标签")
    author: str | None = Field(default=None, description="作者")


class UpdateSkillRequest(BaseModel):
    """更新 Skill 请求"""

    name: str | None = Field(default=None, description="新名称（改名）")
    enabled: bool | None = Field(default=None, description="启用/禁用")
    display_name: str | None = Field(default=None, description="显示名称")
    description: str | None = Field(default=None, description="描述")


class SkillResponse(BaseModel):
    """Skill 响应"""

    id: str
    user_id: str
    name: str
    display_name: str | None
    description: str
    markdown: str
    tags: list[str]
    author: str | None
    version: str | None = None
    enabled: bool
    created_at: str
    updated_at: str


class ListSkillsResponse(BaseModel):
    """Skill 列表响应"""

    skills: list[SkillResponse]
    total: int


# ============================================================
# 工具函数
# ============================================================

def _entry_to_response(entry: SkillConfigEntry) -> SkillResponse:
    return SkillResponse(
        id=entry.id,
        user_id="system",
        name=entry.name,
        display_name=entry.display_name,
        description=entry.description,
        markdown=entry.markdown,
        tags=entry.tags,
        author=entry.author,
        version=entry.version,
        enabled=entry.enabled,
        created_at=entry.created_at,
        updated_at=entry.updated_at,
    )


# ============================================================
# 端点
# ============================================================

@router.get("", response_model=ListSkillsResponse)
async def list_skills(request: Request, user_id: str = "anonymous"):
    """列出已安装 Skill"""
    config = getattr(request.app.state, "config", None)
    require_user_id(user_id, is_auth_enabled(config))
    store = request.app.state.skill_config_store
    entries = store.get_all()
    return ListSkillsResponse(
        skills=[_entry_to_response(e) for e in entries],
        total=len(entries),
    )


@router.post(
    "",
    response_model=SkillResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_skill(
    request: Request,
    body: CreateSkillRequest,
    user_id: str = "anonymous",
):
    """添加 Skill"""
    config = getattr(request.app.state, "config", None)
    require_user_id(user_id, is_auth_enabled(config))
    store = request.app.state.skill_config_store

    if store.get_by_name(body.name):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Skill '{body.name}' 已存在",
        )

    entry = SkillConfigEntry(
        name=body.name,
        display_name=body.display_name,
        description=body.description,
        markdown=body.markdown,
        tags=body.tags,
        author=body.author,
        enabled=True,
    )
    await store.upsert(entry)
    created = store.get_by_name(body.name)
    return _entry_to_response(created)


@router.get("/{skill_id}", response_model=SkillResponse)
async def get_skill(
    request: Request,
    skill_id: str,
    user_id: str = "anonymous",
):
    """获取单个 Skill"""
    config = getattr(request.app.state, "config", None)
    require_user_id(user_id, is_auth_enabled(config))
    store = request.app.state.skill_config_store
    entry = store.get_by_name(skill_id)
    if not entry:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Skill '{skill_id}' 不存在",
        )
    return _entry_to_response(entry)


@router.patch("/{skill_id}", response_model=SkillResponse)
async def update_skill(
    request: Request,
    skill_id: str,
    body: UpdateSkillRequest,
    user_id: str = "anonymous",
):
    """更新 Skill"""
    config = getattr(request.app.state, "config", None)
    require_user_id(user_id, is_auth_enabled(config))
    store = request.app.state.skill_config_store
    entry = store.get_by_name(skill_id)
    if not entry:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Skill '{skill_id}' 不存在",
        )

    # 改名：冲突检查 + 删除旧键
    if body.name is not None and body.name != entry.name:
        if store.get_by_name(body.name):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Skill '{body.name}' 已存在",
            )
        await store.delete(entry.name)
        entry.name = body.name
        entry.id = body.name

    if body.enabled is not None:
        entry.enabled = body.enabled
    if body.display_name is not None:
        entry.display_name = body.display_name
    if body.description is not None:
        entry.description = body.description

    await store.upsert(entry)
    return _entry_to_response(entry)


@router.delete("/{skill_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_skill(
    request: Request,
    skill_id: str,
    user_id: str = "anonymous",
):
    """删除 Skill"""
    config = getattr(request.app.state, "config", None)
    require_user_id(user_id, is_auth_enabled(config))
    store = request.app.state.skill_config_store
    deleted = await store.delete(skill_id)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Skill '{skill_id}' 不存在",
        )
