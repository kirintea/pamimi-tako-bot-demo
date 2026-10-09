# -*- coding: utf-8 -*-

"""渠道管理 API 路由

端点：
- GET    /channels                 — 列出配置文件中的渠道（从 config_store 读取）
- POST   /channels                 — 新增渠道（写入 JSON 配置文件）
- GET    /channels/{channel_id}    — 获取单个渠道
- PATCH  /channels/{channel_id}    — 更新渠道（写入 JSON 配置文件，热加载自动感知）
- DELETE /channels/{channel_id}    — 删除渠道（从 JSON 配置文件移除并停止运行实例）
- POST   /channels/{channel_id}/start — 热启动单个渠道（启用 config + 连接）
- POST   /channels/{channel_id}/stop   — 热停止单个渠道（禁用 config + 断开）
- GET    /channels/status          — 运行态快照（来自 ChannelManager）
- GET    /channels/manifests       — 渠道元数据（供前端渲染添加表单 + 依赖灯）

架构变更（2026-10-07）：
- 渠道连接配置改为 JSON 文件驱动（core.channels.config_store），
  不再使用数据库存储 bot_id/secret 等连接信息。
- 原 DB 读写逻辑（storage.list_channels 等）已随 channels 表一并删除，如需回退请查 git 历史。
"""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field

from core.channels.catalog import get_channel_catalog
from core.channels.config_store import ChannelConfigStore, ChannelEntry
from core.channels.registry import REGISTRY

from loguru import logger

router = APIRouter(prefix="/channels", tags=["channels"])


# ============================================================
# 请求 / 响应 Schema
# ============================================================

class ChannelConfigRequest(BaseModel):
    """渠道配置（创建/局部更新共用；字段均可选）"""
    bot_id: str | None = Field(default=None, description="企微 Bot ID")
    secret: str | None = Field(default=None, description="企微 Secret")
    allow_from: list[str] | None = Field(default=None, description="发送者白名单")
    welcome_message: str | None = Field(default=None, description="欢迎语")


class CreateChannelRequest(BaseModel):
    """新增渠道请求"""
    name: str = Field(description="渠道名称（同一配置文件唯一）")
    type: str = Field(default="wecom", description="渠道类型")
    enabled: bool = Field(default=False, description="创建后是否立即启用并启动")
    config: ChannelConfigRequest = Field(default_factory=ChannelConfigRequest)


class UpdateChannelRequest(BaseModel):
    """更新渠道请求"""
    name: str | None = Field(default=None, description="新名称")
    enabled: bool | None = Field(default=None, description="启用/禁用（触发热切换）")
    config: ChannelConfigRequest | None = Field(default=None, description="配置覆盖")


class ChannelResponse(BaseModel):
    """渠道响应"""
    id: str
    name: str
    type: str
    enabled: bool
    status: str = "stopped"
    config: dict = Field(default_factory=dict)


class ListChannelsResponse(BaseModel):
    """渠道列表响应"""
    channels: list[ChannelResponse]
    total: int


class StatusResponse(BaseModel):
    """运行态响应"""
    runtime: dict = Field(default_factory=dict)


class ManifestsResponse(BaseModel):
    """渠道清单响应"""
    manifests: list[dict] = Field(default_factory=list)


# ============================================================
# 工具函数
# ============================================================

def _entry_to_response(entry: ChannelEntry, runtime: dict | None = None) -> ChannelResponse:
    """ChannelEntry → ChannelResponse（config 中 secret 脱敏）"""
    cfg = dict(entry.config)
    if cfg.get("secret"):
        cfg["secret"] = "********"
    rt = runtime or {}
    return ChannelResponse(
        id=entry.id,
        name=entry.name,
        type=entry.type,
        enabled=entry.enabled,
        status="running" if rt.get("running") else ("stopped" if not entry.enabled else "stopped"),
        config=cfg,
    )


_frozen_cache: set[str] | None = None


def _get_frozen_set() -> set[str]:
    """延迟读取项目 venv 的 pip freeze（只执行一次）。"""
    global _frozen_cache
    if _frozen_cache is not None:
        return _frozen_cache

    python = sys.executable
    venv_python_candidates = [
        os.path.join(os.getcwd(), ".venv", "Scripts", "python.exe"),
        os.path.join(os.getcwd(), ".venv", "bin", "python"),
        os.path.join(os.getcwd(), "venv", "Scripts", "python.exe"),
        os.path.join(os.getcwd(), "venv", "bin", "python"),
    ]
    for vp in venv_python_candidates:
        if os.path.isfile(vp):
            python = vp
            break

    try:
        out = subprocess.check_output(
            [python, "-m", "pip", "freeze", "--disable-pip-version-check"],
            stderr=subprocess.DEVNULL,
            timeout=10,
            text=True,
        )
        _frozen_cache = {
            line.split("==")[0].split(">=")[0].split("<=")[0].split("~=")[0]
            .split("[")[0].strip().lower()
            for line in out.splitlines()
            if line and not line.startswith("-")
        }
    except Exception:
        _frozen_cache = set()

    return _frozen_cache


def _check_dependency(pip_name: str) -> bool:
    """探测某个可选依赖是否已安装。"""
    frozen = _get_frozen_set()
    if frozen:
        return pip_name.lower().split("[")[0] in frozen
    try:
        return importlib.util.find_spec(pip_name) is not None
    except (ImportError, ValueError, ModuleNotFoundError):
        return False


