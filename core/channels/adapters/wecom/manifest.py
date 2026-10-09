# -*- coding: utf-8 -*-
"""WeCom 渠道清单（manifest）—— 供前端渲染添加表单 / 提供文档链接

仅描述 UI 元数据，不含运行逻辑；新增字段时同步更新前端表单。字段顺序即表单
展示顺序。secret=True 的字段在列表中脱敏、在传输与落库时加密。
"""

from __future__ import annotations

from typing import Any


WECOM_MANIFEST: dict[str, Any] = {
    "type": "wecom",
    "name": "wecom",
    "display_name": "企业微信 AI 机器人",
    "description": (
        "通过 WebSocket 长连接接入企业微信 AI 机器人，无需公网 IP 或回调地址，"
        "原生支持流式回复。"
    ),
    "doc_url": "https://developer.work.weixin.qq.com/document/path/99999",
    # 运行所需的可选依赖（仅启动该渠道时按需懒导入）；缺失不影响应用启动，
    # 前端据此渲染红绿指示灯。import 名即 find_spec 探测的模块名。
    "dependencies": ["wecom_aibot_sdk", "websockets"],
    "fields": [
        {
            "key": "bot_id",
            "label": "Bot ID",
            "type": "text",
            "required": True,
            "placeholder": "企业微信 AI 机器人 Bot ID",
            "secret": False,
        },
        {
            "key": "secret",
            "label": "Secret",
            "type": "password",
            "required": True,
            "placeholder": "企业微信 AI 机器人 Secret",
            "secret": True,
        },
        {
            "key": "allow_from",
            "label": "允许发送者（白名单）",
            "type": "list",
            "required": False,
            "placeholder": "留空 = 全部允许；填写企业微信 userid 列表",
            "secret": False,
        },
        {
            "key": "welcome_message",
            "label": "欢迎语",
            "type": "textarea",
            "required": False,
            "placeholder": "用户首次进入会话时下发",
            "secret": False,
        },
    ],
}
