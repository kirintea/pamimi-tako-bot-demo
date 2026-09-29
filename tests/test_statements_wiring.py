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
from core.storage_models import (
    AgentData,
    AgentRecord,
    MCPRecord,
    ScheduleRecord,
    SessionConfig,
    SkillRecord,
)
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
# upsert_agent — 客户端 PK，直接返回 record.id
# ------------------------------------------------------------

async def test_upsert_agent_pg_uses_on_conflict_returning():
    db, fake = _db("postgres")
    storage = PostgresStorage(db)
    rec = AgentRecord(
        user_id="u1",
        data=AgentData(name="助手A"),
        created_at=datetime(2026, 1, 1),
        updated_at=datetime(2026, 1, 1),
    )
    rec.id = "agent-1"
    result = await storage.upsert_agent("u1", rec)
    sql, args = _last(fake)
    assert sql == STATEMENTS["upsert_agent"].pick("postgres")
    assert "ON CONFLICT" in sql and "RETURNING" in sql
    assert args[0] == "agent-1" and args[1] == "u1"
    assert result == "agent-1"  # 客户端 PK：直接返回 record.id


async def test_upsert_agent_mysql_uses_on_duplicate_key():
    db, fake = _db("mysql")
    storage = PostgresStorage(db)
    rec = AgentRecord(
        user_id="u1",
        data=AgentData(name="助手A"),
        created_at=datetime(2026, 1, 1),
        updated_at=datetime(2026, 1, 1),
    )
    rec.id = "agent-1"
    result = await storage.upsert_agent("u1", rec)
    sql, _ = _last(fake)
    assert sql == STATEMENTS["upsert_agent"].pick("mysql")
    assert "ON DUPLICATE KEY UPDATE" in sql
    assert "RETURNING" not in sql
    assert result == "agent-1"


# ------------------------------------------------------------
# upsert_mcp / upsert_skill — (user_id, name) 冲突表，MySQL 回查 id
# ------------------------------------------------------------

async def test_upsert_mcp_returns_backend_id():
    db, fake = _db("mysql")
    fake.insert_id = 99
    storage = PostgresStorage(db)
    rec = MCPRecord(user_id="u1", name="fs-tools", transport="stdio")
    result = await storage.upsert_mcp("u1", rec)
    sql, args = _last(fake)
    assert sql == STATEMENTS["upsert_mcp"].pick("mysql")
    assert "ON DUPLICATE KEY UPDATE" in sql
    assert result == 99  # insert_returning_id → FakeBackend.insert_id
    assert args[0] == rec.id and args[2] == "fs-tools"


async def test_upsert_skill_pg_returning():
    db, fake = _db("postgres")
    fake.insert_id = "skill-1"
    storage = PostgresStorage(db)
    rec = SkillRecord(user_id="u1", name="summarize")
    result = await storage.upsert_skill("u1", rec)
    sql, _ = _last(fake)
    assert sql == STATEMENTS["upsert_skill"].pick("postgres")
    assert "RETURNING" in sql
    assert result == "skill-1"


# ------------------------------------------------------------
# upsert_session / upsert_schedule / upsert_message
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


async def test_upsert_schedule_returns_record_id():
    db, fake = _db("mysql")
    storage = PostgresStorage(db)
    rec = ScheduleRecord(
        user_id="u1",
        agent_id="a1",
        name="每日简报",
        cron_expr="0 9 * * *",
        prompt="生成简报",
    )
    rec.id = "sch-1"
    result = await storage.upsert_schedule("u1", rec)
    sql, args = _last(fake)
    assert sql == STATEMENTS["upsert_schedule"].pick("mysql")
    assert "ON DUPLICATE KEY UPDATE" in sql
    assert result == "sch-1"
    assert args[0] == "sch-1" and args[1] == "u1"


async def test_upsert_message_insert_uses_registry():
    db, fake = _db("mysql")
    fake.insert_id = 55
    storage = PostgresStorage(db)
    # FakeBackend.fetchrow 恒返回 None → 必走插入分支
    result = await storage.upsert_message(
        user_id="u1", session_id="s1", msg_id="m1",
        role="user", content="你好", metadata={"k": "v"},
    )
    sql, args = _last(fake)
    assert sql == STATEMENTS["insert_message"].pick("mysql")
    assert result == 55
    assert args == ("u1", "s1", "m1", "user", "你好", json.dumps({"k": "v"}), "web")


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
