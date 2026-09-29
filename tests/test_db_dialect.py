# -*- coding: utf-8 -*-

"""SQL 方言工具测试 — 占位符翻译 / 状态串 / 后端解析"""

from __future__ import annotations

import pytest

from core.db.dialect import format_status, resolve_backend, translate_placeholders


class TestTranslatePlaceholders:
    def test_reorder(self):
        sql, args = translate_placeholders(
            "SELECT * FROM t WHERE a = $2 AND b = $1", (10, 20)
        )
        assert sql == "SELECT * FROM t WHERE a = %s AND b = %s"
        assert args == (20, 10)

    def test_duplicate_placeholder(self):
        """同一 $N 重复出现 → 参数按出现顺序重复"""
        sql, args = translate_placeholders("SELECT $1, $1, $2", ("x", "y"))
        assert sql == "SELECT %s, %s, %s"
        assert args == ("x", "x", "y")

    def test_passthrough_when_no_placeholder(self):
        sql, args = translate_placeholders("SELECT 1", (1, 2))
        assert sql == "SELECT 1"
        assert args == (1, 2)

    def test_json_path_untouched(self):
        """JSON 路径 $.title 中的 $ 后面不是数字，不参与翻译"""
        sql, args = translate_placeholders(
            "SELECT JSON_UNQUOTE(JSON_EXTRACT(config, '$.title')) FROM t WHERE id = $1",
            ("s1",),
        )
        assert "'$.title'" in sql
        assert sql.count("%s") == 1
        assert args == ("s1",)

    def test_multi_digit_placeholder(self):
        sql, args = translate_placeholders("INSERT INTO t VALUES ($10, $1)", tuple(range(10)))
        assert sql == "INSERT INTO t VALUES (%s, %s)"
        assert args == (9, 0)


class TestFormatStatus:
    def test_insert(self):
        assert format_status("INSERT", 1) == "INSERT 0 1"

    def test_update(self):
        assert format_status("UPDATE", 3) == "UPDATE 3"

    def test_delete(self):
        assert format_status("DELETE", 1) == "DELETE 1"


class TestResolveBackend:
    @pytest.mark.parametrize(
        "url,expected",
        [
            ("postgresql://user:pw@localhost:5432/db", "postgres"),
            ("postgres://user:pw@localhost:5432/db", "postgres"),
            ("mysql://user:pw@localhost:3306/db", "mysql"),
            ("mariadb://user:pw@localhost:3306/db", "mysql"),
        ],
    )
    def test_from_url_scheme(self, url, expected):
        assert resolve_backend(url, "auto") == expected

    def test_explicit_wins_over_scheme(self):
        assert resolve_backend("postgresql://x/y", "mysql") == "mysql"

    def test_unknown_scheme_raises(self):
        with pytest.raises(ValueError, match="无法从 URL 解析"):
            resolve_backend("sqlite:///foo.db", "auto")

    def test_empty_url_raises_when_auto(self):
        with pytest.raises(ValueError):
            resolve_backend("", "auto")
