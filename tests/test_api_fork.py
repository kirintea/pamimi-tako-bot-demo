# -*- coding: utf-8 -*-

"""长 prompt 新建对话集成测试 — 分段输入 / 历史堆加 / 压缩触发

验证（原 fork 测试改造；fork 覆盖暂缺，原因见下）：
1. POST /chat/stream 新建会话（uuid sid），长 prompt 按 UTF-8 字节安全切块
   （≤ 60000 字节/条）分多次输入 —— MySQL content 列 TEXT 65535 字节
   （DDL 不可改），单条超长会 1406 Data too long，且历史天然堆加
2. 同一会话历史堆加：GET /messages 返回的 user 消息按序完整拼回原文
3. 各轮 assistant 回复正常落库；新会话出现在会话列表
4. 累积上下文超过触发线（0.6 × 128000，框架按 bytes/4 估算）时触发上下文
   压缩，卸载文件落 user_spaces/{uid}/sessions/{sid}/context.jsonl
   —— 回归覆盖 offload user_id 适配器（UserScopedOffloader）

不使用 fork：copy_conversations_prefix 在 MySQL 后端 1064（`::varchar`
为 PG 专属语法），fork 静默复制 0 行产生空子会话，见
.draft/2026-10-13-fork-copy-mysql-bug.md。本测试重点是长 prompt/压缩链路，
改用新建对话方式。

长文本 fixture 由脚本生成，勿内联长文；缺失时运行：
    .venv/Scripts/python.exe tests/fixtures/gen_long_prompt.py
"""

from __future__ import annotations

import shutil
import time
import uuid
from pathlib import Path

import pytest
import redis as redis_sync
from fastapi.testclient import TestClient
from main import app
from core.config import ConfigManager

UID = "u_api_fork"

# 每条输入的字节上限：MySQL TEXT 65535 字节，留余量
_CHUNK_MAX_BYTES = 60_000
# GET /messages 默认 limit=50，多轮多行历史可能超限，显式拉大
_HISTORY_LIMIT = 500
# 后台异步落库的等待上限
_PERSIST_WAIT_SECS = 30

# 长文本 fixture：触发消息压缩（trigger_ratio × context_size ≈ 76800，
# 框架按 bytes/4 估算）。由脚本从本地语料生成，勿内联长文；缺失时运行：
#   .venv/Scripts/python.exe tests/fixtures/gen_long_prompt.py
_LONG_PROMPT_PATH = Path(__file__).parent / "fixtures" / "long_prompt.txt"

# 本次运行创建的会话（teardown 软删 PG + Redis + 卸载目录）
_created_sessions: list[str] = []


def _cleanup_redis():
    """按 user 前缀扫描清理（覆盖动态 sid）"""
    cfg = ConfigManager.get_instance().load()
    r = redis_sync.from_url(cfg.redis.url, decode_responses=True)
    cursor = 0
    while True:
        cursor, keys = r.scan(
            cursor=cursor,
            match=f"{cfg.redis.key_prefix}{UID}:*",
            count=50,
        )
        if keys:
            r.delete(*keys)
        if cursor == 0:
            break
    r.close()


def _split_utf8_chunks(text: str, max_bytes: int = _CHUNK_MAX_BYTES) -> list[str]:
    """按 UTF-8 字节数切块，每块 ≤ max_bytes（优先在行边界断开）"""
    chunks: list[str] = []
    buf: list[str] = []
    size = 0

    def _flush():
        nonlocal buf, size
        if buf:
            chunks.append("".join(buf))
            buf, size = [], 0

    for line in text.splitlines(keepends=True):
        line_bytes = len(line.encode("utf-8"))
        if line_bytes > max_bytes:
            # 超长单行：先落盘已有缓冲，再按字符切
            _flush()
            for ch in line:
                ch_bytes = len(ch.encode("utf-8"))
                if size + ch_bytes > max_bytes:
                    _flush()
                buf.append(ch)
                size += ch_bytes
            continue
        if size + line_bytes > max_bytes:
            _flush()
        buf.append(line)
        size += line_bytes
    _flush()
    return chunks


