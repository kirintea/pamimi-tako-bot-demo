# -*- coding: utf-8 -*-

"""KV 存储包 — create_kv_store 工厂按 kv.backend 分派"""

from __future__ import annotations

from core.config.schemas import KVConfig
from core.kv.base import KVStore
from core.kv.jsonl_kv import JsonlKVStore
from core.kv.redis_kv import RedisKVStore

__all__ = ["KVStore", "JsonlKVStore", "RedisKVStore", "create_kv_store"]


def create_kv_store(kv_cfg: KVConfig, redis_url: str) -> KVStore:
    """按配置创建 KV 后端

    Args:
        kv_cfg: KV 配置（backend / jsonl_path / jsonl_compact_threshold）
        redis_url: backend=redis 时使用的连接串（取自 config.redis.url）
    """
    if kv_cfg.backend == "jsonl":
        return JsonlKVStore(
            kv_cfg.jsonl_path,
            compact_threshold=kv_cfg.jsonl_compact_threshold,
        )
    if kv_cfg.backend == "redis":
        return RedisKVStore(redis_url)
    raise ValueError(
        f"未知 KV 后端: {kv_cfg.backend!r}（可选 redis / jsonl）"
    )
