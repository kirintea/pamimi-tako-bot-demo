# -*- coding: utf-8 -*-

"""时间列时区测试 — 应用侧生成时间（database.timezone，仅增量）

背景（2026-10-12 对话入库时间差 8 小时）：
MySQL 服务端时区为 UTC，conversations / users / sessions 的
DATETIME DEFAULT CURRENT_TIMESTAMP(3) 与语句里的 NOW() 均落库 UTC 墙钟，
前端按本地时间直读显示差 8 小时。

修复约定（用户拍板）：
- 应用侧按 database.timezone（默认 Asia/Shanghai）生成时间显式传参
- DB 默认值 / NOW() 仅作兜底，写语句不再依赖
- 只修增量，历史数据不动
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
import yaml

from core.config.schemas import DatabaseConfig
from core.database import DatabaseManager
from core.db.statements import STATEMENTS
from core.storage import PostgresStorage
from tests.db_fakes import FakeBackend

SHANGHAI = ZoneInfo("Asia/Shanghai")
ROOT = Path(__file__).resolve().parent.parent


def _db(dialect: str) -> tuple[DatabaseManager, FakeBackend]:
    url = (
        "postgresql://x/y" if dialect == "postgres"
        else "mysql://u:p@localhost:3306/db"
    )
    db = DatabaseManager(DatabaseConfig(url=url))
    fake = FakeBackend(dialect=dialect)
    db._backend = fake
    return db, fake


# ------------------------------------------------------------
# 1. 配置：DatabaseConfig.timezone 默认上海 + yaml 携带
# ------------------------------------------------------------

class TestConfigTimezone:
    def test_database_config_default_timezone_is_shanghai(self):
        assert DatabaseConfig().timezone == "Asia/Shanghai"

    def test_invalid_timezone_rejected(self):
        with pytest.raises(ValueError):
            DatabaseConfig(timezone="Not/AZone")

    def test_yaml_carries_database_timezone(self):
        for name in ("dev.yaml", "prod.yaml"):
            data = yaml.safe_load(
                (ROOT / "configs" / name).read_text(encoding="utf-8")
            )
            assert data["database"].get("timezone") == "Asia/Shanghai", name


# ------------------------------------------------------------
# 2. DatabaseManager.now() 按方言生成
# ------------------------------------------------------------

@pytest.mark.asyncio(loop_scope="session")
class TestDatabaseManagerNow:
    async def test_mysql_now_is_naive_shanghai_wallclock(self):
        db, _ = _db("mysql")
        now = db.now()
        # pymysql 按字段原样写入 → MySQL DATETIME 需要 naive 墙钟时间
        assert now.tzinfo is None, "MySQL 侧应返回 naive 墙钟时间"
        expect = datetime.now(SHANGHAI).replace(tzinfo=None)
        assert abs((now - expect).total_seconds()) < 5

    async def test_postgres_now_is_aware_shanghai(self):
        db, _ = _db("postgres")
        now = db.now()
        # asyncpg TIMESTAMPTZ 需要 aware 时间（自动转 UTC 存储）
        assert now.tzinfo is not None, "PG 侧应返回 aware 时间"
        assert now.utcoffset() == timedelta(hours=8)
        expect = datetime.now(SHANGHAI)
        assert abs((now - expect).total_seconds()) < 5

    async def test_now_uses_configured_timezone(self):
        db, _ = _db("mysql")
        db._config.timezone = "Asia/Tokyo"
        now = db.now()
        expect = datetime.now(ZoneInfo("Asia/Tokyo")).replace(tzinfo=None)
        # 东京 = 上海 + 1 小时，墙钟应差约 3600s
        assert abs((now - datetime.now(SHANGHAI).replace(tzinfo=None)).total_seconds()) > 3000
        assert abs((now - expect).total_seconds()) < 5


# ------------------------------------------------------------
# 3. 写语句显式带时间参数（不再依赖 DB NOW()/DEFAULT）
# ------------------------------------------------------------

class TestWriteStatementsExplicitTimestamps:
    def test_insert_conversation_has_created_at_param(self):
        stmt = STATEMENTS["insert_conversation"]
        for sql in (stmt.pg, stmt.mysql):
            assert '"created_at"' in sql
            assert "$9" in sql  # 渠道仍占 $6，turn_id/turn_seq $7/$8

    def test_upsert_statements_have_no_now_function(self):
        for name in ("upsert_user", "upsert_session_title"):
            stmt = STATEMENTS[name]
            for sql in (stmt.pg, stmt.mysql):
                assert "NOW()" not in sql, f"{name} 仍依赖 DB NOW()：{sql}"

    @pytest.mark.asyncio(loop_scope="session")
    async def test_insert_conversation_passes_created_at(self):
        db, fake = _db("mysql")
        fake.insert_id = 7
        await db.insert_conversation("u1", "s1", "user", "你好")
        _, args = fake.executed[-1]
        assert len(args) == 9
        assert isinstance(args[8], datetime)
        assert args[8].tzinfo is None  # MySQL naive 墙钟
        # 墙钟时间 = 配置时区当前时间（上海）
        expect = datetime.now(SHANGHAI).replace(tzinfo=None)
        assert abs((args[8] - expect).total_seconds()) < 5

    @pytest.mark.asyncio(loop_scope="session")
    async def test_insert_conversation_pg_passes_aware(self):
        db, fake = _db("postgres")
        await db.insert_conversation("u1", "s1", "user", "你好")
        _, args = fake.executed[-1]
        assert isinstance(args[8], datetime)
        assert args[8].tzinfo is not None
        assert args[8].utcoffset() == timedelta(hours=8)

    @pytest.mark.asyncio(loop_scope="session")
    async def test_upsert_user_passes_timestamps(self):
        db, fake = _db("postgres")
        await db.upsert_user("u1", "normal", "Alice", False)
        _, args = fake.executed[-1]
        assert len(args) == 6
        assert isinstance(args[4], datetime) and isinstance(args[5], datetime)
        assert args[4].tzinfo is not None

    @pytest.mark.asyncio(loop_scope="session")
    async def test_upsert_session_title_passes_timestamps(self):
        db, fake = _db("mysql")
        await db.upsert_session_title("u1", "s1", "标题")
        _, args = fake.executed[-1]
        assert len(args) == 6
        assert isinstance(args[4], datetime) and isinstance(args[5], datetime)
        assert args[4].tzinfo is None  # MySQL naive

    @pytest.mark.asyncio(loop_scope="session")
    async def test_update_session_state_no_now_and_passes_param(self):
        db, fake = _db("mysql")
        storage = PostgresStorage(db)
        await storage.update_session_state("u1", "default", "s1", "{}")
        sql, args = fake.executed[-1]
        assert "NOW()" not in sql
        assert "$2" in sql  # updated_at 占位
        assert isinstance(args[1], datetime)
        assert args[1].tzinfo is None

    @pytest.mark.asyncio(loop_scope="session")
    async def test_storage_upsert_session_uses_configured_tz(self):
        from core.storage_models import SessionConfig

        db, fake = _db("mysql")
        storage = PostgresStorage(db)
        await storage.upsert_session(
            "u1", "a1", SessionConfig(name="t"), state_json="", session_id="s1",
        )
        _, args = fake.executed[-1]
        created_at, updated_at = args[8], args[9]
        assert isinstance(created_at, datetime) and isinstance(updated_at, datetime)
        assert created_at.tzinfo is None
        expect = datetime.now(SHANGHAI).replace(tzinfo=None)
        assert abs((created_at - expect).total_seconds()) < 5


# ------------------------------------------------------------
# 4. 读路径：aware 时间序列化为配置时区，naive 原样
# ------------------------------------------------------------

@pytest.mark.asyncio(loop_scope="session")
class TestReadSerialization:
    async def test_conversation_history_converts_aware_to_config_tz(self):
        db, fake = _db("postgres")
        row = {
            "id": 1, "role": "user", "content": "hi", "metadata": None,
            "created_at": datetime(2026, 10, 12, 9, 0, 0, tzinfo=timezone.utc),
            "turn_id": None, "turn_seq": None,
        }

        async def _fetch(sql, *args):
            fake.executed.append((sql, args))
            return [row]

        fake.fetch = _fetch
        result = await db.get_conversation_history("u1", "s1")
        # UTC 09:00 → 上海 17:00（isoformat 带 +08:00，前端直读即本地墙钟）
        assert result["messages"][0]["created_at"] == "2026-10-12T17:00:00+08:00"

    async def test_conversation_history_keeps_naive_as_is(self):
        db, fake = _db("mysql")
        row = {
            "id": 1, "role": "user", "content": "hi", "metadata": None,
            "created_at": datetime(2026, 10, 12, 17, 0, 0, 123456),
            "turn_id": None, "turn_seq": None,
        }

        async def _fetch(sql, *args):
            fake.executed.append((sql, args))
            return [row]

        fake.fetch = _fetch
        result = await db.get_conversation_history("u1", "s1")
        # MySQL 已按配置时区写入 naive 墙钟 → 原样透出
        assert result["messages"][0]["created_at"] == "2026-10-12T17:00:00.123456"

    async def test_get_user_sessions_converts_aware(self):
        db, fake = _db("postgres")
        row = {
            "session_id": "s1",
            "created_at": datetime(2026, 10, 12, 9, 0, 0, tzinfo=timezone.utc),
            "last_active": datetime(2026, 10, 12, 10, 30, 0, tzinfo=timezone.utc),
            "message_count": 3,
            "custom_title": None,
            "first_message": "你好",
        }

        async def _fetch(sql, *args):
            fake.executed.append((sql, args))
            return [row]

        fake.fetch = _fetch
        sessions = await db.get_user_sessions("u1")
        assert sessions[0]["created_at"] == "2026-10-12T17:00:00+08:00"
        assert sessions[0]["last_active"] == "2026-10-12T18:30:00+08:00"
