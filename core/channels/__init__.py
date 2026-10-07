# -*- coding: utf-8 -*-
"""渠道抽象层

解耦「平台协议」与「内核」：
- events.py        InboundMessage / OutboundMessage 数据结构
- base.py          BaseChannel 抽象基类（适配器契约）
- registry.py      渠道类型 → 适配器类 注册表
- manager.py       ChannelManager：启停 / 出站分发 / 热切换 / 状态
- adapters/        各平台适配器实现（wecom 等）

内核只通过 ChannelManager 与总线交互，新增渠道无需改动内核。
"""
