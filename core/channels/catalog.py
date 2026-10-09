# -*- coding: utf-8 -*-

"""渠道全量目录（静态元数据，仅供前端渲染与运行时校验参考）

包含已知可接入的全部 17 种渠道类型，无论后端是否实现适配器：
- backend_supported: 后端是否已有可运行适配器（以 registry.REGISTRY 为准）
- dependencies: 运行所需 pip 包；name=pip 包名（前端 tooltip 展示），
  import_name=importlib.util.find_spec 探测的模块名（探测依赖可用性用）
- fields: 仅后端已接入类型提供（创建表单字段）；其余为空

仅描述 UI 元数据与运行所需依赖，不含运行逻辑；新增字段时同步前端表单。
"""

from __future__ import annotations

from typing import Any

from core.channels.adapters.wecom.manifest import WECOM_MANIFEST
from core.channels.registry import REGISTRY

# 后端实际已实现适配器的类型（运行时以 registry.REGISTRY 校验为准，此处仅作默认值）
BACKEND_SUPPORTED_TYPES: list[str] = list(REGISTRY.keys())

# 各渠道运行所需依赖：pip 包名 + 实际 import 模块名。
# 缺失不影响应用启动，仅影响该渠道启动；前端据此渲染红绿指示灯。
CHANNEL_DEPENDENCIES: dict[str, list[dict[str, str]]] = {
    "wecom": [
        {"name": "wecom-aibot-sdk-python", "import_name": "wecom_aibot_sdk"},
        {"name": "websockets", "import_name": "websockets"},
    ],
    "feishu": [
        {"name": "requests", "import_name": "requests"},
    ],
    "dingtalk": [
        {"name": "requests", "import_name": "requests"},
    ],
    "discord": [
        {"name": "discord.py", "import_name": "discord"},
    ],
    "telegram": [
        {"name": "python-telegram-bot", "import_name": "telegram"},
    ],
    "slack": [
        {"name": "slack-sdk", "import_name": "slack_sdk"},
    ],
    "msteams": [
        {"name": "botbuilder-core", "import_name": "botbuilder"},
    ],
    "mattermost": [
        {"name": "mattermost-driver", "import_name": "mattermostdriver"},
    ],
    "matrix": [
        {"name": "matrix-nio", "import_name": "nio"},
    ],
    "mochat": [
        {"name": "requests", "import_name": "requests"},
        {"name": "websockets", "import_name": "websockets"},
    ],
    "napcat": [
        {"name": "websockets", "import_name": "websockets"},
        {"name": "aiohttp", "import_name": "aiohttp"},
    ],
    "qq": [
        {"name": "aiocqhttp", "import_name": "aiocqhttp"},
    ],
    "signal": [
        {"name": "requests", "import_name": "requests"},
    ],
    "email": [
        {"name": "aiosmtplib", "import_name": "aiosmtplib"},
        {"name": "aioimaplib", "import_name": "aioimaplib"},
    ],
    "whatsapp": [
        {"name": "requests", "import_name": "requests"},
    ],
    "wechat": [
        {"name": "wechaty", "import_name": "wechaty"},
    ],
    "websocket": [
        {"name": "websockets", "import_name": "websockets"},
    ],
}

