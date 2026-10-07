# -*- coding: utf-8 -*-

"""渠道适配器注册表

将渠道类型字符串（"wecom" / "feishu" / "email" / "web"）映射到适配器类。
新增渠道：实现 BaseChannel 子类后在此注册即可，内核无需改动。
"""

from __future__ import annotations

from core.channels.adapters.wecom.runtime import WecomChannel
from core.channels.base import BaseChannel


# 渠道类型 → 适配器类
REGISTRY: dict[str, type[BaseChannel]] = {
    "wecom": WecomChannel,
    # "feishu": FeishuChannel,   # Phase 7 后续补齐
    # "email": EmailChannel,
    # "web": WebChannel,         # 复用现有 /ws/chat（可选封装）
}


def register(channel_cls: type[BaseChannel]) -> type[BaseChannel]:
    """装饰器：将适配器注册到 REGISTRY（以 cls.name 为键）"""
    REGISTRY[channel_cls.name] = channel_cls
    return channel_cls


def get_channel_class(channel_type: str) -> type[BaseChannel] | None:
    return REGISTRY.get(channel_type)
