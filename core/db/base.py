# -*- coding: utf-8 -*-

"""数据库后端抽象 — 统一 PostgreSQL / MySQL 的连接与查询接口

异常体系：
- DatabaseUnavailableError: 数据库未初始化（URL 未配置；RuntimeError 子类，
  既有 except RuntimeError 分支继续有效；FastAPI 层转 503，见 server.py）
- SchemaMissingError: verify_tables 发现必需表缺失
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from core.db.statements import STATEMENTS, Statement


class DatabaseUnavailableError(RuntimeError):
    """数据库不可用（未初始化：URL 未配置，或启动前的短暂窗口）"""


class SchemaMissingError(RuntimeError):
    """初始化校验发现必需表缺失"""


class DatabaseBackend(ABC):
    """数据库后端抽象基类

    子类实现连接池生命周期与查询原语。
    execute/fetch 系列接收**已按方言挑选好的 SQL**；MySQL 实现内部
    再做 $N → %s 占位符翻译，Postgres 实现直接下发 asyncpg。
    """

    #: "postgres" 或 "mysql"
    dialect: str = ""

    def pick(self, stmt: Statement) -> str:
        """按当前方言挑选语句变体"""
        if self.dialect == "postgres":
            return stmt.pg
        if self.dialect == "mysql":
            return stmt.mysql
        raise ValueError(f"未知方言: {self.dialect!r}")

    # ------------------------------------------------------------------
    # 具名语句分派（语句注册表入口）
    # ------------------------------------------------------------------

    async def execute_named(self, name: str, *args) -> str:
        """执行注册表中的写语句，返回 asyncpg 兼容状态串"""
        return await self.execute(self.pick(STATEMENTS[name]), *args)

    async def fetchval_named(self, name: str, *args):
        """执行注册表中的单值查询"""
        return await self.fetchval(self.pick(STATEMENTS[name]), *args)

    async def fetch_named(self, name: str, *args) -> list:
        """执行注册表中的多行查询"""
        return await self.fetch(self.pick(STATEMENTS[name]), *args)

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------

    @abstractmethod
    async def connect(self) -> None:
        """建立连接池"""

    @abstractmethod
    async def close(self) -> None:
        """关闭连接池（幂等）"""

    # ------------------------------------------------------------------
    # 查询原语
    # ------------------------------------------------------------------

    @abstractmethod
    async def execute(self, sql: str, *args) -> str:
        """执行写语句，返回状态串（如 "INSERT 0 1" / "UPDATE 3"）"""

    @abstractmethod
    async def fetch(self, sql: str, *args) -> list:
        """查询多行（每行支持 row["col"] 与 row.get("col") 访问）"""

    @abstractmethod
    async def fetchrow(self, sql: str, *args):
        """查询单行，无匹配返回 None"""

    @abstractmethod
    async def fetchval(self, sql: str, *args):
        """查询单值，无匹配返回 None"""

    @abstractmethod
    async def insert_returning_id(self, stmt: Statement, *args):
        """执行插入语句并返回生成的 ID（语义对齐 PG RETURNING id）"""

    @abstractmethod
    async def run_ddl(self, statements: list[str]) -> None:
        """执行 DDL 列表（幂等冲突可忽略，真实错误抛出）"""

    @abstractmethod
    async def missing_tables(self, required: list[str]) -> list[str]:
        """返回 required 中当前库缺失的表名"""
