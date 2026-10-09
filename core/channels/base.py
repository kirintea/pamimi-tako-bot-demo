# -*- coding: utf-8 -*-

"""渠道适配器抽象基类 —  

设计原则：适配器只负责平台协议，内核逻辑不感知渠道；
入站/出站全部经 ChannelManager 与消息总线解耦。新增渠道只需继承本类并在
core/channels/registry.py 注册，不改内核。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from loguru import logger

from core.channels.events import InboundMessage, OutboundMessage


class BaseChannel(ABC):
    """所有渠道适配器的基类"""

    # 子类必须覆盖
    name: str = "base"
    display_name: str = "Base"
    #: 是否支持进度类消息（如工具调用进度）
    send_progress: bool = False
    #: 是否展示思考过程（多数渠道无低强调 UI → 默认不展示）
    show_reasoning: bool = False

    def __init__(self, config: Any, manager: Any) -> None:
        self.config = config
        self.manager = manager
        self._running = False
        self._error: str | None = None
        self.logger = logger.bind(channel=self.name)

    # ------------------------------------------------------------------
    # 生命周期（子类实现）
    # ------------------------------------------------------------------

    @abstractmethod
    async def start(self) -> None:
        """建立平台连接（企微为 WS 长连接）；持续运行直到 stop()"""

    @abstractmethod
    async def stop(self) -> None:
        """断开平台连接，结束运行循环"""

    @abstractmethod
    async def send(self, msg: OutboundMessage) -> None:
        """一次性发送消息（非流式兜底）"""

    # ------------------------------------------------------------------
    # 流式发送（子类可重写；默认回退到 send）
    # ------------------------------------------------------------------

    async def send_delta(
        self,
        chat_id: str,
        delta: str,
        *,
        stream_id: str,
        stream_end: bool = False,
    ) -> None:
        """流式发送一片 delta。

        契约：stream_id 标识同一次回复；stream_end=True 收尾。
        默认实现在收尾时把累积内容一次性 send；企微等原生流式渠道重写本方法。
        """
        if stream_end and not delta:
            return
        await self.send(
            OutboundMessage(
                channel=self.name,
                chat_id=chat_id,
                content=delta,
                stream_id=stream_id,
                stream_end=stream_end,
            )
        )

    async def send_reasoning_delta(
        self,
        chat_id: str,
        delta: str,
        *,
        stream_id: str,
        stream_end: bool = False,
    ) -> None:
        """思考过程 delta（默认 no-op：多数渠道无低强调 UI）"""
        return

    # ------------------------------------------------------------------
    # 鉴权 / 入站入口
    # ------------------------------------------------------------------

    def is_allowed(self, sender_id: str) -> bool:
        """allow_from 白名单校验；空名单 = 全部允许"""
        allow = getattr(self.config, "allow_from", None) or []
        if not allow:
            return True
        return sender_id in allow

    async def handle_inbound(self, msg: InboundMessage) -> None:
        """入站统一入口：鉴权（去重由子类负责）→ 转发内核。

        子类在收到平台消息时调用，msg 已完成平台 body 解析与媒体下载。
        """
        if not self.is_allowed(msg.sender_id):
            self.logger.debug("sender 不在白名单，忽略: {}", msg.sender_id)
            return
        await self.manager.handle_inbound(msg)
