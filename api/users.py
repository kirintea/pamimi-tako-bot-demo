# -*- coding: utf-8 -*-

"""用户信息接口 — 第三方上报/更新用户资料（父调研 §10.1）

端点：
- POST /users — 按 user_id 幂等 upsert（users 表主键冲突更新）

入参名 ``user_name`` 在端点内映射为服务层 ``display_name``（后端语义不变）；
响应回显字段名保持 ``display_name``。``extra`` 按 D1 决策仅日志记录、暂不入库。

认证：不在 server.py 白名单内 → AUTH 开启时受 X-API-Key 中间件保护。
限流：仅 /chat/stream、/chat/ 限流，本端点不限流（缺口 4 另行处理）。
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from loguru import logger
from pydantic import BaseModel, Field, field_validator

from core.credentials import resolve_channel
from core.validators import coerce_id_strict, validate_channel

router = APIRouter()


class UserUpsertRequest(BaseModel):
    """用户信息上报请求 — user_id 必填，其余可选可补充"""

    user_id: str = Field(
        description="用户标识（^[A-Za-z0-9_][A-Za-z0-9_-]{0,63}$）",
    )
    channel: str = Field(
        default="web",
        description="来源渠道（1-16 位 [A-Za-z0-9_-]，落 users.channel）",
    )
    user_name: str | None = Field(
        default=None,
        description=(
            "用户名称（可选；strip 后 ≤128 字符，超长截断）；"
            "入参名 user_name，端点内映射为服务层 display_name（后端语义）"
        ),
    )
    extra: dict | None = Field(
        default=None,
        description=(
            "任意补充 JSON 对象（多个可补充字段统一放此）；"
            "D1 决策：仅日志记录，暂不入库"
        ),
    )

    @field_validator("channel")
    @classmethod
    def _check_channel(cls, v: str) -> str:
        return validate_channel(v)


@router.post("/users")
async def upsert_user(request: Request, body: UserUpsertRequest) -> dict:
    """创建/更新用户信息（按 user_id 幂等 upsert）

    - 400: user_id 格式非法
    - 422: 缺 user_id / channel 非法 / extra 非对象（pydantic）
    - 503: 数据库未配置 或 user_service 未装配
    - 入参 ``user_name`` 映射为服务层 ``display_name``（后端语义；
      响应回显字段名保持 ``display_name``）
    """
    try:
        user_id = coerce_id_strict(body.user_id, "user_id")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    db = getattr(request.app.state, "database_manager", None)
    if db is None or not db.is_initialized:
        # 写端点不静默降级：DB 不可用时明确 503（与 GET /sessions/{user_id} 一致）
        raise HTTPException(status_code=503, detail="数据库未配置")

    user_service = getattr(request.app.state, "user_service", None)
    if user_service is None:
        raise HTTPException(status_code=503, detail="用户服务未装配")

    # D2：凭证存在时强制覆盖 channel（校验已对 body 值执行，覆盖值由凭证保证合法）
    channel = resolve_channel(request, body.channel, logger)

    # D1 决策：extra 仅日志记录、暂不入库（users 表无扩展列，未来按需再评估）
    if body.extra is not None:
        logger.info(
            "POST /users extra(仅日志不入库): user_id={} channel={} extra={}",
            user_id, channel, body.extra,
        )

    # 入参名 user_name → 服务层参数 display_name（后端语义不变）
    record = await user_service.get_or_create_user(
        user_id,
        display_name=body.user_name,
        channel=channel,
    )
    return {**record, "extra": body.extra}
