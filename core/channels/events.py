# -*- coding: utf-8 -*-

"""渠道消息事件 — InboundMessage / OutboundMessage

轻量数据结构，解耦「平台协议」与「内核」。适配器入站产出 InboundMessage，
ChannelManager 据此触发内核；内核产出 OutboundMessage，由适配器发回平台。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class MessageType(str, Enum):
    """入站消息类型（用于前端/统计展示）"""

    TEXT = "text"
    IMAGE = "image"
    VOICE = "voice"
    FILE = "file"
    MIXED = "mixed"


@dataclass
class InboundMessage:
    """平台 → 内核 的入站消息"""

    channel: str                      # 渠道类型，如 "wecom"
    channel_id: str                   # 该渠道在 DB 中的记录 id（生命周期/状态归属）
    user_id: str                      # 平台侧 user_id（渠道记录的归属用户）
    chat_id: str                      # 平台会话 id（企微 chatid / 飞书 chat_id）
    sender_id: str                    # 发送者（企微 from.userid，默认密文）
    content: str                      # 文本化后的消息内容
    media: list[str] | None = None    # 已下载到本地的媒体文件路径（可选）
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class OutboundMessage:
    """内核 → 平台 的出站消息"""

    channel: str
    chat_id: str
    content: str
    media: list[str] | None = None
    stream_id: str | None = None
    stream_end: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)
