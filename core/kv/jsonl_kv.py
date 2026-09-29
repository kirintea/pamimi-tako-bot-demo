# -*- coding: utf-8 -*-

"""JSONL 文件 KV 后端 — 开发/资源受限场景的本地持久化（D10）

设计（单进程开发用，不支持多进程并发共享）：
- 内存 dict 为主，文件为**追加日志**，每行一条 JSON：
    {"k": key, "v": value, "e": expires_at_epoch_or_null}   # set
    {"k": key, "del": true}                                  # delete 墓碑
- 加载时按顺序重放（last-wins）；过期条目跳过；损坏行（崩溃时的半行）跳过并告警。
- 写入 = 内存更新 + 同步追加一行：单事件循环串行无竞态，单行 <4KB 写入极快。
- 压缩：自上次压缩起追加行数达到 compact_threshold 时，
  以「临时文件 + os.replace」原子重写为快照（每个存活 key 一行），并顺带清除过期条目。
- TTL 为**惰性过期**（get/scan/load 时剔除），与 Redis 的精确 EXPIRE 略有差异，开发可接受。
"""

from __future__ import annotations

import fnmatch
import json
import os
import time
from pathlib import Path

from core.kv.base import KVStore

from loguru import logger


class JsonlKVStore(KVStore):
    """JSONL 追加日志 KV（开发/资源受限场景）"""

    def __init__(self, path: str, compact_threshold: int = 5000) -> None:
        self._dir = Path(path)
        self._file = self._dir / "kv.jsonl"
        self._threshold = max(1, compact_threshold)
        #: key → (value, expires_at | None)
        self._data: dict[str, tuple[str, float | None]] = {}
        self._appends = 0  # 自上次压缩以来的追加行数
        self._loaded = False

    async def ping(self) -> None:
        """创建目录并加载文件（幂等）"""
        if self._loaded:
            return
        self._dir.mkdir(parents=True, exist_ok=True)
        self._load_sync()
        self._loaded = True
        logger.info("JsonlKVStore: 已加载 {} ({} 条)", self._file, len(self._data))

    def _load_sync(self) -> None:
        self._data.clear()
        if not self._file.exists():
            self._file.touch()
            return
        now = time.time()
        total_lines = 0
        with open(self._file, "r", encoding="utf-8") as f:
            for lineno, line in enumerate(f, 1):
                line = line.strip()
                if not line:
                    continue
                total_lines += 1
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    logger.warning(
                        "JsonlKVStore: 跳过损坏行 {}:{}", self._file, lineno
                    )
                    continue
                key = rec.get("k")
                if not isinstance(key, str):
                    continue
                if rec.get("del"):
                    self._data.pop(key, None)
                    continue
                exp = rec.get("e")
                if exp is not None and exp <= now:
                    self._data.pop(key, None)  # 过期条目不恢复
                    continue
                self._data[key] = (rec.get("v", ""), exp)
        # 遗留长日志按"已追加"计数，尽快触发一次压缩
        self._appends = total_lines

    async def get(self, key: str) -> str | None:
        item = self._data.get(key)
        if item is None:
            return None
        value, exp = item
        if exp is not None and exp <= time.time():
            self._data.pop(key, None)  # 惰性过期：内存摘除，文件行待压缩清理
            return None
        return value

    async def set(self, key: str, value: str, ex: int | None = None) -> None:
        exp = time.time() + ex if ex is not None else None
        self._data[key] = (value, exp)
        self._append({"k": key, "v": value, "e": exp})

    async def delete(self, *keys: str) -> int:
        removed = 0
        for key in keys:
            if key in self._data:
                self._data.pop(key, None)
                removed += 1
            # 墓碑总是落盘：覆盖文件中可能残留的旧 value 行
            self._append({"k": key, "del": True})
        return removed

    async def scan_keys(self, match: str) -> list[str]:
        now = time.time()
        for key in [
            k for k, (_, exp) in self._data.items()
            if exp is not None and exp <= now
        ]:
            self._data.pop(key, None)  # 扫描时顺带惰性清理
        return [k for k in self._data if fnmatch.fnmatchcase(k, match)]

    async def aclose(self) -> None:
        # 每次追加均已 flush 落盘，无需额外刷盘；重置加载标记以便再次 ping 时重读
        self._loaded = False

    # ------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------

    def _append(self, record: dict) -> None:
        line = json.dumps(record, ensure_ascii=False)
        with open(self._file, "a", encoding="utf-8") as f:
            f.write(line + "\n")
            f.flush()
        self._appends += 1
        self._maybe_compact()

    def _maybe_compact(self) -> None:
        if self._appends < self._threshold:
            return
        now = time.time()
        # 先从内存清除过期条目，再写快照
        for key in [
            k for k, (_, exp) in self._data.items()
            if exp is not None and exp <= now
        ]:
            self._data.pop(key, None)
        tmp = self._file.with_suffix(".jsonl.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            for key, (value, exp) in self._data.items():
                f.write(
                    json.dumps({"k": key, "v": value, "e": exp}, ensure_ascii=False)
                    + "\n"
                )
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, self._file)  # 原子替换（Windows 亦安全）
        self._appends = 0
        logger.debug("JsonlKVStore: 已压缩 {} ({} 条)", self._file, len(self._data))
