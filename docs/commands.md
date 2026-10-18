# 斜杠指令（Slash Commands）

在对话消息进入 LLM 之前，平台入站层拦截以 `/` 开头的指令文本并直接回执（不落库、不进 AgentState）。

## 指令列表

| 指令 | 功能 |
|------|------|
| `/new` | 新建会话（企微：旋转渠道会话指针，后续消息进入全新 session；Web：返回 `new_session_id` 由前端切换） |
| `/stop` | 中断当前正在生成的回复（请求 cancel） |
| `/compact` | 压缩当前会话上下文（调用 `agent.compress_context()`，摘要替代原始历史） |
| `/help` | 显示可用指令列表 |

> v1 最小集（D8）；`/model` `/history` `/status` 等复用现有 REST 端点，后续可按需新增。

## 可用场景

| 传输 | 入口 | 说明 |
|------|------|------|
| Web SSE | `POST /chat/stream` | 指令回执以 SSE 事件返回：`session` + `text_delta` + `reply_end(command=true)` |
| Web HTTP | `POST /chat/` | Fire-and-Forget 模式：向事件总线发布回执，返回 `{ status: "command", session_id }` |
| Web WS | `/ws/chat` | WebSocket 帧：`text_delta` + `reply_end(command=true)` |
| 渠道（企微等） | `ChannelManager.handle_inbound` | 通过 `send_delta` 直接回执，不占位、不订阅总线 |

## 行为规则

### 指令拦截优先级（D6）

指令拦截 **先于** busy 检查。这意味着即使当前会话正在生成回复，用户仍可发送 `/stop` 中断生成。

### 输入规范化（D7）

- 大小写不敏感：`/HELP` 等同 `/help`
- 全角斜杠自动转换：`／new` → `/new`
- Bot 后缀自动剥离：`/new@mybot` → `/new`

### 未知指令处理（D3）

- 形态像指令（`/[a-z]...`）且与已注册命令 close-match（cutoff 0.6）→ 提示"你是想用 /xxx 吗？"
- 形态不像指令或与任何命令都不接近 → **放行给 LLM**（如 `/etc/hosts 是什么` 不被劫持）

### 不触发指令分发的消息（D4）

- 带媒体附件的消息（`media` 非空）
- Web 端 `message` 为 `list[ContentPart]`（多模态消息）

### 指令回执不落库（D5）

指令结果为 side-channel 回执：

- **不写入** PG `conversations` 表
- **不进入** AgentState 消息历史
- `/new` 的目标恰恰是不污染上下文

## 企微场景

### 渠道会话指针

企微单聊天窗口的所有消息默认进入同一个会话（`derive_session_id` = 确定性 hash），导致上下文越滚越大。

`/new` 通过 **渠道会话指针** 解决：

1. `/new` 铸造全新 `session_id`，写入 KV 指针（永不过期）
2. 后续消息自动进入新会话，旧会话历史独立保留
3. 存量用户不受影响：指针 miss 时自动固化 derive 的默认值

### 指针 key 设计

```
{redis.key_prefix}__chmap__:{user_id}:{channel}:{chat_id}:cur
→ {"session_id": "ch-xxxxxxxx", "last_active": 1729000000}
```

- 以 `:cur` 结尾，**不以** `:meta` 结尾 — 不会被 `list_user_sessions` / `list_all_sessions` 的 SCAN glob 扫出
- TTL 永不过期（`ex=None`）— 过期回退 derive 会让 `/new` 失效
- 指针值含 `last_active` 时间戳：入站消息 touch；空闲超过该渠道 `session_idle_hours`
  （`configs/channels.json` 条目级、按 id 对应，小时，缺省 24，`<=0` 禁用）自动轮换新会话
  （等价于自动 `/new`，旧会话 PG 历史保留、KV 即时清理）

  已知边界（接受）：
  - 空闲轮换后首条若是斜杠指令：确认语 + 会话ID 并进指令回执发出；`/new` 例外——以 `/new` 自身回执为准（避免双会话ID），其会话ID 随下一条普通消息的推送发出
  - `/help`、`/stop` 不创建会话：合并回执已发过会话ID 后，下一条普通消息会再推送一次同一会话ID（仅重复、无错误信息）
  - 过期后首条若是 `/stop`：取消请求落在新会话上，取消不到旧会话（仅当旧 run 挂死超过 `session_idle_hours` 阈值才可能遇到）

## Web 端事件契约

### SSE（`/chat/stream`）

```
event: session
data: {"user_id": "u1", "session_id": "new-sid-xxx"}

event: text_delta
data: {"delta": "✅ 已开启新会话，之前的历史不再携带。"}

event: reply_end
data: {"text": "✅ 已开启新会话，之前的历史不再携带。", "finished": true, "command": true, "new_session_id": "new-sid-xxx"}
```

### WebSocket（`/ws/chat`）

```json
{ "type": "text_delta", "payload": { "delta": "..." } }
{ "type": "reply_end", "payload": { "text": "...", "finished": true, "command": true, "new_session_id": "new-sid-xxx" } }
```

### WebUI 行为

WebUI 的 `useMessages` hook 在收到 `reply_end` 且 `command && new_session_id` 时，调用 `switchSession(newSession_id)` 切换到新会话（内部重连 WS + 加载历史），并在新会话中显示指令回执消息。

## 新增自定义指令

### 方式一：注册到内置指令集

在 `core/command/builtin.py` 的 `BUILTIN_COMMAND_SPECS` 中添加新的 `CommandSpec`，然后在 `register_builtin_commands` 中注册 handler 到路由器：

```python
BUILTIN_COMMAND_SPECS: tuple[CommandSpec, ...] = (
    # ... 现有指令
    CommandSpec("/model", "切换模型", "切换当前会话使用的模型", arg_hint="<model_name>", accepts_args=True),
)

def register_builtin_commands(router: CommandRouter) -> None:
    # ... 现有注册
    router.exact("/model", cmd_model)  # 或 router.prefix("/model ", cmd_model)
```

### 方式二：运行时注册

通过 `CommandRouter` 的 `exact` / `prefix` 方法在运行时注册：

```python
from core.command import get_command_service

svc = get_command_service()
svc.router.exact("/mycommand", my_handler)       # 精确匹配
svc.router.prefix("/deploy ", deploy_handler)     # 前缀匹配（带参数）
```

### Handler 签名

```python
async def my_handler(ctx: CommandContext) -> CommandResult:
    # ctx.raw      — 归一化后的原始文本
    # ctx.args     — prefix 匹配时去掉前缀后的参数部分
    # ctx.user_id  — 用户 id
    # ctx.session_id — 当前会话 id
    # ctx.transport — "web" / "wecom" / ...
    # ctx.session_mgr — SessionManager 实例
    # ctx.session_map — ChannelSessionMap 实例（渠道才有）
    # ctx.status_tracker — SessionStatusTracker 实例
    return CommandResult(text="回执文本", new_session_id=None)
```

## 模块结构

| 文件 | 职责 |
|------|------|
| `core/command/__init__.py` | 导出公共 API |
| `core/command/router.py` | `normalize_command_text` / `CommandResult` / `CommandContext` / `CommandRouter` |
| `core/command/builtin.py` | `BUILTIN_COMMAND_SPECS` + `/new /stop /compact /help` handlers + `build_help_text` + `register_builtin_commands` |
| `core/command/service.py` | `CommandService`（共享入口）+ `get_command_service()` 单例 |
| `core/channels/session_map.py` | `derive_session_id` + `ChannelSessionMap` 渠道会话指针 |