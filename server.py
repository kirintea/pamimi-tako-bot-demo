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
from fastapi.openapi.docs import get_redoc_html, get_swagger_ui_html
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from api.chat import router as chat_router
from api.channels import router as channels_router
from api.images import router as images_router
from api.mcp import router as mcp_router
from api.observe import router as observe_router
from api.rag_proxy import router as rag_proxy_router
from api.skill import router as skill_router
from api.stats import router as stats_router
from api.admin_credentials import router as admin_credentials_router
from api.ws_chat import router as ws_chat_router
from api.users import router as users_router
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


def _api_key_auth_middleware(app, *, auth_required: bool = False, api_key: str = "",
                              db_provider=None):
    """全局 API-Key 鉴权（默认关闭，仅 auth_required=True 或 AUTH_REQUIRED=true 时生效）

    放行路径：/webui、/webui/、/health、/docs、/openapi.json、/redoc
    以及所有以 /webui/ 开头的路径。其余路径需携带正确的 X-API-Key 请求头。

    /rag 与 /webui-config 一并放行：/rag/ 是 LightRAG webui 的反代入口（浏览器直接
    打开，带不了 X-API-Key 头），其页面内 API 由反代注入 LightRAG 自己的 token 认证；
    /webui-config 仅下发功能开关（RAG 地址或 null、auth_required），无敏感信息。

    /static/vendor/swagger-ui/ 一并放行：/docs /redoc 页面（已在白名单）由浏览器
    直接加载其 JS/CSS/favicon，带不了 X-API-Key 头；仅此子目录（本地托管的
    swagger-ui/redoc 公开资源），不含其他 /static 文件。

    优先级：环境变量 AUTH_REQUIRED/API_KEY > YAML 配置 auth.required/auth.api_key。

    按应用凭证（缺口 2，2026-11-11 子计划）——非主 key 走凭证表三分支：
      主 key 命中（env > YAML）→ 放行，不注入 credential（D7 旁路）
      db_provider 未接线 → 维持旧行为 401（单测/旧调用方；生产 create_app 恒接线）
      DB 未初始化 / 查询异常 → 503 {"error":"CredentialStoreUnavailable"}（D3）
      凭证表命中（status=active, cred_type=api_key）→ state["credential"] 注入 + 放行
      凭证表未命中 → 401（同原文案）
    """
    from core.credentials import verify_credential  # 延迟导入避免循环

    _PUBLIC_EXACT = {
        "/webui", "/webui/", "/health", "/docs",
        "/openapi.json", "/redoc",
        "/webui-config", "/rag",
    }
    _PUBLIC_PREFIX = ("/webui/", "/rag/", "/static/vendor/swagger-ui/")

    _UNAUTHORIZED = {"error": "Unauthorized", "hint": "Missing or invalid X-API-Key"}

    async def middleware(scope, receive, send):
        if scope["type"] != "http":
            await app(scope, receive, send)
            return

        async def deny(status: int, payload: dict) -> None:
            await send({
                "type": "http.response.start",
                "status": status,
                "headers": [(b"content-type", b"application/json")],
            })
            await send({
                "type": "http.response.body",
                "body": json.dumps(payload).encode("utf-8"),
            })

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
        if path in _PUBLIC_EXACT or path.startswith(_PUBLIC_PREFIX):
            await app(scope, receive, send)
            return

        headers = {k.lower(): v for k, v in scope.get("headers", [])}
        provided = headers.get(b"x-api-key", b"").decode("latin-1")
        expected = os.environ.get("API_KEY") or api_key
        scope.setdefault("state", {})
        if expected and provided == expected:
            # D7 主 key 旁路：只存 api_key，不注入 credential（运维身份非应用身份）
            scope["state"]["api_key"] = provided
            await app(scope, receive, send)
            return

        if not provided:
            await deny(401, _UNAUTHORIZED)
            return

        # --- 缺口 2：非主 key 走按应用凭证表 ---
        if db_provider is None:
            # 未接入凭证库（旧行为兼容）：错误 key 一律 401
            await deny(401, _UNAUTHORIZED)
            return
        db = db_provider()
        if db is None or not getattr(db, "is_initialized", False):
            # D3：凭证库不可用 ≠ 凭证错误，客户端需可区分
            await deny(503, {"error": "CredentialStoreUnavailable"})
            return
        try:
            cred = await verify_credential(db, provided)
        except Exception:
            await deny(503, {"error": "CredentialStoreUnavailable"})
            return
        if cred is None:
            await deny(401, _UNAUTHORIZED)
            return
        scope["state"]["credential"] = cred
        scope["state"]["api_key"] = provided
        await app(scope, receive, send)

    return middleware


