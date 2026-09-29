# -*- coding: utf-8 -*-

"""消息渠道 channel 测试 — 三表 DDL 列 / 写语句参数 / 写入路径显式传参 / OTel Resource tag

Task 13（决策 D11）：
- conversations / messages / sessions 三表各 1 条 ADD COLUMN channel VARCHAR(16) NOT NULL DEFAULT 'web'
- insert_conversation（末位 $6）/ insert_message（末位 $7）新增 channel 参数
- 写入方法默认 channel="web"（调用点显式传参在 api/chat.py 等 6 处，无单测覆盖，验收清单 grep 核对）
- OTel Resource 属性含 channel（core/tracing/setup.py._resource_attributes 独立函数可单测）

FakeBackend 约定同 tests/test_statements_wiring.py：
executed[-1] == (按方言 pick 后的 SQL, args)；fetchrow 恒返回 None → upsert_message 必走插入分支。
"""

from __future__ import annotations

import json

import pytest

from core.config.schemas import DatabaseConfig, OTelConfig
from core.database import DatabaseManager
from core.db.ddl import DDL_MYSQL, DDL_POSTGRES
from core.db.statements import STATEMENTS
from core.storage import PostgresStorage
from tests.db_fakes import FakeBackend

CHANNEL_TABLES = ("conversations", "messages", "sessions")


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
# DDL：三表 channel 列
# ------------------------------------------------------------

class TestDdlChannelColumn:
    def test_pg_three_channel_alter(self):
        stmts = [s for s in DDL_POSTGRES if "ADD COLUMN IF NOT EXISTS channel" in s]
        assert len(stmts) == 3, f"PG channel ALTER 应为 3 条，实为 {len(stmts)}"
        for table in CHANNEL_TABLES:
            assert any(s.lstrip().startswith(f"ALTER TABLE {table}") for s in stmts), (
                f"PG 缺 {table} 的 channel ALTER"
            )

    def test_mysql_three_channel_alter(self):
        stmts = [s for s in DDL_MYSQL if "ADD COLUMN channel" in s]
        assert len(stmts) == 3, f"MySQL channel ALTER 应为 3 条，实为 {len(stmts)}"
        for table in CHANNEL_TABLES:
            assert any(s.startswith(f"ALTER TABLE {table}") for s in stmts), (
                f"MySQL 缺 {table} 的 channel ALTER"
            )
        # MySQL 的 ADD COLUMN 无 IF NOT EXISTS → 必须是裸 ADD COLUMN（1060 幂等）
        for s in stmts:
            assert "IF NOT EXISTS" not in s

    def test_channel_definition_default_web(self):
        for joined in ("\n".join(DDL_POSTGRES), "\n".join(DDL_MYSQL)):
            assert "channel VARCHAR(16) NOT NULL DEFAULT 'web'" in joined


# ------------------------------------------------------------
# 写语句：channel 末位参数
# ------------------------------------------------------------

class TestStatementChannelParam:
    def test_insert_conversation_channel_is_sixth(self):
        stmt = STATEMENTS["insert_conversation"]
        for sql in (stmt.pg, stmt.mysql):
            assert "channel" in sql
            assert "$6" in sql
            assert "$7" not in sql

    def test_insert_message_channel_is_seventh(self):
        stmt = STATEMENTS["insert_message"]
        for sql in (stmt.pg, stmt.mysql):
            assert "channel" in sql
            assert "$7" in sql


# ------------------------------------------------------------
# 写入路径：默认 web + 显式传参
# ------------------------------------------------------------

@pytest.mark.asyncio(loop_scope="session")
class TestWritePathChannel:
    async def test_insert_conversation_default_web(self):
        db, fake = _db("mysql")
        fake.insert_id = 7
        result = await db.insert_conversation("u1", "s1", "user", "你好", {"a": 1})
        sql, args = _last(fake)
        assert sql == STATEMENTS["insert_conversation"].pick("mysql")
        assert result == 7
        assert args[:4] == ("u1", "s1", "user", "你好")
        assert args[-1] == "web"
        assert json.loads(args[-2]) == {"a": 1}

    async def test_insert_conversation_explicit_feishu(self):
        db, fake = _db("mysql")
        await db.insert_conversation("u1", "s1", "user", "hi", channel="feishu")
        _, args = _last(fake)
        assert args[-2] is None  # metadata 未传 → None
        assert args[-1] == "feishu"

    async def test_upsert_message_default_web(self):
        db, fake = _db("mysql")
        fake.insert_id = 55
        storage = PostgresStorage(db)
        result = await storage.upsert_message(
            user_id="u1", session_id="s1", msg_id="m1",
            role="user", content="你好", metadata={"k": "v"},
        )
        sql, args = _last(fake)
        assert sql == STATEMENTS["insert_message"].pick("mysql")
        assert result == 55
        assert args == ("u1", "s1", "m1", "user", "你好", json.dumps({"k": "v"}), "web")

    async def test_upsert_message_explicit_wechat(self):
        db, fake = _db("mysql")
        storage = PostgresStorage(db)
        await storage.upsert_message(
            user_id="u1", session_id="s1", msg_id="m2",
            role="assistant", content="ok", channel="wechat",
        )
        _, args = _last(fake)
        assert args[-1] == "wechat"
        assert json.loads(args[-2]) == {}


# ------------------------------------------------------------
# OTel Resource channel tag
# ------------------------------------------------------------

class TestOtelChannelTag:
    def test_resource_channel_defaults_to_web(self):
        from core.tracing.setup import _resource_attributes

        cfg = OTelConfig(endpoint="http://localhost:4317", environment="development")
        attrs = _resource_attributes(cfg)
        assert attrs["channel"] == "web"
        assert attrs["service.name"] == cfg.service_name
        assert attrs["deployment.environment"] == "development"

    def test_resource_channel_configurable(self):
        from core.tracing.setup import _resource_attributes

        cfg = OTelConfig(
            endpoint="http://localhost:4317",
            environment="production",
            channel="feishu",
        )
        assert _resource_attributes(cfg)["channel"] == "feishu"
