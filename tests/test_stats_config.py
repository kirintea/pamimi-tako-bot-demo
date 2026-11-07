# -*- coding: utf-8 -*-

"""stats.backend 配置 schema 测试"""

import pytest
from pydantic import ValidationError

from core.config.schemas import AppConfig, StatsConfig


def test_stats_config_default_facts():
    assert StatsConfig().backend == "facts"


def test_stats_config_rejects_unknown_backend():
    with pytest.raises(ValidationError):
        StatsConfig(backend="nope")


def test_app_config_has_stats_default():
    cfg = AppConfig(
        otel={"endpoint": "http://x:4317", "environment": "dev"},
        llm={"api_key": "k", "base_url": "http://x/v1", "model": "m"},
    )
    assert cfg.stats.backend == "facts"
