# -*- coding: utf-8 -*-
"""WeCom AI Bot 渠道配置模型

与 ChannelEntry（core.channels.config_store）字段对齐（bot_id / secret / allow_from /
welcome_message）。适配器构造时把通用配置收敛为本模型，避免与存储层耦合。
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class WecomSettings(BaseModel):
    """WeCom 渠道运行所需的最小配置集合"""

    bot_id: str = ""
    secret: str = ""
    allow_from: list[str] = Field(default_factory=list)
    welcome_message: str = ""

    @classmethod
    def coerce(cls, config) -> "WecomSettings":
        """从 ChannelEntry / dict / 带属性对象 收敛为 WecomSettings"""
        if isinstance(config, WecomSettings):
            return config
        if isinstance(config, dict):
            return cls(
                bot_id=config.get("bot_id", "") or "",
                secret=config.get("secret", "") or "",
                allow_from=config.get("allow_from") or [],
                welcome_message=config.get("welcome_message", "") or "",
            )
        # 兼容 ChannelEntry 等带属性的对象
        return cls(
            bot_id=getattr(config, "bot_id", "") or "",
            secret=getattr(config, "secret", "") or "",
            allow_from=getattr(config, "allow_from", None) or [],
            welcome_message=getattr(config, "welcome_message", "") or "",
        )
