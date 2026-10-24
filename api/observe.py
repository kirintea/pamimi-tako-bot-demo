# -*- coding: utf-8 -*-

"""对话观测台 BFF —— root 专属，只读查询

端点：
- GET /admin/observe/sessions/{user_id}/{session_id}/turns — 轮次 + 节点树

会话列表**不走此路由**：复用既有 GET /admin/sessions（字段已含 channel /
display_name / message_count），搜索前端过滤。

只读代理：不写任何数据。节点组装在 core/observe.build_turns。

信任模型（继承自 api/chat.py 的 /admin/sessions，非本端点引入）：
``viewer`` 为查询参数，表示调用者身份（demo 阶段用查询参数表达调用者；
生产环境应改由认证 token 解析）。本端点返回原始消息正文，比会话列表
更敏感，故留档此缺口。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from loguru import logger

from api.chat import _resolve_viewer_role
from core.observe import build_turns

router = APIRouter(prefix="/admin/observe", tags=["observe"])

# 取消息行窗口下限 —— 每轮多行（user/thinking/tool/text），实际窗口随
# limit 放大（约 8 行/轮，3+k 行留余量）；默认 limit=50 时仍是 500
_MSG_FETCH_LIMIT = 500
# 默认返回最近 N 轮
_DEFAULT_TURN_LIMIT = 50
_MAX_TURN_LIMIT = 200


@router.get("/sessions/{user_id}/{session_id}/turns")
async def observe_turns(
    request: Request,
    user_id: str,
    session_id: str,
    viewer: str | None = None,
    limit: int = _DEFAULT_TURN_LIMIT,
) -> dict[str, Any]:
    """会话轮次 + 节点树（root 专属）

    Args:
        user_id: 会话所属用户
        session_id: 会话 ID
        viewer: 发起查看的用户（查询参数，同现有约定；缺省无角色 → 403）
        limit: 返回最近 N 轮

    Returns:
        {"user_id", "session_id", "channel", "turns": [...]}
    """
    role = await _resolve_viewer_role(request, viewer)
    if role != "root":
        raise HTTPException(status_code=403, detail="需要 root 角色")

    db = getattr(request.app.state, "database_manager", None)
    if not db or not db.is_initialized:
        raise HTTPException(status_code=503, detail="数据库未配置")

    limit = max(1, min(limit, _MAX_TURN_LIMIT))

    history = await db.get_conversation_history(
        user_id,
        session_id,
        limit=max(_MSG_FETCH_LIMIT, limit * 8),
        # 中间链路（thinking / tool_call）落库即被「轮内压缩」软删成 archived。
        # 观测台存在的意义就是看这段过程，必须放开 status 过滤。
        include_archived=True,
    )
    turns = build_turns(history.get("messages") or [])
    if len(turns) > limit:
        turns = turns[-limit:]

    # channel 用于顶栏/卡片展示（conversations 表任取一行即可）
    try:
        channel = await db.fetchval(
            'SELECT "channel" FROM conversations '
            'WHERE "user_id" = $1 AND "session_id" = $2 LIMIT 1',
            user_id,
            session_id,
        )
    except Exception as e:  # noqa: BLE001 —— channel 缺失不阻塞主数据
        logger.warning("查询会话 channel 失败，降级为空串: {}", e)
        channel = None

    return {
        "user_id": user_id,
        "session_id": session_id,
        "channel": channel or "",
        "turns": turns,
    }