# 各渠道展示元数据（display_name / description / doc_url）
CHANNEL_META: dict[str, dict[str, Any]] = {
    "wecom": {
        "display_name": "企业微信 AI 机器人",
        "description": (
            "通过 WebSocket 长连接接入企业微信 AI 机器人，无需公网 IP 或回调地址，"
            "原生支持流式回复。"
        ),
        "doc_url": "https://developer.work.weixin.qq.com/document/path/99999",
    },
    "feishu": {
        "display_name": "飞书",
        "description": "接入飞书机器人，支持群聊与单聊中的消息收发与命令。",
        "doc_url": "",
    },
    "dingtalk": {
        "display_name": "钉钉",
        "description": "接入钉钉机器人，支持群聊与单聊中的消息收发与命令。",
        "doc_url": "",
    },
    "discord": {
        "display_name": "Discord",
        "description": "接入 Discord 频道，让智能体在服务器里直接对话。",
        "doc_url": "",
    },
    "telegram": {
        "display_name": "Telegram",
        "description": "接入 Telegram Bot，支持私聊与群组消息。",
        "doc_url": "",
    },
    "slack": {
        "display_name": "Slack",
        "description": "接入 Slack 工作区，让智能体在频道中工作。",
        "doc_url": "",
    },
    "msteams": {
        "display_name": "Microsoft Teams",
        "description": "接入 Microsoft Teams，支持团队频道消息。",
        "doc_url": "",
    },
    "mattermost": {
        "display_name": "Mattermost",
        "description": "接入 Mattermost，自托管团队沟通平台。",
        "doc_url": "",
    },
    "matrix": {
        "display_name": "Matrix",
        "description": "接入 Matrix 联邦网络（如 Element），去中心化聊天。",
        "doc_url": "",
    },
    "mochat": {
        "display_name": "墨匣 Mochat",
        "description": "接入 Mochat 微信生态运营平台（基于微信个人号/企微）。",
        "doc_url": "",
    },
    "napcat": {
        "display_name": "NapCat（QQ）",
        "description": "接入 NapCat 框架，连接 QQ 账号收发消息。",
        "doc_url": "",
    },
    "qq": {
        "display_name": "QQ",
        "description": "接入 QQ 频道/群，让智能体在 QQ 中对话。",
        "doc_url": "",
    },
    "signal": {
        "display_name": "Signal",
        "description": "接入 Signal 私聊（经由 signal-cli REST API）。",
        "doc_url": "",
    },
    "email": {
        "display_name": "邮件 Email",
        "description": "通过 IMAP/SMTP 接入邮箱，收发邮件。",
        "doc_url": "",
    },
    "whatsapp": {
        "display_name": "WhatsApp",
        "description": "接入 WhatsApp Business API 收发消息。",
        "doc_url": "",
    },
    "wechat": {
        "display_name": "微信",
        "description": "接入微信（基于 Wechaty 等方案），支持私聊与群聊。",
        "doc_url": "",
    },
    "websocket": {
        "display_name": "WebSocket",
        "description": "通过原生 WebSocket 长连接接入自定义客户端。",
        "doc_url": "",
    },
}

# 目录顺序（同时作为前端下拉顺序）
CHANNEL_ORDER: list[str] = [
    "wecom",
    "feishu",
    "dingtalk",
    "discord",
    "telegram",
    "slack",
    "msteams",
    "mattermost",
    "matrix",
    "mochat",
    "napcat",
    "qq",
    "signal",
    "email",
    "whatsapp",
    "wechat",
    "websocket",
]


def get_channel_catalog() -> list[dict[str, Any]]:
    """返回全量渠道目录（静态部分，不含运行时依赖可用性）。

    每个条目：type / name / display_name / description / doc_url /
    backend_supported / dependencies[{name, import_name}] / fields。
    运行时依赖可用性由 api/channels.py 的 _enrich_manifest 叠加。
    """
    catalog: list[dict[str, Any]] = []
    for ch_type in CHANNEL_ORDER:
        meta = CHANNEL_META.get(ch_type, {})
        entry: dict[str, Any] = {
            "type": ch_type,
            "name": ch_type,
            "display_name": meta.get("display_name", ch_type),
            "description": meta.get("description", ""),
            "doc_url": meta.get("doc_url", ""),
            "backend_supported": ch_type in REGISTRY,
            "dependencies": list(CHANNEL_DEPENDENCIES.get(ch_type, [])),
            # fields 仅后端已接入类型提供
            "fields": WECOM_MANIFEST.get("fields", []) if ch_type == "wecom" else [],
        }
        catalog.append(entry)
    return catalog
