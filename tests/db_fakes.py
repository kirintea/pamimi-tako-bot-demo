# -*- coding: utf-8 -*-

"""测试用内存后端 — 可注入连接/DDL/表缺失故障，记录执行过的 SQL"""

from __future__ import annotations

from core.db.base import DatabaseBackend
from core.db.statements import Statement


class FakeBackend(DatabaseBackend):
    """In-memory 后端替身

    Args:
        dialect: "postgres" 或 "mysql"，决定 pick() 挑选哪个变体
    """

    def __init__(self, dialect: str = "postgres") -> None:
        self.dialect = dialect
        self.executed: list[tuple[str, tuple]] = []
        self.ddl_run: list[str] = []
        self.connect_error: Exception | None = None
        self.ddl_error: Exception | None = None
        self.missing: list[str] = []
        self.insert_id: int | str = 99
        self.fetchval_result = None
        self.connected = False

    async def connect(self) -> None:
        if self.connect_error is not None:
            raise self.connect_error
        self.connected = True

    async def close(self) -> None:
        self.connected = False

    async def execute(self, sql: str, *args) -> str:
        self.executed.append((sql, args))
        return "UPDATE 1"

    async def fetch(self, sql: str, *args) -> list:
        self.executed.append((sql, args))
        return []

    async def fetchrow(self, sql: str, *args):
        self.executed.append((sql, args))
        return None

    async def fetchval(self, sql: str, *args):
        self.executed.append((sql, args))
        return self.fetchval_result

    async def insert_returning_id(self, stmt: Statement, *args) -> int | str:
        self.executed.append((self.pick(stmt), args))
        return self.insert_id

    async def run_ddl(self, statements: list[str]) -> None:
        self.ddl_run.extend(statements)
        if self.ddl_error is not None:
            raise self.ddl_error

    async def missing_tables(self, required: list[str]) -> list[str]:
        return list(self.missing)
