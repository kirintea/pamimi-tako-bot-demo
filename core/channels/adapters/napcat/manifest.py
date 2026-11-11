# -*- coding: utf-8 -*-
"""NapCat 渠道清单（manifest）—— 供前端渲染添加表单 / 提供文档链接

仅描述 UI 元数据，不含运行逻辑；新增字段时同步更新前端表单与
config.py 的 coerce 归一。字段顺序即表单展示顺序。secret=True 的字段在
列表/传输/落库响应中脱敏（见 api/channels.py _entry_to_response）。

字段类型受 WebUI 通用表单控件限制（仅 text/password/textarea/list）：
enum 用 text + placeholder 说明取值，bool 用 text（""/false/0=否），
JSON 用 text（config.py 宽松解析，非法回退默认）。
"""

from __future__ import annotations

from typing import Any


NAPCAT_MANIFEST: dict[str, Any] = {
    "type": "napcat",
    "name": "napcat",
    "display_name": "NapCat (QQ)",
    "description": (
        "通过 OneBot v11 正向 WebSocket 接入 NapCat（个人 QQ 号桥），"
        "支持私聊与群聊，无需公网 IP。"
    ),
    "doc_url": "https://napneko.github.io/",
    # 运行所需依赖（仅启动该渠道时按需确认；缺失不影响应用启动，
    # 前端据此渲染红绿指示灯。aiohttp 为本项目根依赖，正常恒绿）
    "dependencies": ["aiohttp"],
    "fields": [
        {
            "key": "ws_url",
            "label": "WebSocket 地址",
            "type": "text",
            "required": True,
            "placeholder": "ws://127.0.0.1:3001（NapCat 网络配置 → Websocket 服务器）",
            "secret": False,
        },
        {
            "key": "access_token",
            "label": "Access Token",
            "type": "password",
            "required": False,
            "placeholder": "NapCat WebSocket Token（留空 = 不鉴权）",
            "secret": True,
        },
        {
            "key": "allow_from",
            "label": "允许发送者（白名单）",
            "type": "list",
            "required": True,
            "placeholder": "QQ 号列表，逗号/换行分隔；* = 全部；留空 = 拒绝所有（安全默认）",
            "secret": False,
        },
        {
            "key": "group_policy",
            "label": "群聊回复策略",
            "type": "text",
            "required": False,
            "placeholder": "mention（默认，仅@我/回复我）/ open（全部回复）/ 0~1 概率，如 0.2",
            "secret": False,
        },
        {
            "key": "group_policy_overrides",
            "label": "按群覆盖策略",
            "type": "text",
            "required": False,
            "placeholder": 'JSON，如 {"123456":"open","789012":0.5}；未列出的群用全局策略',
            "secret": False,
        },
        {
            "key": "welcome_new_members",
            "label": "新成员欢迎",
            "type": "text",
            "required": False,
            "placeholder": "true（默认）/ false — 新成员进群时通知 Agent 迎新",
            "secret": False,
        },
        {
            "key": "max_image_bytes",
            "label": "入站图片上限（字节）",
            "type": "text",
            "required": False,
            "placeholder": "默认 20971520（20MB），超限图片丢弃",
            "secret": False,
        },
    ],
}
