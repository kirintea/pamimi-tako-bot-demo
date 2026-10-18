# Token 约束

## 背景

Agent 执行任务时会进行多轮工具调用与思考，这些中间过程在企业微信等渠道中不会展示给用户。当一次回复涉及较多中间步骤时，用户侧往往只感知到「长时间没有输出」，而后台可能已经连续执行了多轮工具。

为避免 Agent 陷入死循环式任务流程，或持续产出幻觉内容，系统在 **Token** 与 **轮次** 两个维度设置了硬性约束。以下约束的取值与字段以 `core/config/schemas.py` 与 `configs/*.yaml` 的实际配置为准。

## 核心硬兜底

| 约束项             | 取值        | 配置字段         | 说明                                                                                 |
| ------------------ | ----------- | ---------------- | ------------------------------------------------------------------------------------ |
| 单次输出长度上限   | 4096 tokens | `llm.max_tokens`   | 限制单条回复的最大输出长度，防止单次输出过长。                                       |
| 会话上下文历史上限 | 128K tokens | `llm.context_size` | 会话上下文窗口硬上限；历史逼近上限时由压缩机制处理，防止上下文无限膨胀。             |
| 自动轮次上限       | 50 轮       | `agent.max_iters`  | Agent 后台自动执行（工具调用 / ReAct 迭代）的最大轮次，用于阻断死循环式任务流程。     |

## 完整约束清单

除上述核心兜底外，系统还有以下 token / 轮次相关约束，配置于 [../configs/dev.yaml](../configs/dev.yaml) 与 `configs/prod.yaml`。

### Token 约束

| 约束项           | 配置字段                                            | 当前值 | 作用                                                     |
| ---------------- | --------------------------------------------------- | ------ | -------------------------------------------------------- |
| 单条工具结果截断 | `agent.tool_result_limit`                             | 15000  | 单次工具返回超过即截断，防止单条工具占满上下文。         |
| 回复加权预算     | `middleware.budget_control.token_budget`              | 80000  | 单次回复累计消耗（加权）接近时注入收尾提示，引导结束。   |
| ↳ 加权权重       | `middleware.budget_control.input_weight` / `output_weight` | 1 / 2  | 消耗 = 输入×1 + 输出×2（输出 token 成本更高）。          |
| 回填 token 预算  | `context.backfill_token_budget`                       | 15000  | 会话从持久化恢复时加载历史消息的 token 上限。             |
| 压缩触发比例     | `agent.context_trigger_ratio`                         | 0.6    | 输入 token 超过 60%×窗口时，将较早消息压缩为摘要。        |
| 压缩后保留比例   | `agent.context_reserve_ratio`                         | 0.15   | 压缩后为最近消息保留约 15% 窗口。                         |
| 压缩前预警比例   | `agent.injection.context_buffer_ratio`                | 0.15   | 逼近压缩阈值前 15% 开始下发预警事件。                     |

### 轮次 / 条数约束

| 约束项         | 配置字段                       | 当前值       | 作用                                                         |
| -------------- | ------------------------------ | ------------ | ------------------------------------------------------------ |
| 回填消息条数   | `context.backfill_message_limit` | 20           | 会话恢复时最多加载多少条历史消息。                           |
| Fork 保留轮次  | `context.fork_window_turns`      | 10（默认值） | fork 出的新会话仅保留最近 N 轮，系统提示词天然保留。         |

> **说明**
>
> - 回复预算由 `ReplyBudgetControlMiddleware` 接线（`core/agent/factory.py`），消耗公式 = `input_tokens × input_weight + output_tokens × output_weight`，接近预算时向上下文注入收尾提示，引导智能体结束，而非硬中断。
> - 配置字段 `agent.reply_token_budget` 为遗留字段，当前未被代码引用；实际生效的回复预算是 `middleware.budget_control.token_budget`。
