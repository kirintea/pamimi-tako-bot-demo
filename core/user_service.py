# -*- coding: utf-8 -*-

"""用户服务 — 身份规范化 + 角色（root / normal）解析 + 懒注册

设计要点：
- ``root`` 身份由配置 + 环境变量**权威**决定（创建时配置，不可经 DB 降级）：
  ``AgentConfig.root_user_ids`` + 环境变量 ``ROOT_USER_IDS``（逗号分隔）。
- 普通用户首次访问时懒 upsert 一行到 ``users`` 表（role='normal'，
  记录 display_name / last_active）。
- 数据库未配置（DatabaseManager 未初始化）时，服务优雅降级：角色完全由
  config / env 推导（命中 root 集合即 root，否则 normal），不抛错、不阻断。

本服务是 SessionManager / AgentFactory / API 路由统一的角色入口。
"""

from __future__ import annotations

import os

from loguru import logger

from core.config.schemas import AppConfig
from core.database import DatabaseManager
from core.validators import coerce_id, is_valid_id


class UserService:
    """用户身份与角色服务"""

    def __init__(self, config: AppConfig, db: DatabaseManager | None = None) -> None:
        self._config = config
        self._db = db
        self._root_ids = self._resolve_root_ids(config)

    # ------------------------------------------------------------------
    # root 识别（配置 + 环境变量权威）
    # ------------------------------------------------------------------

    @staticmethod
    def _resolve_root_ids(config: AppConfig) -> set[str]:
        """合并 ``AgentConfig.root_user_ids`` 与环境变量 ``ROOT_USER_IDS``"""
        ids: set[str] = set(getattr(config.agent, "root_user_ids", []) or [])
        env_val = os.environ.get("ROOT_USER_IDS", "")
        if env_val:
            ids |= {x.strip() for x in env_val.split(",") if x.strip()}
        # 仅保留合法标识符，避免脏配置污染匹配
        return {coerce_id(x) for x in ids if is_valid_id(x)}

    def is_configured_root(self, user_id: object) -> bool:
        """user_id 是否为预置 root（配置/环境变量权威）"""
        return coerce_id(user_id) in self._root_ids

    # ------------------------------------------------------------------
    # 角色解析
    # ------------------------------------------------------------------

    async def get_role(self, user_id: object) -> str:
        """返回用户角色：``'root'`` 或 ``'normal'``

        优先级：
        1. 命中预置 root 集合（config/env）→ ``'root'``
        2. 数据库 users 表已登记 role → 取表值
        3. 其他 → ``'normal'``
        """
        uid = coerce_id(user_id)
        if self.is_configured_root(uid):
            return "root"
        if self._db is not None and self._db.is_initialized:
            try:
                role = await self._db.get_user_role(uid)
                if role in ("root", "normal"):
                    return role
            except Exception as e:  # noqa: BLE001
                logger.warning("UserService.get_role 查库失败（降级 normal）: {}", e)
        return "normal"

    async def get_or_create_user(
        self,
        user_id: object,
        display_name: str | None = None,
    ) -> dict:
        """懒注册/更新用户，返回用户记录字典

        Args:
            user_id: 外部用户标识（经 coerce_id 规范化）
            display_name: 可选展示名

        Returns:
            ``{"user_id": ..., "role": ..., "is_root": bool}``
        """
        uid = coerce_id(user_id)
        role = "root" if self.is_configured_root(uid) else "normal"
        is_root = role == "root"

        if self._db is not None and self._db.is_initialized:
            try:
                await self._db.upsert_user(
                    user_id=uid,
                    role=role,
                    display_name=display_name,
                    is_root=is_root,
                )
            except Exception as e:  # noqa: BLE001
                logger.warning("UserService.upsert_user 失败（已忽略）: {}", e)

        return {"user_id": uid, "role": role, "is_root": is_root}

    async def list_all_users(self) -> list[dict]:
        """列出所有已登记用户（root 管理视图用）

        数据库未初始化时返回空列表。
        """
        if self._db is None or not self._db.is_initialized:
            return []
        try:
            return await self._db.list_users()
        except Exception as e:  # noqa: BLE001
            logger.warning("UserService.list_all_users 失败: {}", e)
            return []
