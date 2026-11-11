# -*- coding: utf-8 -*-

"""RAG WebUI 反代 — /rag/ → 上游 LightRAG webui（自动登录注入）

前端侧边栏「知识库 RAG」点击后新标签打开 /rag/，本模块把该前缀反代到配置的
RAG 服务（rag.api_url），并在 API 请求上自动注入 Bearer token，实现用户无感
自动登录。rag.api_url 为空时不启用，前端退回「即将支持，规划中」占位提示。

为什么必须反代而不是直接跳转上游地址：
    LightRAG webui 是 SPA，登录态存于上游自身 origin 的 localStorage
    （LIGHTRAG-API-TOKEN），跨站无法写入，URL 也不支持携带 token 自动登录。
    反代后页面与 API 均落在本站 origin，凭据只存服务端：
      1. HTML 响应被改写 __LIGHTRAG_CONFIG__.apiPrefix: "" → "/rag"，
         SPA 的全部 API 请求（axios baseURL / fetch 均拼 apiPrefix）随之打到 /rag/*
      2. 本模块转发 API 时用服务端配置的账号换取 JWT 并注入 Authorization 头，
         token 过期或上游 401 时自动重新登录

路径映射（/rag/{path} → 上游）：
    API 前缀（/login /query /documents /graph* /api/* /auth* /health /ui/*）
        → {api_url}/{path}（注入 token）
    其余（assets / favicon 等静态资源）
        → {api_url}{webui_path}{path}

上游不可用时返回 502；静态资源上游 404 时回退 index.html（SPA fallback）。
"""

from __future__ import annotations

import asyncio
import base64
import json
import re
import time
from typing import TYPE_CHECKING

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from loguru import logger

if TYPE_CHECKING:
    from core.config import ConfigManager  # noqa: F401 — 仅类型标注用

router = APIRouter()

# LightRAG 的 API 路径前缀（对照上游 openapi.json 45 条路由）。
# 命中 → 转发到 {api_url}/{path} 并注入 token；未命中 → 视为 webui 静态资源。
_RAG_API_PREFIXES = (
    "/api/",
    "/auth",      # /auth/verify 与 /auth-status
    "/documents",
    "/graph",     # /graph/* 与 /graphs
    "/health",
    "/login",
    "/query",
    "/ui/",
)

# index.html 里服务端注入的配置对象；apiPrefix 为空串表示 API 打根路径。
# 改写为 "/rag" 后 SPA 的 axios/fetch 全部走进本站反代前缀。
_API_PREFIX_RE = re.compile(r'("apiPrefix"\s*:\s*)""')

# 逐跳（hop-by-hop）头：不转发给上游 / 不回传给客户端
# if-none-match / if-modified-since 一并剔除：HTML 内容被改写（apiPrefix/token 脚本），
# 若沿用条件请求头，上游 304 会让浏览器继续用未改写的旧缓存
_HOP_BY_HOP_REQ = {
    "host", "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "te", "trailers", "transfer-encoding", "upgrade", "content-length",
    "if-none-match", "if-modified-since",
}
_HOP_BY_HOP_RESP = {
    "connection", "keep-alive", "transfer-encoding", "content-encoding", "content-length",
    "content-security-policy",  # 由本模块按需注入（见 _RAG_CSP），避免与平台中间件冲突
}

# /rag/ 页面的 Content-Security-Policy：LightRAG webui 为同源自包含构建
# （KaTeX/sigma 等均已打进 assets），仅需放开 inline/eval 与 data/blob 资源。
# 带上该头后平台 _security_headers_middleware 的 existing 检查会保留本策略。
_RAG_CSP = (
    "default-src 'self' 'unsafe-inline' 'unsafe-eval' data: blob:; "
    "img-src 'self' data: blob:; "
    "media-src 'self' data: blob:; "
    "connect-src 'self' ws: wss:; "
    "font-src 'self' data:; "
    "object-src 'none'; "
    "base-uri 'self'"
)

# LightRAG SPA 的登录态判定依赖其自身 localStorage（LIGHTRAG-API-TOKEN），
# 仅 API 层注入 token 不足以跳过登录页——启动逻辑为：
#   本地有 token 且 auth-status 返回版本信息 → 直接 login(本地 token)（不预先验证）
# 因此 HTML 响应在 <head> 注入同步脚本，把服务端缓存的 token 写进 localStorage，
# SPA 初始化（type=module，文档解析后执行）读到即进入已登录态。
# token 为 JWT base64url 字符集，无引号/尖括号，内联安全。
# 注意 JS 花括号与 str.format 冲突，用 %s 占位。
_TOKEN_SYNC_TEMPLATE = '<script>try{localStorage.setItem("LIGHTRAG-API-TOKEN","%s")}catch(e){}</script>'


def _inject_token_sync(html: str, token: str | None) -> str:
    """把 token 同步脚本插入 <head> 开头（先于 SPA 任何模块执行）"""
    if not token:
        return html
    script = _TOKEN_SYNC_TEMPLATE % token
    idx = html.find("<head>")
    if idx >= 0:
        return html[: idx + len("<head>")] + script + html[idx + len("<head>"):]
    return script + html


