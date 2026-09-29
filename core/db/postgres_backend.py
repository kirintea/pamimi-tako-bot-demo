# -*- coding: utf-8 -*-

"""PostgreSQL 后端 — asyncpg 连接池实现（逻辑自原 core/database.py 迁出）"""

from __future__ import annotations

import asyncpg

from core.db.base import DatabaseBackend
from core.db.statements import Statement

from loguru import logger

#: asyncpg 幂等性冲突错误码（可安全忽略）
_PG_IDEMPOTENT_CODES = {
    "42P07",  # duplicate_table
    "42710",  # duplicate_object
    "42P16",  # invalid_table_definition (IF NOT EXISTS 兜底)
}


class PostgresBackend(DatabaseBackend):
    """PostgreSQL 后端（asyncpg 连接池）"""

    dialect = "postgres"

    def __init__(self, url: str, pool_size: int = 10) -> None:
        self._url = url
        self._pool_size = pool_size
        self._pool: asyncpg.Pool | None = None

    async def connect(self) -> None:
        self._pool = await asyncpg.create_pool(
            self._url,
            min_size=2,
            max_size=self._pool_size,
            command_timeout=30,
        )
        logger.info("PostgreSQL 连接池已创建 (pool_size={})", self._pool_size)

    async def close(self) -> None:
        if self._pool:
            await self._pool.close()
            self._pool = None

    def _require(self) -> asyncpg.Pool:
        if self._pool is None:
            raise RuntimeError("PostgreSQL 连接池未初始化")
        return self._pool

    async def execute(self, sql: str, *args) -> str:
        async with self._require().acquire() as conn:
            return await conn.execute(sql, *args)

    async def fetch(self, sql: str, *args) -> list:
        async with self._require().acquire() as conn:
            return await conn.fetch(sql, *args)

    async def fetchrow(self, sql: str, *args):
        async with self._require().acquire() as conn:
            return await conn.fetchrow(sql, *args)

    async def fetchval(self, sql: str, *args):
        async with self._require().acquire() as conn:
            return await conn.fetchval(sql, *args)

    async def insert_returning_id(self, stmt: Statement, *args):
        async with self._require().acquire() as conn:
            return await conn.fetchval(stmt.pg, *args)

    async def run_ddl(self, statements: list[str]) -> None:
        async with self._require().acquire() as conn:
            for ddl in statements:
                try:
                    await conn.execute(ddl)
                except Exception as e:
                    pgcode = getattr(e, "sqlstate", None) or getattr(e, "pgcode", None)
                    if pgcode and str(pgcode) in _PG_IDEMPOTENT_CODES:
                        logger.debug("DDL 幂等跳过 ({}): {}", pgcode, e)
                    else:
                        logger.error("DDL 执行失败: {}", e)
                        raise

    async def missing_tables(self, required: list[str]) -> list[str]:
        rows = await self.fetch(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = 'public'"
        )
        existing = {r["table_name"] for r in rows}
        return [t for t in required if t not in existing]
