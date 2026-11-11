# -*- coding: utf-8 -*-
"""CORS 中间件测试（父调研 §7 缺口 1）

背景：全仓原无 CORSMiddleware，浏览器类第三方应用（跨域页面）的 fetch/SSE
全部被浏览器拦。子计划：.draft/2026-11-10-cors-middleware.md（父调研 §7 缺口 1）。

覆盖：
  - 配置层（Task 1）：默认关闭；credentials+* 拒绝；enabled+空 origins 拒绝
  - 行为层（Task 2 追加）：简单请求回显 / 非白名单源无头 / 预检短路 auth /
    401 响应带 ACAO / 通配源
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

# 注意：不放进 try/skip 块——CORSConfig 尚不存在时要以 collection error 失败（TDD 红灯），
# 而不是被 skip 掉。server 导入才走项目惯例的缺依赖跳过。
from core.config.schemas import CORSConfig

try:
    from core.config import ConfigManager
    from server import create_app
    _HAVE_SERVER = True
    _IMPORT_ERROR = None
except Exception as exc:  # pragma: no cover
    _HAVE_SERVER = False
    _IMPORT_ERROR = exc


@pytest.fixture(autouse=True)
def _require_server():
    if not _HAVE_SERVER:
        pytest.skip(f"server.py 不可导入, 跳过 CORS 测试: {_IMPORT_ERROR}")


# ----------------------------------------------------------------
# 配置层（Task 1）
# ----------------------------------------------------------------

def test_cors_defaults_disabled_and_safe():
    """默认关闭 + 保守默认值：不配 = 行为与旧版完全一致"""
    c = CORSConfig()
    assert c.enabled is False
    assert c.allow_origins == []
    assert c.allow_credentials is False
    assert c.allow_headers == ["*"]
    assert c.allow_methods == ["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"]


def test_cors_rejects_credentials_with_wildcard_origin():
    """credentials + * 是浏览器禁用的组合，等效对任意源开放凭据 → 启动即拒"""
    with pytest.raises(ValidationError, match="allow_credentials"):
        CORSConfig(enabled=True, allow_origins=["*"], allow_credentials=True)


def test_cors_rejects_enabled_without_origins():
    """开了 CORS 却没配任何来源 = 白忙配置 → 启动即拒"""
    with pytest.raises(ValidationError, match="allow_origins"):
        CORSConfig(enabled=True, allow_origins=[])


def test_cors_wildcard_without_credentials_ok():
    c = CORSConfig(enabled=True, allow_origins=["*"])
    assert c.enabled is True


# ----------------------------------------------------------------
# 行为层（Task 2）
# ----------------------------------------------------------------

ORIGIN = "https://partner-app.example.com"


@pytest.fixture
def make_client(monkeypatch):
    """构造 create_app 应用（不进 lifespan，无需 DB/Redis）。

    CORS 配置每个用例显式钉死（不依赖 configs/*.yaml 的 cors 段，避免
    dev/prod 配置变动影响断言）；AUTH 用环境变量按用例开关——鉴权中间件
    按请求读 env（server.py _api_key_auth_middleware）。
    """
    from fastapi.testclient import TestClient

    monkeypatch.setenv("LLM_API_KEY", "test-key")
    monkeypatch.setenv("LLM_BASE_URL", "http://localhost:1/v1")

    def _make(*, enabled=True, origins=None, auth_required=False, **cors_kw):
        if auth_required:
            monkeypatch.setenv("AUTH_REQUIRED", "true")
            monkeypatch.setenv("API_KEY", "secret-key-123")
        else:
            monkeypatch.setenv("AUTH_REQUIRED", "false")

        ConfigManager._instance = None
        config = ConfigManager.get_instance().load()
        cors = config.middleware.cors
        cors.enabled = enabled
        cors.allow_origins = list(origins if origins is not None else [])
        for key, value in cors_kw.items():
            setattr(cors, key, value)

        return TestClient(create_app(config))

    return _make


def test_cors_disabled_emits_no_headers(make_client):
    """关闭（默认）：带 Origin 也不回 CORS 头——与旧版行为一致"""
    client = make_client(enabled=False, origins=[ORIGIN])
    resp = client.get("/docs", headers={"Origin": ORIGIN})
    assert resp.status_code == 200
    assert resp.headers.get("access-control-allow-origin") is None


def test_cors_allowed_origin_reflected(make_client):
    client = make_client(enabled=True, origins=[ORIGIN])
    resp = client.get("/docs", headers={"Origin": ORIGIN})
    assert resp.status_code == 200
    assert resp.headers.get("access-control-allow-origin") == ORIGIN


def test_cors_disallowed_origin_no_header(make_client):
    """非白名单源：简单请求不回 ACAO（浏览器侧自行拦截）"""
    client = make_client(enabled=True, origins=[ORIGIN])
    resp = client.get("/docs", headers={"Origin": "https://evil.example.com"})
    assert resp.status_code == 200
    assert resp.headers.get("access-control-allow-origin") is None


def test_cors_wildcard_origin(make_client):
    client = make_client(enabled=True, origins=["*"])
    resp = client.get("/docs", headers={"Origin": ORIGIN})
    assert resp.headers.get("access-control-allow-origin") == "*"


def test_preflight_not_blocked_by_auth(make_client):
    """关键：预检 OPTIONS 不带 X-API-Key——CORS 必须在 auth 外层短路，否则 401。

    这是整个顺序设计（D3）的守卫测试：CORS 若挂到 auth 内层，本用例立即红。
    """
    client = make_client(enabled=True, origins=[ORIGIN], auth_required=True)
    resp = client.options(
        "/chat/stream",
        headers={
            "Origin": ORIGIN,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "x-api-key, content-type",
        },
    )
    assert resp.status_code == 200, (
        f"预检应被 CORS 短路返回 200（不得进 auth），实际 {resp.status_code}: "
        f"{resp.text[:200]}"
    )
    assert resp.headers.get("access-control-allow-origin") == ORIGIN
    assert "POST" in resp.headers.get("access-control-allow-methods", "")
    # allow_headers=["*"]：镜像回显预检请求的自定义头（X-API-Key）
    assert "x-api-key" in resp.headers.get(
        "access-control-allow-headers", ""
    ).lower()


def test_cross_origin_request_keeps_auth_and_exposes_401(make_client):
    """CORS 不放松鉴权：跨域真实请求无 key 仍 401；
    401 带 ACAO，浏览器 JS 才能读到错误体（缺口 1 的直接收益）。"""
    client = make_client(enabled=True, origins=[ORIGIN], auth_required=True)
    resp = client.post(
        "/chat/",
        json={"user_id": "u_cors", "message": "hi"},
        headers={"Origin": ORIGIN},
    )
    assert resp.status_code == 401
    assert resp.headers.get("access-control-allow-origin") == ORIGIN