async def _database_unavailable_handler(
    request: Request, exc: DatabaseUnavailableError
) -> JSONResponse:
    """数据库不可用 → 503（未初始化/运行期故障下 /mcp /skill 等未守卫路由的统一响应）"""
    return JSONResponse(
        status_code=503,
        content={"detail": f"数据库不可用: {exc}"},
    )


# ------------------------------------------------------------
# /ws/chat 协议文档（方案 A，人工批准：.draft/2026-11-10-ws-in-openapi-docs.md）
# OpenAPI 3.x 无 WebSocket 通道概念，@router.websocket 不会写入 schema，
# 故在 app.openapi() 生成结果上注入伪路径；帧结构对照 api/ws_chat.py 实现。
# ------------------------------------------------------------
_WS_CHAT_DESCRIPTION = """WebSocket 全双工流式对话通道（替代 SSE 单向通信）。

> **注意**：这是 WebSocket 升级端点，Swagger UI 的 **Try it out 不可用**（点击会发起普通 GET → **404**）。请用 WebSocket 客户端连接，例如浏览器 `new WebSocket(...)` 或 `wscat`。

## 连接

```
wss://<host>/ws/chat?user_id=<uid>&session_id=<sid>&device_id=<did>&channel=<ch>
```

| Query 参数 | 必填 | 默认 | 说明 |
| --- | --- | --- | --- |
| `user_id` | 认证启用时必填 | `anonymous` | 用户标识（认证启用时不允许 anonymous） |
| `session_id` | 否 | 自动创建 UUID | 会话 ID，不传则新建 |
| `device_id` | 否 | `unknown` | 设备标识，用于多端状态广播 |
| `channel` | 否 | `web` | 渠道标识（连接级归因，1-16 位 `[A-Za-z0-9_-]`；非法 → `error` 帧 + close **4400**） |

## 认证（AUTH_REQUIRED=true 时）

连接后**首帧**必须为：

```json
{"type": "auth", "payload": {"api_key": "<API_KEY>"}}
```

否则服务端关闭连接（close code **4401**）；认证启用且 `user_id` 非法 → close code **4400**。未启用认证时忽略 `auth` 帧。

连接建立后服务端先发 `connected`。

## 客户端 → 服务端（入站帧）

| type | payload | 说明 |
| --- | --- | --- |
| `auth` | `{"api_key": "..."}` | 首帧认证（见上） |
| `chat` | `{"message": "文本或多模态数组"}` | 发起一轮对话 |
| `cancel` | `{}` | 取消当前连接的生成任务 |
| `interrupt` | `{}` | 多端中断请求（任意设备可发） |
| `ping` | `{}` | 心跳，服务端回 `pong` |

`message` 支持多模态（ContentPart 数组）：

```json
{"type": "chat", "payload": {"message": [
  {"type": "text", "text": "描述"},
  {"type": "image", "key": "<由 /images/upload 返回的对象存储 key>"}
]}}
```

## 服务端 → 客户端（出站帧）

| type | payload 字段 | 说明 |
| --- | --- | --- |
| `connected` | `session_id`, `user_id`, `device_id` | 连接建立确认 |
| `pong` | — | `ping` 应答 |
| `busy` | `owner`, `started_at`, `message` | 其他设备正在生成，本次 `chat` 被拒 |
| `pending_reply` | `text`, `reply_status` | 重连时补发上一轮未读回复 |
| `generation_in_progress` | `message`, `partial_text` | 重连时上一轮仍在生成 |
| `other_device_active` | `device_id`, `message` | 其他设备正在生成的广播 |
| `text_delta` | `delta` | 回复文本增量 |
| `thinking_delta` | `delta` | 思考文本增量 |
| `tool_call` | `tool_name`, `tool_call_id`, `tool_args` | 工具调用开始 |
| `tool_result` | `tool_call_id`, `state`, `result` | 工具执行结果 |
| `reply_end` | `text`, `finished`, `finished_reason`, `command`, `new_session_id` | 本轮结束 |
| `interrupted` | `reason`, `session_id` | 生成被中断 |
| `interrupt_requested` | `status` | 中断请求受理结果（`cancel_requested` / `no_active_generation`） |
| `error` | `message` | 错误描述 |

一轮对话时序：`connected` → (`thinking_delta` / `text_delta` / `tool_call` / `tool_result`)* → `reply_end`。
"""

