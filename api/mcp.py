# -*- coding: utf-8 -*-

"""MCP 管理 API 路由

端点：
- GET    /mcp             — 列出已安装 MCP
- POST   /mcp             — 添加 MCP
- PATCH  /mcp/{mcp_id}    — 更新 MCP（启用/禁用、改名）
- DELETE /mcp/{mcp_id}    — 删除 MCP

配置来源：configs/mcps.json（JSON 文件，单一真源），由 MCPConfigStore 读写。
运行时 AgentFactory 直接读取同一文件，故此处与管理平面共享同一真相源，不再使用数据库。
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field

from core.mcp.config_store import MCPConfigEntry
from core.validators import is_auth_enabled, require_user_id

from loguru import logger

router = APIRouter(prefix="/mcp", tags=["mcp"])


# ============================================================
# 请求 / 响应 Schema
# ============================================================

class CreateMCPRequest(BaseModel):
    """添加 MCP 请求"""

    name: str = Field(description="MCP 名称（唯一）")
    transport: str = Field(default="stdio", description="传输方式: stdio / http / streamable_http")
    command: str | None = Field(default=None, description="stdio 命令")
    args: list[str] = Field(default_factory=list, description="stdio 参数")
    url: str | None = Field(default=None, description="HTTP MCP 地址")
    headers: dict[str, str] = Field(default_factory=dict, description="HTTP 请求头")
    display_name: str | None = Field(default=None, description="显示名称")
    description: str = Field(default="", description="描述")


class UpdateMCPRequest(BaseModel):
    """更新 MCP 请求"""

    name: str | None = Field(default=None, description="新名称（改名）")
    enabled: bool | None = Field(default=None, description="启用/禁用")
    display_name: str | None = Field(default=None, description="显示名称")
    description: str | None = Field(default=None, description="描述")


class MCPResponse(BaseModel):
    """MCP 响应"""

    id: str
    user_id: str
    name: str
    transport: str
    command: str | None
    args: list[str]
    url: str | None
    headers: dict[str, str]
    display_name: str | None
    description: str
    enabled: bool
    note: str | None = Field(
        default=None,
        description="操作提示：MCP 变更后已有会话不会自动重载，需新开会话生效",
    )
    created_at: str
    updated_at: str


class ActionResponse(BaseModel):
    """通用操作结果（含提示）"""

    ok: bool = True
    message: str
    note: str | None = None


class ListMCPsResponse(BaseModel):
    """MCP 列表响应"""

    mcps: list[MCPResponse]
    total: int


# ============================================================
# 工具函数
# ============================================================

def _entry_to_response(entry: MCPConfigEntry) -> MCPResponse:
    return MCPResponse(
        id=entry.name,
        user_id="system",
        name=entry.name,
        transport=entry.transport,
        command=entry.command,
        args=entry.args,
        url=entry.url,
        headers=entry.headers,
        display_name=entry.display_name,
        description=entry.description,
        enabled=entry.enabled,
        created_at="",
        updated_at="",
    )


# MCP 变更后，已有会话内的 MCP 客户端不会自动重载，新会话才会读取最新配置。
_REQUIRES_NEW_SESSION_NOTE = (
    "MCP 配置已保存。已有会话不会自动重载 MCP 客户端，请新开会话以加载新的 MCP 工具。"
)


def _refresh_runtime_mcp(request: Request) -> None:
    """把文件中的 MCP 配置同步进运行时 config.mcp_servers（新会话生效；老会话不变）。"""
    config = getattr(request.app.state, "config", None)
    if config is None:
        return
    store = request.app.state.mcp_config_store
    try:
        config.mcp_servers = store.get_mcp_configs()
    except Exception as e:  # noqa: BLE001
        logger.warning("刷新运行时 MCP 配置失败（下次重启生效）: {}", e)


# ============================================================
# 端点
# ============================================================

@router.get("", response_model=ListMCPsResponse)
async def list_mcps(request: Request, user_id: str = "anonymous"):
    """列出已安装 MCP"""
    config = getattr(request.app.state, "config", None)
    require_user_id(user_id, is_auth_enabled(config))
    store = request.app.state.mcp_config_store
    entries = store.get_all()
    return ListMCPsResponse(
        mcps=[_entry_to_response(e) for e in entries],
        total=len(entries),
    )


@router.post(
    "",
    response_model=MCPResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_mcp(
    request: Request,
    body: CreateMCPRequest,
    user_id: str = "anonymous",
):
    """添加 MCP"""
    config = getattr(request.app.state, "config", None)
    require_user_id(user_id, is_auth_enabled(config))
    store = request.app.state.mcp_config_store

    if store.get_by_name(body.name):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"MCP '{body.name}' 已存在",
        )

    entry = MCPConfigEntry(
        name=body.name,
        transport=body.transport,
        command=body.command,
        args=body.args,
        url=body.url,
        headers=body.headers,
        display_name=body.display_name,
        description=body.description,
        enabled=True,
    )
    await store.upsert(entry)
    _refresh_runtime_mcp(request)
    created = store.get_by_name(body.name)
    resp = _entry_to_response(created)
    resp.note = _REQUIRES_NEW_SESSION_NOTE
    return resp


@router.patch("/{mcp_id}", response_model=MCPResponse)
async def update_mcp(
    request: Request,
    mcp_id: str,
    body: UpdateMCPRequest,
    user_id: str = "anonymous",
):
    """更新 MCP"""
    config = getattr(request.app.state, "config", None)
    require_user_id(user_id, is_auth_enabled(config))
    store = request.app.state.mcp_config_store
    entry = store.get_by_name(mcp_id)
    if not entry:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"MCP '{mcp_id}' 不存在",
        )

    # 改名：冲突检查 + 删除旧键
    if body.name is not None and body.name != entry.name:
        if store.get_by_name(body.name):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"MCP '{body.name}' 已存在",
            )
        await store.delete(entry.name)
        entry.name = body.name

    if body.enabled is not None:
        entry.enabled = body.enabled
    if body.display_name is not None:
        entry.display_name = body.display_name
    if body.description is not None:
        entry.description = body.description

    await store.upsert(entry)
    _refresh_runtime_mcp(request)
    resp = _entry_to_response(entry)
    resp.note = _REQUIRES_NEW_SESSION_NOTE
    return resp


@router.delete("/{mcp_id}", response_model=ActionResponse, status_code=status.HTTP_200_OK)
async def delete_mcp(
    request: Request,
    mcp_id: str,
    user_id: str = "anonymous",
):
    """删除 MCP"""
    config = getattr(request.app.state, "config", None)
    require_user_id(user_id, is_auth_enabled(config))
    store = request.app.state.mcp_config_store
    deleted = await store.delete(mcp_id)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"MCP '{mcp_id}' 不存在",
        )
    _refresh_runtime_mcp(request)
    return ActionResponse(
        ok=True,
        message=f"MCP '{mcp_id}' 已删除",
        note=_REQUIRES_NEW_SESSION_NOTE,
    )
