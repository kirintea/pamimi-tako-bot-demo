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
        None, None,
    )
    id2 = await db_manager.insert_returning_id(
        "insert_conversation", "it_user", "it_sess", "assistant", "嗨", None, "web",
        None, None,
    )
    assert isinstance(id1, int) and isinstance(id2, int)
    assert id2 > id1


# ============================================================
# Task 9 — 存储层端到端（接入语句注册表后的真实 MySQL 路径）
# ============================================================

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


async def test_get_user_sessions_with_title(db_manager):
    """get_user_sessions：JSON_UNQUOTE 取自定义标题 + message_count 聚合

    message_count 只统计 role='user' 行（PG COUNT FILTER / MySQL SUM(CASE) 双方言一致）。
    """
    # 清理历史累积（dmx_agent_db 复用库 + 本测试可重跑，message_count 断言需确定性）
    await db_manager.execute(
        "DELETE FROM conversations WHERE user_id = 'it_user2' AND session_id = 'it-sess-us'",
    )
    await db_manager.insert_conversation(
        "it_user2", "it-sess-us", "user", "第一条消息", None,
    )
    await db_manager.insert_conversation(
        "it_user2", "it-sess-us", "assistant", "第一条回复", None,
    )
    await db_manager.insert_conversation(
        "it_user2", "it-sess-us", "user", "第二条消息", None,
    )
    await db_manager.upsert_session_title("it_user2", "it-sess-us", "会话标题X")
    sessions = await db_manager.get_user_sessions("it_user2", limit=10)
    target = [s for s in sessions if s["session_id"] == "it-sess-us"]
    assert target and target[0]["title"] == "会话标题X"
    # 2 条 user 消息（assistant 行不计入）
    assert target[0]["message_count"] == 2


# ============================================================
# Task 13 — 消息渠道 channel（D11）
# ============================================================

async def test_channel_column_on_tables(db_manager):
    """Task 13：conversations/sessions 均有 channel 列，默认值 'web'（信息_schema 中字符串默认值带引号，宽松断言）"""
    rows = await db_manager.fetch(
        # 显式别名同 test_tables_created：MySQL 8 服务端返回大写键，别名钉死小写键
        "SELECT table_name AS table_name, column_default AS column_default "
        "FROM information_schema.columns "
        "WHERE table_schema = DATABASE() AND column_name = 'channel'"
    )
    by_table = {r["table_name"]: str(r["column_default"]) for r in rows}
    for table in ("conversations", "sessions"):
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


# ============================================================
# 回归 — MySQL 1052：ODKU 行别名下旧值引用歧义（2026-10-09 线上故障）
# ============================================================

async def test_upsert_user_display_name_null_keeps_old(db_manager):
    """upsert_user 冲突更新：display_name=NULL 保留旧值（COALESCE 旧值分支）

    修复前 AS new_values 行别名下裸列名歧义 → 1052
    "Column 'display_name' in field list is ambiguous"（线上 warning 吞掉）。
    """
    uid = "it_user_1052"
    await db_manager.upsert_user(uid, "normal", "Alice", False)
    # NULL 不覆盖 → 保留 'Alice'；role/is_root 照常更新
    await db_manager.upsert_user(uid, "root", None, True)
    row = await db_manager.fetchrow(
        'SELECT "role", "display_name", "is_root" FROM users WHERE "user_id" = $1', uid,
    )
    assert row["display_name"] == "Alice"
    assert row["role"] == "root"
    # 非 NULL 照常覆盖
    await db_manager.upsert_user(uid, "root", "Alicia", True)
    row2 = await db_manager.fetchrow(
        'SELECT "display_name" FROM users WHERE "user_id" = $1', uid,
    )
    assert row2["display_name"] == "Alicia"


async def test_upsert_user_returns_id_on_conflict(db_manager):
    """upsert_user 冲突更新时回查 user_id（mysql_id_sql 路径）"""
    uid = "it_user_1052_id"
    assert await db_manager.upsert_user(uid, "normal", None, False) == uid
    assert await db_manager.upsert_user(uid, "normal", None, False) == uid


async def test_upsert_session_title_conflict_updates(db_manager):
    """upsert_session_title 二次写入走冲突更新（修复前 1052 'config' 歧义）"""
    await db_manager.upsert_session_title("it_user", "it-sess-1052", "标题一")
    await db_manager.upsert_session_title("it_user", "it-sess-1052", "标题二")
    title = await db_manager.get_session_title("it_user", "it-sess-1052")
    assert title == "标题二"
    row = await db_manager.fetchrow(
        'SELECT "config" FROM sessions WHERE "id" = $1', "it-sess-1052",
    )
    cfg = row["config"]
    if isinstance(cfg, str):
        cfg = json.loads(cfg)
    # JSON_MERGE_PATCH(旧 config, 新 config)：title 取新值
    assert cfg["title"] == "标题二"


async def test_upsert_session_fork_fields_preserved(db_manager):
    """upsert_session 冲突更新：parent/depth 传空时保留原有 fork 血缘

    修复前 AS new_values 行别名下 "parent_session_id"/"depth" 裸引用歧义 → 1052。
    """
    from core.storage import PostgresStorage
    from core.storage_models import SessionConfig

    storage = PostgresStorage(db_manager)
    cfg = SessionConfig(name="1052-fork")
    rec = await storage.upsert_session(
        "it_user_1052s", "default", cfg,
        state_json="{}", parent_session_id="it-parent-1052", depth=3,
    )
    # 更新不带 fork 信息（parent=None, depth=None→0）→ 血缘保留
    await storage.upsert_session(
        "it_user_1052s", "default", cfg,
        state_json="{}", session_id=rec.id,
    )
    row = await db_manager.fetchrow(
        'SELECT "parent_session_id", "depth" FROM sessions WHERE "id" = $1', rec.id,
    )
    assert row["parent_session_id"] == "it-parent-1052"
    assert row["depth"] == 3


async def test_upsert_user_display_name_empty_string_keeps_old(db_manager):
    """upsert_user 冲突更新：display_name='' 归一为 None → 保留旧值"""
    uid = "it_user_empty_name"
    await db_manager.upsert_user(uid, "normal", "Alice", False)
    await db_manager.upsert_user(uid, "normal", "", False)
    row = await db_manager.fetchrow(
        'SELECT "display_name" FROM users WHERE "user_id" = $1', uid,
    )
    assert row["display_name"] == "Alice"
    await db_manager.upsert_user(uid, "normal", "   ", False)
    row2 = await db_manager.fetchrow(
        'SELECT "display_name" FROM users WHERE "user_id" = $1', uid,
    )
    assert row2["display_name"] == "Alice"


async def test_get_all_sessions_returns_display_name_and_channel(db_manager):
    """get_all_sessions 返回 display_name + channel 字段"""
    await db_manager.upsert_user("admin_user_1", "normal", "张三", False)
    await db_manager.insert_conversation(
        user_id="admin_user_1",
        session_id="admin-sess-001",
        role="user",
        content="你好",
        metadata=None,
        channel="wecom",
    )
    sessions = await db_manager.get_all_sessions(limit=10)
    assert len(sessions) >= 1
    target = next((s for s in sessions if s["session_id"] == "admin-sess-001"), None)
    assert target is not None
    assert target["display_name"] == "张三"
    assert target["channel"] == "wecom"
