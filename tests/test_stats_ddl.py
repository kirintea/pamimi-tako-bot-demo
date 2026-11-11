# -*- coding: utf-8 -*-

"""stats_daily 事实表 DDL 测试"""

from core.db.ddl import DDL_MYSQL, DDL_POSTGRES, REQUIRED_TABLES


def test_stats_daily_in_required_tables():
    assert "stats_daily" in REQUIRED_TABLES


def test_pg_has_stats_daily_create():
    joined = "\n".join(DDL_POSTGRES)
    assert 'CREATE TABLE IF NOT EXISTS stats_daily' in joined
    assert 'CONSTRAINT "uq_stats_daily" UNIQUE' in joined


def test_mysql_has_stats_daily_create():
    joined = "\n".join(DDL_MYSQL)
    assert 'CREATE TABLE IF NOT EXISTS stats_daily' in joined
    assert 'CONSTRAINT "uq_stats_daily" UNIQUE' in joined