_WS_CHAT_PATH_ITEM = {
    "get": {
        "tags": ["websocket"],
        "summary": "WebSocket 流式对话",
        "description": _WS_CHAT_DESCRIPTION,
        "operationId": "websocket_chat",
        # 非标准扩展：标记这是 WebSocket 升级端点（非普通 HTTP GET）
        "x-websocket": True,
        "parameters": [
            {
                "name": "user_id",
                "in": "query",
                "required": False,
                "description": "用户标识（认证启用时必填，不允许 anonymous）",
                "schema": {"type": "string", "default": "anonymous"},
            },
            {
                "name": "session_id",
                "in": "query",
                "required": False,
                "description": "会话 ID，不传则自动创建",
                "schema": {"type": "string"},
            },
            {
                "name": "device_id",
                "in": "query",
                "required": False,
                "description": "设备标识，用于多端状态广播",
                "schema": {"type": "string", "default": "unknown"},
            },
            {
                "name": "channel",
                "in": "query",
                "required": False,
                "description": "渠道标识（连接级归因，1-16 位 [A-Za-z0-9_-]；非法 → error 帧 + close 4400）",
                "schema": {"type": "string", "default": "web"},
            },
        ],
        "responses": {
            "101": {"description": "Switching Protocols — WebSocket 升级成功"},
        },
    }
}

# SSE 端点 → 200 响应标注 text/event-stream（替代误导性的 application/json）
_SSE_ANNOTATIONS = (
    ("/chat/stream", "post"),
    ("/sessions/{session_id}/stream", "get"),
)

# 启动时落盘的 OpenAPI schema 路径（仓库根 openapi.json，人工指令 2026-11-10）
# HTTP/S 路由或文档改动后，重启服务即自动刷新该文件。
_OPENAPI_DUMP_PATH = Path(__file__).resolve().parent / "openapi.json"


