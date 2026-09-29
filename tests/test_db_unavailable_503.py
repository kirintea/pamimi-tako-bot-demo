# -*- coding: utf-8 -*-

"""DatabaseUnavailableError → HTTP 503 处理器测试"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from core.db.base import DatabaseUnavailableError
from server import _database_unavailable_handler


def _make_app() -> FastAPI:
    app = FastAPI()
    app.add_exception_handler(DatabaseUnavailableError, _database_unavailable_handler)

    @app.get("/boom")
    async def boom():
        raise DatabaseUnavailableError("数据库未初始化")

    return app


def test_route_raising_unavailable_returns_503():
    client = TestClient(_make_app())
    resp = client.get("/boom")
    assert resp.status_code == 503
    body = resp.json()
    assert "数据库不可用" in body["detail"]
    assert "数据库未初始化" in body["detail"]


def test_handler_returns_json_content_type():
    client = TestClient(_make_app())
    resp = client.get("/boom")
    assert resp.headers["content-type"].startswith("application/json")
