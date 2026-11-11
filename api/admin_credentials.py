"""凭证管理（admin）—— 创建/列表/吊销。viewer root 守卫；明文 key 仅创建回显一次。"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from api.chat import _resolve_viewer_role
from core.credentials import generate_api_key, hash_api_key

router = APIRouter(prefix="/admin/credentials", tags=["admin"])


class CredentialCreate(BaseModel):
    app_id: str = Field(min_length=1, max_length=64)
    channel: str = Field(pattern=r"^[A-Za-z0-9_-]{1,16}$")
    description: str | None = Field(default=None, max_length=255)
    cred_type: str = Field(default="api_key", max_length=16)
    scopes: str = Field(default="chat", max_length=255)
    key: str | None = None            # 自备明文（可选），缺省服务端生成


async def _require_root(request: Request, viewer: str | None) -> None:
    if not viewer:
        raise HTTPException(status_code=403, detail="需要 root 角色")
    if await _resolve_viewer_role(request, viewer) != "root":
        raise HTTPException(status_code=403, detail="需要 root 角色")


def _db(request: Request):
    db = getattr(request.app.state, "database_manager", None)
    if db is None or not getattr(db, "is_initialized", False):
        raise HTTPException(status_code=503, detail="数据库未配置或未初始化")
    return db


@router.post("", status_code=201)
async def create_credential(body: CredentialCreate, request: Request,
                            viewer: str | None = None):
    await _require_root(request, viewer)
    key = body.key or generate_api_key()
    if len(key) < 16:
        raise HTTPException(status_code=422, detail="key 长度至少 16")
    h = hash_api_key(key)
    db = _db(request)
    existing = await db.fetch_named("get_credential_by_hash", h)
    if existing:
        raise HTTPException(status_code=409, detail="key 已存在")
    cred_id = await db.insert_returning_id(
        "insert_credential",
        body.cred_type, h, key[:8], body.app_id, body.channel,
        body.scopes, body.description, db.now(),
    )
    return {"id": cred_id, "app_id": body.app_id, "channel": body.channel,
            "cred_type": body.cred_type, "scopes": body.scopes,
            "key_prefix": key[:8], "key": key}       # 明文仅此一次（D1/P2）


@router.get("")
async def list_credentials(request: Request, viewer: str | None = None):
    await _require_root(request, viewer)
    rows = await _db(request).fetch_named("list_credentials")
    return {"items": [dict(r) for r in rows]}


@router.post("/{cred_id}/revoke")
async def revoke_credential(cred_id: int, request: Request,
                            viewer: str | None = None):
    await _require_root(request, viewer)
    db = _db(request)
    before = await db.fetch_named("get_credential_by_id", cred_id)
    if before is None:
        raise HTTPException(status_code=404, detail="凭证不存在")
    if before["status"] != "revoked":
        await db.execute_named("revoke_credential", cred_id, db.now())
    row = await db.fetch_named("get_credential_by_id", cred_id)
    return dict(row)
