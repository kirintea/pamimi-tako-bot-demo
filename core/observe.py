# -*- coding: utf-8 -*-

"""对话观测台数据组装 —— conversations 行 → turns DTO

数据流: PG conversations（get_conversation_history）
        → build_turns（本模块，纯函数）
        → api/observe.py → WebUI 右栏树型节点 + 中栏工具路径

节点顺序按 **id 升序** —— 不要用 turn_seq：v3 里 user 行的 turn_seq=0，
而 turn_rows 首条 assistant 行的 turn_seq 也是 0，会撞车。
"""

from __future__ import annotations

from typing import Any

# 旧数据（无 metadata.type）兜底成 text 节点时的内容截断长度（user_message 用）
_USER_MSG_MAX = 50


def _shorten(text: str, limit: int = _USER_MSG_MAX) -> str:
    return text if len(text) <= limit else text[:limit]


def build_turns(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """把 conversations 行组装成 turns DTO

    Args:
        messages: get_conversation_history 的 messages 列表，每条含
            id / role / content / metadata / created_at / turn_id / turn_seq。

    Returns:
        轮次列表（按消息先后）。每项：
        {
            "turn_id": "t1" | "",
            "user_message": "…",
            "created_at": "…",
            "nodes": [{"seq", "kind", "text"? | "tool_name"? …}],
            "tool_summary": {"count": N, "path": ["find_device", …]},
        }

    映射规则（见设计文档「节点映射规则」）：
        role=user                              → kind=user
        metadata.type=thinking                 → kind=thinking（text=metadata.text）
        metadata.type=tool_call                → kind=tool
        metadata.type=text / 无 type（旧数据）   → kind=text
    """
    if not messages:
        return []

    grouped: dict[str, list[dict[str, Any]]] = {}
    order: list[str] = []
    for msg in messages:
        tid = msg.get("turn_id") or ""
        # 无 turn_id 的旧数据：每条各自成轮（用 id 做分组键，输出时还原为空）
        key = tid or f"__row_{msg.get('id')}"
        if key not in grouped:
            grouped[key] = []
            order.append(key)
        grouped[key].append(msg)

    turns: list[dict[str, Any]] = []
    for key in order:
        rows = sorted(grouped[key], key=lambda r: r.get("id") or 0)
        nodes: list[dict[str, Any]] = []
        tool_path: list[str] = []
        user_message = ""
        created_at = ""

        for idx, row in enumerate(rows):
            meta = row.get("metadata") or {}
            mtype = meta.get("type")
            node: dict[str, Any] = {"seq": idx}

            if row.get("role") == "user":
                node["kind"] = "user"
                node["text"] = row.get("content") or ""
                if not user_message:
                    user_message = _shorten(node["text"])
            elif mtype == "thinking":
                node["kind"] = "thinking"
                node["text"] = meta.get("text") or ""
            elif mtype == "tool_call":
                node["kind"] = "tool"
                node["tool_name"] = meta.get("tool_name") or ""
                node["tool_args"] = meta.get("tool_args")
                node["result"] = meta.get("result")
                node["state"] = meta.get("state")
                if node["tool_name"]:
                    tool_path.append(node["tool_name"])
            else:
                # text 或旧数据（无 type）→ 正文
                node["kind"] = "text"
                node["text"] = row.get("content") or ""

            if not created_at:
                created_at = row.get("created_at") or ""
            nodes.append(node)

        if not user_message:
            # 无 user 行的轮（异常数据）：取首个 text 节点前 50 字兜底
            for node in nodes:
                if node.get("text"):
                    user_message = _shorten(node["text"])
                    break

        turns.append({
            "turn_id": key if not key.startswith("__row_") else "",
            "user_message": user_message,
            "created_at": created_at,
            "nodes": nodes,
            "tool_summary": {"count": len(tool_path), "path": tool_path},
        })

    return turns
