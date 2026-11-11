# -*- coding: utf-8 -*-
"""从 conversations 表重建历史回复流程，实测每次模型调用的 token 消耗。

- 按 (user_id, session_id, turn_id) 分组为「一次回复」
- 每个 assistant 步骤 = 一次模型调用（tool_call / text）
- 模拟 ReplyBudgetControlMiddleware：累计 input×1 + output×2
- 输出 JSON 报告（避免控制台编码问题）
"""

import asyncio
import io
import json
import os
import sys
import urllib.parse
from collections import Counter
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv(".env", override=True)

import aiomysql

from core.token_counter import count_tokens

MODEL = os.environ.get("LLM_MODEL_NAME", "default")

# 系统提示词 + 技能全文 ≈ 每次推理固定前缀（估算）
def base_prompt_tokens() -> int:
    total = 0
    try:
        from configs.prompt import SYSTEM_PROMPT
        total += count_tokens(SYSTEM_PROMPT, MODEL)
    except Exception as e:  # noqa: BLE001
        print(f"prompt load fail: {e}")
    # 技能 SKILL.md
    for cand in [
        Path("workspaces/agent_space/skills/hbl-device-diagnosis/SKILL.md"),
        Path("skills/hbl-device-diagnosis/SKILL.md"),
    ]:
        if cand.exists():
            total += count_tokens(cand.read_text(encoding="utf-8"), MODEL)
            break
    return total


async def fetch_rows():
    url = os.environ["DATABASE_URL"]
    u = urllib.parse.urlparse(url)
    conn = await aiomysql.connect(
        host=u.hostname, port=u.port, user=u.username,
        password=u.password, db=u.path.lstrip("/"),
    )
    cur = await conn.cursor(aiomysql.DictCursor)
    await cur.execute(
        """
        SELECT user_id, session_id, role, content, metadata, turn_id, turn_seq, created_at
        FROM conversations
        WHERE status != 'deleted'
        ORDER BY id ASC
        """
    )
    rows = await cur.fetchall()
    conn.close()
    return rows


def md(row):
    m = row["metadata"]
    if isinstance(m, str):
        m = json.loads(m)
    return m or {}


def split_turns(rows):
    """先按会话聚合（id 交错），会话内按 turn_id/user 行切分为回复。"""
    sessions = []
    sess_index = {}
    for r in rows:
        skey = (r["user_id"], r["session_id"])
        if skey not in sess_index:
            sess_index[skey] = []
            sessions.append(sess_index[skey])
        sess_index[skey].append(r)

    turns = []
    for srows in sessions:
        cur = None
        last_turn_key = None
        for r in srows:
            new_turn = (
                r["role"] == "user"
                or (r["turn_id"] and r["turn_id"] != last_turn_key)
                or cur is None
            )
            if new_turn:
                if cur:
                    turns.append(cur)
                cur = {"user_id": r["user_id"], "session_id": r["session_id"],
                       "turn_id": r["turn_id"], "created_at": r["created_at"], "rows": []}
                last_turn_key = r["turn_id"]
            cur["rows"].append(r)
        if cur:
            turns.append(cur)
    return turns


def analyze_turn(t, base_tokens):
    """返回该回复的实测/模拟指标。"""
    model_calls = []   # 每次模型调用: (input_tokens, output_tokens)
    tool_results = []  # 工具结果 token
    tool_names = []
    user_tokens = 0
    context = base_tokens  # 模拟发送给模型的上下文大小

    for r in t["rows"]:
        if r["role"] == "user":
            n = count_tokens(r["content"] or "", MODEL)
            user_tokens += n
            context += n
            continue
        m = md(r)
        mtype = m.get("type")
        if mtype == "tool_call":
            # 工具结果回填上下文；该行本身代表上一次模型调用的输出（工具调用）
            args = m.get("tool_args") or ""
            if not isinstance(args, str):
                args = json.dumps(args, ensure_ascii=False)
            out_n = count_tokens(args, MODEL)
            res = m.get("result") or ""
            if not isinstance(res, str):
                res = json.dumps(res, ensure_ascii=False)
            res_n = count_tokens(res, MODEL)
            # 模型调用（发出 tool_call）：input=context, output=args
            model_calls.append((context, out_n))
            context += out_n + res_n
            tool_results.append(res_n)
            tool_names.append(m.get("tool_name") or "?")
        else:
            # text 步骤：模型调用输出（可能是思考/中间文本）
            text = m.get("text") or r["content"] or ""
            out_n = count_tokens(text, MODEL)
            model_calls.append((context, out_n))
            context += out_n

    weighted = sum(i * 1 + o * 2 for i, o in model_calls)
    return {
        "session_id": t["session_id"],
        "turn_id": t["turn_id"],
        "created_at": str(t["created_at"]),
        "model_calls": len(model_calls),
        "tool_calls": len(tool_results),
        "user_tokens": user_tokens,
        "tool_result_total": sum(tool_results),
        "tool_result_max": max(tool_results) if tool_results else 0,
        "final_context": context,
        "weighted_budget": weighted,
        "tool_names": Counter(tool_names),
        "input_sum": sum(i for i, _ in model_calls),
        "output_sum": sum(o for _, o in model_calls),
    }


