# -*- coding: utf-8 -*-
"""校准：对每次预算触发（注入 Hint）的回复，计算 Hint 注入前的模拟累计 token。

预算变更史：
  ~2026-10-21 17:52  token_budget = 80000
  2026-10-21 17:52+  token_budget = 500000
对比 真实预算 vs 模拟累计 → 得出模拟/真实比例系数。
"""

import asyncio
import io
import json
import os
import sys
import urllib.parse
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv(".env", override=True)

import aiomysql

from core.token_counter import count_tokens

MODEL = os.environ.get("LLM_MODEL_NAME", "default")
BUDGET_CHANGE_AT = "2026-10-21 17:52:28"


def base_prompt_tokens() -> int:
    total = 0
    from configs.prompt import SYSTEM_PROMPT
    total += count_tokens(SYSTEM_PROMPT, MODEL)
    p = Path("workspaces/agent_space/skills/hbl-device-diagnosis/SKILL.md")
    if p.exists():
        total += count_tokens(p.read_text(encoding="utf-8"), MODEL)
    return total


async def main():
    url = os.environ["DATABASE_URL"]
    u = urllib.parse.urlparse(url)
    conn = await aiomysql.connect(
        host=u.hostname, port=u.port, user=u.username,
        password=u.password, db=u.path.lstrip("/"),
    )
    cur = await conn.cursor(aiomysql.DictCursor)
    await cur.execute(
        "SELECT user_id, session_id, role, content, metadata, turn_id, turn_seq, created_at "
        "FROM conversations WHERE status != 'deleted' ORDER BY id ASC"
    )
    rows = await cur.fetchall()
    conn.close()

    base = base_prompt_tokens()

    # group by turn_id
    turns = {}
    order = []
    for r in rows:
        tid = r["turn_id"]
        if tid not in turns:
            turns[tid] = []
            order.append(tid)
        turns[tid].append(r)

    results = []
    for tid in order:
        trows = turns[tid]
        # does this turn contain the hint?
        hint_seq = None
        for r in trows:
            m = r["metadata"]
            if isinstance(m, str):
                m = json.loads(m)
            m = m or {}
            text = (m.get("text") or "") + (r["content"] or "")
            if "预算即将耗尽" in text:
                hint_seq = r["turn_seq"]
                break
        if hint_seq is None:
            continue

        ctx = base
        cum = 0.0
        cum_before_hint = None
        tool_calls_before = 0
        for r in trows:
            m = r["metadata"]
            if isinstance(m, str):
                m = json.loads(m)
            m = m or {}
            if r["role"] == "user":
                ctx += count_tokens(r["content"] or "", MODEL)
                continue
            if m.get("type") == "tool_call":
                args = m.get("tool_args") or ""
                if not isinstance(args, str):
                    args = json.dumps(args, ensure_ascii=False)
                out_n = count_tokens(args, MODEL)
                if r["turn_seq"] >= hint_seq and cum_before_hint is None:
                    cum_before_hint = cum
                cum += ctx * 1 + out_n * 2
                res = m.get("result") or ""
                if not isinstance(res, str):
                    res = json.dumps(res, ensure_ascii=False)
                res_n = count_tokens(res, MODEL)
                if r["turn_seq"] < hint_seq:
                    tool_calls_before += 1
                ctx += out_n + res_n
            else:
                text = m.get("text") or r["content"] or ""
                out_n = count_tokens(text, MODEL)
                if r["turn_seq"] >= hint_seq and cum_before_hint is None:
                    cum_before_hint = cum
                cum += ctx * 1 + out_n * 2
                ctx += out_n
        if cum_before_hint is None:
            cum_before_hint = cum

        created = str(trows[0]["created_at"])
        budget = 80000 if created < BUDGET_CHANGE_AT else 500000
        results.append({
            "created": created,
            "budget": budget,
            "sim_cum_before_hint": round(cum_before_hint),
            "ratio": round(budget / max(cum_before_hint, 1), 3),
            "tools_before_hint": tool_calls_before,
        })

    print(f"{'created':26s} {'budget':>8s} {'sim_cum':>9s} {'ratio':>6s} {'tools':>5s}")
    for r in results:
        print(f"{r['created']:26s} {r['budget']:8d} {r['sim_cum_before_hint']:9d} "
              f"{r['ratio']:6.3f} {r['tools_before_hint']:5d}")

    ratios = [r["ratio"] for r in results]
    if ratios:
        print(f"\nratio mean={sum(ratios)/len(ratios):.3f} min={min(ratios):.3f} max={max(ratios):.3f}")


if __name__ == "__main__":
    asyncio.run(main())
