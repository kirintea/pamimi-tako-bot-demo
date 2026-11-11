# -*- coding: utf-8 -*-

"""渠道管理设置 — configs/channel.yaml（行为开关）

与 configs/channels.json（渠道实例清单，ChannelConfigStore 负责）分工：
本模块只管渠道的全局行为开关，当前唯一键：

- ``push_session_id``：新会话创建时是否向渠道聊天输出「本次对话会话ID: …」

加载策略：
- 启动时 ``ChannelSettings.load()`` 读取；文件缺失 / 解析失败回退默认值，
  不阻断启动（与渠道 fail-safe 语义一致）
- 使用点 ``refresh()`` 按 mtime 惰性重载 —— 触发点是低频事件（新会话 /
  空闲轮换），无需 watcher；坏文件只报警一次，修复后 mtime 变化自动重试
- 复用 ``core.config.loader.YamlLoader``：支持 ``${VAR:-default}`` 占位
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any

from loguru import logger

from core.config.loader import YamlLoader

# 与 config_store 同源推导（仓库根/configs）
_DEFAULT_CONFIG_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "configs",
)

CHANNEL_SETTINGS_PATH = os.environ.get(
    "CHANNEL_SETTINGS_PATH",
    os.path.join(_DEFAULT_CONFIG_DIR, "channel.yaml"),
)

_DEFAULT_PUSH_SESSION_ID = True

_TRUE_STRINGS = {"true", "yes", "on", "1"}
_FALSE_STRINGS = {"false", "no", "off", "0"}


def _as_bool(value: Any, *, current: bool) -> bool:
    """push_session_id 宽松归一。

    - 合法布尔/常见字符串 → 归一结果
    - None / 空串（yaml 里写了键但没写值）→ 回退默认 true
    - 非法值（如 "banana"）→ 记 warning 并沿用 current，不因单个错值翻转行为
    """
    if isinstance(value, bool):
        return value
    if value is None or value == "":
        return _DEFAULT_PUSH_SESSION_ID
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        # yaml 的 1/0 解析为 int；bool 之外的数值仅认 1/0，其余非法
        if value == 1:
            return True
        if value == 0:
            return False
        logger.warning("ChannelSettings: push_session_id 非法值 {!r}，沿用 {}", value, current)
        return current
    text = str(value).strip().lower()
    if text in _TRUE_STRINGS:
        return True
    if text in _FALSE_STRINGS:
        return False
    logger.warning("ChannelSettings: push_session_id 非法值 {!r}，沿用 {}", value, current)
    return current


@dataclass
class ChannelSettings:
    """渠道管理设置快照（可注入实例；path=None 表示不跟踪文件）"""

    push_session_id: bool = _DEFAULT_PUSH_SESSION_ID
    #: 配置文件路径；None = 注入实例（测试/显式构造），refresh 不做文件重载
    path: str | None = None
    _mtime: float | None = field(default=None, repr=False, compare=False)

    # ------------------------------------------------------------------
    # 加载 / 热重载
    # ------------------------------------------------------------------

    @classmethod
    def load(cls, path: str | None = None) -> "ChannelSettings":
        """读取配置文件；缺失/失败回退默认值（不抛异常）"""
        settings = cls(path=path or CHANNEL_SETTINGS_PATH)
        settings._reload(force=True)
        return settings

    def refresh(self) -> "ChannelSettings":
        """按 mtime 惰性重载（文件无变化 / 未跟踪文件时原样返回）"""
        self._reload(force=False)
        return self

    def _reload(self, *, force: bool) -> None:
        if self.path is None:
            return
        try:
            mtime = os.path.getmtime(self.path)
        except OSError:
            # 文件暂不存在：保持现值（load 初始即默认值；后建文件时 mtime 为 None 会重读）
            return
        if not force and self._mtime is not None and mtime == self._mtime:
            return  # 未变化：不重复解析
        try:
            raw = YamlLoader.load(self.path)
        except FileNotFoundError:
            return
        except Exception as e:
            # 记 mtime：坏文件只报一次，修复保存后 mtime 变化自动重试（防刷屏）
            self._mtime = mtime
            logger.warning(
                "ChannelSettings: 解析失败，沿用现值 path={} err={}", self.path, e,
            )
            return
        self._mtime = mtime
        if not isinstance(raw, dict):
            logger.warning(
                "ChannelSettings: 配置根节点应为映射，沿用现值 path={}", self.path,
            )
            return
        old = self.push_session_id
        self.push_session_id = _as_bool(
            raw.get("push_session_id"), current=self.push_session_id,
        )
        if self.push_session_id != old or force:
            logger.info(
                "ChannelSettings: 已加载 path={} push_session_id={}",
                self.path, self.push_session_id,
            )
