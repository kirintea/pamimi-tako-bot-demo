# -*- coding: utf-8 -*-

"""运营统计 SQL 注册表 — 存在性与方言约束"""

from __future__ import annotations

import re

import pytest

from core.db.statements import STATEMENTS

STATS_NAMES = [
    "stats_conv_series",
    "stats_conv_total",
    "stats_active_series",
    "stats_active_total",
    "stats_new_series",
    "stats_new_total",
    "stats_channel_conv",
    "stats_channel_active",
    "stats_channel_new",
    "stats_channel_options",
    "stats_bucket_channel_conv",
    "stats_bucket_channel_active",
    "stats_bucket_channel_new",
]


def test_all_stats_names_registered():
    assert set(STATS_NAMES) <= set(STATEMENTS)


@pytest.mark.parametrize("name", STATS_NAMES)
def test_both_variants_present_nonempty(name):
    stmt = STATEMENTS[name]
    assert stmt.pg.strip(), f"{name}: pg 变体为空"
    assert stmt.mysql.strip(), f"{name}: mysql 变体为空"


@pytest.mark.parametrize("name", STATS_NAMES)
def test_mysql_variant_has_no_pg_only_syntax(name):
    """mysql 变体不得出现 PG 专有语法（translate_identifiers 只管标识符/占位符）"""
    mysql = STATEMENTS[name].mysql
    for token in ("RETURNING", "ON CONFLICT", "->>", "::jsonb", "EXCLUDED",
                  "date_trunc", "AT TIME ZONE", "::text"):
        assert token not in mysql, f"{name}: mysql 变体含 PG 专有语法 {token!r}"


@pytest.mark.parametrize("name", STATS_NAMES)
def test_pg_variant_uses_quoted_identifiers(name):
    """PG 变体的列名走双引号（MySQL 侧由 translate_identifiers 转反引号）"""
    assert '"' in STATEMENTS[name].pg


@pytest.mark.parametrize(
    "name",
    [n for n in STATS_NAMES if n.endswith("_series") or n.startswith("stats_bucket_channel_")],
)
def test_series_variants_take_granularity_and_tz(name):
    """分桶系列：pg 用 $1=粒度 $2=时区；mysql 不用 $2（naive 墙钟无需转换）
    bucket_channel_* 同为 series 式占位符，一并覆盖"""
    stmt = STATEMENTS[name]
    pg_ph = set(re.findall(r"\$(\d+)", stmt.pg))
    mysql_ph = set(re.findall(r"\$(\d+)", stmt.mysql))
    assert {"1", "2"} <= pg_ph
    assert "1" in mysql_ph and "2" not in mysql_ph


@pytest.mark.parametrize("name", STATS_NAMES)
def test_mysql_placeholder_indices_within_arg_span(name):
    """mysql 变体引用的 $N 必须落在 pg 变体的参数跨度内（共用 args 元组）"""
    pg_ph = {int(m) for m in re.findall(r"\$(\d+)", STATEMENTS[name].pg)}
    mysql_ph = {int(m) for m in re.findall(r"\$(\d+)", STATEMENTS[name].mysql)}
    assert mysql_ph <= pg_ph, f"{name}: mysql 引用了 pg 未定义的参数 {mysql_ph - pg_ph}"


@pytest.mark.parametrize("name", STATS_NAMES)
def test_mysql_translated_sql_is_pyformat_safe(name):
    """走完 MySQLBackend 的翻译管线后，% 只能以 %s 或 %% 形式出现

    pymysql 的 cur.execute(sql, args) 会做 `query % args`，任何未转义的
    字面 %（如 DATE_FORMAT 的 '%Y-%m-01'）都会抛
    "ValueError: unsupported format character"。
    """
    from core.db.dialect import translate_identifiers, translate_placeholders

    stmt = STATEMENTS[name]
    sql, _ = translate_placeholders(stmt.mysql, (None,) * 32)
    sql = translate_identifiers(sql)
    i = 0
    while i < len(sql):
        if sql[i] == "%":
            nxt = sql[i + 1 : i + 2]
            assert nxt in ("s", "%"), f"{name}: 位置 {i} 的 %{nxt!r} 未转义"
            i += 2
        else:
            i += 1
