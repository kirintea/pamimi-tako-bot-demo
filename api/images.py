# -*- coding: utf-8 -*-

"""图片上传 API

端点：
- POST /images/upload        — 请求图片上传凭证（presigned URL 或代理接口）
- PUT  /images/upload/{key}  — 本地存储模式的代理上传
- GET  /images/{key}/url     — 获取图片访问 URL（历史消息加载用）
- GET  /images/files/{key}   — 本地存储模式的静态文件访问
"""

from __future__ import annotations

import os

from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

router = APIRouter()


# ============================================================
# 请求 / 响应 Schema
# ============================================================


class UploadRequest(BaseModel):
    """上传凭证请求"""
    filename: str = Field(description="文件名")
    content_type: str = Field(description="MIME 类型")
    size: int = Field(description="文件大小（字节）")


class UploadResponse(BaseModel):
    """上传凭证响应"""
    upload_url: str = Field(description="上传地址（presigned URL 或代理接口）")
    key: str = Field(description="对象 key")
    expires_in: int = Field(description="有效期（秒）")
    headers: dict[str, str] | None = Field(default=None, description="上传时的额外 headers")


# ============================================================
# 端点
# ============================================================


@router.post("/images/upload", response_model=UploadResponse)
async def request_upload(request: Request, body: UploadRequest):
    """请求图片上传凭证

    SaaS 模式：返回 presigned PUT URL，前端直传存储服务。
    本地模式：返回后端代理 URL，前端 POST 文件到后端。
    """
    storage = request.app.state.object_storage
    image_config = request.app.state.config.image

    # 校验
    if not image_config.enabled:
        raise HTTPException(403, "图片输入未启用")
    if body.content_type not in image_config.allowed_types:
        raise HTTPException(415, f"不支持的文件类型: {body.content_type}")
    if body.size > image_config.max_size_mb * 1024 * 1024:
        raise HTTPException(413, f"文件大小超过限制: {image_config.max_size_mb}MB")

    user_id = request.query_params.get("user_id", "anonymous")
    prefix = request.app.state.config.object_storage.prefix

    cred = storage.generate_upload_credential(
        user_id,
        body.content_type,
        body.size,
        prefix=prefix,
    )

    return UploadResponse(
        upload_url=cred.upload_url,
        key=cred.key,
        expires_in=cred.expires_in,
        headers=cred.headers,
    )


@router.get("/images/{key:path}/url")
async def get_image_access_url(request: Request, key: str):
    """获取图片访问 URL（历史消息加载用）"""
    storage = request.app.state.object_storage
    ttl = request.app.state.config.image.presigned_url_ttl
    access = storage.generate_access_url(key, ttl=ttl)
    return {"url": access.url, "expires_in": access.expires_in}


@router.put("/images/upload/{key:path}")
async def proxy_upload(request: Request, key: str, file: UploadFile = File(...)):
    """本地存储模式的代理上传（前端 POST 文件到后端写磁盘）

    仅 backend=local 时使用。S3/OSS 模式下前端直接 PUT 到 presigned URL。
    """
    storage = request.app.state.object_storage
    data = await file.read()
    content_type = file.content_type or "application/octet-stream"
    await storage.put_object(key, data, content_type)
    return {"key": key, "size": len(data)}


@router.get("/images/files/{key:path}")
async def serve_local_image(request: Request, key: str):
    """本地存储模式的静态文件访问

    仅 backend=local 时使用。S3/OSS 模式下前端直接访问 presigned GET URL。
    """
    storage = request.app.state.object_storage
    base_dir = getattr(storage, "_base_dir", None)
    if base_dir is None:
        raise HTTPException(404, "本地存储未配置")

    file_path = os.path.join(base_dir, key)
    # 路径穿越防护
    real_root = os.path.realpath(base_dir)
    real_file = os.path.realpath(file_path)
    if not real_file.startswith(real_root + os.sep):
        raise HTTPException(403, "非法路径")
    if not os.path.exists(real_file) or not os.path.isfile(real_file):
        raise HTTPException(404, "文件不存在")

    return FileResponse(real_file)