async def main():
    rows = await fetch_rows()
    base = base_prompt_tokens()
    print(f"base prompt+skill tokens = {base}")
    print(f"total rows = {len(rows)}")

    turns = split_turns(rows)
    stats = [analyze_turn(t, base) for t in turns]
    stats = [s for s in stats if s["model_calls"] > 0]

    # 全量分布
    def pct(vals, p):
        vals = sorted(vals)
        return vals[min(len(vals) - 1, int(len(vals) * p))] if vals else 0

    calls = [s["model_calls"] for s in stats]
    tools = [s["tool_calls"] for s in stats]
    budgets = [s["weighted_budget"] for s in stats]
    finals = [s["final_context"] for s in stats]

    print(f"replies = {len(stats)}")
    print(f"model_calls/reply: mean={sum(calls)/len(calls):.1f} p50={pct(calls,.5)} p90={pct(calls,.9)} p99={pct(calls,.99)} max={max(calls)}")
    print(f"tool_calls/reply:  mean={sum(tools)/len(tools):.1f} p50={pct(tools,.5)} p90={pct(tools,.9)} p99={pct(tools,.99)} max={max(tools)}")
    print(f"weighted budget/reply: mean={sum(budgets)/len(budgets):.0f} p50={pct(budgets,.5)} p90={pct(budgets,.9)} p99={pct(budgets,.99)} max={max(budgets)}")
    print(f"final context/reply: mean={sum(finals)/len(finals):.0f} p90={pct(finals,.9)} p99={pct(finals,.99)} max={max(finals)}")

    # 超过当前 500000 预算的回复
    over = [s for s in stats if s["weighted_budget"] >= 500000]
    print(f"replies >= 500000 budget: {len(over)}")
    over_iters = [s for s in stats if s["model_calls"] >= 50]
    print(f"replies >= 50 model calls (max_iters=50): {len(over_iters)}")

    # 批量设备查询类会话（含 find_device 的回复）单独分析
    fd = [s for s in stats if "find_device" in s["tool_names"]]
    print(f"\n--- replies containing find_device: {len(fd)} ---")
    if fd:
        fdc = [s["model_calls"] for s in fd]
        fdb = [s["weighted_budget"] for s in fd]
        print(f"model_calls: mean={sum(fdc)/len(fdc):.1f} max={max(fdc)}")
        print(f"weighted: mean={sum(fdb)/len(fdb):.0f} p90={pct(fdb,.9)} max={max(fdb)}")
        # 每次 find_device 的平均工具结果大小
        total_fd_res = 0
        cnt_fd = 0
        for s in fd:
            pass  # 结果大小在 analyze 中只留了汇总，重新粗算
        print(f"tool_result per reply: mean={sum(s['tool_result_total']/max(1,s['tool_calls']) for s in fd)/len(fd):.0f} tokens")

    # 按「每回复工具调用数」分桶看预算增长
    print("\n--- budget by tool_calls bucket ---")
    buckets = {}
    for s in stats:
        b = min(s["tool_calls"] // 10 * 10, 60)
        buckets.setdefault(b, []).append(s["weighted_budget"])
    for b in sorted(buckets):
        v = buckets[b]
        print(f"  tools {b:2d}-{b+9:2d}: n={len(v):4d} mean_budget={sum(v)/len(v):9.0f} max={max(v):9.0f}")

    # 单工具结果大小分布（判断 tool_result_limit 影响）
    all_res = []
    for s in stats:
        pass

    # 输出 top 20 最重回复明细
    top = sorted(stats, key=lambda s: -s["weighted_budget"])[:20]
    print("\n--- top 20 heaviest replies ---")
    for s in top:
        print(f"  {s['created_at']} sess={s['session_id'][:12]} tools={s['tool_calls']} calls={s['model_calls']} "
              f"weighted={s['weighted_budget']} final_ctx={s['final_context']} "
              f"tools_list={dict(s['tool_names'])}")

    # 保存明细供后续引用
    out = {
        "base_tokens": base,
        "reply_count": len(stats),
        "mean_model_calls": sum(calls) / len(calls),
        "p90_model_calls": pct(calls, .9),
        "max_model_calls": max(calls),
        "mean_weighted": sum(budgets) / len(budgets),
        "p90_weighted": pct(budgets, .9),
        "p99_weighted": pct(budgets, .99),
        "max_weighted": max(budgets),
        "over_500k": len(over),
        "over_50_calls": len(over_iters),
        "top": [{k: (dict(v) if isinstance(v, Counter) else v) for k, v in s.items()} for s in top],
    }
    Path("logs/token_usage_report.json").parent.mkdir(exist_ok=True)
    Path("logs/token_usage_report.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    print("\nreport -> logs/token_usage_report.json")


if __name__ == "__main__":
    asyncio.run(main())
