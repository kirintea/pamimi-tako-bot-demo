# 🐙Pamimi Tako Bot Demo

基于 AgentScope 2.0.7 的 AI Agent 平台服务。把 Agent 当服务跑：多用户隔离、多实例无状态、工具调用全程守卫、沙箱执行环境。

这是一个学习用的 demo 项目，用于探索 AgentScope 的服务化能力。

## 特性

### 核心能力

- **多用户会话隔离** — 每个 `(user_id, session_id)` 维护独立 Agent 状态
- **会话分支（Fork）** — 基于已有会话创建分支，支持多级 fork，PG 历史拷贝 + 原子回滚
- **多实例无状态** — RedisMessageBus 实现分布式锁、Pub/Sub 事件广播、回放日志
- **流式输出** — SSE + WebSocket 双通道实时推送（text/thinking/tool_call/tool_result）
- **多设备并发** — SessionStatusTracker 通过 Redis 广播 idle/generating/interrupting 状态，跨设备取消

### 工具与安全

- **内置工具** — Bash/Read/Write/Edit/Glob/Grep + TaskCreate/List/Get/Update + PowerShell
- **MCP 扩展** — stdio（有状态子进程）和 HTTP/SSE 两种传输协议，热加载
- **Skill 系统** — SKILL.md 文件驱动，支持上传/管理
- **工具守卫** — 工具名级黑白名单（`tool_guard`）
- **命令守卫** — 命令内容级安全守卫（`command_guard`），拦截 `rm -rf /`、反弹 shell 等
- **路径守卫** — 多域读写隔离（`path_guard`），用户空间 / 共享空间分权
- **Docker 沙箱** — 工具执行转发到隔离容器，agent_space 只读挂载

### 存储

- **双后端数据库** — PostgreSQL（asyncpg）/ MySQL（aiomysql），按 URL scheme 自动切换
- **双后端 KV** — Redis（生产）/ JSONL（开发单进程），会话状态 + 元数据持久化
- **有序持久化（v3）** — `turn_id` + `turn_seq`，text/thinking/tool_call/tool_result 分行存储
- **对象存储** — 本地文件系统 / S3 / 阿里云 OSS，图片上传 + presigned URL

### 上下文管理

- **自动压缩** — 触发比例 0.6，保留比例 0.15，结构化压缩 schema
- **上下文卸载** — 历史消息写入 JSONL 文件，Agent 可通过工具按需读取
- **PG 回填** — KV 未命中时从 PostgreSQL 恢复近期消息
- **回复预算** — 加权 token 预算控制（input:output = 1:2）

### 可观测性

- **OpenTelemetry** — OTLP gRPC 上报，自动记录 LLM 调用、工具调用、完整链路
- **Jaeger** — 分布式追踪可视化
- **Grafana** — 统一监控大盘
- **Prometheus** — 指标查询
- **Loki** — 日志聚合

### 前端

- **React 19 SPA** — TypeScript + TailwindCSS + Radix UI
- **中英双语** — i18next 支持
- **功能页面** — 对话、MCP 管理、Skill 管理、渠道管理、设置

### 渠道接入

- **企业微信** — WebSocket 长连接，无需公网 IP，原生流式回复
- **可扩展** — 注册式架构，预留飞书/邮件/Web 等渠道

## 快速开始

### 1. 环境准备

```bash
# Python 3.12+
uv sync
# 或
pip install -r requirements.txt
```

### 2. 配置

```bash
cp .env.example .env

# 编辑 .env，填入 API Key
LLM_API_KEY=sk-xxx
LLM_BASE_URL=https://api.siliconflow.cn/v1
LLM_MODEL_NAME=Qwen/Qwen3.6-35B-A3B
```

### 3. 启动依赖服务

```bash
# 一键启动 Redis + PostgreSQL + 可观测性栈
cd docker && docker compose up -d

# 或按需启动单个服务（模块化 compose 在 docker/deploy_yml/）
docker compose -f docker/deploy_yml/redis.yml up -d
docker compose -f docker/deploy_yml/postgres.yml up -d
```

默认连接：Redis `redis://localhost:6379/0`，PostgreSQL `postgresql://user:password@localhost:5432/dmx_agent_db`

### 4. 启动服务

