# -*- coding: utf-8 -*-

"""KV 存储抽象 — SessionManager 持久化层接口（语义对齐 SessionManager 所用的 Redis 子集）"""

from __future__ import annotations

from abc import ABC, abstractmethod


class KVStore(ABC):
    """KV 存储接口

    实现方约定：
    - get/set 的值为 str（JSON 文本），set 的 ex 为 TTL 秒数（None = 不过期）
    - scan_keys 的 match 为 glob 通配（语义对齐 Redis SCAN MATCH，`*` 匹配任意字符）
    - delete 返回实际删除的 key 数量
    """

    @abstractmethod
    async def ping(self) -> None:
        """初始化并测试连通性（幂等；jsonl 后端在此加载文件）"""

    @abstractmethod
    async def get(self, key: str) -> str | None:
        """读取值，不存在或已过期返回 None"""

    @abstractmethod
    async def set(self, key: str, value: str, ex: int | None = None) -> None:
        """写入值，ex 为 TTL 秒"""

    @abstractmethod
    async def delete(self, *keys: str) -> int:
        """删除多个 key，返回实际删除数"""

    @abstractmethod
    async def scan_keys(self, match: str) -> list[str]:
        """按 glob 模式列出匹配的 key"""

    @abstractmethod
    async def aclose(self) -> None:
        """关闭存储（幂等）"""
