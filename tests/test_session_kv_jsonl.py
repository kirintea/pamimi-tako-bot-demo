# -*- coding: utf-8 -*-

"""SessionManager × JSONL KV 接线测试 + /health Redis 豁免（全程无需 Redis）"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from agentscope.state import AgentState

from api.chat import health
from core.config import ConfigManager
from core.kv.jsonl_kv import JsonlKVStore
from core.session import SessionManager

pytestmark = pytest.mark.asyncio(loop_scope="session")


@pytest.fixture
def cfg(tmp_path):
    """深拷贝真实配置并切到 jsonl 后端

    ConfigManager.load() 缓存单例，必须 model_copy(deep=True) 后再改，
    否则会污染其他测试（如 test_multi_instance_state 仍需 redis 后端）。
    """
    cfg = ConfigManager.get_instance().load().model_copy(deep=True)
    cfg.kv.backend = "jsonl"
    cfg.kv.jsonl_path = str(tmp_path / "kv")
    return cfg


async def test_initialize_uses_jsonl_backend(cfg):
    mgr = SessionManager(cfg, session_ttl=1800, max_sessions=10)
    await mgr.initialize()
    assert isinstance(mgr._kv, JsonlKVStore)
    await mgr.shutdown()
    assert mgr._kv is None


async def test_meta_roundtrip_without_redis(cfg):
    mgr = SessionManager(cfg, session_ttl=1800, max_sessions=10)
    await mgr.initialize()
    await mgr.save_session_meta("u_kv", "s_kv", title="标题A", message_count=3)
    meta = await mgr.load_session_meta("u_kv", "s_kv")
    assert meta is not None
    assert meta["title"] == "标题A"
    assert meta["message_count"] == 3
    assert meta["created_at"] > 0
    await mgr.shutdown()


async def test_list_user_sessions_sorted(cfg):
    mgr = SessionManager(cfg, session_ttl=1800, max_sessions=10)
    await mgr.initialize()
    prefix = mgr._redis_prefix
    await mgr._kv.set(
        f"{prefix}u_list:s1:meta",
        json.dumps({"session_id": "s1", "last_active": 100.0}), ex=1800,
    )
    await mgr._kv.set(
        f"{prefix}u_list:s2:meta",
        json.dumps({"session_id": "s2", "last_active": 200.0}), ex=1800,
    )
    await mgr._kv.set(
        f"{prefix}u_other:s3:meta",
        json.dumps({"session_id": "s3", "last_active": 300.0}), ex=1800,
    )
    sessions = await mgr.list_user_sessions("u_list")
    # last_active 倒序；其他用户的 key 不泄漏
    assert [s["session_id"] for s in sessions] == ["s2", "s1"]
    await mgr.shutdown()


async def test_delete_session_removes_meta(cfg):
    mgr = SessionManager(cfg, session_ttl=1800, max_sessions=10)
    await mgr.initialize()
    await mgr.save_session_meta("u_del", "s_del", title="待删")
    assert await mgr.delete_session("u_del", "s_del") is True
    assert await mgr.load_session_meta("u_del", "s_del") is None
    assert await mgr.list_user_sessions("u_del") == []
    await mgr.shutdown()


async def test_fork_session_copies_state(cfg):
    mgr = SessionManager(cfg, session_ttl=1800, max_sessions=10)
    await mgr.initialize()
    uid, parent = "u_fork", "s_parent"
    state = AgentState()
    await mgr._kv.set(
        mgr._redis_key(uid, parent), state.model_dump_json(), ex=1800,
    )
    child = await mgr.fork_session(uid, parent)
    child_state = await mgr._kv.get(mgr._redis_key(uid, child))
    assert child_state == state.model_dump_json()  # 值拷贝，key 独立
    child_meta = await mgr.load_session_meta(uid, child)
    assert child_meta["parent_session_id"] == parent
    assert child_meta["title"].endswith("(分支)")
    await mgr.shutdown()


async def test_fork_without_parent_state_raises(cfg):
    mgr = SessionManager(cfg, session_ttl=1800, max_sessions=10)
    await mgr.initialize()
    with pytest.raises(ValueError, match="父会话没有可 fork 的状态"):
        await mgr.fork_session("u_none", "s_missing")
    await mgr.shutdown()


# ------------------------------------------------------------
# /health：kv.backend=jsonl 时 Redis 故障降为提示（不置 503）
# ------------------------------------------------------------

def _fake_request(kv_backend: str) -> SimpleNamespace:
    state = SimpleNamespace(
        session_manager=SimpleNamespace(active_count=0),
        config=SimpleNamespace(
            redis=SimpleNamespace(url="redis://localhost:6379/0"),
            kv=SimpleNamespace(backend=kv_backend),
        ),
        database_manager=None,  # Task 8 后 /health 显示 not_configured
    )
    return SimpleNamespace(app=SimpleNamespace(state=state))


def _patch_redis_down(monkeypatch):
    import redis

    def _boom(url, **kwargs):
        raise ConnectionError("Connection refused")

    monkeypatch.setattr(redis, "from_url", _boom)


async def test_health_redis_optional_when_kv_jsonl(monkeypatch):
    _patch_redis_down(monkeypatch)
    resp = await health(_fake_request("jsonl"))
    body = json.loads(resp.body)
    assert resp.status_code == 200
    assert body["checks"]["redis"].startswith("unavailable (kv.backend=jsonl")
    assert body["status"] == "ok"


async def test_health_redis_hard_fail_when_kv_redis(monkeypatch):
    _patch_redis_down(monkeypatch)
    resp = await health(_fake_request("redis"))
    body = json.loads(resp.body)
    assert resp.status_code == 503
    assert body["checks"]["redis"].startswith("error:")