```bash
APP_ENV=dev python main.py
# 或
./scripts/start.sh
```

### 5. 访问

| 入口 | 地址 | 说明 |
|------|------|------|
| WebUI | http://localhost:8090/webui | React 19 SPA（需先 `cd webui && npm run build`） |
| 旧版界面 | http://localhost:8090/ | 静态 HTML |
| API 文档 | http://localhost:8090/docs | Swagger |

## 架构

```
main.py                    # 启动入口 — 配置加载 + 日志 + OTel + uvicorn
server.py                  # FastAPI 应用 — create_app(config) + lifespan + 路由注册

api/
├── chat.py                # 对话 API（/chat/stream, /chat/, /sessions/*）
├── ws_chat.py             # WebSocket 对话（/ws/chat）
├── channels.py            # 渠道管理（/channels）
├── mcp.py                 # MCP 管理（/mcp）
├── skill.py               # Skill 管理（/skill）
├── images.py              # 图片上传（/images）
└── static/                # 旧版前端

core/
├── agent/factory.py       # Agent 工厂（模型/工具/中间件组装，9 种 provider）
├── config/                # 配置加载（YAML + ${ENV} 解析）
├── database.py            # 数据库管理器（PG/MySQL 双后端，fail-fast）
├── db/                    # 后端实现（base/factory/postgres/mysql/dialect/statements/ddl）
├── storage.py             # 存储层（Session/Conversation CRUD）
├── chat_service.py        # Chat 服务层（Fire-and-Forget 事件驱动）
├── session.py             # 会话管理器（KV 持久化 + fork + PG 回填）
├── kv/                    # KV 存储（RedisKVStore / JsonlKVStore）
├── redis_message_bus.py   # Redis 分布式消息总线
├── message_bus.py         # 内存消息总线（Redis 不可用时降级）
├── session_status.py      # 多设备并发状态同步
├── workspace.py           # 双域工作空间（agent_space + user_spaces）
├── user_service.py        # 用户服务（root 身份 + 注册）
├── token_counter.py       # tiktoken 精确计数
├── multimodal.py          # 多模态消息构建（文本 + 图片）
├── object_storage/        # 对象存储（local / S3 / OSS）
├── formatter/             # SiliconFlow 兼容 formatter
├── rag/                   # RAG 向量存储（placeholder）
├── tracing/               # OTel 追踪初始化
└── log/                   # 日志初始化（loguru + 文件轮转）

middleware/
├── tool_guard.py          # 工具名级黑白名单
├── command_guard.py       # 命令内容级安全守卫
├── path_guard.py          # 路径访问守卫（多域隔离）
├── docker_sandbox_proxy.py # Docker 沙箱代理
├── context_guard.py       # 上下文压缩守卫
├── rate_limit.py          # 速率限制（滑动窗口）
├── tool_manager.py        # 工具选择性加载
└── tracing_context.py     # OTel 上下文注入

webui/                     # React 19 SPA
├── src/pages/             # ChatPage, MCPPage, SkillPage, SettingsPage, SetupPage
├── src/components/        # ~35 个 UI 组件
├── src/api/               # API 客户端层
└── src/i18n/              # 中英双语

configs/
├── dev.yaml               # 开发环境配置
├── prod.yaml              # 生产环境配置
├── tools.yaml             # 工具选择性加载
├── mcps.json              # MCP 服务配置（热加载）
├── skills.json            # Skill 元数据（热加载）
├── channels.json          # 渠道配置（热加载）
└── prompt.py              # 系统提示词

docker/
├── deploy_yml/            # 模块化部署（redis/postgres/mysql/minio/milvus/...）
├── docker-compose.yaml    # 可观测性栈
└── *.yaml / *.yml         # 各组件配置

health_check/              # 健康检查（HTTP/Redis/PG/MySQL/LLM）
scripts/start.sh           # 启动脚本
workspaces/                # 双域工作空间
```

## API 端点

