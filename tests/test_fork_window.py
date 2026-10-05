# -*- coding: utf-8 -*-

"""Fork 纯逻辑单测 —— 窗口截断 + 历史→状态重建（无需 Postgres / Redis）

直接调用 SessionManager 的静态方法，验证两类核心逻辑：
1. _history_to_state：跳过 thinking/tool_call 内部行，仅保留 user/assistant 文本
   （等价于 新式 fork 的「隐藏/内部消息剔除」+ 本项目轮内压缩）
2. _apply_fork_window：按 user 消息切分轮次，仅保留最近 N 轮
"""

import pytest

from core.session import SessionManager


def _text(msg):
    """从 Msg.content（可能是 TextBlock 列表或字符串）提取纯文本。"""
    content = getattr(msg, "content", None)
    if isinstance(content, list):
        return "".join(getattr(b, "text", "") for b in content)
    return content


def _msgs():
    """构造一段混合历史：3 轮，每轮含 user + 思考 + 工具 + 文本答案。"""
    return [
        {"role": "user", "content": "Q1", "metadata": None},
        {"role": "assistant", "content": "", "metadata": {"type": "thinking", "text": "..."}},
        {"role": "assistant", "content": "", "metadata": {"type": "tool_call", "tool_name": "x"}},
        {"role": "assistant", "content": "A1", "metadata": {"type": "text"}},
        {"role": "user", "content": "Q2", "metadata": None},
        {"role": "assistant", "content": "A2", "metadata": {"type": "text"}},
        {"role": "user", "content": "Q3", "metadata": None},
        {"role": "assistant", "content": "A3", "metadata": {"type": "text"}},
    ]


def test_history_to_state_skips_internal_rows():
    """内部行（thinking/tool_call）被剔除，仅保留 user + assistant 文本。"""
    state = SessionManager._history_to_state(_msgs(), "s1")
    assert state is not None
    roles = [getattr(m, "role", None) for m in state.context]
    # 3 个 user + 3 个 assistant 文本 = 6 条；thinking/tool 被丢弃
    assert roles == ["user", "assistant", "user", "assistant", "user", "assistant"]
    texts = [_text(m) for m in state.context]
    assert texts == ["Q1", "A1", "Q2", "A2", "Q3", "A3"]


def test_history_to_state_empty_returns_none():
    """纯内部行历史应返回 None（无有效上下文）。"""
    msgs = [
        {"role": "assistant", "content": "", "metadata": {"type": "thinking"}},
        {"role": "assistant", "content": "", "metadata": {"type": "tool_call"}},
    ]
    assert SessionManager._history_to_state(msgs, "s2") is None


def test_apply_fork_window_keeps_last_n_turns():
    """window=2 时仅保留最后 2 轮（Q2/A2, Q3/A3）。"""
    state = SessionManager._history_to_state(_msgs(), "s3")
    out = SessionManager._apply_fork_window(state, 2)
    contents = [_text(m) for m in out.context]
    assert contents == ["Q2", "A2", "Q3", "A3"]


def test_apply_fork_window_noop_when_small():
    """轮数 <= window 时不做截断。"""
    state = SessionManager._history_to_state(_msgs(), "s4")
    out = SessionManager._apply_fork_window(state, 10)
    assert len(out.context) == 6


def test_apply_fork_window_zero_disabled():
    """window<=0 视为关闭窗口。"""
    state = SessionManager._history_to_state(_msgs(), "s5")
    out = SessionManager._apply_fork_window(state, 0)
    assert len(out.context) == 6


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
