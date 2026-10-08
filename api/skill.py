# -*- coding: utf-8 -*-

"""Skill 管理 API 路由

端点：
- GET    /skill               — 列出已安装 Skill
- POST   /skill               — 添加 Skill
- GET    /skill/{skill_id}    — 获取单个 Skill
- PATCH  /skill/{skill_id}    — 更新 Skill（启用/禁用、改名）
- DELETE /skill/{skill_id}    — 删除 Skill

配置来源：configs/skills.json（JSON 文件，管理平面元数据），由 SkillConfigStore 读写。
运行时 Skill 由 agent_space/skills 目录（文件系统，LocalSkillLoader 扫描）加载。

**重要**：前端添加 Skill 时，需要同时在 agent_space/skills/ 目录下创建实际的 SKILL.md 文件，
因为该目录是运行时加载的真相源，skills.json 只是元数据缓存。
"""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field

from core.skill.config_store import SkillConfigEntry
from core.validators import is_auth_enabled, require_user_id

from loguru import logger

router = APIRouter(prefix="/skill", tags=["skill"])


# ============================================================
# 文件操作辅助函数
# ============================================================

def _get_skills_dir(request: Request) -> str | None:
    """从 app.state 获取 skills 目录路径"""
    store = getattr(request.app.state, "skill_config_store", None)
    if store is None:
        return None
    return store.skills_dir


def _create_skill_file(skills_dir: str, name: str, markdown: str) -> None:
    """在 agent_space/skills/ 目录下创建 SKILL.md 文件

    Args:
        skills_dir: skills 目录路径（如 workspaces/agent_space/skills）
        name: skill 名称（用作子目录名）
        markdown: SKILL.md 的完整内容（含 frontmatter）
    """
    skill_dir = Path(skills_dir) / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    skill_file = skill_dir / "SKILL.md"
    skill_file.write_text(markdown, encoding="utf-8")
    logger.info("已在目录创建 Skill 文件: {}", skill_file)


def _delete_skill_file(skills_dir: str, name: str) -> bool:
    """删除 agent_space/skills/ 目录下的 Skill 文件夹

    Args:
        skills_dir: skills 目录路径
        name: skill 名称

    Returns:
        是否成功删除
    """
    import shutil
    skill_dir = Path(skills_dir) / name
    if skill_dir.exists() and skill_dir.is_dir():
        shutil.rmtree(skill_dir)
        logger.info("已删除 Skill 目录: {}", skill_dir)
        return True
    return False


def _is_path_safe(file_path: str) -> bool:
    """验证文件路径安全性（防止路径穿越）"""
    # 检查是否包含 ..
    if '..' in file_path:
        return False

    # 检查是否是绝对路径
    if file_path.startswith('/') or file_path.startswith('\\'):
        return False

    # 检查是否包含危险字符
    dangerous_chars = '<>:"|?*'
    if any(char in file_path for char in dangerous_chars):
        return False

    return True


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
    files: dict[str, str] | None = Field(
        default=None,
        description="额外文件映射（文件路径 → 内容），用于压缩包上传",
    )


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
    """添加 Skill

    重要：同时在 agent_space/skills/ 目录下创建实际的 SKILL.md 文件，
    因为该目录是运行时加载的真相源。
    """
    config = getattr(request.app.state, "config", None)
    require_user_id(user_id, is_auth_enabled(config))
    store = request.app.state.skill_config_store

    if store.get_by_name(body.name):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Skill '{body.name}' 已存在",
        )

    # 1. 先在 agent_space/skills/ 目录下创建实际的 SKILL.md 文件
    skills_dir = _get_skills_dir(request)
    if skills_dir:
        try:
            _create_skill_file(skills_dir, body.name, body.markdown)

            # 2. 如果有额外文件，写入到 skill 目录
            if body.files:
                skill_dir = Path(skills_dir) / body.name
                for file_path, content in body.files.items():
                    # 安全校验：防止路径穿越
                    if not _is_path_safe(file_path):
                        raise HTTPException(
                            status_code=status.HTTP_400_BAD_REQUEST,
                            detail=f"非法文件路径: {file_path}",
                        )

                    safe_path = skill_dir / file_path
                    # 确保路径在 skill 目录内
                    if not str(safe_path.resolve()).startswith(str(skill_dir.resolve())):
                        raise HTTPException(
                            status_code=status.HTTP_400_BAD_REQUEST,
                            detail=f"文件路径越界: {file_path}",
                        )

                    safe_path.parent.mkdir(parents=True, exist_ok=True)
                    safe_path.write_text(content, encoding="utf-8")
                    logger.debug("已写入文件: {}", safe_path)
        except HTTPException:
            raise
        except Exception as e:
            logger.exception("创建 Skill 文件失败: {}", e)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"创建 Skill 文件失败: {e}",
            )
    else:
        logger.warning("skills_dir 未配置，跳过创建实际文件（仅更新元数据）")

    # 2. 更新元数据配置（skills.json）
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
    """删除 Skill

    同时删除 agent_space/skills/ 目录下的实际 SKILL.md 文件。
    """
    config = getattr(request.app.state, "config", None)
    require_user_id(user_id, is_auth_enabled(config))
    store = request.app.state.skill_config_store

    # 1. 删除实际的 Skill 文件
    skills_dir = _get_skills_dir(request)
    if skills_dir:
        try:
            _delete_skill_file(skills_dir, skill_id)
        except Exception as e:
            logger.exception("删除 Skill 文件失败: {}", e)
            # 继续删除元数据，即使文件删除失败
    else:
        logger.warning("skills_dir 未配置，跳过删除实际文件（仅删除元数据）")

    # 2. 删除元数据配置
    deleted = await store.delete(skill_id)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Skill '{skill_id}' 不存在",
        )
