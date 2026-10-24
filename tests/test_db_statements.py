# -*- coding: utf-8 -*-

"""语句注册表测试 — 双方言一致性与 MySQL 变体无 PG 语法"""

from __future__ import annotations

import re

import pytest

from core.db.statements import STATEMENTS

REQUIRED_NAMES = [
    "upsert_session",
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


def test_auto_increment_statements_have_no_id_sql():
    """自增表走 lastrowid，不需要回查"""
    for name in ("insert_conversation",):
        assert STATEMENTS[name].mysql_id_sql is None
    for name in ("upsert_session",):
        assert STATEMENTS[name].mysql_id_sql is None


# ON DUPLICATE KEY UPDATE 中未限定的列引用（LHS 赋值目标除外）。
# LHS 语法上必须裸列名（"x" = ...），故排除后随 = 的引用。
_BARE_ODKU_REF_RE = re.compile(r'(?<![.\w])"([A-Za-z_]\w*)"(?!\s*=)')


def test_mysql_odku_old_row_refs_are_qualified():
    """AS new_values 行别名下，ODKU 旧值引用必须表限定

    MySQL 8.0.19+ 行别名（VALUES (...) AS new_values）使 ODKU 中的裸列名
    同时指向表列与别名行，报 1052 ER_NON_UNIQ_ERROR
    "Column 'x' in field list is ambiguous"（日志实锤：
    upsert_user 的 display_name / upsert_session_title 的 config）。
    规范：新值写 new_values."col"，旧值写 表名."col"。
    """
    for name, stmt in STATEMENTS.items():
        if "AS new_values" not in stmt.mysql:
            continue
        section = stmt.mysql.split("ON DUPLICATE KEY UPDATE", 1)[1]
        bare = _BARE_ODKU_REF_RE.findall(section)
        assert not bare, (
            f"{name}: mysql 变体 ODKU 存在未限定列引用 {bare}"
            f"（行别名下歧义 → MySQL 1052，须用 new_values.\"col\" / 表名.\"col\"）"
        )
