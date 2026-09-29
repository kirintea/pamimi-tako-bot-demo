# -*- coding: utf-8 -*-

"""/health 数据库检查测试 — ok / not_configured / 运行期 error → 503"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from api.chat import health
from core.config.schemas import DatabaseConfig
from core.database import DatabaseManager
from tests.db_fakes import FakeBackend

pytestmark = pytest.mark.asyncio(loop_scope="session")


def _patch_redis_ok(monkeypatch):
    import redis
    monkeypatch.setattr(
        redis, "from_url",
        lambda url, **kw: SimpleNamespace(ping=lambda: True),
    )


def _config() -> SimpleNamespace:
    return SimpleNamespace(
        redis=SimpleNamespace(url="redis://localhost:6379/0"),
    )


def _fake_request(db, config) -> SimpleNamespace:
    state = SimpleNamespace(
        session_manager=SimpleNamespace(active_count=0),
        config=config,
        database_manager=db,
    )
    return SimpleNamespace(app=SimpleNamespace(state=state))


async def test_not_configured_returns_200(monkeypatch):
    """URL 未配置（有意跳过初始化）→ not_configured + 200"""
    _patch_redis_ok(monkeypatch)
    db = DatabaseManager(DatabaseConfig(url=""))
    resp = await health(_fake_request(db, _config()))
    body = json.loads(resp.body)
    assert resp.status_code == 200
    assert body["checks"]["database"] == "not_configured"


async def test_initialized_ok(monkeypatch):
    _patch_redis_ok(monkeypatch)
    db = DatabaseManager(DatabaseConfig(url="postgresql://x/y"))
    fake = FakeBackend()
    fake.fetchval_result = 1
    db._backend = fake
    resp = await health(_fake_request(db, _config()))
    body = json.loads(resp.body)
    assert resp.status_code == 200
    assert body["checks"]["database"] == "ok"


async def test_runtime_db_error_returns_503(monkeypatch):
    """运行期 DB 故障（启动成功后）→ 进程不退出，/health 置 503（监控可见）"""
    _patch_redis_ok(monkeypatch)
    db = DatabaseManager(DatabaseConfig(url="postgresql://x/y"))
    fake = FakeBackend()

    async def _boom(sql, *args):
        raise RuntimeError("connection reset by peer")

    fake.fetchval = _boom
    db._backend = fake
    resp = await health(_fake_request(db, _config()))
    body = json.loads(resp.body)
    assert resp.status_code == 503
    assert body["checks"]["database"].startswith("error:")


async def test_no_database_manager_defaults_not_configured(monkeypatch):
    """app.state 上完全没有 database_manager → not_configured，不 500"""
    _patch_redis_ok(monkeypatch)
    resp = await health(_fake_request(None, _config()))
    body = json.loads(resp.body)
    assert resp.status_code == 200
    assert body["checks"]["database"] == "not_configured"
