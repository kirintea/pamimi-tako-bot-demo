# -*- coding: utf-8 -*-

"""storage.py / database.py 接入语句注册表测试

FakeBackend.executed[-1] == (按方言 pick 后的 SQL, args)；
insert_returning_id 返回 FakeBackend.insert_id（默认 99）。
"""

from __future__ import annotations

import json
from datetime import datetime

import pytest

from core.config.schemas import DatabaseConfig
from core.database import DatabaseManager
from core.db.statements import STATEMENTS
from core.storage import PostgresStorage
from core.storage_models import SessionConfig
from tests.db_fakes import FakeBackend

pytestmark = pytest.mark.asyncio(loop_scope="session")


def _db(dialect: str) -> tuple[DatabaseManager, FakeBackend]:
    url = (
        "postgresql://x/y" if dialect == "postgres"
        else "mysql://u:p@localhost:3306/db"
    )
    db = DatabaseManager(DatabaseConfig(url=url))
    fake = FakeBackend(dialect=dialect)
    db._backend = fake
    return db, fake


def _last(fake: FakeBackend) -> tuple[str, tuple]:
    """最后一次原语调用记录 (picked_sql, args)"""
    return fake.executed[-1]


# ------------------------------------------------------------
# upsert_session
# ------------------------------------------------------------

async def test_upsert_session_mysql_sql_returns_record():
    db, fake = _db("mysql")
    storage = PostgresStorage(db)
    record = await storage.upsert_session(
        "u1", "a1", SessionConfig(name="测试会话"),
        state_json="", session_id="s1",
    )
    sql, args = _last(fake)
    assert sql == STATEMENTS["upsert_session"].pick("mysql")
    assert "ON DUPLICATE KEY UPDATE" in sql
    assert "RETURNING" not in sql
    assert args[0] == "s1" and args[1] == "u1" and args[2] == "a1"
    assert record.id == "s1"
    assert record.config.name == "测试会话"


# ------------------------------------------------------------
# database.py 便捷方法
# ------------------------------------------------------------

async def test_insert_conversation_returns_id():
    db, fake = _db("mysql")
    fake.insert_id = 101
    result = await db.insert_conversation("u1", "s1", "user", "你好", {"a": 1})
    sql, args = _last(fake)
    assert sql == STATEMENTS["insert_conversation"].pick("mysql")
    assert result == 101
    assert args[:4] == ("u1", "s1", "user", "你好")
    assert json.loads(args[-2]) == {"a": 1}
    assert args[-1] == "web"


async def test_upsert_session_title_mysql_json_merge_patch():
    db, fake = _db("mysql")
    await db.upsert_session_title("u1", "s1", "新标题")
    sql, args = _last(fake)
    assert sql == STATEMENTS["upsert_session_title"].pick("mysql")
    assert "JSON_MERGE_PATCH" in sql
    # 参数顺序: (session_id, user_id, config_json)
    assert args[0] == "s1" and args[1] == "u1"
    assert json.loads(args[2]) == {"title": "新标题"}


async def test_get_session_title_mysql_json_unquote():
    db, fake = _db("mysql")
    fake.fetchval_result = "我的标题"
    title = await db.get_session_title("u1", "s1")
    sql, args = _last(fake)
    assert sql == STATEMENTS["get_session_title"].pick("mysql")
    assert "JSON_UNQUOTE" in sql and "JSON_EXTRACT" in sql
    assert args == ("s1", "u1")
    assert title == "我的标题"


async def test_get_user_sessions_mysql_json_unquote():
    db, fake = _db("mysql")
    rows = await db.get_user_sessions("u1", limit=5)
    sql, args = _last(fake)
    assert sql == STATEMENTS["get_user_sessions"].pick("mysql")
    assert "JSON_UNQUOTE" in sql and "JSON_EXTRACT" in sql
    assert args == ("u1", 5)
    assert rows == []  # FakeBackend.fetch 恒返回 []


# ------------------------------------------------------------
# _row_to_session — MySQL TEXT NULL 兼容（方法本身是同步的）
# ------------------------------------------------------------

async def test_row_to_session_state_json_none_becomes_empty_string():
    db, _ = _db("mysql")
    storage = PostgresStorage(db)
    row = {
        "id": "s1",
        "user_id": "u1",
        "agent_id": "a1",
        "source": "user",
        "config": json.dumps({"title": "t"}),  # MySQL JSON 列读回 str
        "state_json": None,  # MySQL TEXT NULL
        "parent_session_id": None,
        "depth": 0,
        "created_at": datetime(2026, 1, 1),
        "updated_at": datetime(2026, 1, 1),
    }
    record = storage._row_to_session(row)
    assert record.state_json == ""
    assert record.id == "s1"
