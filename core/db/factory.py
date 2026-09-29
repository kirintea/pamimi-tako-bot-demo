# -*- coding: utf-8 -*-

"""数据库后端工厂 — 按配置 / URL scheme 创建对应后端"""

from __future__ import annotations

from core.config.schemas import DatabaseConfig
from core.db.base import DatabaseBackend
from core.db.dialect import resolve_backend


def create_backend(config: DatabaseConfig) -> DatabaseBackend:
    """解析后端类型并实例化（驱动模块延迟导入，未选用的后端不加载）"""
    kind = resolve_backend(config.url, config.backend)
    if kind == "postgres":
        from core.db.postgres_backend import PostgresBackend
        return PostgresBackend(config.url, pool_size=config.pool_size)
    from core.db.mysql_backend import MySQLBackend
    return MySQLBackend(config.url, pool_size=config.pool_size)