def _jwt_exp(token: str) -> float | None:
    """解析 JWT payload 的 exp（秒级 epoch），失败返回 None（不做验签，仅读过期时间）"""
    try:
        parts = token.split(".")
        if len(parts) != 3:
            return None
        payload_b64 = parts[1] + "=" * (-len(parts[1]) % 4)
        data = json.loads(base64.urlsafe_b64decode(payload_b64))
        exp = data.get("exp")
        return float(exp) if exp is not None else None
    except Exception:  # noqa: BLE001 — 非 JWT 结构时静默降级
        return None


class RagProxyService:
    """上游 LightRAG 服务反代：HTTP 客户端 + 登录 token 缓存/刷新"""

    def __init__(self, *, api_url: str, webui_path: str, username: str, password: str) -> None:
        self.api_url = api_url.rstrip("/")
        self.webui_path = webui_path if webui_path.startswith("/") else f"/{webui_path}"
        if not self.webui_path.endswith("/"):
            self.webui_path += "/"
        self.username = username
        self.password = password
        # read=120：流式端点（/query/stream）两次 chunk 间的空闲超时上限
        self.client = httpx.AsyncClient(
            timeout=httpx.Timeout(10.0, read=120.0),
            follow_redirects=False,  # 上游 307（如 / → /webui/）原样透传给浏览器
        )
        self._token: str | None = None
        self._token_exp: float | None = None
        self._cooldown_until: float = 0.0  # 登录失败冷却，防凭据错误时每请求都重登
        self._lock = asyncio.Lock()

    # ── token 管理 ──────────────────────────────────────────
    async def _login(self) -> str | None:
        try:
            resp = await self.client.post(
                f"{self.api_url}/login",
                data={"username": self.username, "password": self.password},
            )
        except httpx.RequestError as e:
            logger.warning("RAG 反代登录请求失败（上游不可达）: {}", e)
            return None
        if resp.status_code != 200:
            # 上游对失败登录有速率限制（login_rate_limit），勿高频重试
            logger.warning(
                "RAG 反代登录失败（HTTP {}，用户 {}）: {}",
                resp.status_code, self.username, resp.text[:200],
            )
            return None
        token = resp.json().get("access_token")
        if not token:
            logger.warning("RAG 反代登录响应缺少 access_token")
            return None
        self._token = token
        self._token_exp = _jwt_exp(token)
        self._cooldown_until = 0.0
        logger.info(
            "RAG 反代登录成功（用户 {}，token 有效期至 {}）",
            self.username,
            time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(self._token_exp)) if self._token_exp else "未知",
        )
        return token

    async def get_token(self, *, force: bool = False) -> str | None:
        """取可用 token：缓存命中直接返回；过期前 60s 或 force 时重登。

        force 用于上游 401 后的重登（token 可能被上游侧失效）。
        登录失败后 30s 冷却，冷却期内不再发起新登录（返回旧 token，交由上游 401 暴露）。
        """
        if not self.username:
            return None  # 未配置凭据：不注入认证，交给上游手动登录
        async with self._lock:
            now = time.time()
            if not force and self._token and (self._token_exp is None or now < self._token_exp - 60):
                return self._token
            if force or now >= self._cooldown_until:
                token = await self._login()
                if token is None:
                    self._cooldown_until = now + 30.0
                    return self._token
                return token
            return self._token

    async def aclose(self) -> None:
        await self.client.aclose()


# 模块级单例：lifespan 启动时按配置创建（uvicorn workers>1 时各进程独立持有）
_service: RagProxyService | None = None


async def startup(config) -> None:
    """按 rag 配置创建反代服务；api_url 为空则不启用"""
    global _service
    api_url = (config.rag.api_url or "").strip()
    if not api_url:
        logger.info("RAG WebUI 反代未启用（rag.api_url 为空，前端显示占位提示）")
        return
    _service = RagProxyService(
        api_url=api_url,
        webui_path=config.rag.webui_path or "/webui/",
        username=(config.rag.webui_username or "").strip(),
        password=config.rag.webui_password or "",
    )
    logger.info(
        "RAG WebUI 反代已启用: /rag/ → {}{}（自动登录: {}）",
        _service.api_url, _service.webui_path,
        _service.username or "未配置凭据",
    )


async def shutdown() -> None:
    global _service
    if _service is not None:
        await _service.aclose()
        _service = None
        logger.info("RAG WebUI 反代已关闭")


# ── 端点 ────────────────────────────────────────────────────

@router.get("/webui-config")
async def webui_config(request: Request) -> JSONResponse:
    """前端功能开关下发

    - rag_url: 反代入口，null 时前端显示占位提示
    - auth_required: AUTH 是否开启（is_auth_enabled：env AUTH_REQUIRED > YAML
      auth.required）——setup 页据此把 API Key 从「可选」改必填强校验（D9）
    """
    from core.validators import is_auth_enabled

    config = getattr(request.app.state, "config", None)
    return JSONResponse({
        "rag_url": "/rag/" if _service is not None else None,
        "auth_required": is_auth_enabled(config),
    })