@pytest.fixture
def client():
    _created_sessions.clear()
    _cleanup_redis()
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c
        # teardown：软删会话（PG active→deleted + Redis 状态）+ 卸载目录 + Redis 残留
        for sid in list(_created_sessions):
            try:
                c.post(f"/sessions/{UID}/{sid}/delete")
            except Exception:  # noqa: BLE001
                pass
            shutil.rmtree(
                Path("workspaces/user_spaces") / UID / "sessions" / sid,
                ignore_errors=True,
            )
    _cleanup_redis()


def test_long_prompt_new_conversation(client):
    # 1. 长 prompt 切块（同一会话分多次输入，历史堆加）
    if not _LONG_PROMPT_PATH.is_file():
        pytest.skip(
            f"缺少长文本 fixture {_LONG_PROMPT_PATH}，"
            f"请先运行 .venv/Scripts/python.exe tests/fixtures/gen_long_prompt.py"
        )
    prompt = _LONG_PROMPT_PATH.read_text(encoding="utf-8")
    chunks = _split_utf8_chunks(prompt)
    assert len(chunks) >= 2, "长文本应切为多块（content 列字节上限）"
    assert "".join(chunks) == prompt, "切块拼接应还原原文"

    sid = f"s_long_{uuid.uuid4().hex[:12]}"
    _created_sessions.append(sid)

    # 2. 分段输入：每次一段，SSE 消费至流结束
    for i, chunk in enumerate(chunks, start=1):
        with client.stream("POST", "/chat/stream", json={
            "user_id": UID,
            "session_id": sid,
            "message": chunk,
        }) as r:
            assert r.status_code == 200, (
                f"第 {i}/{len(chunks)} 段输入失败: {r.status_code}"
            )
            for line in r.iter_lines():
                pass

    # 3. 历史落库是后台异步：轮询等待全部 user 行写入
    #    （压缩触发时最后一轮还会多一次摘要 LLM 调用，多留余量）
    payload: dict = {}
    msgs: list[dict] = []
    deadline = time.time() + _PERSIST_WAIT_SECS
    while True:
        resp = client.get(
            f"/sessions/{UID}/{sid}/messages",
            params={"limit": _HISTORY_LIMIT},
        )
        assert resp.status_code == 200, (
            f"获取消息历史失败: {resp.status_code} {resp.text}"
        )
        payload = resp.json()
        msgs = payload.get("messages", [])
        users = [m for m in msgs if m.get("role") == "user"]
        if len(users) >= len(chunks) or time.time() > deadline:
            break
        time.sleep(0.5)

    # 4. 历史完整性：user 消息按序完整拼回原文
    users = [m for m in msgs if m.get("role") == "user"]
    assert len(users) == len(chunks), (
        f"user 消息 {len(users)} 条应等于输入块数 {len(chunks)}"
        f"（has_more={payload.get('has_more')}）"
    )
    assert "".join(m.get("content") or "" for m in users) == prompt, (
        "user 消息按序拼接应还原长 prompt 原文"
    )
    assert any(m.get("role") == "assistant" for m in msgs), (
        "应有 assistant 回复落库"
    )

    # 5. 新会话出现在会话列表
    r_list = client.get(f"/sessions/{UID}")
    assert r_list.status_code == 200, f"会话列表失败: {r_list.status_code}"
    sessions = r_list.json().get("sessions", [])
    assert any(s.get("session_id") == sid for s in sessions), (
        "新建会话应出现在会话列表中"
    )

    # 6. 压缩触发证据：卸载文件存在（UserScopedOffloader 回归覆盖）
    offload_file = (
        Path("workspaces/user_spaces") / UID / "sessions" / sid / "context.jsonl"
    )
    assert offload_file.is_file(), (
        f"未触发上下文压缩（缺少 {offload_file}）。"
        f"可运行 .venv/Scripts/python.exe tests/fixtures/gen_long_prompt.py "
        f"--target-tokens 110000 加大 fixture 后重试"
    )
