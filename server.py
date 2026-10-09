# -*- coding: utf-8 -*-

"""FastAPI 应用定义 — 路由 + 生命周期

职责：
    1. create_app(config) 工厂函数，接收已加载的配置
    2. lifespan 管理资源生命周期（DB/Redis/Session/Workspace/ChatService）
    3. 注册所有 API 路由 + 静态文件

由 main.py 调用：
    app = create_app(config)
"""

from __future__ import annotations

import asyncio
import json
import os
import threading
import webbrowser
from pathlib import Path

from loguru import logger

from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from api.chat import router as chat_router
from api.channels import router as channels_router
from api.images import router as images_router
from api.mcp import router as mcp_router
from api.skill import router as skill_router
from api.ws_chat import router as ws_chat_router
# from api.workspace import router as workspace_router
# from api.webui import router as webui_router

from core.chat_service import ChatService
from core.channels.manager import ChannelManager
from core.config import ConfigManager
from core.database import DatabaseManager
from core.db.base import DatabaseUnavailableError
from core.message_bus import InMemoryMessageBus
from core.redis_message_bus import RedisMessageBus
from core.session import SessionManager
from core.session_status import SessionStatusTracker
from core.storage import PostgresStorage
from core.user_service import UserService
from core.workspace import LocalWorkspaceManager


# ------------------------------------------------------------
# 轻量级中间件（纯 ASGI 实现，不缓冲响应体，SSE / WebSocket 不受影响）
# ------------------------------------------------------------
def _security_headers_middleware(app):
    """始终开启的安全响应头（对开发 / 生产均无害）"""
    _SEC_HEADERS = {
        b"strict-transport-security": b"max-age=31536000; includeSubDomains",
        b"content-security-policy": (
            b"default-src 'self'; "
            b"script-src 'self' 'unsafe-inline' 'unsafe-eval'; "
            b"style-src 'self' 'unsafe-inline'; "
            b"img-src 'self' data: blob:; "
            b"connect-src 'self' ws: wss:; "
            b"font-src 'self' data:; "
            b"object-src 'none'; "
            b"base-uri 'self'"
        ),
        b"x-content-type-options": b"nosniff",
        b"referrer-policy": b"no-referrer",
    }

    async def middleware(scope, receive, send):
        if scope["type"] != "http":
            await app(scope, receive, send)
            return

        # 静态前端资源不加强缓存，避免改完 CSS/JS 仍命中旧缓存
        _no_cache_static = scope.get("path", "").startswith("/static/")

        sent_start = False

        async def send_wrapper(message):
            nonlocal sent_start
            if message["type"] == "http.response.start" and not sent_start:
                sent_start = True
                headers = list(message.get("headers", []))
                existing = {k.lower(): v for k, v in headers}
                for key, val in _SEC_HEADERS.items():
                    if key not in existing:
                        headers.append((key, val))
                if _no_cache_static:
                    _cc = b"cache-control"
                    headers = [(k, v) for k, v in headers if k.lower() != _cc]
                    headers.append((_cc, b"no-cache"))
                message["headers"] = headers
            await send(message)

        await app(scope, receive, send_wrapper)

    return middleware


def _api_key_auth_middleware(app, *, auth_required: bool = False, api_key: str = ""):
    """全局 API-Key 鉴权（默认关闭，仅 auth_required=True 或 AUTH_REQUIRED=true 时生效）

    放行路径：/webui、/webui/、/health、/docs、/openapi.json、/redoc
    以及所有以 /webui/ 开头的路径。其余路径需携带正确的 X-API-Key 请求头。

    优先级：环境变量 AUTH_REQUIRED/API_KEY > YAML 配置 auth.required/auth.api_key。
    """
    _PUBLIC_EXACT = {
        "/webui", "/webui/", "/health", "/docs",
        "/openapi.json", "/redoc",
    }

    async def middleware(scope, receive, send):
        if scope["type"] != "http":
            await app(scope, receive, send)
            return

        # 环境变量优先，YAML 配置兜底
        env_required = os.environ.get("AUTH_REQUIRED", "").lower()
        if env_required == "true":
            is_auth_required = True
        elif env_required == "false":
            is_auth_required = False
        else:
            is_auth_required = auth_required

        if not is_auth_required:
            await app(scope, receive, send)
            return

        path = scope.get("path", "")
        if path in _PUBLIC_EXACT or path.startswith("/webui/"):
            await app(scope, receive, send)
            return

        headers = {k.lower(): v for k, v in scope.get("headers", [])}
        provided = headers.get(b"x-api-key", b"").decode("latin-1")
        expected = os.environ.get("API_KEY") or api_key
        if expected and provided == expected:
            # 将已验证的 API key 存入 scope state，供下游端点使用
            scope.setdefault("state", {})["api_key"] = provided
            await app(scope, receive, send)
            return

        body = json.dumps(
            {"error": "Unauthorized", "hint": "Missing or invalid X-API-Key"}
        ).encode("utf-8")
        await send({
            "type": "http.response.start",
            "status": 401,
            "headers": [(b"content-type", b"application/json")],
        })
        await send({"type": "http.response.body", "body": body})

    return middleware


