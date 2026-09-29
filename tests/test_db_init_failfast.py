# -*- coding: utf-8 -*-

"""DatabaseManager 初始化失败 fail-fast 测试 — ERROR 日志后抛出，连接随失败关闭"""

from __future__ import annotations

import pytest

from core.config.schemas import DatabaseConfig
from core.database import DatabaseManager
from core.db.base import SchemaMissingError
from core.db.ddl import get_ddl
from tests.db_fakes import FakeBackend

pytestmark = pytest.mark.asyncio(loop_scope="session")


def _patch_factory(monkeypatch, backend: FakeBackend) -> list[DatabaseConfig]:
    """把 core.database.create_backend 换成返回指定 FakeBackend 的工厂"""
    created: list[DatabaseConfig] = []

    def _factory(config: DatabaseConfig) -> FakeBackend:
        created.append(config)
        return backend

    monkeypatch.setattr("core.database.create_backend", _factory)
    return created


async def test_connect_error_raises(monkeypatch):
    """连接失败必须向上抛出（fail-fast），且不留下半初始化状态"""
    backend = FakeBackend()
    backend.connect_error = RuntimeError("connection refused")
    _patch_factory(monkeypatch, backend)
    db = DatabaseManager(DatabaseConfig(url="postgresql://x/y"))
    with pytest.raises(RuntimeError, match="connection refused"):
        await db.initialize()
    assert db.is_initialized is False
    assert db._backend is None
    assert backend.connected is False


async def test_success_initializes(monkeypatch):
    backend = FakeBackend()
    _patch_factory(monkeypatch, backend)
    db = DatabaseManager(DatabaseConfig(url="postgresql://x/y"))
    await db.initialize()
    assert db.is_initialized is True


async def test_auto_create_tables_false_skips_ddl(monkeypatch):
    backend = FakeBackend()
    _patch_factory(monkeypatch, backend)
    db = DatabaseManager(DatabaseConfig(
        url="mysql://u:p@localhost:3306/db",
        auto_create_tables=False,
    ))
    await db.initialize()
    assert db.is_initialized is True
    assert backend.ddl_run == []


async def test_ddl_executes_by_dialect(monkeypatch):
    backend = FakeBackend(dialect="mysql")
    _patch_factory(monkeypatch, backend)
    db = DatabaseManager(DatabaseConfig(url="mysql://u:p@localhost:3306/db"))
    await db.initialize()
    assert backend.ddl_run == get_ddl("mysql")


async def test_missing_tables_raises_and_closes(monkeypatch):
    """缺表（verify_tables=true）→ SchemaMissingError 抛出 + 连接随失败关闭"""
    backend = FakeBackend()
    backend.missing = ["mcps", "skills"]
    _patch_factory(monkeypatch, backend)
    db = DatabaseManager(DatabaseConfig(
        url="postgresql://x/y", verify_tables=True,
    ))
    with pytest.raises(SchemaMissingError, match="mcps"):
        await db.initialize()
    assert db.is_initialized is False
    assert db._backend is None
    assert backend.connected is False  # 连接已随失败关闭


async def test_ddl_error_closes_backend(monkeypatch):
    """DDL 执行失败 → 异常向上抛出 + 已建立的连接被关闭（不留半开连接）"""
    backend = FakeBackend()
    backend.ddl_error = RuntimeError("boom: syntax error")
    _patch_factory(monkeypatch, backend)
    db = DatabaseManager(DatabaseConfig(
        url="postgresql://x/y", auto_create_tables=True,
    ))
    with pytest.raises(RuntimeError, match="boom"):
        await db.initialize()
    assert db.is_initialized is False
    assert backend.connected is False  # 旧实现不关闭 → 本断言是红驱动


async def test_verify_tables_false_skips_check(monkeypatch):
    backend = FakeBackend()
    backend.missing = ["mcps"]
    _patch_factory(monkeypatch, backend)
    db = DatabaseManager(DatabaseConfig(url="postgresql://x/y", verify_tables=False))
    await db.initialize()
    assert db.is_initialized is True


async def test_empty_url_skips_factory(monkeypatch):
    """URL 置空是有意的"无库运行"逃生口：不调工厂、正常启动"""
    created = _patch_factory(monkeypatch, FakeBackend())
    db = DatabaseManager(DatabaseConfig(url=""))
    await db.initialize()
    assert created == []
    assert db.is_initialized is False
