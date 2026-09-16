# -*- coding: utf-8 -*-

"""多模态消息构建工具

提供 ContentPart 类型定义和 _build_user_msg 函数，
供 api/chat.py、api/ws_chat.py、core/chat_service.py 共用。
"""

from __future__ import annotations

import base64
import json
from typing import Literal, Union

from pydantic import BaseModel, Field

from agentscope.message import UserMsg
from agentscope.message._block import Base64Source, DataBlock, TextBlock

from core.object_storage.base import ObjectStorageBase


# ============================================================
# 多模态内容类型
# ============================================================


class TextContent(BaseModel):
    """文本内容"""
    type: Literal["text"] = "text"
    text: str


class ImageContent(BaseModel):
    """图片内容"""
    type: Literal["image"] = "image"
    key: str = Field(description="对象存储 key（由 /images/upload 返回）")


ContentPart = Union[TextContent, ImageContent]


# ============================================================
# 消息构建
# ============================================================

# 扩展名 → MIME 类型映射
_EXT_MEDIA_MAP: dict[str, str] = {
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "gif": "image/gif",
    "webp": "image/webp",
}


async def build_user_msg(
    message: str | list[ContentPart],
    storage: ObjectStorageBase,
    name: str = "user",
) -> UserMsg:
    """将 API 请求转换为 AgentScope UserMsg

    纯文本 → 直接传 str
    多模态 → 从对象存储读取图片，构建 list[TextBlock | DataBlock]
    """
    if isinstance(message, str):
        return UserMsg(name=name, content=message)

    blocks: list = []
    for part in message:
        if isinstance(part, TextContent):
            blocks.append(TextBlock(text=part.text))
        elif isinstance(part, ImageContent):
            img_bytes = await storage.get_object_bytes(part.key)
            ext = part.key.rsplit(".", 1)[-1].lower()
            media_type = _EXT_MEDIA_MAP.get(ext, "image/png")
            blocks.append(
                DataBlock(
                    source=Base64Source(
                        data=base64.b64encode(img_bytes).decode(),
                        media_type=media_type,
                    )
                )
            )

    if not blocks:
        blocks.append(TextBlock(text=""))

    return UserMsg(name=name, content=blocks)


def serialize_message_for_storage(message: str | list[ContentPart]) -> str:
    """将多模态消息序列化为可存储的字符串

    纯文本 → 原样返回
    多模态 → JSON 序列化（保留 type/text/key 结构，前端可还原）
    """
    if isinstance(message, str):
        return message
    return json.dumps(
        [part.model_dump() for part in message],
        ensure_ascii=False,
    )
