# -*- coding: utf-8 -*-

"""DDL 双方言测试 — 建表对齐、MySQL 无 PG 语法"""

from __future__ import annotations

import re

import pytest

from core.db.ddl import (
    DDL_MYSQL,
    DDL_POSTGRES,
    MYSQL_IDEMPOTENT_ERRORS,
    REQUIRED_TABLES,
    get_ddl,
)


def _created_tables(statements: list[str]) -> set[str]:
    tables = set()
    for stmt in statements:
        m = re.search(r"CREATE TABLE IF NOT EXISTS (\w+)", stmt)
        if m:
            tables.add(m.group(1))
    return tables


class TestParity:
    def test_pg_mysql_create_same_tables(self):
        assert _created_tables(DDL_POSTGRES) == _created_tables(DDL_MYSQL)

    def test_required_tables_match_ddl(self):
        assert _created_tables(DDL_POSTGRES) == set(REQUIRED_TABLES)
        assert _created_tables(DDL_MYSQL) == set(REQUIRED_TABLES)

    def test_pg_ddl_statement_count(self):
        """DDL_POSTGRES = 原样迁出 29 条 + Task 13 追加 3 条 channel ALTER = 32 条"""
        assert len(DDL_POSTGRES) == 32


class TestMysqlDdlHasNoPgSyntax:
    @pytest.mark.parametrize(
        "token",
        [
            "JSONB",
            "TIMESTAMPTZ",
            "BIGSERIAL",
            "RETURNING",
            "ON CONFLICT",
            "CREATE INDEX IF NOT EXISTS",
            "ADD COLUMN IF NOT EXISTS",
            "ALTER COLUMN",
            "BOOLEAN",
            "TRUE",
            "DEFAULT ''",  # TEXT 列不允许 DEFAULT（state_json 特判见下）
        ],
    )
    def test_token_absent(self, token):
        for stmt in DDL_MYSQL:
            assert token not in stmt, f"MySQL DDL 含 PG/非法语法 {token!r}:\n{stmt}"

    def test_state_json_is_text_null(self):
        joined = "\n".join(DDL_MYSQL)
        assert "state_json" in joined and "TEXT NULL" in joined


class TestGetDdl:
    def test_postgres(self):
        assert get_ddl("postgres") is DDL_POSTGRES

    def test_mysql(self):
        assert get_ddl("mysql") is DDL_MYSQL

    def test_unknown_raises(self):
        with pytest.raises(ValueError, match="未知方言"):
            get_ddl("sqlite")

    def test_mysql_idempotent_errors(self):
        assert MYSQL_IDEMPOTENT_ERRORS == {1050, 1060, 1061}
