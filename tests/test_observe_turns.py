# -*- coding: utf-8 -*-

"""turns 组装纯函数测试 —— conversations 行 → 节点树 DTO

覆盖 core/observe.py 的 build_turns：
- 按 turn_id 分组、按 id 升序排节点
- metadata.type → node.kind 映射（user/thinking/tool/text）
- 旧数据兜底：无 type → text 节点；无 turn_id → 每条自成轮
- tool_summary 聚合本轮工具名
"""

from core.observe import build_turns


def _msg(id, role, content, *, turn_id=None, turn_seq=None, metadata=None):
    return {
        "id": id,
        "role": role,
        "content": content,
        "metadata": metadata,
        "created_at": f"2026-10-15T10:16:0{id}+08:00",
        "turn_id": turn_id,
        "turn_seq": turn_seq,
    }


def test_build_turns_maps_node_kinds():
    rows = [
        _msg(1, "user", "查设备", turn_id="t1", turn_seq=0),
        _msg(2, "assistant", "", turn_id="t1", turn_seq=0,
             metadata={"type": "thinking", "text": "先找设备"}),
        _msg(3, "assistant", "", turn_id="t1", turn_seq=1,
             metadata={"type": "tool_call", "tool_name": "find_device",
                       "tool_args": {"key": "DT1"}, "result": "found",
                       "state": "success"}),
        _msg(4, "assistant", "查到了", turn_id="t1", turn_seq=2,
             metadata={"type": "text"}),
    ]
    turns = build_turns(rows)
    assert len(turns) == 1
    turn = turns[0]
    assert turn["turn_id"] == "t1"
    assert turn["user_message"] == "查设备"
    kinds = [n["kind"] for n in turn["nodes"]]
    assert kinds == ["user", "thinking", "tool", "text"]
    assert [n["seq"] for n in turn["nodes"]] == [0, 1, 2, 3]


def test_build_turns_tool_node_fields():
    rows = [
        _msg(1, "user", "查", turn_id="t1"),
        _msg(2, "assistant", "", turn_id="t1",
             metadata={"type": "tool_call", "tool_name": "find_device",
                       "tool_args": {"key": "DT1"}, "result": "found",
                       "state": "success"}),
    ]
    tool = build_turns(rows)[0]["nodes"][1]
    assert tool["kind"] == "tool"
    assert tool["tool_name"] == "find_device"
    assert tool["tool_args"] == {"key": "DT1"}
    assert tool["result"] == "found"
    assert tool["state"] == "success"


def test_build_turns_tool_summary():
    rows = [
        _msg(1, "user", "查", turn_id="t1"),
        _msg(2, "assistant", "", turn_id="t1",
             metadata={"type": "tool_call", "tool_name": "find_device"}),
        _msg(3, "assistant", "", turn_id="t1",
             metadata={"type": "tool_call", "tool_name": "query_status"}),
        _msg(4, "assistant", "好了", turn_id="t1", metadata={"type": "text"}),
    ]
    summary = build_turns(rows)[0]["tool_summary"]
    assert summary == {"count": 2, "path": ["find_device", "query_status"]}


def test_build_turns_orders_by_id_not_turn_seq():
    """user 行与首条 assistant 行 turn_seq 都是 0，必须按 id 升序"""
    rows = [
        _msg(3, "assistant", "回复", turn_id="t1", turn_seq=1,
             metadata={"type": "text"}),
        _msg(1, "user", "提问", turn_id="t1", turn_seq=0),
        _msg(2, "assistant", "", turn_id="t1", turn_seq=0,
             metadata={"type": "thinking", "text": "想想"}),
    ]
    kinds = [n["kind"] for n in build_turns(rows)[0]["nodes"]]
    assert kinds == ["user", "thinking", "text"]


def test_build_turns_legacy_row_without_type_is_text():
    """旧数据：无 metadata.type → 整条 content 当正文"""
    rows = [
        _msg(1, "user", "老问题", turn_id="t1"),
        _msg(2, "assistant", "老回复", turn_id="t1", metadata=None),
    ]
    nodes = build_turns(rows)[0]["nodes"]
    assert nodes[0]["kind"] == "user"
    assert nodes[1]["kind"] == "text"
    assert nodes[1]["text"] == "老回复"


def test_build_turns_legacy_row_with_thinking_key():
    """旧数据：metadata.thinking（非 type=thinking）→ 仍按 text 处理"""
    rows = [
        _msg(1, "user", "问", turn_id="t1"),
        _msg(2, "assistant", "答", turn_id="t1",
             metadata={"thinking": "旧思考", "tool_calls": []}),
    ]
    nodes = build_turns(rows)[0]["nodes"]
    assert nodes[1]["kind"] == "text"
    assert nodes[1]["text"] == "答"


def test_build_turns_without_turn_id_each_row_own_turn():
    rows = [
        _msg(1, "user", "第一问", turn_id=None),
        _msg(2, "assistant", "第一答", turn_id=None, metadata=None),
        _msg(3, "user", "第二问", turn_id=None),
    ]
    turns = build_turns(rows)
    assert len(turns) == 3
    assert turns[0]["user_message"] == "第一问"
    assert turns[2]["user_message"] == "第二问"
    # 旧数据轮无 turn_id，输出空串
    assert turns[0]["turn_id"] == ""


def test_build_turns_empty():
    assert build_turns([]) == []


def test_build_turns_user_message_truncated_to_50():
    long = "x" * 80
    turns = build_turns([_msg(1, "user", long, turn_id="t1")])
    assert turns[0]["user_message"] == "x" * 50


def test_build_turns_user_message_fallback_without_user_row():
    rows = [
        _msg(1, "assistant", "", turn_id="t1",
             metadata={"type": "thinking", "text": "只有思考"}),
        _msg(2, "assistant", "兜底正文很长" + "x" * 60, turn_id="t1",
             metadata={"type": "text"}),
    ]
    turn = build_turns(rows)[0]
    # 首个有 text 的节点是 thinking（text="只有思考"）
    assert turn["user_message"] == "只有思考"


def test_build_turns_tool_summary_skips_empty_tool_name():
    rows = [
        _msg(1, "user", "查", turn_id="t1"),
        _msg(2, "assistant", "", turn_id="t1",
             metadata={"type": "tool_call", "tool_name": ""}),
        _msg(3, "assistant", "", turn_id="t1",
             metadata={"type": "tool_call", "tool_name": "find_device"}),
        _msg(4, "assistant", "", turn_id="t1",
             metadata={"type": "tool_call"}),  # 缺 tool_name
    ]
    summary = build_turns(rows)[0]["tool_summary"]
    assert summary == {"count": 1, "path": ["find_device"]}


def test_build_turns_created_at_from_first_row():
    rows = [
        _msg(2, "assistant", "答", turn_id="t1", turn_seq=1, metadata={"type": "text"}),
        _msg(1, "user", "问", turn_id="t1", turn_seq=0),
    ]
    turn = build_turns(rows)[0]
    # 首行是 id=1 的 user 行
    assert turn["created_at"] == "2026-10-15T10:16:01+08:00"
