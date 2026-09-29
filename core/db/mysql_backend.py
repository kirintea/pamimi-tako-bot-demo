# -*- coding: utf-8 -*-

"""MySQL 后端 — aiomysql 连接池实现

要求 MySQL >= 5.7.22（推荐 8.0）/ MariaDB >= 10.3。
URL 形如: mysql://user:password@host:3306/dbname（必须带库名）。
通用 SQL 在此翻译为 %s 占位符（见 core/db/dialect.py）。
"""

from __future__ import annotations

from urllib.parse import unquote, urlparse

import aiomysql
from aiomysql.cursors import DictCursor

from core.db.base import DatabaseBackend
from core.db.ddl import MYSQL_IDEMPOTENT_ERRORS
from core.db.dialect import format_status, translate_placeholders
from core.db.statements import Statement

from loguru import logger


def _verb_of(sql: str) -> str:
    """取 SQL 首词（INSERT/UPDATE/DELETE/...）用于拼 asyncpg 兼容状态串"""
    stripped = sql.lstrip()
    if not stripped:
        return "UNKNOWN"
    return stripped.split(None, 1)[0].upper()


class MySQLBackend(DatabaseBackend):
    """MySQL/MariaDB 后端（aiomysql 连接池）"""

    dialect = "mysql"

    def __init__(self, url: str, pool_size: int = 10) -> None:
        self._url = url
        self._pool_size = pool_size
        self._pool: aiomysql.Pool | None = None

    async def connect(self) -> None:
        parsed = urlparse(self._url)
        if parsed.scheme not in ("mysql", "mariadb"):
            raise ValueError(f"MySQL 后端收到非 MySQL URL: {self._url}")
        if not parsed.path or parsed.path == "/":
            raise ValueError(
                f"MySQL URL 必须包含数据库名，如 mysql://user:pw@host:3306/platform"
                f"（收到: {self._url}）"
            )
        self._pool = await aiomysql.create_pool(
            host=parsed.hostname or "localhost",
            port=parsed.port or 3306,
            user=unquote(parsed.username or ""),
            password=unquote(parsed.password or ""),
            db=parsed.path.lstrip("/"),
            minsize=1,
            maxsize=self._pool_size,
            autocommit=True,
            charset="utf8mb4",
        )
        logger.info(
            "MySQL 连接池已创建 (maxsize={}, db={})",
            self._pool_size, parsed.path.lstrip("/"),
        )

    async def close(self) -> None:
        if self._pool:
            self._pool.close()
            await self._pool.wait_closed()
            self._pool = None

    def _require(self) -> aiomysql.Pool:
        if self._pool is None:
            raise RuntimeError("MySQL 连接池未初始化")
        return self._pool

    async def execute(self, sql: str, *args) -> str:
        sql2, args2 = translate_placeholders(sql, args)
        async with self._require().acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(sql2, args2 or None)
                return format_status(_verb_of(sql), cur.rowcount)

    async def fetch(self, sql: str, *args) -> list[dict]:
        sql2, args2 = translate_placeholders(sql, args)
        async with self._require().acquire() as conn:
            async with conn.cursor(DictCursor) as cur:
                await cur.execute(sql2, args2 or None)
                return await cur.fetchall()

    async def fetchrow(self, sql: str, *args) -> dict | None:
        sql2, args2 = translate_placeholders(sql, args)
        async with self._require().acquire() as conn:
            async with conn.cursor(DictCursor) as cur:
                await cur.execute(sql2, args2 or None)
                return await cur.fetchone()

    async def fetchval(self, sql: str, *args):
        row = await self.fetchrow(sql, *args)
        if row is None:
            return None
        return next(iter(row.values()))

    async def insert_returning_id(self, stmt: Statement, *args):
        """插入并返回 ID（对齐 PG RETURNING 语义）

        - mysql_id_sql 存在（(user_id,name) 冲突表）→ 回查现有行 id
        - 否则自增表取 cur.lastrowid
        - 客户端主键表不经过本方法（调用方直接返回 record.id）
        """
        sql2, args2 = translate_placeholders(stmt.mysql, args)
        async with self._require().acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(sql2, args2 or None)
                if stmt.mysql_id_sql:
                    id_sql, id_args = translate_placeholders(
                        stmt.mysql_id_sql,
                        tuple(args[i - 1] for i in stmt.mysql_id_args),
                    )
                    await cur.execute(id_sql, id_args or None)
                    row = await cur.fetchone()
                    return row[0] if row else None
                return cur.lastrowid

    async def run_ddl(self, statements: list[str]) -> None:
        async with self._require().acquire() as conn:
            async with conn.cursor() as cur:
                for ddl in statements:
                    try:
                        # DDL 无占位符，args=None 跳过 % 插值
                        await cur.execute(ddl)
                    except Exception as e:
                        errno = e.args[0] if getattr(e, "args", None) else None
                        if errno in MYSQL_IDEMPOTENT_ERRORS:
                            logger.debug("DDL 幂等跳过 ({}): {}", errno, e)
                        else:
                            logger.error("DDL 执行失败: {}", e)
                            raise

    async def missing_tables(self, required: list[str]) -> list[str]:
        # 显式别名：MySQL 8 information_schema 返回列名 TABLE_NAME（大写），
        # DictCursor 键随服务端返回的列名走，不加别名则 r["table_name"] KeyError
        rows = await self.fetch(
            "SELECT table_name AS table_name FROM information_schema.tables "
            "WHERE table_schema = DATABASE()"
        )
        existing = {r["table_name"] for r in rows}
        return [t for t in required if t not in existing]
