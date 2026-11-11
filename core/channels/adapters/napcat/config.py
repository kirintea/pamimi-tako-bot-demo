# -*- coding: utf-8 -*-
"""NapCat 渠道配置模型

与 ChannelEntry（core.channels.config_store）字段对齐（ws_url / access_token /
allow_from / group_policy / group_policy_overrides / welcome_new_members /
max_image_bytes）。适配器构造时把通用配置收敛为本模型，避免与存储层耦合。

宽松解析（计划 D5）：WebUI 表单对非 list 字段一律提交**字符串**，此处负责
字符串 → 类型归一；非法值回退默认并记 warning，不抛异常（避免渠道因单个
字段写错而完全无法启动——ws_url / allow_from 除外，它们是启动硬校验项）。
"""

from __future__ import annotations

import json
from typing import Any

from loguru import logger
from pydantic import BaseModel, Field

DEFAULT_GROUP_POLICY = "mention"
DEFAULT_MAX_IMAGE_BYTES = 20 * 1024 * 1024
_TRUE_STRINGS = {"true", "1", "yes", "on"}
_FALSE_STRINGS = {"false", "0", "no", "off"}


def _coerce_policy(value: Any, *, field: str) -> str | float:
    """群策略归一：`mention` / `open` / [0,1] 浮点；非法回退 mention。"""
    if value is None or value == "":
        return DEFAULT_GROUP_POLICY
    if isinstance(value, bool):  # bool 是 int 子类，必须先拦（True ≡ 1.0 会被误读成"全回"）
        logger.warning("NapCat {}: 布尔值非法，回退 {}", field, DEFAULT_GROUP_POLICY)
        return DEFAULT_GROUP_POLICY
    if isinstance(value, (int, float)):
        f = float(value)
        return f if 0.0 <= f <= 1.0 else _policy_out_of_range(field)
    text = str(value).strip()
    if text in ("mention", "open"):
        return text
    try:
        f = float(text)
    except ValueError:
        logger.warning("NapCat {}: 非法值 {!r}，回退 {}", field, value, DEFAULT_GROUP_POLICY)
        return DEFAULT_GROUP_POLICY
    if not 0.0 <= f <= 1.0:
        return _policy_out_of_range(field)
    return f


def _policy_out_of_range(field: str) -> str:
    logger.warning("NapCat {}: 概率超出 [0,1]，回退 {}", field, DEFAULT_GROUP_POLICY)
    return DEFAULT_GROUP_POLICY


def _coerce_overrides(value: Any) -> dict[str, str | float]:
    """按群覆盖归一：JSON 对象字符串或 dict；逐值过 _coerce_policy，非法项丢弃。"""
    raw: Any = value
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return {}
        try:
            raw = json.loads(text)
        except (json.JSONDecodeError, ValueError):
            logger.warning("NapCat group_policy_overrides: 非法 JSON {!r}，回退 {{}}", value)
            return {}
    if not isinstance(raw, dict):
        # 无参调用时 loguru 不做 format，单花括号按原样输出
        logger.warning("NapCat group_policy_overrides: 类型非法，回退 {}")
        return {}
    out: dict[str, str | float] = {}
    for key, val in raw.items():
        if key is None or str(key).strip() == "":
            continue
        policy = _coerce_policy(val, field=f"group_policy_overrides[{key}]")
        # 非法值被回退成 mention——原意丢失，宁可丢弃该项（回退全局策略）
        if policy == DEFAULT_GROUP_POLICY and val not in (None, "", "mention", DEFAULT_GROUP_POLICY):
            continue
        out[str(key).strip()] = policy
    return out


def _coerce_allow_from(value: Any) -> list[str]:
    """白名单归一：list 直取；字符串按逗号/换行切分（手编 channels.json 友好）。"""
    if value is None:
        return []
    if isinstance(value, str):
        parts = value.replace("\n", ",").split(",")
        return [p.strip() for p in parts if p.strip()]
    if isinstance(value, (list, tuple)):
        out: list[str] = []
        for item in value:
            text = str(item).strip()
            if text:
                out.append(text)
        return out
    logger.warning("NapCat allow_from: 类型非法（{}），按空名单处理", type(value).__name__)
    return []


def _coerce_int(value: Any, *, default: int, minimum: int, field: str) -> int:
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        logger.warning("NapCat {}: 布尔值非法，回退 {}", field, default)
        return default
    try:
        n = int(value)
    except (TypeError, ValueError):
        logger.warning("NapCat {}: 非法值 {!r}，回退 {}", field, value, default)
        return default
    if n < minimum:
        logger.warning("NapCat {}: {} 小于下限 {}，回退 {}", field, n, minimum, default)
        return default
    return n


def _coerce_bool(value: Any, *, default: bool, field: str) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text == "":
        return default
    if text in _FALSE_STRINGS:
        return False
    if text in _TRUE_STRINGS:
        return True
    logger.warning("NapCat {}: 非法值 {!r}，按 {} 处理", field, value, default)
    return default


class NapcatSettings(BaseModel):
    """NapCat 渠道运行所需的配置集合"""

    ws_url: str = ""
    access_token: str = ""
    allow_from: list[str] = Field(default_factory=list)
    # "mention" | "open" | float∈[0,1]（0.0≡mention，1.0≡open）
    group_policy: str | float = DEFAULT_GROUP_POLICY
    group_policy_overrides: dict[str, str | float] = Field(default_factory=dict)
    welcome_new_members: bool = True
    max_image_bytes: int = DEFAULT_MAX_IMAGE_BYTES

    @classmethod
    def coerce(cls, config: Any) -> "NapcatSettings":
        """从 ChannelEntry / dict / 带属性对象 收敛为 NapcatSettings"""
        if isinstance(config, cls):
            return config
        if isinstance(config, dict):
            raw: dict[str, Any] = config
        elif config is not None:
            raw = {
                key: getattr(config, key, None)
                for key in (
                    "ws_url",
                    "access_token",
                    "allow_from",
                    "group_policy",
                    "group_policy_overrides",
                    "welcome_new_members",
                    "max_image_bytes",
                )
            }
        else:
            raw = {}

        return cls(
            ws_url=str(raw.get("ws_url") or "").strip(),
            access_token=str(raw.get("access_token") or "").strip(),
            allow_from=_coerce_allow_from(raw.get("allow_from")),
            group_policy=_coerce_policy(raw.get("group_policy"), field="group_policy"),
            group_policy_overrides=_coerce_overrides(raw.get("group_policy_overrides")),
            welcome_new_members=_coerce_bool(
                raw.get("welcome_new_members"), default=True, field="welcome_new_members"
            ),
            max_image_bytes=_coerce_int(
                raw.get("max_image_bytes"),
                default=DEFAULT_MAX_IMAGE_BYTES,
                minimum=1,
                field="max_image_bytes",
            ),
        )
