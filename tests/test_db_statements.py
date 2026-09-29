# -*- coding: utf-8 -*-

"""语句注册表测试 — 双方言一致性与 MySQL 变体无 PG 语法"""

from __future__ import annotations

import re

import pytest

from core.db.statements import STATEMENTS

REQUIRED_NAMES = [
    "upsert_agent",
    "upsert_session",
    "upsert_mcp",
    "upsert_skill",
    "upsert_schedule",
    "insert_message",
    "insert_conversation",
    "upsert_session_title",
    "get_session_title",
    "get_user_sessions",
]


def _placeholder_set(sql: str) -> set[str]:
    return set(re.findall(r"\$(\d+)", sql))


def test_required_names_present():
    assert set(REQUIRED_NAMES) <= set(STATEMENTS)


@pytest.mark.parametrize("name", REQUIRED_NAMES)
def test_pg_mysql_placeholder_parity(name):
    """pg/mysql 变体的 $N 编号集合必须一致（参数顺序共用）"""
    stmt = STATEMENTS[name]
    assert _placeholder_set(stmt.pg) == _placeholder_set(stmt.mysql), (
        f"{name}: $N 集合不一致 pg={_placeholder_set(stmt.pg)} "
        f"mysql={_placeholder_set(stmt.mysql)}"
    )


@pytest.mark.parametrize("name", REQUIRED_NAMES)
def test_mysql_variant_has_no_pg_syntax(name):
    stmt = STATEMENTS[name]
    for token in ("RETURNING", "ON CONFLICT", "->>", "::jsonb", "EXCLUDED"):
        assert token not in stmt.mysql, f"{name}: mysql 变体含 PG 语法 {token!r}"


@pytest.mark.parametrize("name", REQUIRED_NAMES)
def test_pg_upsert_variant_has_returning_or_plain_insert(name):
    stmt = STATEMENTS[name]
    if name.startswith("upsert") or name.startswith("insert"):
        # 写语句：pg 侧要么 RETURNING 要么是纯 INSERT（由调用方按方言取 ID）
        assert "ON CONFLICT" in stmt.pg or "INSERT" in stmt.pg


def test_mcp_id_lookup_args():
    stmt = STATEMENTS["upsert_mcp"]
    # 参数顺序: $1=id $2=user_id $3=name → 回查 (user_id, name) = (2, 3)
    assert stmt.mysql_id_sql is not None
    assert stmt.mysql_id_args == (2, 3)
    assert "WHERE user_id = $1 AND name = $2" in stmt.mysql_id_sql


def test_skill_id_lookup_args():
    stmt = STATEMENTS["upsert_skill"]
    # 参数顺序: $1=id $2=user_id $3=name → 同上
    assert stmt.mysql_id_sql is not None
    assert stmt.mysql_id_args == (2, 3)


def test_auto_increment_statements_have_no_id_sql():
    """自增表走 lastrowid，不需要回查"""
    for name in ("insert_conversation", "insert_message"):
        assert STATEMENTS[name].mysql_id_sql is None
    for name in ("upsert_agent", "upsert_session", "upsert_schedule"):
        assert STATEMENTS[name].mysql_id_sql is None
