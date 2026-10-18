# -*- coding: utf-8 -*-
"""渠道会话空闲阈值 — configs/channels.json 渠道条目级 session_idle_hours

N 与 channels 数组条目 id 对应：每个渠道各配各的，未配置/非法回退默认 24。
"""

from __future__ import annotations

import json

import pytest

from core.channels.config_store import (
    DEFAULT_SESSION_IDLE_HOURS,
    ChannelConfigStore,
    ChannelEntry,
)

pytestmark = pytest.mark.asyncio(loop_scope="session")


def _write(path, data: dict) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


async def _store(path) -> ChannelConfigStore:
    store = ChannelConfigStore(config_path=str(path))
    await store._load_config()
    return store


async def test_missing_key_uses_default(tmp_path):
    p = tmp_path / "channels.json"
    _write(p, {"version": 1, "channels": [
        {"id": "c1", "type": "wecom", "name": "n", "enabled": False, "config": {}},
    ]})
    store = await _store(p)
    entry = store.get_by_id("c1")
    assert entry.session_idle_hours is None  # 未配置
    assert DEFAULT_SESSION_IDLE_HOURS == 24.0


async def test_reads_per_channel_values(tmp_path):
    """不同渠道不同 N — 按条目 id 一一对应"""
    p = tmp_path / "channels.json"
    _write(p, {"version": 1, "channels": [
        {"id": "wecom-a", "type": "wecom", "name": "a", "enabled": True,
         "session_idle_hours": 24, "config": {}},
        {"id": "feishu-b", "type": "feishu", "name": "b", "enabled": True,
         "session_idle_hours": 8, "config": {}},
        {"id": "tg-c", "type": "telegram", "name": "c", "enabled": False,
         "session_idle_hours": 0, "config": {}},
    ]})
    store = await _store(p)
    assert store.get_by_id("wecom-a").session_idle_hours == 24.0
    assert store.get_by_id("feishu-b").session_idle_hours == 8.0
    assert store.get_by_id("tg-c").session_idle_hours == 0.0  # 0 = 禁用轮换


@pytest.mark.parametrize(
    "bad",
    ["abc", True, "nan", float("nan"), "inf", {}, [], ""],
    ids=["str-abc", "bool", "str-nan", "float-nan", "str-inf", "dict", "list", "empty-str"],
)
async def test_invalid_value_resets_to_none(tmp_path, bad):
    """非法值（非数字 / bool / 非有限浮点）解析为 None → 生效时回退默认 24

    - bool: float(True)==1.0 会把「true」静默当成「1 小时轮换」，必须拒绝
    - nan/inf: float() 能解析，但写回 json.dumps 会输出裸 NaN/Infinity
      （非严格 JSON，jq/node 拒收），会经 upsert 写回路径污染 channels.json
    """
    p = tmp_path / "channels.json"
    _write(p, {"version": 1, "channels": [
        {"id": "c1", "type": "wecom", "name": "n", "enabled": False,
         "session_idle_hours": bad, "config": {}},
    ]})
    store = await _store(p)
    assert store.get_by_id("c1").session_idle_hours is None


async def test_to_dict_roundtrip_preserves_field(tmp_path):
    e = ChannelEntry.from_dict({
        "id": "c1", "type": "wecom", "name": "n", "enabled": True,
        "session_idle_hours": 3.5, "config": {"bot_id": "b"},
    })
    d = e.to_dict()
    assert d["session_idle_hours"] == 3.5
    assert ChannelEntry.from_dict(d).session_idle_hours == 3.5
    # 未配置的条目 to_dict 不写该键（保持文件干净）
    e2 = ChannelEntry.from_dict({"id": "c2", "type": "wecom", "name": "n", "enabled": False, "config": {}})
    assert "session_idle_hours" not in e2.to_dict()


async def test_api_upsert_does_not_wipe_field(tmp_path):
    """API upsert（如改名/启用）写回文件时不能把手工配置的 N 抹掉"""
    p = tmp_path / "channels.json"
    _write(p, {"version": 1, "channels": [
        {"id": "c1", "type": "wecom", "name": "旧名", "enabled": False,
         "session_idle_hours": 8, "config": {}},
    ]})
    store = await _store(p)
    entry = store.get_by_id("c1")
    entry.name = "新名"
    await store.upsert(entry)
    data = json.loads(p.read_text(encoding="utf-8"))
    ch = next(c for c in data["channels"] if c["id"] == "c1")
    assert ch["session_idle_hours"] == 8
    assert ch["name"] == "新名"


async def test_zero_and_negative_survive_write_back(tmp_path):
    """0 / -1 必须原样贯穿 to_dict 与 upsert 写回（禁用轮换语义），不能折叠成 None"""
    e0 = ChannelEntry.from_dict({
        "id": "z", "type": "wecom", "name": "n", "enabled": True,
        "session_idle_hours": 0, "config": {},
    })
    assert e0.session_idle_hours == 0.0
    d0 = e0.to_dict()
    assert "session_idle_hours" in d0
    assert d0["session_idle_hours"] == 0
    e_neg = ChannelEntry.from_dict({
        "id": "neg", "type": "wecom", "name": "n", "enabled": True,
        "session_idle_hours": -1, "config": {},
    })
    assert e_neg.session_idle_hours == -1.0
    assert e_neg.to_dict()["session_idle_hours"] == -1
    # upsert 写回路径同样保留 0
    p = tmp_path / "channels.json"
    _write(p, {"version": 1, "channels": [
        {"id": "z", "type": "wecom", "name": "n", "enabled": True,
         "session_idle_hours": 0, "config": {}},
    ]})
    store = await _store(p)
    entry = store.get_by_id("z")
    entry.name = "改名后"
    await store.upsert(entry)
    data = json.loads(p.read_text(encoding="utf-8"))
    ch = next(c for c in data["channels"] if c["id"] == "z")
    assert ch["session_idle_hours"] == 0
    assert ch["name"] == "改名后"
