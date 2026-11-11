# -*- coding: utf-8 -*-

"""/docs /redoc 本地资源化测试（方案 A）

背景：server.py 安全响应头中间件对所有响应注入严格 CSP（script-src/style-src/
img-src 仅允许 'self'），而 FastAPI 默认 /docs /redoc 引用 cdn.jsdelivr.net /
fastapi.tiangolo.com / fonts.googleapis.com 的资源，被 CSP 拦截导致白屏
（详见 .draft/2026-11-10-docs-blank-csp.md）。

验收标准（方案 A）：
  - /docs /redoc HTML 只引用本地 /static/vendor/swagger-ui/ 资源，无外部域名
  - HTML 引用的本地资源均可访问（200）
  - AUTH_REQUIRED=true 时上述资源仍 200（鉴权白名单覆盖）
  - CSP 响应头保持原样（不靠放宽 CSP 修复）

若 server.py 因环境缺少依赖无法导入, 整体跳过并给出说明（与 test_server_auth 一致）。
"""

import re

import pytest

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
        pytest.skip(f"server.py 不可导入, 跳过 docs 测试: {_IMPORT_ERROR}")


@pytest.fixture
def client(monkeypatch):
    """构造完整 create_app 应用（不进 lifespan，无需 DB/Redis）。"""
    from fastapi.testclient import TestClient

    # 配置加载 fail-fast 要求 LLM 变量存在（docs 测试不实际调用 LLM）
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    monkeypatch.setenv("LLM_BASE_URL", "http://localhost:1/v1")

    ConfigManager._instance = None
    config = ConfigManager.get_instance().load()
    app = create_app(config)
    return TestClient(app)


def _local_assets(html: str) -> list[str]:
    """提取 HTML 中引用的本地资源路径（src/href 以 / 开头）。"""
    return re.findall(r'(?:src|href)="(/[^"]+)"', html)


# ----------------------------------------------------------------
# /docs：只引用本地资源
# ----------------------------------------------------------------

def test_docs_html_references_local_swagger(client):
    resp = client.get("/docs")
    assert resp.status_code == 200
    html = resp.text
    assert "/static/vendor/swagger-ui/swagger-ui-bundle.js" in html
    assert "/static/vendor/swagger-ui/swagger-ui.css" in html
    # 不允许任何外部资源域名（CSP 会拦截 → 白屏）
    assert "cdn.jsdelivr.net" not in html
    assert "fastapi.tiangolo.com" not in html


def test_docs_local_assets_served(client):
    html = client.get("/docs").text
    assets = _local_assets(html)
    assert assets, "/docs 应引用至少一个本地资源"
    for path in assets:
        resp = client.get(path)
        assert resp.status_code == 200, f"本地资源不可访问: {path}"
        assert "text/html" not in resp.headers.get(
            "content-type", ""
        ) or path.endswith(".svg"), f"{path} 不应返回 HTML（疑似 SPA fallback）"


def test_docs_csp_header_unchanged(client):
    """修复不得依赖放宽 CSP —— CSP 头必须仍在且不包含外部域白名单。"""
    resp = client.get("/docs")
    csp = resp.headers.get("content-security-policy", "")
    assert csp, "CSP 响应头不应丢失"
    assert "cdn.jsdelivr.net" not in csp
    assert "script-src 'self'" in csp


# ----------------------------------------------------------------
# /redoc：同因同修
# ----------------------------------------------------------------

def test_redoc_html_references_local_assets(client):
    resp = client.get("/redoc")
    assert resp.status_code == 200
    html = resp.text
    assert "/static/vendor/swagger-ui/redoc.standalone.js" in html
    assert "cdn.jsdelivr.net" not in html
    # ReDoc 默认加载 Google Fonts，同样被 CSP 拦截
    assert "fonts.googleapis.com" not in html
    assert "fonts.gstatic.com" not in html


def test_redoc_local_assets_served(client):
    html = client.get("/redoc").text
    assets = _local_assets(html)
    assert assets, "/redoc 应引用至少一个本地资源"
    for path in assets:
        resp = client.get(path)
        assert resp.status_code == 200, f"本地资源不可访问: {path}"


# ----------------------------------------------------------------
# 鉴权白名单：AUTH_REQUIRED=true 时 docs 及其资源仍可访问
# ----------------------------------------------------------------

def test_docs_and_assets_bypass_auth_when_required(client, monkeypatch):
    monkeypatch.setenv("AUTH_REQUIRED", "true")
    monkeypatch.setenv("API_KEY", "secret-key-123")

    resp = client.get("/docs")
    assert resp.status_code == 200, "/docs 应在鉴权白名单内"

    html = resp.text
    for path in _local_assets(html):
        resp = client.get(path)
        assert resp.status_code == 200, (
            f"docs 本地资源未进鉴权白名单, 被 401 拦截: {path}"
        )

    resp = client.get("/redoc")
    assert resp.status_code == 200, "/redoc 应在鉴权白名单内"
    for path in _local_assets(resp.text):
        resp = client.get(path)
        assert resp.status_code == 200, f"redoc 本地资源被 401 拦截: {path}"


def test_protected_path_still_requires_key(client, monkeypatch):
    """白名单放行 docs 资源不得导致受保护路径失守。"""
    monkeypatch.setenv("AUTH_REQUIRED", "true")
    monkeypatch.setenv("API_KEY", "secret-key-123")
    resp = client.get("/mcp")
    assert resp.status_code == 401
