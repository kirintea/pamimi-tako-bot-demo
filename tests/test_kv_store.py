# -*- coding: utf-8 -*-

"""KV 存储测试 — JsonlKVStore 单测（无需 Redis）+ 工厂分派 + 配置默认值"""

from __future__ import annotations

import json

import pytest

from core.config.schemas import KVConfig
from core.kv import create_kv_store
from core.kv.jsonl_kv import JsonlKVStore
from core.kv.redis_kv import RedisKVStore

pytestmark = pytest.mark.asyncio(loop_scope="session")


@pytest.fixture
def kv(tmp_path) -> JsonlKVStore:
    return JsonlKVStore(str(tmp_path / "kv"), compact_threshold=100000)


async def test_set_get_roundtrip(kv):
    await kv.ping()
    await kv.set("k1", "v1", ex=1800)
    assert await kv.get("k1") == "v1"
    assert await kv.get("nope") is None


async def test_delete(kv):
    await kv.ping()
    await kv.set("a", "1")
    await kv.set("b", "2")
    assert await kv.delete("a", "missing") == 1
    assert await kv.get("a") is None
    assert await kv.get("b") == "2"


async def test_scan_keys_glob(kv):
    await kv.ping()
    prefix = "agentscope:session:"
    await kv.set(f"{prefix}u1:s1:meta", "{}")
    await kv.set(f"{prefix}u1:s2:meta", "{}")
    await kv.set(f"{prefix}u2:s3:meta", "{}")
    await kv.set(f"{prefix}u1:s1", "{}")  # 无 :meta 后缀，不应命中
    keys = await kv.scan_keys(f"{prefix}u1:*:meta")
    assert sorted(keys) == sorted([f"{prefix}u1:s1:meta", f"{prefix}u1:s2:meta"])


async def test_ttl_lazy_expiry(kv):
    await kv.ping()
    await kv.set("gone", "v", ex=-1)  # 立即过期
    assert await kv.get("gone") is None
    assert await kv.scan_keys("gone") == []


async def test_persistence_across_reopen(tmp_path):
    path = str(tmp_path / "kv")
    s1 = JsonlKVStore(path)
    await s1.ping()
    await s1.set("keep", "hello", ex=1800)
    await s1.aclose()

    s2 = JsonlKVStore(path)
    await s2.ping()
    assert await s2.get("keep") == "hello"
    await s2.aclose()


async def test_tombstone_prevents_resurrection(tmp_path):
    path = str(tmp_path / "kv")
    s1 = JsonlKVStore(path)
    await s1.ping()
    await s1.set("k", "v")
    assert await s1.delete("k") == 1
    await s1.aclose()

    s2 = JsonlKVStore(path)
    await s2.ping()
    assert await s2.get("k") is None  # 墓碑行覆盖先前的 value 行
    await s2.aclose()


async def test_malformed_line_skipped(tmp_path):
    d = tmp_path / "kv"
    d.mkdir()
    (d / "kv.jsonl").write_text(
        "not-json-at-all\n"
        + json.dumps({"k": "ok", "v": "yes", "e": None}) + "\n",
        encoding="utf-8",
    )
    store = JsonlKVStore(str(d))
    await store.ping()
    assert await store.get("ok") == "yes"  # 损坏行跳过，后续行照常加载
    await store.aclose()


async def test_compaction_rewrites_file(tmp_path):
    path = str(tmp_path / "kv")
    store = JsonlKVStore(path, compact_threshold=3)
    await store.ping()
    await store.set("k1", "v1")
    await store.set("k1", "v2")  # 覆盖 → 文件第 2 行
    await store.set("k2", "v")   # 第 3 行 → 触发压缩（重写为 2 行快照）
    await store.set("k3", "v")   # 压缩后第 1 行
    lines = [
        l for l in (tmp_path / "kv" / "kv.jsonl")
        .read_text(encoding="utf-8").splitlines() if l.strip()
    ]
    assert len(lines) == 3  # 未压缩会是 4 行
    await store.aclose()

    s2 = JsonlKVStore(path)
    await s2.ping()
    assert await s2.get("k1") == "v2"  # last-wins
    assert await s2.get("k3") == "v"
    await s2.aclose()


def test_factory_dispatch():
    assert isinstance(
        create_kv_store(KVConfig(backend="jsonl"), "redis://x"), JsonlKVStore
    )
    assert isinstance(
        create_kv_store(KVConfig(backend="redis"), "redis://localhost:6379/0"),
        RedisKVStore,
    )


def test_factory_unknown_backend_raises():
    with pytest.raises(ValueError, match="未知 KV 后端"):
        create_kv_store(KVConfig(backend="sqlite"), "redis://x")


def test_kv_config_defaults():
    kv = KVConfig()
    assert kv.backend == "redis"
    assert kv.jsonl_path == "workspaces/.history/kv_data"
    assert kv.jsonl_compact_threshold == 5000