### 对话

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/chat/stream` | 流式对话（SSE） |
| POST | `/chat/` | Fire-and-Forget 触发 |
| GET | `/sessions/{session_id}/stream` | SSE 事件流订阅 |
| POST | `/sessions/{user_id}/{session_id}/interrupt` | 取消生成 |
| GET | `/health` | 健康检查 |

### 会话

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/sessions` | 列出活跃会话 |
| GET | `/sessions/{user_id}` | 用户历史会话列表 |
| GET | `/sessions/{user_id}/{session_id}/messages` | 消息历史 |
| POST | `/sessions/{user_id}/{session_id}/fork` | 会话分支 |
| DELETE | `/sessions/{user_id}/{session_id}` | 删除会话 |

### WebSocket

| 方法 | 路径 | 说明 |
|------|------|------|
| WS | `/ws/chat?user_id=&session_id=` | 全双工对话通道 |

### MCP / Skill / 渠道 / 图片

| 方法 | 路径 | 说明 |
|------|------|------|
| CRUD | `/mcp` | MCP 管理 |
| CRUD | `/skill` | Skill 管理 |
| CRUD | `/channels` | 渠道管理 |
| POST | `/images/upload` | 图片上传 |

## 配置

配置文件在 `configs/`，通过 `APP_ENV` 选择环境。所有外部地址支持 `${VAR:-default}` 语法。

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `LLM_API_KEY` | — | LLM API 密钥（必填） |
| `LLM_BASE_URL` | — | LLM 接口地址（必填） |
| `LLM_MODEL_NAME` | `qwen-max` | 模型名 |
| `DATABASE_URL` | `postgresql://user:password@localhost:5432/dmx_agent_db` | 数据库连接串 |
| `REDIS_URL` | `redis://localhost:6379/0` | Redis 连接串 |
| `OTEL_ENDPOINT` | `http://localhost:4317` | OTel gRPC 端点 |
| `APP_ENV` | `dev` | 环境（dev / prod） |
| `AUTH_REQUIRED` | `false` | 是否启用 API Key 认证 |
| `API_KEY` | — | API Key |

### LLM 支持的 Provider

openai / dashscope / anthropic / deepseek / gemini / moonshot / ollama / siliconflow / xai

### KV 后端

- **redis**（默认）— 生产用，支持多实例
- **jsonl** — 开发用，本地文件，单进程，无 Redis 依赖

### 沙箱模式

- **local**（默认）— 直接本机执行
- **docker** — 转发到沙箱容器，workspaces 隔离挂载

## 健康检查

```bash
# 一键检查
python health_check/check_all.py

# 单独检查
python health_check/check_http.py
python health_check/check_redis.py
python health_check/check_postgres.py
python health_check/check_llm.py
```

## 部署

### 单容器

```bash
docker compose build && docker compose up -d
```

### 应用 + 沙箱分离

```bash
docker compose -f docker-compose.sandbox.yml up -d
```

### 多实例 + Nginx 负载均衡

```bash
cd docker
docker compose -f docker-compose.multi-instance.yaml up -d --scale pamimi-tako-bot-demo=3
```

## 项目结构（目录）

```
pamimi-tako-bot-demo/
├── main.py / server.py
├── api/           # 路由层
├── core/          # 核心模块
├── middleware/     # 安全守卫 + 中间件
├── webui/         # React 19 SPA
├── configs/       # 配置文件
├── docker/        # 部署编排
├── health_check/  # 健康检查
├── scripts/       # 启动脚本
├── workspaces/    # 双域工作空间
├── tests/         # 测试
└── docs/          # 设计文档
```

## 相关文档

| 文档 | 说明 |
|------|------|
| [docs/middleware-guards.md](docs/middleware-guards.md) | 中间件守卫体系 |
| [docs/budget-control.md](docs/budget-control.md) | 预算控制 |
| [docs/上下文管理.md](docs/上下文管理.md) | 上下文压缩与卸载 |
| [docs/docker-deployment.md](docs/docker-deployment.md) | Docker 部署指南 |
| [docs/sandbox-guide.md](docs/sandbox-guide.md) | 沙箱隔离指南 |
| [docs/persistence-design.md](docs/persistence-design.md) | 持久化设计 |

## TODO

- 补文档。
- 重新写前端界面，当前刚好凑合能用。
- 守卫测试，测试样例不是很多，也不确定这是不是真有用。
- 服务实例和沙箱和用户之间的分配问题，计划是一个用户分一个沙箱，但这边验证资源有限，先标记一下吧。
- RAG 向量存储集成（当前是 placeholder）。