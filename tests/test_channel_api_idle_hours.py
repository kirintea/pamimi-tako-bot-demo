# -*- coding: utf-8 -*-
"""渠道 API — session_idle_hours（会话空闲自动新开阈值 N）字段直通

约定：
    N 是 ChannelEntry 条目级字段（不在 config dict 内）。
    创建/更新/响应回显同名字段；未配置=None（消费侧默认 24）；<=0=禁用；
    bool / NaN / inf / 非数字 → 422。更新用 model_fields_set 区分「未提及」与
    「显式置 null」（置 null = 回退默认）。
"""

from __future__ import annotations

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.channels import router
from core.channels.config_store import ChannelConfigStore


class _MgrStub:
    def __init__(self) -> None:
        self.calls = 0

    async def on_config_change(self) -> None:
        self.calls += 1

    async def get_runtime_status(self) -> dict:
        # 列表端点会拉运行态快照，stub 返回空即可（本文件只测配置字段）
        return {}


@pytest.fixture
def client(tmp_path):
    app = FastAPI()
    app.include_router(router)
    # ChannelConfigStore 构造参数名为 config_path（非 path）
    app.state.channel_config_store = ChannelConfigStore(
        config_path=str(tmp_path / "channels.json")
    )
    app.state.channel_manager = _MgrStub()
    tc = TestClient(app)
    tc.channels_path = tmp_path / "channels.json"  # 供落盘契约断言读回
    return tc


def _disk_channels(client) -> list[dict]:
    """读回落盘的 channels.json 条目列表。

    entry 级 vs config 内是热加载契约的命门：若写进 config，
    from_dict 重载后字段会静默消失而 API 响应测试全绿，必须断言落盘结构。
    """
    return json.loads(client.channels_path.read_text(encoding="utf-8"))["channels"]


def _create(client, **kw):
    """POST /channels 创建渠道，body 含 name/type/config 与 kw 额外键"""
    body = {"name": "t", "type": "wecom", "config": {}}
    body.update(kw)
    return client.post("/channels", json=body)


def _raw_json_body(**kw) -> str:
    """构造原始 JSON 文本：TestClient 的 json= 编码器拒绝 NaN/Infinity，
    须手写 body（json.dumps 默认 allow_nan=True）才能把非法值送到服务端。"""
    body = {"name": "t", "type": "wecom", "config": {}}
    body.update(kw)
    return json.dumps(body)  # 允许 NaN/Infinity 字面量，服务端解析后由校验拒绝


def test_create_without_idle_hours_returns_null(client):
    r = _create(client)
    assert r.status_code == 201, r.text
    assert r.json()["session_idle_hours"] is None

    # 落盘契约：未配置 → 条目顶层无该键，且不落在 config dict 内
    entry = _disk_channels(client)[0]
    assert "session_idle_hours" not in entry
    assert "session_idle_hours" not in entry["config"]


def test_create_with_idle_hours_roundtrip(client):
    r = _create(client, session_idle_hours=8)
    assert r.status_code == 201, r.text
    data = r.json()
    assert data["session_idle_hours"] == 8.0
    ch_id = data["id"]

    g = client.get(f"/channels/{ch_id}")
    assert g.status_code == 200
    assert g.json()["session_idle_hours"] == 8.0

    lst = client.get("/channels")
    assert lst.status_code == 200
    assert lst.json()["channels"][0]["session_idle_hours"] == 8.0

    # 落盘契约：配置了 → 写在条目顶层（不在 config 内）
    entry = _disk_channels(client)[0]
    assert entry["session_idle_hours"] == 8
    assert "session_idle_hours" not in entry["config"]


def test_update_sets_idle_hours(client):
    ch_id = _create(client).json()["id"]
    r = client.patch(f"/channels/{ch_id}", json={"session_idle_hours": 1.5})
    assert r.status_code == 200, r.text
    assert r.json()["session_idle_hours"] == 1.5


def test_update_explicit_null_resets_to_default(client):
    ch_id = _create(client, session_idle_hours=8).json()["id"]
    r = client.patch(f"/channels/{ch_id}", json={"session_idle_hours": None})
    assert r.status_code == 200, r.text
    assert r.json()["session_idle_hours"] is None

    # 落盘契约：置 null = 回退默认 → 顶层键随之消失
    entry = _disk_channels(client)[0]
    assert "session_idle_hours" not in entry
    assert "session_idle_hours" not in entry["config"]


def test_update_null_on_entry_without_key(client):
    # 从未配置过的条目显式置 null → 200 且保持 None（幂等，不报错）
    ch_id = _create(client).json()["id"]
    r = client.patch(f"/channels/{ch_id}", json={"session_idle_hours": None})
    assert r.status_code == 200, r.text
    assert r.json()["session_idle_hours"] is None
    assert "session_idle_hours" not in _disk_channels(client)[0]


def test_update_zero_and_negative(client):
    # PATCH 0 / -1 同样成功回显（禁用语义由消费侧承担，与创建路径对称）
    ch_id = _create(client).json()["id"]
    r0 = client.patch(f"/channels/{ch_id}", json={"session_idle_hours": 0})
    assert r0.status_code == 200, r0.text
    assert r0.json()["session_idle_hours"] == 0.0

    r1 = client.patch(f"/channels/{ch_id}", json={"session_idle_hours": -1})
    assert r1.status_code == 200, r1.text
    assert r1.json()["session_idle_hours"] == -1.0


def test_update_without_field_keeps_value(client):
    ch_id = _create(client, session_idle_hours=8).json()["id"]
    r = client.patch(f"/channels/{ch_id}", json={"name": "新名字"})
    assert r.status_code == 200, r.text
    assert r.json()["session_idle_hours"] == 8.0
    assert r.json()["name"] == "新名字"


@pytest.mark.parametrize(
    "bad",
    [True, "abc", float("nan"), float("inf"), float("-inf"), 10**400],
    ids=["bool", "str-abc", "nan", "inf", "-inf", "huge-int"],
)
def test_invalid_values_rejected(client, bad):
    # 创建与 PATCH 均须 422（JSON 解析层拒绝也算 422）。
    # 10**400：JSON 任意精度整数字面量解析成功，float() 转换抛 OverflowError，
    # 漏捕会冒成 500。
    r = client.post(
        "/channels",
        content=_raw_json_body(session_idle_hours=bad),
        headers={"Content-Type": "application/json"},
    )
    assert r.status_code == 422, r.text

    ch_id = _create(client).json()["id"]
    p = client.patch(
        f"/channels/{ch_id}",
        content=json.dumps({"session_idle_hours": bad}),
        headers={"Content-Type": "application/json"},
    )
    assert p.status_code == 422, p.text


def test_zero_and_negative_survive(client):
    # 0 / 负数创建成功并回显（禁用语义由消费侧承担）
    r0 = _create(client, session_idle_hours=0)
    assert r0.status_code == 201, r0.text
    assert r0.json()["session_idle_hours"] == 0.0

    r1 = _create(client, session_idle_hours=-1)
    assert r1.status_code == 201, r1.text
    assert r1.json()["session_idle_hours"] == -1.0


def test_config_update_does_not_touch_idle(client):
    ch_id = _create(client, session_idle_hours=8).json()["id"]
    r = client.patch(
        f"/channels/{ch_id}", json={"config": {"welcome_message": "hi"}}
    )
    assert r.status_code == 200, r.text
    assert r.json()["session_idle_hours"] == 8.0
    assert r.json()["config"]["welcome_message"] == "hi"