def _dump_openapi_schema(schema: dict, path: Path) -> bool:
    """把 OpenAPI schema 落盘为 JSON（UTF-8、中文不转义、内容无变化则不写）。

    Args:
        schema: 完整 OpenAPI schema（dict）
        path: 目标文件路径

    Returns:
        bool: 是否发生写入（False = 内容无变化，跳过写入）

    Raises:
        OSError: 写入失败时抛出（由调用方决定告警策略，不得阻断启动）
    """
    text = json.dumps(schema, ensure_ascii=False, indent=2) + "\n"
    if path.exists() and path.read_text(encoding="utf-8") == text:
        return False
    path.write_text(text, encoding="utf-8")
    return True


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
        # kv.backend=jsonl（单进程本地开发）时不依赖 Redis：直接用进程内总线，跳过连接重试。
        message_bus: RedisMessageBus | None = None
        _bus_ok = False
        if config.kv.backend == "jsonl":
            app.state.message_bus = InMemoryMessageBus()
            logger.info("消息总线：InMemoryMessageBus（kv.backend=jsonl，单进程，无需 Redis）")
        else:
            # Redis 不可达时退避重试；仍失败则降级为 InMemoryMessageBus（单实例进程内总线），
            # 应用继续启动：对话触发 / 锁 / 事件回放与订阅在无 Redis 时仍可用，
            # 仅跨实例广播与多端状态跟踪不可用（Q4 降级单实例语义）。
            message_bus = RedisMessageBus(config.redis.url)
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
            if config.kv.backend == "jsonl":
                logger.info("会话管理器已就绪 (KV: jsonl)")
            else:
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
        if _bus_ok and message_bus is not None and message_bus._redis:
            status_tracker = SessionStatusTracker(message_bus._redis)
            app.state.session_status_tracker = status_tracker
            logger.info("会话状态跟踪器已就绪（多端并发模式）")
        else:
            app.state.session_status_tracker = None
            logger.info("会话状态跟踪器未启用（多端并发状态同步不可用）")

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
        from core.channels.session_map import ChannelSessionMap

        session_map = ChannelSessionMap(
            session_mgr.kv, app.state.config.redis.key_prefix,
        )
        channel_manager = ChannelManager(
            config_store=channel_config_store,
            bus=app.state.message_bus,
            chat_service=chat_service,
            object_storage=obj_storage,
            db=getattr(app.state, "database_manager", None),
            session_mgr=session_mgr,
            session_map=session_map,
            status_tracker=status_tracker,
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

        # RAG WebUI 反代（rag.api_url 非空时启用；/rag/ → 上游 LightRAG，自动登录注入）
        from api.rag_proxy import startup as rag_proxy_startup
        await rag_proxy_startup(config)

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
        if message_bus is not None and app.state.message_bus is not message_bus:
            # 降级场景：关闭未使用的 Redis 实例（_redis=None 时 aclose 自行跳过）
            await message_bus.aclose()
        logger.info("消息总线已关闭")

        # 关闭 RAG WebUI 反代（释放上游 HTTP 客户端）
        from api.rag_proxy import shutdown as rag_proxy_shutdown
        await rag_proxy_shutdown()

        if db_mgr.is_initialized:
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
        # 关闭默认 /docs /redoc：其 HTML 引用 cdn.jsdelivr.net / Google Fonts 等
        # 外部资源，会被安全响应头中间件的 CSP（仅允许 'self'）拦截导致白屏。
        # 改为下方自注册路由，资源本地托管于 api/static/vendor/swagger-ui/。
        docs_url=None,
        redoc_url=None,
    )

    # ------------------------------------------------------------
    # /docs /redoc — 本地资源版（方案 A，见 .draft/2026-11-10-docs-blank-csp.md）
    # CSP 不放宽；升级 swagger-ui/redoc 时替换 vendor 目录内文件即可。
    # 注意：create_app 末尾 app 会被 ASGI 中间件函数重新赋值，闭包引用的
    # 必须是此处捕获的 FastAPI 实例属性，不能在请求时再读 app.*。
    # ------------------------------------------------------------
    _openapi_url = app.openapi_url
    _app_title = app.title

    @app.get("/docs", include_in_schema=False)
    async def swagger_ui_local():
        return get_swagger_ui_html(
            openapi_url=_openapi_url,
            title=f"{_app_title} - Swagger UI",
            swagger_js_url="/static/vendor/swagger-ui/swagger-ui-bundle.js",
            swagger_css_url="/static/vendor/swagger-ui/swagger-ui.css",
            swagger_favicon_url="/static/vendor/swagger-ui/favicon.svg",
        )

    @app.get("/redoc", include_in_schema=False)
    async def redoc_local():
        return get_redoc_html(
            openapi_url=_openapi_url,
            title=f"{_app_title} - ReDoc",
            redoc_js_url="/static/vendor/swagger-ui/redoc.standalone.js",
            redoc_favicon_url="/static/vendor/swagger-ui/favicon.svg",
            with_google_fonts=False,
        )

    # ------------------------------------------------------------
    # OpenAPI 扩展：注入 /ws/chat 伪路径 + SSE 响应标注（方案 A）
    # 覆写实例方法而非类方法；原 openapi() 带缓存（app.openapi_schema），
    # 注入保持幂等，路由变化触发重新生成后会再次注入。
    # ------------------------------------------------------------
    _orig_openapi = app.openapi

    def _openapi_with_non_http():
        schema = _orig_openapi()
        paths = schema.setdefault("paths", {})

        # 1) WebSocket 端点（OpenAPI 无 ws 通道概念，以 get 伪路径 + x-websocket 标记）
        if "/ws/chat" not in paths:
            paths["/ws/chat"] = _WS_CHAT_PATH_ITEM

        # 2) SSE 端点：200 响应从 application/json 改为 text/event-stream
        for path, method in _SSE_ANNOTATIONS:
            op = paths.get(path, {}).get(method)
            if not op:
                continue
            resp = op.setdefault("responses", {}).setdefault(
                "200", {"description": "Successful Response"}
            )
            content = resp.setdefault("content", {})
            if "application/json" in content and "text/event-stream" not in content:
                content.pop("application/json")
                content["text/event-stream"] = {
                    "schema": {"type": "string"},
                    "example": "data: {\"type\":\"text_delta\",\"payload\":{\"delta\":\"...\"}}\n\n",
                }
                resp["description"] = "SSE 事件流（text/event-stream）"

        return schema

    app.openapi = _openapi_with_non_http

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
    app.include_router(observe_router)
    app.include_router(rag_proxy_router)  # /webui-config + /rag/*（LightRAG webui 反代）
    app.include_router(admin_credentials_router)
    app.include_router(stats_router)
    app.include_router(ws_chat_router, tags=["websocket"])
    app.include_router(users_router, tags=["users"])

    # app.include_router(workspace_router)
    # app.include_router(webui_router)  # WebUI 兼容层 (/webui/*)

    # 静态文件服务（CSS/JS 等）
    app.mount("/static", StaticFiles(directory=_static_dir), name="static")

    # ------------------------------------------------------------
    # 启动时落盘 OpenAPI schema → 仓库根 openapi.json（人工指令 2026-11-10）
    # 此处全部路由已注册，app.openapi()（含 /ws/chat 注入 + SSE 标注）即最终 schema；
    # 内容无变化不重写（不扰动 git），落盘失败仅告警、不阻断启动。
    # ------------------------------------------------------------
    try:
        _dumped = _dump_openapi_schema(app.openapi(), _OPENAPI_DUMP_PATH)
        logger.info(
            "OpenAPI schema 已{}: {}",
            "更新" if _dumped else "同步（无变化）",
            _OPENAPI_DUMP_PATH,
        )
    except Exception as _dump_err:  # noqa: BLE001
        logger.warning("OpenAPI schema 落盘失败（不影响启动）: {}", _dump_err)

    # ------------------------------------------------------------
    # 中间件（纯 ASGI 包裹，置于最终返回前）
    # 顺序：API-Key 鉴权在内，安全响应头在外（对所有响应生效，含 401）
    # ------------------------------------------------------------
    # 在 rate_limit/CORS 包裹前捕获 FastAPI 实例——凭证校验的 db_provider
    # 需读 app.state.database_manager（lifespan 里赋值），闭包不能引用
    # 之后被重新赋值的中间件变量 app。
    fastapi_app = app

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
        # 惰性取值：lifespan 前（未赋值 → None）/DB 未配置（未初始化）均安全（D3 → 503）
        db_provider=lambda: getattr(fastapi_app.state, "database_manager", None),
    )

    # CORS：必须包在 auth 外层——预检 OPTIONS 不携带 X-API-Key，若 auth 在
    # 外层会把预检 401 掉；同时保持在安全响应头内层，让预检/401/429 响应
    # 也带上安全头（D3 顺序：安全头 → CORS → auth → rate_limit → 路由）。
    # enabled=false（schema 默认）时整段不挂载，行为与旧版完全一致。
    # WS 不受 CORS 管辖（浏览器 WebSocket 握手不执行 CORS 检查），无需处理。
    _cors_cfg = getattr(config.middleware, "cors", None)
    if _cors_cfg is not None and _cors_cfg.enabled:
        # FastAPI 文档惯用路径；实为 starlette.middleware.cors 同一类（已验证 is 同一对象）
        from fastapi.middleware.cors import CORSMiddleware

        app = CORSMiddleware(
            app,
            allow_origins=_cors_cfg.allow_origins,
            allow_methods=_cors_cfg.allow_methods,
            allow_headers=_cors_cfg.allow_headers,
            expose_headers=_cors_cfg.expose_headers,
            max_age=_cors_cfg.max_age,
            allow_credentials=_cors_cfg.allow_credentials,
        )

    app = _security_headers_middleware(app)

    return app