async def _database_unavailable_handler(
    request: Request, exc: DatabaseUnavailableError
) -> JSONResponse:
    """数据库不可用 → 503（未初始化/运行期故障下 /mcp /skill 等未守卫路由的统一响应）"""
    return JSONResponse(
        status_code=503,
        content={"detail": f"数据库不可用: {exc}"},
    )


def create_app(config) -> FastAPI:
    """创建 FastAPI 应用实例

    Args:
        config: ConfigManager.load() 返回的配置对象

    Returns:
        配置完成的 FastAPI 实例
    """

    # ============================================================
    # 1. FastAPI 生命周期
    # ============================================================
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        """服务启动 / 关闭时的资源管理"""
        # --- 启动 ---
        app.state.config = config

        # 创建数据库管理器（按 backend 分派 PostgreSQL/MySQL；建表/校验见 database.* 配置）。
        # 初始化失败 fail-fast：initialize() 抛出 → lifespan 异常 → uvicorn 启动失败退出，
        # 下方日志块不会执行（server.py 无需 try/except，异常自然冒泡即为启动失败）。
        db_mgr = DatabaseManager(config.database)
        await db_mgr.initialize()
        app.state.database_manager = db_mgr
        if db_mgr.is_initialized:
            logger.info("数据库已就绪 (backend={})", config.database.backend)
        else:
            logger.warning(
                "数据库未配置（database.url 为空），跳过初始化；/health 显示 not_configured"
            )

        # 用户服务（角色解析 + 懒注册）
        # DB 未配置时优雅降级：角色完全由 config.agent.root_user_ids +
        # 环境变量 ROOT_USER_IDS 推导，不阻断启动。
        user_service = UserService(config, db_mgr)
        app.state.user_service = user_service
        logger.info(
            "用户服务已就绪（root 预置: {}）",
            sorted(user_service._root_ids) if user_service._root_ids else "无",
        )

        # 应用配置对象（供 /context 等端点读取 request.app.state.config）
        app.state.config = config

        # 创建 PostgreSQL 存储层（Agent/Session/MCP/Skill/Message/Schedule CRUD）
        storage = PostgresStorage(db_mgr)
        app.state.storage = storage
        logger.info("PostgreSQL 存储层已就绪")

        # 创建消息总线（Redis 分布式实现，支持多实例无状态部署）
        # 注意：RedisMessageBus 构造函数当前仅接受 redis_url（见 core/redis_message_bus.py:36），
        # key_prefix / session_ttl 由 SessionManager 使用，此处无需传递。
        # Redis 不可达时退避重试；仍失败则降级为 InMemoryMessageBus（单实例进程内总线），
        # 应用继续启动：对话触发 / 锁 / 事件回放与订阅在无 Redis 时仍可用，
        # 仅跨实例广播与多端状态跟踪不可用（Q4 降级单实例语义）。
        message_bus = RedisMessageBus(config.redis.url)
        _bus_ok = False
        for _attempt in range(3):
            try:
                await message_bus.initialize()
                _bus_ok = True
                break
            except Exception as _bus_err:  # noqa: BLE001
                if _attempt < 2:
                    logger.warning(
                        "Redis 消息总线初始化失败（第 {} 次），2s 后重试: {}",
                        _attempt + 1, _bus_err,
                    )
                    await asyncio.sleep(2)
                else:
                    logger.warning(
                        "Redis 消息总线不可用，应用降级运行"
                        "（/health 仍可用，跨实例广播不可用）: {}",
                        _bus_err,
                    )
        if _bus_ok:
            app.state.message_bus = message_bus
            logger.info("消息总线已就绪 (RedisMessageBus: {})", config.redis.url)
        else:
            app.state.message_bus = InMemoryMessageBus()
            logger.warning("消息总线降级为 InMemoryMessageBus（单实例，无跨实例广播）")

        # 创建会话管理器（Agent 实例按需创建）
        session_mgr = SessionManager(
            config=config,
            session_ttl=config.redis.session_ttl,
            max_sessions=getattr(config.server, "max_sessions", 100),
            storage=storage,
            db=db_mgr,
            user_service=user_service,
        )
        # 初始化 Redis 连接（与消息总线一致：失败降级而非硬崩溃，app 仍服务 /health）
        try:
            await session_mgr.initialize()
            logger.info("会话管理器已就绪 (Redis: {})", config.redis.url)
        except Exception as _sess_err:  # noqa: BLE001
            logger.warning(
                "SessionManager Redis 初始化失败，降级运行（会话持久化不可用）: {}",
                _sess_err,
            )
        app.state.session_manager = session_mgr

        # 创建工作区管理器（沙箱根目录，启动时自动创建）
        sandbox_dir = os.path.abspath(config.agent.sandbox_dir)
        workspace_mgr = LocalWorkspaceManager(base_dir=sandbox_dir)
        app.state.workspace_manager = workspace_mgr
        logger.info("工作区管理器已就绪 (沙箱目录: {})", sandbox_dir)

        # 创建对象存储（图片上传等）
        from core.object_storage import create_object_storage
        storage_cfg = config.object_storage
        if storage_cfg.backend == "local":
            obj_storage = create_object_storage("local", base_dir=storage_cfg.local_dir)
        elif storage_cfg.backend == "s3":
            obj_storage = create_object_storage(
                "s3",
                bucket=storage_cfg.s3_bucket,
                endpoint=storage_cfg.s3_endpoint,
                access_key=storage_cfg.s3_access_key,
                secret_key=storage_cfg.s3_secret_key,
                region=storage_cfg.s3_region,
                addressing_style=storage_cfg.s3_addressing_style,
            )
        elif storage_cfg.backend == "aliyun_oss":
            obj_storage = create_object_storage(
                "aliyun_oss",
                bucket=storage_cfg.oss_bucket,
                endpoint=storage_cfg.oss_endpoint,
                access_key=storage_cfg.oss_access_key,
                secret_key=storage_cfg.oss_secret_key,
            )
        else:
            raise ValueError(f"不支持的存储后端: {storage_cfg.backend}")
        app.state.object_storage = obj_storage
        logger.info("对象存储已就绪 (backend={})", storage_cfg.backend)

        # 创建 Chat 服务（Fire-and-Forget 模式）
        # 先初始化 SessionStatusTracker（多端并发状态广播）
        status_tracker = None
        if _bus_ok and message_bus._redis:
            status_tracker = SessionStatusTracker(message_bus._redis)
            app.state.session_status_tracker = status_tracker
            logger.info("会话状态跟踪器已就绪（多端并发模式）")
        else:
            app.state.session_status_tracker = None
            logger.info("会话状态跟踪器未启用（Redis 不可用）")

        # 传入 app.state.message_bus（降级时为 InMemoryMessageBus），
        # 避免 ChatService.run 持有 _redis=None 的 RedisMessageBus 而崩溃
        chat_service = ChatService(session_mgr, app.state.message_bus, status_tracker)
        app.state.chat_service = chat_service
        logger.info("Chat 服务已就绪")

        # 创建渠道配置存储（JSON 文件驱动，热加载 + 单实例文件锁）
        from core.channels.config_store import ChannelConfigStore
        channel_config_store = ChannelConfigStore()
        await channel_config_store.start()
        app.state.channel_config_store = channel_config_store
        logger.info(
            "渠道配置存储已就绪（配置文件: {}）",
            channel_config_store._path,
        )

        # 创建渠道管理器（入站→内核→出站 闭环；从 JSON 配置文件拉起启用渠道）
        channel_manager = ChannelManager(
            config_store=channel_config_store,
            bus=app.state.message_bus,
            chat_service=chat_service,
            object_storage=obj_storage,
        )
        app.state.channel_manager = channel_manager

        # 注册热加载回调：配置文件变更时自动 diff 启停
        channel_config_store._on_change = channel_manager.on_config_change

        # 启动期拉起所有已启用的渠道
        await channel_manager.start_all()
        logger.info("渠道管理器已就绪（已拉起启用中的渠道）")

        # 创建 MCP 配置存储（JSON 文件驱动，单一真源）
        # YAML 不参与 MCP 导入（AppConfig 无 mcp_servers 字段）。AgentFactory 在创建
        # 会话时直接读取此文件，REST /mcp 也读写此文件。
        from core.mcp.config_store import MCPConfigStore

        # on_change：mcps.json 被外部编辑或 REST 写入后记录日志。
        # 新建会话时 AgentFactory 直接读文件，天然取到最新配置；已有会话仍用创建时的客户端。
        async def _on_mcp_change() -> None:
            logger.info(
                "MCPConfigStore: 配置变更（{} 条启用），新建会话时生效",
                len(mcp_config_store.get_mcp_configs()),
            )

        mcp_config_store = MCPConfigStore(on_change=_on_mcp_change)
        await mcp_config_store.start()
        app.state.mcp_config_store = mcp_config_store
        logger.info(
            "MCP 配置存储已就绪（配置文件: {}，{} 条启用）",
            mcp_config_store._path,
            len(mcp_config_store.get_mcp_configs()),
        )

        # 创建 Skill 配置存储（JSON 文件驱动，管理平面元数据 + 目录自动同步）
        # 运行时 Skill 仍由 agent_space/skills 目录（文件系统）加载；本存储仅作管理/展示目录，
        # 并在启动与运行时定期扫描该目录，把磁盘 SKILL.md 自动同步进 skills.json。
        from core.skill.config_store import SkillConfigStore

        skill_config_store = SkillConfigStore(
            skills_dir=os.path.join(workspace_mgr.agent_space_dir, "skills"),
        )
        await skill_config_store.start()
        app.state.skill_config_store = skill_config_store
        logger.info("Skill 配置存储已就绪（配置文件: {}）", skill_config_store._path)

        # 自动打开浏览器 (仅开发环境)
        if config.otel.environment == "development":
            def _open_browser():
                webbrowser.open(f"http://localhost:{config.server.port}/")
            threading.Thread(target=_open_browser, daemon=True).start()

        yield

        # --- 关闭 ---
        # 先排空后台持久化任务（DRAIN：await gather / 取消），避免关闭时丢失在途对话写入。
        # shutdown_persist_tasks 由 api/chat.py / api/ws_chat.py 提供（同步或异步版本皆可，
        # 用 iscoroutinefunction 兼容；未定义时跳过，不影响关闭流程）。
        try:
            from api.chat import shutdown_persist_tasks as _chat_shutdown  # type: ignore
        except Exception:  # noqa: BLE001
            _chat_shutdown = None
        try:
            from api.ws_chat import shutdown_persist_tasks as _ws_shutdown  # type: ignore
        except Exception:  # noqa: BLE001
            _ws_shutdown = None
        for _fn in (_chat_shutdown, _ws_shutdown):
            if _fn is None:
                continue
            try:
                if asyncio.iscoroutinefunction(_fn):
                    await _fn()
                else:
                    _fn()
            except Exception as _persist_err:  # noqa: BLE001
                logger.warning("关闭时排空持久化任务失败（已忽略）: {}", _persist_err)

        # 先停止所有渠道（断开 WS 长连接），再关闭会话管理器
        channel_mgr = getattr(app.state, "channel_manager", None)
        if channel_mgr is not None:
            try:
                await channel_mgr.stop_all()
                logger.info("渠道管理器已停止")
            except Exception as _ch_err:  # noqa: BLE001
                logger.warning("关闭渠道管理器异常（已忽略）: {}", _ch_err)

        # 停止渠道配置存储（热加载轮询 + 释放文件锁）
        ch_cfg_store = getattr(app.state, "channel_config_store", None)
        if ch_cfg_store is not None:
            try:
                await ch_cfg_store.stop()
                logger.info("渠道配置存储已停止")
            except Exception as _cs_err:  # noqa: BLE001
                logger.warning("关闭渠道配置存储异常（已忽略）: {}", _cs_err)

        # 停止 MCP / Skill 配置存储（热加载轮询）
        for _store in (
            getattr(app.state, "mcp_config_store", None),
            getattr(app.state, "skill_config_store", None),
        ):
            if _store is not None:
                try:
                    await _store.stop()
                except Exception as _store_err:  # noqa: BLE001
                    logger.warning("关闭配置存储异常（已忽略）: {}", _store_err)

        await session_mgr.shutdown()
        logger.info("会话管理器已关闭")

        await app.state.message_bus.aclose()
        if app.state.message_bus is not message_bus:
            # 降级场景：关闭未使用的 Redis 实例（_redis=None 时 aclose 自行跳过）
            await message_bus.aclose()
        logger.info("消息总线已关闭")

        await db_mgr.shutdown()
        logger.info("数据库连接池已关闭")

        if config.otel.enabled:
            from core.tracing import TracingSetup
            TracingSetup.shutdown()
            logger.info("OTel 追踪已关闭")

    # ============================================================
    # 2. 创建 FastAPI 应用
    # ============================================================
    app = FastAPI(
        title="Pamimi Tako Bot Demo",
        description="基于 AgentScope 的对话智能体平台",
        version="0.2.1",
        lifespan=lifespan,
    )

    # 数据库不可用统一转 503（未初始化/运行期故障下 /mcp /skill 等路由不抛 500）
    app.add_exception_handler(DatabaseUnavailableError, _database_unavailable_handler)

    # 仓库根目录（server.py 位于仓库根，故 parents[0] 即根目录）
    # 以绝对路径替代 os.getcwd()，避免进程 cwd 变化时静态 / webui 路径错位。
    _repo_root = str(Path(__file__).resolve().parents[0])

    # 静态文件（前端界面）
    _static_dir = os.path.join(_repo_root, "api", "static")

    @app.get("/")
    async def serve_frontend():
        """前端对话界面（旧版）"""
        index_path = os.path.join(_static_dir, "index.html")
        if os.path.exists(index_path):
            return FileResponse(index_path)
        return {"error": "Frontend not found", "path": index_path}

    @app.get("/webui")
    @app.get("/webui/{path:path}")
    async def serve_webui(path: str = ""):
        """新版 WebUI 入口"""
        webui_dir = os.path.join(_repo_root, "webui", "dist")
        if not os.path.isdir(webui_dir):
            return {"error": "WebUI not built", "hint": "cd webui && npm run build"}
        # 目标文件：默认 index.html，否则拼接 path
        file_path = os.path.join(webui_dir, path) if path else os.path.join(webui_dir, "index.html")
        # 路径穿越防护：解析真实路径并校验仍位于 webui_dir 之内
        real_root = os.path.realpath(webui_dir)
        real_file = os.path.realpath(file_path)
        if not real_file.startswith(real_root + os.sep):
            return {"error": "Forbidden path", "path": path}
        if os.path.exists(real_file) and os.path.isfile(real_file):
            return FileResponse(real_file)
        # SPA fallback: 非文件路径都返回 index.html
        return FileResponse(os.path.join(webui_dir, "index.html"))

    # ============================================================
    # 3. 注册 API 路由
    # ============================================================
    app.include_router(chat_router, tags=["chat"])
    app.include_router(channels_router)
    app.include_router(images_router, tags=["images"])
    app.include_router(mcp_router)
    app.include_router(skill_router)
    app.include_router(ws_chat_router, tags=["websocket"])

    # app.include_router(workspace_router)
    # app.include_router(webui_router)  # WebUI 兼容层 (/webui/*)

    # 静态文件服务（CSS/JS 等）
    app.mount("/static", StaticFiles(directory=_static_dir), name="static")

    # ------------------------------------------------------------
    # 中间件（纯 ASGI 包裹，置于最终返回前）
    # 顺序：API-Key 鉴权在内，安全响应头在外（对所有响应生效，含 401）
    # ------------------------------------------------------------
    # 速率限制（在 auth 之前，基于 API Key 或 user_id 限流）
    from middleware.rate_limit import RateLimitMiddleware
    rl_config = getattr(config.middleware, "rate_limit", None)
    if rl_config and rl_config.enabled:
        app = RateLimitMiddleware(
            app,
            requests_per_minute=rl_config.requests_per_minute,
            enabled=True,
        )

    app = _api_key_auth_middleware(
        app,
        auth_required=getattr(config.auth, "required", False),
        api_key=getattr(config.auth, "api_key", ""),
    )
    app = _security_headers_middleware(app)

    return app
