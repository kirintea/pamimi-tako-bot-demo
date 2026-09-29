# -*- coding: utf-8 -*-

"""Redis KV 后端 — SessionManager 原内联 Redis 调用的迁出（行为不变）"""

from __future__ import annotations

import redis.asyncio as aioredis

from core.kv.base import KVStore

from loguru import logger


class RedisKVStore(KVStore):
    """Redis 实现（decode_responses=True，与旧 SessionManager 行为一致）"""

    def __init__(self, url: str) -> None:
        self._url = url
        self._r: aioredis.Redis | None = None

    async def ping(self) -> None:
        self._r = aioredis.from_url(self._url, decode_responses=True)
        await self._r.ping()
        logger.info("RedisKVStore: 连接成功 ({})", self._url)

    def _client(self) -> aioredis.Redis:
        if self._r is None:
            raise RuntimeError("RedisKVStore 未初始化，请先调用 ping()")
        return self._r

    async def get(self, key: str) -> str | None:
        return await self._client().get(key)

    async def set(self, key: str, value: str, ex: int | None = None) -> None:
        await self._client().set(key, value, ex=ex)

    async def delete(self, *keys: str) -> int:
        if not keys:
            return 0
        return await self._client().delete(*keys)

    async def scan_keys(self, match: str) -> list[str]:
        keys: list[str] = []
        cursor = 0
        while True:
            cursor, batch = await self._client().scan(
                cursor=cursor, match=match, count=100
            )
            keys.extend(batch)
            if cursor == 0:
                break
        return keys

    async def aclose(self) -> None:
        if self._r:
            await self._r.aclose()
            self._r = None
