# 预算控制

> 控制单次回复的 token 消耗与上下文窗口占用，防止资源被耗尽

平台有三类 token 相关的控制机制，职责不同：

| 机制 | 配置项 | 作用 | 触发后果 |
|------|--------|------|----------|
| **回复预算** | `middleware.budget_control.*` | 限制单次回复的加权 token 消耗 | 注入收尾提示，并禁止继续调用工具 |
| **上下文压缩** | `agent.context_trigger_ratio` 等 | 限制模型上下文窗口的使用比例 | 自动压缩较早消息为结构化摘要 |
| **回填预算** | `context.backfill_message_limit` | 限制会话恢复时加载的历史消息条数 | 只加载最近 N 条历史 |

三者独立运行、互不干扰：回复预算管"这次回复花多少"，上下文压缩管"智能体能记住多少"，回填预算管"恢复会话时带入多少历史"。

> 取值与字段以 `core/config/schemas.py` 与 `configs/*.yaml` 为准；完整 token/轮次约束速查见 [token-constraints.md](token-constraints.md)。

## 回复预算

回复预算由 AgentScope 的 `ReplyBudgetControlMiddleware` 实现（本项目在 [factory.py:519-530](../core/agent/factory.py#L519-L530) 挂载），在单次回复内按 `reply_id` 累计加权 token 消耗，超限后强制智能体收尾。

### 工作原理

```
用户消息（ReplyStartEvent → 计数归零）
  │
  ▼
智能体推理 ──→ 工具调用 ──→ 智能体推理 ──→ ...
  │                │                │
  ▼                ▼                ▼
ModelCallEndEvent: 累计 input×1 + output×2（每次模型调用都累计，
                   含多轮工具调用中反复送入的完整上下文）
  │
  ▼
下一次推理前检查：累计 ≥ token_budget ?
  ├─ 否 → 正常推理
  └─ 是 → ① 向上下文注入收尾提示（HintBlock）
          ② 强制 tool_choice=none（禁止再调用工具）
          → 智能体只能输出总结性文本，回复结束（ReplyEndEvent → 计数清理）
```

加权公式：`消耗 = input_tokens × input_weight + output_tokens × output_weight`

`input_weight=1, output_weight=2`（本项目配置）意味着输出 token 的"成本"是输入的两倍——反映实际 API 定价中输出更贵的特点。注意每次模型调用的 input 都包含完整上下文，因此**多轮工具调用的回复消耗增长很快**，这正是预算控制要约束的场景。

### 触发行为（精确语义）

与"接近预算就提醒"的直觉不同，中间件的实际行为是：

- 判定条件为 `累计消耗 ≥ token_budget`（**达到即触发**，无"接近"缓冲）；
- 触发后**不只是提示**：同时把本轮后续推理的 `tool_choice` 强制为 `none`，工具调用被硬性禁止，智能体只能给出最终结论；
- 提示以 `HintBlock` 形式追加到上下文最后一条助手消息（若无则新建一条）；
- 计数状态存于 `agent.state.middle_context`（按 `reply_id` 隔离），跨 HITL 中断/恢复保持，`ReplyEndEvent` 时清理。

因此它是**硬收尾**而非软引导：一旦超限，该回复不会再产生新的工具调用。

### 配置

在 `configs/dev.yaml`（或 `prod.yaml`）的 `middleware.budget_control` 下配置：

```yaml
middleware:
  budget_control:
    enabled: true              # 总开关
    token_budget: 80000        # 单次回复加权 token 预算
    input_weight: 1            # 输入 token 权重
    output_weight: 2           # 输出 token 权重
```

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `enabled` | `bool` | `true` | 是否启用预算控制（schema 默认值） |
| `token_budget` | `int` | `80000` | 单次回复的加权 token 预算上限 |
| `input_weight` | `int` | `1` | 输入 token 的权重系数 |
| `output_weight` | `int` | `2` | 输出 token 的权重系数 |

> 中间件构造参数名为 `input_token_weight` / `output_token_weight`（框架默认均为 1），本项目配置字段为 `input_weight` / `output_weight`（输出默认 2），在 factory 中转接。`prod.yaml` 未配置该节，运行时使用上表 schema 默认值。

### 提示语

超限后注入的收尾提示硬编码于 [factory.py:526-529](../core/agent/factory.py#L526-L529)：

```
你本次回复的 token 预算即将耗尽。请尽快总结当前进展并给出结论，不要再调用工具。
```

（框架自带默认提示为英文版 wrap-up 文案，本项目通过 `hint_message` 覆盖为以上中文提示。）

### 遗留字段

`agent.reply_token_budget`（schema 与 `dev.yaml` 中仍存在）为**遗留字段，当前代码未引用**；实际生效的回复预算只有 `middleware.budget_control.token_budget`。两者在配置里保持同值（80000）只是历史原因，改预算请改 `middleware.budget_control.token_budget`。

### 调优建议

| 场景 | 建议 |
|------|------|
| 测试拦截守卫、调试工具链 | 临时 `enabled: false` 或调大 `token_budget: 200000` |
| 生产环境、成本敏感 | 保持默认 80000，或降低至 50000 |
| 复杂分析任务（多轮工具调用） | 调大至 120000–150000（工具调用轮数多时 input 重复累计） |

## 上下文压缩

上下文压缩由 AgentScope 的 `ContextConfig` 控制（本项目在 [factory.py:143-193](../core/agent/factory.py#L143-L193) 构造），管理模型上下文窗口的使用量。每次推理前检查，历史累积超过阈值时自动将较早消息压缩为摘要。

### 与回复预算的区别

| 维度 | 回复预算 | 上下文压缩 |
|------|----------|------------|
| **控制对象** | 单次回复的 token 消耗量（花费） | 模型上下文窗口的使用比例（记忆） |
| **触发条件** | 加权累计 `≥ token_budget` | 估算输入 token `> trigger_ratio × 模型上下文长度` |
| **触发后果** | 注入收尾提示 + 禁用工具 | 将较早消息压缩为结构化摘要 |
| **感知范围** | 单次回复（ReplyStart → ReplyEnd） | 整个会话的累积历史 |
| **是否可配置权重** | 是（`input_weight` / `output_weight`） | 否 |

简单来说：
- **回复预算** = "这次回复最多花多少 token" → 经济控制
- **上下文压缩** = "智能体最多能记住多少" → 记忆管理

### 配置

在 `configs/dev.yaml` 的 `agent` 下配置：

```yaml
agent:
  # 上下文压缩阈值 — 超过模型上下文的 60% 时触发压缩
  context_trigger_ratio: 0.6
  # 压缩后保留近期上下文比例 — 保留 ~15% 给最近消息
  context_reserve_ratio: 0.15
  # 单条工具结果 token 上限 — 防止单次工具调用占满上下文
  tool_result_limit: 15000
```

| 参数 | dev 值 | prod 值 | 说明 |
|------|--------|---------|------|
| `context_trigger_ratio` | `0.6` | `0.8` | 输入 token 超过该比例 × 模型上下文长度时触发压缩（框架上限 0.9） |
| `context_reserve_ratio` | `0.15` | `0.1` | 压缩后保留给最近消息的上下文比例（必须小于 trigger_ratio） |
| `tool_result_limit` | `15000` | `15000`（默认值） | 单条工具结果的最大 token 数，超出则截断 |

模型上下文长度取 `llm.context_size`（默认 128000），因此 dev 下压缩阈值约为 76K tokens、prod 下约为 102K tokens。

### 压缩产物

压缩触发后，AgentScope 会：

1. 将较早消息标记为待压缩（保留 `reserve_ratio × 上下文长度` 的近期消息）
2. 调用模型生成结构化摘要，用摘要替换被压缩的消息

摘要结构由本项目自定义（[factory.py:156-192](../core/agent/factory.py#L156-L192)），共 6 个字段：

| 字段 | 内容 |
|------|------|
| `task_overview` | 用户的核心需求和成功标准 |
| `current_state` | 已完成的工作、修改的文件、关键输出 |
| `important_discoveries` | 约束、决策、错误信息、失败的尝试 |
| `next_steps` | 具体的待办事项、阻塞项、优先级 |
| `context_to_preserve` | 用户偏好、领域细节、做出的承诺 |
| `tool_outputs_summary` | 重要工具调用的结果摘要（文件内容、API 响应等） |

压缩提示词同样为项目自定义（[factory.py:147-155](../core/agent/factory.py#L147-L155)），要求保留文件路径/API 端点/错误信息原文、使用绝对时间与完整路径。

详见 [上下文管理.md](上下文管理.md) 中的"压缩上下文"章节。

### 压缩前预警

当上下文逼近压缩阈值、但尚未触发压缩时，运行时注入会提前下发预警（配置于 `agent.injection`，接线见 [factory.py:195-202](../core/agent/factory.py#L195-L202)）：

```yaml
agent:
  injection:
    context_buffer_ratio: 0.15   # 压缩阈值前 15% 开始预警
    emit_hint_event: true        # 预警同时推送 HintBlockEvent（前端可渲染）
```

判定条件为输入 token 超过 `(trigger_ratio - context_buffer_ratio) × 上下文长度`（dev 下即 0.45 × 窗口），此时向上下文注入"当前 X tokens，达到 Y tokens 将触发压缩"的提示。约束：`context_buffer_ratio < context_trigger_ratio`。

### 约束校验

Agent 构造时（AgentScope 框架内）会校验以下关系，不满足直接报错：

- `context_trigger_ratio ≤ 0.9`（为压缩本身预留空间）
- `context_reserve_ratio < context_trigger_ratio`
- `injection.context_buffer_ratio < context_trigger_ratio`

另外，若系统提示词（加已有摘要）本身就超过压缩阈值，无法压缩，会在触发时报 `RuntimeError`。

### Token 计数说明

压缩阈值判定使用框架的 `model.count_tokens()` 估算。`core.token_counter.count_tokens` 的精确计数目前仅用于系统提示词的调试日志，尚未接入 `ContextConfig`（见 [factory.py:102-103](../core/agent/factory.py#L102-L103) 的 TODO）；接入前压缩/截断的 token 数为估算值。

## 回填预算

回填预算控制从 PostgreSQL 恢复会话（KV 未命中）时加载的历史消息量，避免一次性带入过多历史。入口见 [session.py:161-166](../core/session.py#L161-L166)、实现见 [session.py:968-1009](../core/session.py#L968-L1009)。

```yaml
context:
  backfill_message_limit: 20        # 回填消息条数上限（生效）
  backfill_token_budget: 15000      # 回填 token 预算（预留字段，当前未生效）
  fork_window_turns: 10             # fork 时保留的最近轮数（默认值）
```

| 参数 | 说明 |
|------|------|
| `backfill_message_limit` | 回填时最多加载的消息条数（**当前唯一实际生效的回填限制**） |
| `backfill_token_budget` | ⚠️ schema 预留字段，**代码未按 token 截断回填**，配置暂无效果 |
| `fork_window_turns` | fork 出的新会话仅保留最近 N 轮历史（系统提示词由 AgentFactory 注入，天然保留） |

回填只在会话恢复（KV 未命中）时生效一次，且会跳过 thinking/tool_call 内部行，仅保留 user/assistant 文本；不影响后续的回复预算或上下文压缩。

## 三者的关系

```
会话生命周期
│
├─ 会话恢复（PG → KV，KV 未命中时）
│   └─ 回填预算：最多加载 N 条历史消息（token 预算字段暂未生效）
│
├─ 每次推理前
│   ├─ 压缩前预警：输入 token 超过 (trigger-buffer)×窗口 → 注入预警提示
│   └─ 上下文压缩：输入 token 超过 trigger×窗口 → 压缩较早消息为摘要
│
└─ 单次回复过程中
    └─ 回复预算：加权累计 ≥ token_budget → 注入收尾提示 + 禁用工具调用
```

三者各司其职，互不冲突：
- **回填预算**控制"恢复时加载多少历史"
- **上下文压缩**控制"上下文窗口里留多少记忆"
- **回复预算**控制"这次回复最多消耗多少 token"
