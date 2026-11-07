# -*- coding: utf-8 -*-

"""stats_daily 事实表纯函数 + 写路径 hook 测试"""

from __future__ import annotations

from datetime import date

import pytest

from core.config.schemas import DatabaseConfig
from core.database import DatabaseManager
from core.stats import (
    METRIC_SESSION_ACTIVE,
    METRIC_SESSION_NEW,
    METRIC_TURN,
    METRIC_TURN_LEGACY,
    METRIC_UV,
    conversation_facts,
    day_to_bucket,
    session_created_fact,
)
from core.storage import PostgresStorage
from core.storage_models import SessionConfig
from tests.db_fakes import FakeBackend, find_executed


def _db(dialect: str) -> tuple[DatabaseManager, FakeBackend]:
    url = "postgresql://x/y" if dialect == "postgres" else "mysql://u:p@localhost:3306/db"
    db = DatabaseManager(DatabaseConfig(url=url))
    fake = FakeBackend(dialect=dialect)
    db._backend = fake
    return db, fake


# ------------------------------------------------------------
# 纯函数
# ------------------------------------------------------------

def test_conversation_facts_user_with_turn():
    facts = conversation_facts(date(2026, 10, 22), "web", "u1", "s1", "user", "t1", 101)
    assert set(facts) == {
        (date(2026, 10, 22), "web", METRIC_UV, "u1"),
        (date(2026, 10, 22), "web", METRIC_SESSION_ACTIVE, "s1"),
        (date(2026, 10, 22), "web", METRIC_TURN, "t1"),
    }


def test_conversation_facts_legacy_user_no_turn():
    facts = conversation_facts(date(2026, 10, 22), "wecom", "u1", "s1", "user", None, 7)
    assert (date(2026, 10, 22), "wecom", METRIC_TURN_LEGACY, "7") in facts
    assert not any(f[2] == METRIC_TURN for f in facts)


def test_conversation_facts_assistant_no_legacy():
    facts = conversation_facts(date(2026, 10, 22), "web", "u1", "s1", "assistant", None, 8)
    assert not any(f[2] == METRIC_TURN_LEGACY for f in facts)


def test_session_created_fact():
    assert session_created_fact(date(2026, 10, 22), "web", "s9") == (
        date(2026, 10, 22), "web", METRIC_SESSION_NEW, "s9",
    )


def test_day_to_bucket():
    d = date(2026, 10, 22)  # 周四
    assert day_to_bucket(d, "day") == date(2026, 10, 22)
    assert day_to_bucket(d, "week") == date(2026, 10, 19)  # 周一
    assert day_to_bucket(d, "month") == date(2026, 10, 1)


# ------------------------------------------------------------
# 写路径 hook
# ------------------------------------------------------------

@pytest.mark.asyncio(loop_scope="session")
async def test_insert_conversation_records_stats_facts():
    db, fake = _db("postgres")
    fake.insert_id = 42
    await db.insert_conversation("u1", "s1", "user", "你好", channel="web", turn_id="t1")
    _conv_sql, conv_args = fake.executed[-2]
    day = conv_args[-1].date()
    sql, args = fake.executed[-1]
    assert 'INSERT INTO "stats_daily"' in sql
    assert 'ON CONFLICT' in sql and 'DO NOTHING' in sql
    triples = set(zip(args[::5], args[1::5], args[2::5], args[3::5]))
    assert triples == {
        (day, "web", METRIC_UV, "u1"),
        (day, "web", METRIC_SESSION_ACTIVE, "s1"),
        (day, "web", METRIC_TURN, "t1"),
    }


@pytest.mark.asyncio(loop_scope="session")
async def test_record_stats_facts_mysql_uses_insert_ignore():
    db, fake = _db("mysql")
    await db.record_stats_facts([(date(2026, 10, 22), "web", METRIC_UV, "u1")])
    sql, args = fake.executed[-1]
    assert sql.startswith('INSERT IGNORE INTO "stats_daily"')
    assert args[:4] == (date(2026, 10, 22), "web", METRIC_UV, "u1")
    assert len(args) == 5  # 末位 created_at


@pytest.mark.asyncio(loop_scope="session")
async def test_upsert_session_title_new_session_records_session_new():
    db, fake = _db("postgres")
    await db.upsert_session_title("u1", "s1", "标题", channel="wecom")
    stat_sql, stat_args = fake.executed[-1]
    assert 'INSERT INTO "stats_daily"' in stat_sql
    assert stat_args[1] == "wecom"
    assert stat_args[2] == METRIC_SESSION_NEW
    assert stat_args[3] == "s1"


@pytest.mark.asyncio(loop_scope="session")
async def test_upsert_session_title_existing_session_no_session_new():
    db, fake = _db("postgres")
    fake.fetchrow_result = {"x": 1}  # 已存在 → 不记 session_new
    await db.upsert_session_title("u1", "s1", "改标题", channel="web")
    assert all('stats_daily' not in s for s, _ in fake.executed)


@pytest.mark.asyncio(loop_scope="session")
async def test_upsert_session_new_records_session_new():
    db, fake = _db("postgres")
    storage = PostgresStorage(db)
    await storage.upsert_session("u1", "ag_01", SessionConfig(name="s"), channel="wecom")
    stat_sql, stat_args = fake.executed[-1]
    assert 'INSERT INTO "stats_daily"' in stat_sql
    assert stat_args[1] == "wecom"
    assert stat_args[2] == METRIC_SESSION_NEW
