# -*- coding: utf-8 -*-
"""批量获取 20-40 条设备/订单信息的预算外推。

方法：
1. 从历史 conversations 采样各 MCP 工具的真实结果 token 大小
2. 按批量任务形态模拟 ReAct 循环：
   - base = 系统提示词 + SKILL.md
   - 每轮：模型调用（input=当前 ctx, output=工具参数/文本）→ 结果回填
   - 上下文超 trigger×256k 时压缩（保留 reserve×256k 近期消息）
3. 真实值 ≈ 模拟 × 校准系数（11-02 触发点：sim 739k ↔ real 500k → 0.676）
4. 输出 N=20/30/40 × 每条 1 次/2 次工具调用的 6 组场景
"""

import asyncio
import io
import json
import os
import sys
import urllib.parse
from collections import defaultdict
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv(".env", override=True)

import aiomysql

from core.token_counter import count_tokens

MODEL = os.environ.get("LLM_MODEL_NAME", "default")
CALIB = 0.676          # sim→real 校准系数（11-02 实测触发点）
CONTEXT_SIZE = 256000
TRIGGER = 0.6          # context_trigger_ratio
RESERVE = 0.15         # context_reserve_ratio
INPUT_W, OUTPUT_W = 1, 2


async def sample_tool_results():
    """按工具名采样历史结果 token 大小（均值/中位数/最大）。"""
    url = os.environ["DATABASE_URL"]
    u = urllib.parse.urlparse(url)
    conn = await aiomysql.connect(
        host=u.hostname, port=u.port, user=u.username,
        password=u.password, db=u.path.lstrip("/"),
    )
    cur = await conn.cursor(aiomysql.DictCursor)
    await cur.execute(
        "SELECT metadata FROM conversations WHERE status != 'deleted' "
        "AND metadata LIKE '%tool_call%'"
    )
    rows = await cur.fetchall()
    conn.close()

    samples = defaultdict(list)
    for r in rows:
        m = r["metadata"]
        if isinstance(m, str):
            m = json.loads(m)
        m = m or {}
        if m.get("type") != "tool_call":
            continue
        res = m.get("result") or ""
        if not isinstance(res, str):
            res = json.dumps(res, ensure_ascii=False)
        samples[m.get("tool_name") or "?"].append(count_tokens(res, MODEL))

    stats = {}
    for name, vals in samples.items():
        vals.sort()
        stats[name] = {
            "n": len(vals),
            "mean": sum(vals) // len(vals),
            "p50": vals[len(vals) // 2],
            "p90": vals[int(len(vals) * 0.9)],
            "max": vals[-1],
        }
    return stats


def simulate(n_records, calls_per_record, result_tokens, tool_out_tokens,
             base_tokens, label):
    """模拟一次批量回复，返回加权预算（sim / real）。"""
    ctx = base_tokens
    cum = 0.0
    calls = 0
    compactions = 0
    trigger_at = TRIGGER * CONTEXT_SIZE
    reserve = RESERVE * CONTEXT_SIZE

    # 流程：Skill 加载 → [TaskCreate 可选] → N×(工具调用+中间思考) → 总结
    steps = [("skill", 16000)]  # (kind, extra tokens)
    for _ in range(n_records * calls_per_record):
        steps.append(("tool", result_tokens))
    steps.append(("final", 0))

    for kind, res_tok in steps:
        # 模型调用
        out = 60 if kind == "tool" else (tool_out_tokens if kind == "skill" else 900)
        cum += ctx * INPUT_W + out * OUTPUT_W
        calls += 1
        # 上下文增长（工具结果回填）
        growth = out + res_tok if kind != "final" else 0
        ctx += growth
        # 中间思考文本（历史实测每工具轮 ~200-400 tok）已含在 out 里
        if kind == "tool":
            think = 300
            cum += ctx * INPUT_W + think * OUTPUT_W
            calls += 1
            ctx += think
        # 压缩检查
        if ctx > trigger_at:
            ctx = reserve + 4000  # 压缩后 ≈ 保留近端 + 摘要
            compactions += 1

    return {
        "label": label,
        "records": n_records,
        "tool_calls": n_records * calls_per_record,
        "model_calls": calls,
        "sim_weighted": int(cum),
        "real_weighted": int(cum * CALIB),
        "compactions": compactions,
    }


async def main():
    stats = await sample_tool_results()

    from configs.prompt import SYSTEM_PROMPT
    base = count_tokens(SYSTEM_PROMPT, MODEL)
    p = Path("workspaces/agent_space/skills/hbl-device-diagnosis/SKILL.md")
    if p.exists():
        base += count_tokens(p.read_text(encoding="utf-8"), MODEL)

    print(f"base prompt+skill = {base}")
    print(f"calibration sim->real = {CALIB}")
    print("\n--- 历史工具结果大小（token）---")
    for name in sorted(stats, key=lambda k: -stats[k]["mean"]):
        s = stats[name]
        short = name.split("__")[-1]
        print(f"  {short:42s} n={s['n']:4d} mean={s['mean']:6d} p50={s['p50']:6d} "
              f"p90={s['p90']:6d} max={s['max']:6d}")

    # 关键工具的历史均值
    def m(short, default):
        for k, v in stats.items():
            if k.endswith(short):
                return v["mean"]
        return default

    fd = m("find_device", 1200)          # 单台设备信息
    osec = m("query_order_sections", 3000)  # 订单分段
    odet = m("query_order_detail", 3000)  # 订单详情
    dst = m("query_device_status", 2000)  # 设备状态

    print(f"\n采用: find_device={fd} order_sections={osec} "
          f"order_detail={odet} device_status={dst}")

    print("\n=== 场景 A：每条 1 次工具调用（find_device 批量定位）===")
    print(f"{'N':>4} {'模型轮次':>8} {'sim预算':>10} {'real预算':>10} {'压缩':>4}")
    for n in (20, 30, 40):
        r = simulate(n, 1, fd, 100, base, f"N={n} x1")
        print(f"{n:>4} {r['model_calls']:>8} {r['sim_weighted']:>10} "
              f"{r['real_weighted']:>10} {r['compactions']:>4}")

    print("\n=== 场景 B：每条 2 次工具调用（定位 + 状态/订单详情）===")
    for n in (20, 30, 40):
        r = simulate(n, 2, (fd + dst) // 2, 100, base, f"N={n} x2")
        print(f"{n:>4} {r['model_calls']:>8} {r['sim_weighted']:>10} "
              f"{r['real_weighted']:>10} {r['compactions']:>4}")

    print("\n=== 场景 C：每条 2 次工具调用（订单：order_query + order_detail）===")
    for n in (20, 30, 40):
        r = simulate(n, 2, osec, 100, base, f"N={n} x2 order")
        print(f"{n:>4} {r['model_calls']:>8} {r['sim_weighted']:>10} "
              f"{r['real_weighted']:>10} {r['compactions']:>4}")

    # 真实历史批量样本对照
    print("\n=== 真实历史批量样本（sim 值 ×0.676 ≈ real）===")
    samples_ref = [
        ("10-10 21次find_device", 27, 718431),
        ("10-22 16订单分段(25工具)", 34, 1185798),
        ("10-22 20工具(8锁错误)", 27, 975147),
        ("10-23 15次状态查询", 21, 724151),
    ]
    for label, calls, sim in samples_ref:
        print(f"  {label:34s} 轮次={calls:3d} sim={sim:8d} real≈{int(sim*CALIB):8d}")


if __name__ == "__main__":
    asyncio.run(main())
