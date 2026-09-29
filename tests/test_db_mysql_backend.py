# -*- coding: utf-8 -*-

"""MySQL 后端单测 — URL 校验与工厂分派（无需真实数据库）"""

from __future__ import annotations

import pytest

from core.config.schemas import DatabaseConfig
from core.db.factory import create_backend
from core.db.mysql_backend import MySQLBackend
from core.db.postgres_backend import PostgresBackend

pytestmark = pytest.mark.asyncio(loop_scope="session")


class TestUrlValidation:
    async def test_missing_database_name_raises(self):
        backend = MySQLBackend("mysql://user:pw@localhost:3306")
        with pytest.raises(ValueError, match="数据库名"):
            await backend.connect()

    async def test_bad_scheme_raises(self):
        backend = MySQLBackend("postgresql://user:pw@localhost/db")
        with pytest.raises(ValueError, match="非 MySQL URL"):
            await backend.connect()

    async def test_connect_failure_leaves_no_pool(self):
        """连接失败不得留下半初始化的池"""
        backend = MySQLBackend("mysql://user:pw@127.0.0.1:1/nope")
        with pytest.raises(Exception):
            await backend.connect()
        assert backend._pool is None


class TestFactory:
    def test_postgres_by_scheme(self):
        b = create_backend(DatabaseConfig(url="postgresql://u:p@h/db"))
        assert isinstance(b, PostgresBackend)

    def test_mysql_by_scheme(self):
        b = create_backend(DatabaseConfig(url="mysql://u:p@h:3306/db"))
        assert isinstance(b, MySQLBackend)

    def test_mariadb_scheme_maps_to_mysql(self):
        b = create_backend(DatabaseConfig(url="mariadb://u:p@h:3306/db"))
        assert isinstance(b, MySQLBackend)

    def test_explicit_backend_overrides_scheme(self):
        b = create_backend(DatabaseConfig(url="postgresql://u:p@h/db", backend="mysql"))
        assert isinstance(b, MySQLBackend)

    def test_unknown_scheme_raises(self):
        with pytest.raises(ValueError, match="无法从 URL 解析"):
            create_backend(DatabaseConfig(url="sqlite:///x"))
