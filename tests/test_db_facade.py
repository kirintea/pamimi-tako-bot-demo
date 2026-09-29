# -*- coding: utf-8 -*-

"""DatabaseManager 门面测试 — 未初始化保护与语句分派"""

from __future__ import annotations

import pytest

from core.config.schemas import DatabaseConfig
from core.database import DatabaseManager
from core.db.base import DatabaseUnavailableError
from tests.db_fakes import FakeBackend

pytestmark = pytest.mark.asyncio(loop_scope="session")


def _make_db(dialect: str = "postgres") -> tuple[DatabaseManager, FakeBackend]:
    db = DatabaseManager(DatabaseConfig(url=f"{dialect}://user:pw@localhost/db"))
    fake = FakeBackend(dialect=dialect)
    db._backend = fake
    return db, fake


async def test_execute_before_init_raises_unavailable():
    db = DatabaseManager(DatabaseConfig(url="postgresql://x/y"))
    with pytest.raises(DatabaseUnavailableError, match="数据库未初始化"):
        await db.execute("SELECT 1")


async def test_generic_execute_delegates_to_backend():
    db, fake = _make_db()
    await db.execute("UPDATE t SET a = $1", 1)
    assert fake.executed == [("UPDATE t SET a = $1", (1,))]


async def test_execute_named_picks_pg_variant():
    db, fake = _make_db("postgres")
    await db.execute_named("upsert_agent", "id1", "u1", "user", "{}", None, None)
    sql = fake.executed[-1][0]
    assert "ON CONFLICT" in sql
    assert "RETURNING" in sql


async def test_execute_named_picks_mysql_variant():
    db, fake = _make_db("mysql")
    await db.execute_named("upsert_agent", "id1", "u1", "user", "{}", None, None)
    sql = fake.executed[-1][0]
    assert "ON DUPLICATE KEY" in sql
    assert "RETURNING" not in sql


async def test_insert_returning_id_delegates_with_statement():
    db, fake = _make_db("mysql")
    result = await db.insert_returning_id("upsert_mcp", "m1", "u1", "srv")
    assert result == 99
    sql = fake.executed[-1][0]
    assert "ON DUPLICATE KEY" in sql


async def test_is_initialized_reflects_backend():
    db = DatabaseManager(DatabaseConfig(url="postgresql://x/y"))
    assert db.is_initialized is False
    db._backend = FakeBackend()
    assert db.is_initialized is True


class TestInitializeViaFactory:
    async def test_initialize_dispatches_via_factory(self, monkeypatch):
        """initialize 经工厂创建后端，并按方言取 DDL（Task 6 接线）"""
        from core.db.ddl import get_ddl

        fake = FakeBackend(dialect="mysql")
        monkeypatch.setattr("core.database.create_backend", lambda cfg: fake)
        db = DatabaseManager(DatabaseConfig(url="mysql://u:p@localhost:3306/db"))
        await db.initialize()
        assert db.is_initialized is True
        assert fake.connected is True
        assert fake.ddl_run == get_ddl("mysql")

    async def test_initialize_skips_ddl_when_disabled(self, monkeypatch):
        fake = FakeBackend(dialect="mysql")
        monkeypatch.setattr("core.database.create_backend", lambda cfg: fake)
        db = DatabaseManager(DatabaseConfig(
            url="mysql://u:p@localhost:3306/db",
            auto_create_tables=False,
        ))
        await db.initialize()
        assert db.is_initialized is True
        assert fake.ddl_run == []
