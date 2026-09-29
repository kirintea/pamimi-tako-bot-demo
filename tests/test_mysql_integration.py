# -*- coding: utf-8 -*-

"""MySQL 集成测试 — 需要真实 MySQL（环境变量 TEST_MYSQL_URL 门控）

一次性启动 MySQL（本地已有 mysql:8.0.26 镜像，无需 pull）：
    docker run --rm -d --name mysql-test -p 3307:3306 \
        -e MYSQL_ROOT_PASSWORD=test \
        -e MYSQL_DATABASE=platform_test \
        -e MYSQL_USER=test -e MYSQL_PASSWORD=test \
        mysql:8.0.26

运行：
    TEST_MYSQL_URL=mysql://test:test@localhost:3307/platform_test \
        .venv/Scripts/python.exe -m pytest tests/test_mysql_integration.py -v

清理：
    docker stop mysql-test
"""

from __future__ import annotations

import os

import pytest
import pytest_asyncio

from core.config.schemas import DatabaseConfig
from core.db.ddl import REQUIRED_TABLES

TEST_MYSQL_URL = os.environ.get("TEST_MYSQL_URL", "")

pytestmark = [
    pytest.mark.asyncio(loop_scope="session"),
    pytest.mark.skipif(
        not TEST_MYSQL_URL,
        reason="TEST_MYSQL_URL 未设置，跳过 MySQL 集成测试",
    ),
]


@pytest_asyncio.fixture(loop_scope="session")
async def db_manager():
    from core.database import DatabaseManager
    db = DatabaseManager(DatabaseConfig(
        url=TEST_MYSQL_URL,
        auto_create_tables=True,
        verify_tables=True,
    ))
    await db.initialize()
    assert db.is_initialized, "初始化失败（fail-fast 下 initialize 已抛出，能走到这里即成功）"
    yield db
    await db.shutdown()


async def test_tables_created(db_manager):
    rows = await db_manager.fetch(
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_schema = DATABASE()"
    )
    existing = {r["table_name"] for r in rows}
    assert set(REQUIRED_TABLES) <= existing


async def test_select_1(db_manager):
    assert await db_manager.fetchval("SELECT 1") == 1


async def test_insert_returning_id_incrementing(db_manager):
    """自增表 ID 走 lastrowid（Task 9 之前仅用注册表路径）"""
    id1 = await db_manager.insert_returning_id(
        "insert_conversation", "it_user", "it_sess", "user", "你好", None,
    )
    id2 = await db_manager.insert_returning_id(
        "insert_conversation", "it_user", "it_sess", "assistant", "嗨", None,
    )
    assert isinstance(id1, int) and isinstance(id2, int)
    assert id2 > id1
