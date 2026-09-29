# -*- coding: utf-8 -*-

"""DatabaseConfig 策略字段测试"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from core.config.schemas import DatabaseConfig


class TestDefaults:
    def test_defaults(self):
        cfg = DatabaseConfig()
        assert cfg.backend == "auto"
        assert cfg.auto_create_tables is True
        assert cfg.verify_tables is True

    def test_legacy_yaml_keys_still_valid(self):
        """旧配置（只有 url/pool_size）必须继续可解析"""
        cfg = DatabaseConfig(url="postgresql://u:p@localhost/db", pool_size=5)
        assert cfg.backend == "auto"
        assert cfg.auto_create_tables is True


class TestValidation:
    @pytest.mark.parametrize("bad", ["sqlite", "POSTGRES", "pg"])
    def test_invalid_backend(self, bad):
        with pytest.raises(ValidationError):
            DatabaseConfig(backend=bad)

    def test_valid_combination(self):
        cfg = DatabaseConfig(
            backend="mysql",
            auto_create_tables=False,
            verify_tables=False,
        )
        assert cfg.backend == "mysql"
        assert cfg.auto_create_tables is False
        assert cfg.verify_tables is False