@router.get("/rag")
async def rag_root_redirect() -> RedirectResponse:
    return RedirectResponse(url="/rag/")


@router.api_route(
    "/rag/{path:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"],
)
async def rag_proxy(path: str, request: Request):
    """反代 /rag/* → 上游 RAG 服务（API 注入 token，静态资源透传）"""
    service = _service
    if service is None:
        return JSONResponse({"detail": "RAG 服务未配置（rag.api_url 为空）"}, status_code=404)

    rel = f"/{path}" if path else ""
    is_api = rel.startswith(_RAG_API_PREFIXES)
    is_login = rel == "/login"
    target_path = rel if is_api else f"{service.webui_path}{path}" if path else service.webui_path
    url = f"{service.api_url}{target_path}"

    headers = {
        k: v for k, v in request.headers.items()
        if k.lower() not in _HOP_BY_HOP_REQ
    }
    # /login 本身不用注入（登录请求凭表单换 token）；其余 API 注入服务端缓存的 token
    if is_api and not is_login:
        token = await service.get_token()
        if token:
            headers["authorization"] = f"Bearer {token}"

    body = await request.body()

    try:
        upstream = await _send_with_retry(
            service, request, url, headers, body,
            retry_auth=bool(is_api and not is_login),
        )
    except httpx.RequestError as e:
        logger.warning("RAG 反代请求失败 ({} {}): {}", request.method, url, e)
        return JSONResponse({"detail": f"RAG 服务不可达: {e}"}, status_code=502)

    # 静态资源上游 404 → 回退 index.html（hash 路由刷新等场景的 SPA fallback）
    if upstream.status_code == 404 and not is_api and request.method == "GET":
        await upstream.aclose()
        try:
            upstream = await service.client.get(
                f"{service.api_url}{service.webui_path}index.html",
                headers={"accept": "text/html"},
            )
        except httpx.RequestError as e:
            return JSONResponse({"detail": f"RAG 服务不可达: {e}"}, status_code=502)
        if upstream.status_code != 200:
            return JSONResponse(
                {"detail": f"RAG 服务返回 {upstream.status_code}"}, status_code=502,
            )

    resp_headers = {
        k: v for k, v in upstream.headers.items()
        if k.lower() not in _HOP_BY_HOP_RESP
    }

    # HTML（index.html 等）：改写 apiPrefix、注入 token 同步脚本并禁缓存
    content_type = upstream.headers.get("content-type", "")
    if "text/html" in content_type:
        raw = await upstream.aread()
        await upstream.aclose()
        html = raw.decode("utf-8", errors="replace")
        html = _API_PREFIX_RE.sub(lambda m: m.group(1) + '"/rag"', html, count=1)
        token = await service.get_token()
        html = _inject_token_sync(html, token)
        # no-store：HTML 内含注入的 token（且随服务端续登变化），禁止任何形式缓存
        resp_headers["cache-control"] = "no-store"
        resp_headers.pop("etag", None)
        resp_headers.pop("last-modified", None)
        resp_headers["content-security-policy"] = _RAG_CSP
        if token:
            resp_headers["x-new-token"] = token
        return HTMLResponse(html, status_code=upstream.status_code, headers=resp_headers)

    # x-new-token：LightRAG 响应拦截器见此头即刷新其 localStorage token，
    # 使前端持有的 token 跟随服务端续登不断档（否则约 48h 过期后 401 → 登录页）。
    # API 请求在上方已按需登录；静态资源用当前缓存（可能尚未登录，为空则不带）
    if service._token:
        resp_headers["x-new-token"] = service._token

    # 其余（JS/CSS/字体/图片/SSE 等）：流式透传
    async def _gen():
        try:
            async for chunk in upstream.aiter_bytes():
                yield chunk
        finally:
            await upstream.aclose()

    return StreamingResponse(
        _gen(),
        status_code=upstream.status_code,
        headers=resp_headers,
        media_type=content_type or None,
    )


async def _send_with_retry(
    service: RagProxyService,
    request: Request,
    url: str,
    headers: dict[str, str],
    body: bytes,
    *,
    retry_auth: bool,
):
    """转发请求；API 收到 401 时强制重登后重试一次（token 被上游侧失效的场景）"""
    upstream = await service.client.send(
        service.client.build_request(
            request.method, url,
            params=request.query_params,
            headers=headers,
            content=body if body else None,
        ),
        stream=True,
    )
    if retry_auth and upstream.status_code == 401:
        await upstream.aclose()
        token = await service.get_token(force=True)
        if token:
            headers = {**headers, "authorization": f"Bearer {token}"}
            upstream = await service.client.send(
                service.client.build_request(
                    request.method, url,
                    params=request.query_params,
                    headers=headers,
                    content=body if body else None,
                ),
                stream=True,
            )
    return upstream
