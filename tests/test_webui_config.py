# -*- coding: utf-8 -*-

"""GET /webui-config 下发 auth_required（D9 方案 A）

- AUTH 关闭 → auth_required: false，既有 rag_url 字段不丢
- AUTH 开启（env / 配置双源）→ auth_required: true（setup 页 API Key 必填依据）
"""

from __future__ import annotations

from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.rag_proxy import router


def _client(config=None) -> TestClient:
    app = FastAPI()
    app.include_router(router)
    app.state.config = config
    return TestClient(app)


def test_webui_config_exposes_auth_required(monkeypatch):
    monkeypatch.delenv("AUTH_REQUIRED", raising=False)
    r = _client().get("/webui-config")
    assert r.status_code == 200
    data = r.json()
    assert data["auth_required"] is False
    assert "rag_url" in data  # 既有字段不丢


def test_webui_config_auth_required_true_via_env(monkeypatch):
    monkeypatch.setenv("AUTH_REQUIRED", "true")
    r = _client().get("/webui-config")
    assert r.status_code == 200
    assert r.json()["auth_required"] is True


def test_webui_config_auth_required_true_via_config(monkeypatch):
    monkeypatch.delenv("AUTH_REQUIRED", raising=False)
    config = SimpleNamespace(auth=SimpleNamespace(required=True, api_key=""))
    r = _client(config=config).get("/webui-config")
    assert r.status_code == 200
    assert r.json()["auth_required"] is True
