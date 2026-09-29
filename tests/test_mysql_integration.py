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

import json
import os
import uuid

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
    # 显式别名：MySQL 8 服务端返回 TABLE_NAME（大写键），别名钉死小写键
    rows = await db_manager.fetch(
        "SELECT table_name AS table_name FROM information_schema.tables "
        "WHERE table_schema = DATABASE()"
    )
    existing = {r["table_name"] for r in rows}
    assert set(REQUIRED_TABLES) <= existing


async def test_select_1(db_manager):
    assert await db_manager.fetchval("SELECT 1") == 1


async def test_insert_returning_id_incrementing(db_manager):
    """自增表 ID 走 lastrowid（Task 9 之前仅用注册表路径）"""
    id1 = await db_manager.insert_returning_id(
        "insert_conversation", "it_user", "it_sess", "user", "你好", None, "web",
    )
    id2 = await db_manager.insert_returning_id(
        "insert_conversation", "it_user", "it_sess", "assistant", "嗨", None, "web",
    )
    assert isinstance(id1, int) and isinstance(id2, int)
    assert id2 > id1


# ============================================================
# Task 9 — 存储层端到端（接入语句注册表后的真实 MySQL 路径）
# ============================================================

async def test_storage_agent_roundtrip(db_manager):
    """upsert_agent → get_agent 端到端（客户端 PK 路径）"""
    from core.storage import PostgresStorage
    from core.storage_models import AgentData, AgentRecord

    storage = PostgresStorage(db_manager)
    rec = AgentRecord(
        user_id="it_user", data=AgentData(name="集成测试Agent"),
    )
    rec.id = "it-agent-001"
    stored_id = await storage.upsert_agent("it_user", rec)
    assert stored_id == rec.id
    fetched = await storage.get_agent("it_user", rec.id)
    assert fetched is not None
    assert fetched.data.name == "集成测试Agent"


async def test_storage_session_title_merge(db_manager):
    """upsert_session_title 写入后可读回；标题行 state_json TEXT NULL → _row_to_session 读作 \"\""""
    from core.storage import PostgresStorage

    storage = PostgresStorage(db_manager)
    await db_manager.upsert_session_title("it_user", "it-sess-1", "集成标题")
    title = await db_manager.get_session_title("it_user", "it-sess-1")
    assert title == "集成标题"
    # 整行读回，验证 TEXT NULL 兼容
    rows = await db_manager.fetch(
        "SELECT * FROM sessions WHERE id = %s", "it-sess-1",
    )
    record = storage._row_to_session(rows[0])
    assert record.state_json == ""
    assert record.config.name  # SessionConfig 默认 name 字段仍在


async def test_storage_mcp_name_conflict_returns_same_id(db_manager):
    """(user_id, name) 冲突 upsert：MySQL 走回查 id，两次 upsert 返回同一条"""
    from core.storage import PostgresStorage
    from core.storage_models import MCPRecord

    storage = PostgresStorage(db_manager)
    rec = MCPRecord(user_id="it_user", name="it-mcp", transport="stdio")
    id1 = await storage.upsert_mcp("it_user", rec)
    rec2 = MCPRecord(user_id="it_user", name="it-mcp", transport="stdio")
    id2 = await storage.upsert_mcp("it_user", rec2)
    assert id1 == id2
    all_mcps = await storage.list_mcps("it_user")
    assert len([m for m in all_mcps if m.name == "it-mcp"]) == 1


async def test_storage_message_roundtrip(db_manager):
    """upsert_message 插入返回自增 id；内容/元数据读回一致"""
    from core.storage import PostgresStorage

    storage = PostgresStorage(db_manager)
    msg_id = uuid.uuid4().hex
    id1 = await storage.upsert_message(
        user_id="it_user", session_id="it-sess-msg", msg_id=msg_id,
        role="user", content="集成消息", metadata={"n": 1},
    )
    assert isinstance(id1, int)
    rows = await db_manager.fetch(
        "SELECT content, metadata FROM messages WHERE msg_id = %s", msg_id,
    )
    assert rows[0]["content"] == "集成消息"
    assert json.loads(rows[0]["metadata"]) == {"n": 1}
    # 同 msg_id 再写 → 去重走 UPDATE 分支，不产生新行
    await storage.upsert_message(
        user_id="it_user", session_id="it-sess-msg", msg_id=msg_id,
        role="user", content="集成消息-更新", metadata={"n": 2},
    )
    again = await db_manager.fetchval(
        "SELECT content FROM messages WHERE msg_id = %s", msg_id,
    )
    assert again == "集成消息-更新"


async def test_get_user_sessions_with_title(db_manager):
    """get_user_sessions：JSON_UNQUOTE 取自定义标题 + message_count 聚合"""
    # 清理历史累积（app_db 复用库 + 本测试可重跑，message_count 断言需确定性）
    await db_manager.execute(
        "DELETE FROM conversations WHERE user_id = 'it_user2' AND session_id = 'it-sess-us'",
    )
    await db_manager.insert_conversation(
        "it_user2", "it-sess-us", "user", "第一条消息", None,
    )
    await db_manager.insert_conversation(
        "it_user2", "it-sess-us", "assistant", "第一条回复", None,
    )
    await db_manager.upsert_session_title("it_user2", "it-sess-us", "会话标题X")
    sessions = await db_manager.get_user_sessions("it_user2", limit=10)
    target = [s for s in sessions if s["session_id"] == "it-sess-us"]
    assert target and target[0]["title"] == "会话标题X"
    assert target[0]["message_count"] == 2


# ============================================================
# Task 13 — 消息渠道 channel（D11）
# ============================================================

async def test_channel_column_on_three_tables(db_manager):
    """Task 13：三表均有 channel 列，默认值 'web'（信息_schema 中字符串默认值带引号，宽松断言）"""
    rows = await db_manager.fetch(
        # 显式别名同 test_tables_created：MySQL 8 服务端返回大写键，别名钉死小写键
        "SELECT table_name AS table_name, column_default AS column_default "
        "FROM information_schema.columns "
        "WHERE table_schema = DATABASE() AND column_name = 'channel'"
    )
    by_table = {r["table_name"]: str(r["column_default"]) for r in rows}
    for table in ("conversations", "messages", "sessions"):
        assert table in by_table, f"{table} 缺 channel 列（ALTER 未生效？）"
        assert "web" in by_table[table]


async def test_insert_conversation_channel_roundtrip(db_manager):
    """显式 channel 落库可读回；方法默认值写 'web'"""
    id1 = await db_manager.insert_conversation(
        "it_user4", "it-sess-ch", "user", "渠道消息", None, "feishu",
    )
    assert isinstance(id1, int)
    row = await db_manager.fetchrow(
        "SELECT channel FROM conversations WHERE id = %s", id1,
    )
    assert row["channel"] == "feishu"
    # 未传 channel → 方法默认值 'web'
    id2 = await db_manager.insert_conversation(
        "it_user4", "it-sess-ch", "assistant", "默认渠道",
    )
    row2 = await db_manager.fetchrow(
        "SELECT channel FROM conversations WHERE id = %s", id2,
    )
    assert row2["channel"] == "web"
