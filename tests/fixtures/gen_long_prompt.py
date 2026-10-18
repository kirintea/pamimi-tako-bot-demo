# -*- coding: utf-8 -*-

"""长文本测试 fixture 生成器 — long_prompt.txt

用途：测试需要足够长的 prompt 触发消息/上下文压缩（trigger_ratio × context_size），
或需要大段中文语料时，不要把长文内联进测试源码、也不要整段读进对话上下文。
本脚本从本地语料目录拼接生成 txt，测试运行时只读文件。

语料来源（本地，gitignored）：.draft/agentscope_docs_2.0.7_zh（AgentScope 中文文档）

运行方式：
    .venv/Scripts/python.exe tests/fixtures/gen_long_prompt.py
    # 可选参数
    .venv/Scripts/python.exe tests/fixtures/gen_long_prompt.py --target-tokens 90000

输出：tests/fixtures/long_prompt.txt（拼接语料 + 首行阅读指令）
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SOURCE = REPO_ROOT / ".draft" / "agentscope_docs_2.0.7_zh"
DEFAULT_OUTPUT = Path(__file__).resolve().parent / "long_prompt.txt"

# 压缩触发线：trigger_ratio 0.6 × context_size 128000 ≈ 76800（tokens）。
# 注意：框架压缩判定用 agentscope model._base.count_tokens 的默认实现，
# 即 bytes/4 估算（非 tiktoken），触发要求「上下文总字节 / 4 > 76800」
# 即 ≥ 307200 字节。tiktoken 目标 95000 ≈ 34 万字节 ≈ 85000（bytes/4），
# 另留 system prompt / tools schema 余量；不足时用 --target-tokens 调大。
DEFAULT_TARGET_TOKENS = 95000

PROMPT_HEADER = (
    "请认真阅读并记住以下资料，稍后我会基于这些资料向你提问。\n\n"
)


def estimate_tokens(text: str) -> int:
    """估算 token 数：优先 tiktoken cl100k_base，缺失时退化为启发式"""
    try:
        import tiktoken

        enc = tiktoken.get_encoding("cl100k_base")
        return len(enc.encode(text))
    except Exception:  # noqa: BLE001
        # 中文约 1.2 token/字，ASCII 约 0.25 token/字 的粗略混合估算
        cjk = sum(1 for ch in text if ord(ch) > 0x2E80)
        return int(cjk * 1.2 + (len(text) - cjk) * 0.25)


def collect_markdown(source: Path) -> list[Path]:
    """按稳定顺序收集语料文件（*.md）"""
    return sorted(source.rglob("*.md"))


def build_prompt(
    source: Path = DEFAULT_SOURCE,
    target_tokens: int = DEFAULT_TARGET_TOKENS,
) -> tuple[str, int, int]:
    """拼接语料直到达到目标 token 量

    Returns:
        (文本, 使用的文件数, 估算 token 数)
    """
    if not source.is_dir():
        raise FileNotFoundError(f"语料目录不存在: {source}")

    files = collect_markdown(source)
    if not files:
        raise FileNotFoundError(f"语料目录下没有 *.md: {source}")

    parts: list[str] = []
    used = 0
    tokens = estimate_tokens(PROMPT_HEADER)
    for path in files:
        chunk = path.read_text(encoding="utf-8", errors="replace").strip()
        if not chunk:
            continue
        section = f"\n\n===== {path.relative_to(source).as_posix()} =====\n\n{chunk}"
        added = estimate_tokens(section)
        if tokens + added > target_tokens and used > 0:
            break
        parts.append(section)
        tokens += added
        used += 1

    return PROMPT_HEADER + "".join(parts).lstrip("\n"), used, tokens


def main() -> int:
    parser = argparse.ArgumentParser(description="生成长文本测试 fixture")
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--target-tokens", type=int, default=DEFAULT_TARGET_TOKENS)
    args = parser.parse_args()

    text, used, tokens = build_prompt(args.source, args.target_tokens)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(text, encoding="utf-8")

    raw_bytes = len(text.encode("utf-8"))
    print(
        f"已生成 {args.output} | 语料文件 {used} 个 | "
        f"字符 {len(text)} | 字节 {raw_bytes}（bytes/4={raw_bytes // 4}） | "
        f"约 {tokens} tokens (目标 ≥ {args.target_tokens})"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