def _enrich_manifest(manifest: dict) -> dict:
    """为 manifest 计算依赖可用性，供前端渲染红绿指示灯。"""
    deps = list(manifest.get("dependencies") or [])
    checked = [
        {
            "name": d.get("name", ""),
            "import_name": d.get("import_name", d.get("name", "")),
            "available": _check_dependency(d.get("name", "")),
        }
        for d in deps
    ]
    enriched = dict(manifest)
    enriched["dependencies"] = checked
    enriched["dependencies_satisfied"] = all(d["available"] for d in checked)
    return enriched


def _get_manager(request: Request):
    mgr = getattr(request.app.state, "channel_manager", None)
    if mgr is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="渠道管理器未就绪",
        )
    return mgr


def _get_config_store(request: Request) -> ChannelConfigStore:
    store = getattr(request.app.state, "channel_config_store", None)
    if store is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="渠道配置存储未就绪",
        )
    return store


# ============================================================
# 端点
# ============================================================

@router.get("", response_model=ListChannelsResponse)
async def list_channels(request: Request):
    """列出配置文件中的所有渠道"""
    store = _get_config_store(request)
    mgr = _get_manager(request)
    runtime = await mgr.get_runtime_status()
    entries = store.get_all()
    return ListChannelsResponse(
        channels=[_entry_to_response(e, runtime.get(e.id)) for e in entries],
        total=len(entries),
    )


@router.get("/status", response_model=StatusResponse)
async def channels_status(request: Request):
    """运行态快照（来自 ChannelManager）"""
    mgr = _get_manager(request)
    runtime = await mgr.get_runtime_status()
    return StatusResponse(runtime=runtime)


@router.get("/manifests", response_model=ManifestsResponse)
async def channels_manifests(request: Request):
    """渠道元数据（前端用于渲染添加表单 + 依赖可用性指示灯）。

    返回全量目录：已知可接入的全部渠道类型，
    无论后端是否实现适配器；backend_supported 标记后端是否已接入，
    dependencies 含运行时探测到的可用性（available）。
    """
    catalog = get_channel_catalog()
    return ManifestsResponse(manifests=[_enrich_manifest(m) for m in catalog])


@router.post(
    "",
    response_model=ChannelResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_channel(
    request: Request,
    body: CreateChannelRequest,
):
    """新增渠道（写入 JSON 配置文件，可选立即启用）"""
    store = _get_config_store(request)
    mgr = _get_manager(request)

    # 类型白名单：仅后端已接入（registry 中实现的）适配器可创建
    if body.type not in REGISTRY:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"渠道类型 '{body.type}' 后端尚未支持，无法创建。"
                "可接入类型见 GET /channels/manifests。"
            ),
        )

    # ID 唯一性（用 name 生成稳定 ID）
    import uuid
    channel_id = f"{body.type}-{uuid.uuid4().hex[:12]}"
    if store.get_by_id(channel_id):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"渠道 ID '{channel_id}' 已存在",
        )

    entry = ChannelEntry(
        id=channel_id,
        type=body.type,
        name=body.name,
        enabled=body.enabled,
        config=body.config.model_dump(exclude_none=True),
    )
    await store.upsert(entry)

    # 创建即启用 → 热启动
    if body.enabled:
        await mgr.on_config_change()

    return _entry_to_response(entry)


@router.get("/{channel_id}", response_model=ChannelResponse)
async def get_channel(request: Request, channel_id: str):
    """获取单个渠道"""
    store = _get_config_store(request)
    entry = store.get_by_id(channel_id)
    if not entry:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"渠道 '{channel_id}' 不存在",
        )
    return _entry_to_response(entry)


@router.patch("/{channel_id}", response_model=ChannelResponse)
async def update_channel(
    request: Request,
    channel_id: str,
    body: UpdateChannelRequest,
):
    """更新渠道（写入 JSON 配置文件，热加载自动感知）"""
    store = _get_config_store(request)
    mgr = _get_manager(request)

    entry = store.get_by_id(channel_id)
    if not entry:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"渠道 '{channel_id}' 不存在",
        )

    # 局部更新
    if body.name is not None:
        entry.name = body.name
    if body.enabled is not None:
        entry.enabled = body.enabled
    if body.config is not None:
        patch = body.config.model_dump(exclude_none=True)
        entry.config.update(patch)

    await store.upsert(entry)

    # 启用态热切换
    if body.enabled is not None:
        await mgr.on_config_change()

    return _entry_to_response(entry)


@router.post("/{channel_id}/start", response_model=ChannelResponse)
async def start_channel(request: Request, channel_id: str):
    """热启动单个渠道（启用配置 + 连接）"""
    store = _get_config_store(request)
    mgr = _get_manager(request)

    entry = store.get_by_id(channel_id)
    if not entry:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"渠道 '{channel_id}' 不存在",
        )

    if not entry.enabled:
        entry.enabled = True
        await store.upsert(entry)

    await mgr.on_config_change()
    return _entry_to_response(entry)


@router.post("/{channel_id}/stop", response_model=ChannelResponse)
async def stop_channel(request: Request, channel_id: str):
    """热停止单个渠道（禁用配置 + 断开）"""
    store = _get_config_store(request)
    mgr = _get_manager(request)

    entry = store.get_by_id(channel_id)
    if not entry:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"渠道 '{channel_id}' 不存在",
        )

    if entry.enabled:
        entry.enabled = False
        await store.upsert(entry)

    await mgr.on_config_change()
    return _entry_to_response(entry)


@router.delete("/{channel_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_channel(request: Request, channel_id: str):
    """删除渠道（从配置文件移除 + 停止运行实例）"""
    store = _get_config_store(request)
    mgr = _get_manager(request)

    entry = store.get_by_id(channel_id)
    if not entry:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"渠道 '{channel_id}' 不存在",
        )

    # 先停止实例
    await mgr._stop_channel(channel_id)
    # 再从配置文件删除
    await store.delete(channel_id)