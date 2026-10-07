# -*- coding: utf-8 -*-

"""渠道配置存储 — JSON 文件驱动（热加载）

设计目标：
- 替代数据库存储渠道连接配置（bot_id/secret 等），改为 JSON 文件
- 支持热加载：文件修改后自动检测并通知 manager 切换实例
- 单实例锁：同一 channel_id 只允许一个进程连接（基于文件锁）
- 配置文件不存在时自动生成带示例的默认文件
- 配置文件路径可通过环境变量 CHANNELS_CONFIG_PATH 覆盖

配置文件 schema v1:
{
  "version": 1,
  "channels": [
    {
      "id": "wecom-main",
      "type": "wecom",
      "name": "企微主机器人",
      "enabled": true,
      "config": {
        "bot_id": "...",
        "secret": "..."
      }
    }
  ]
}

单实例锁：
- 启动连接前 acquire_lock(channel_id)（基于 fcntl/portalocker 排他锁）
- 进程退出或 stop 时 release_lock(channel_id)
- 其它进程 acquire 同一 channel_id 会失败 → 跳过该渠道
"""

from __future__ import annotations

import asyncio
import copy
import json
import os
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from loguru import logger


# ============================================================
# 配置路径
# ============================================================

_DEFAULT_CONFIG_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "configs",
)

CHANNELS_CONFIG_PATH = os.environ.get(
    "CHANNELS_CONFIG_PATH",
    os.path.join(_DEFAULT_CONFIG_DIR, "channels.json"),
)

# 锁文件目录（与配置文件同级）
_LOCK_DIR = os.path.join(os.path.dirname(CHANNELS_CONFIG_PATH), ".locks")

# 热加载轮询间隔（秒）
_POLL_INTERVAL = 2.0


# ============================================================
# 数据模型
# ============================================================

@dataclass
class ChannelEntry:
    """单条渠道配置（channels 表已移除，此类为 JSON 文件驱动的渠道记录模型）"""
    id: str
    type: str
    name: str
    enabled: bool
    config: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "type": self.type,
            "name": self.name,
            "enabled": self.enabled,
            "config": dict(self.config),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ChannelEntry":
        return cls(
            id=data.get("id", str(uuid.uuid4().hex[:16])),
            type=data.get("type", "wecom"),
            name=data.get("name", data.get("type", "unnamed")),
            enabled=data.get("enabled", False),
            config=dict(data.get("config", {})),
        )


# ============================================================
# 默认配置模板
# ============================================================

_DEFAULT_CONFIG: dict[str, Any] = {
    "version": 1,
    "_comment": (
        "渠道连接配置文件（热加载）。修改后保存即生效，无需重启。"
        "secret 字段以明文存储，请确保此文件权限为 600 且已加入 .gitignore。"
    ),
    "channels": [
        {
            "id": "wecom-example",
            "type": "wecom",
            "name": "企业微信示例（请修改配置后启用）",
            "enabled": False,
            "config": {
                "bot_id": "",
                "secret": "",
                "allow_from": [],
                "welcome_message": "",
            },
        },
    ],
}


# ============================================================
# 文件锁（跨进程单实例保证）
# ============================================================

class FileLock:
    """基于文件排他锁的进程间互斥（Windows: msvcrt.locking, Unix: fcntl.flock）"""

    def __init__(self, path: str) -> None:
        self._path = path

    def try_acquire(self) -> bool:
        """尝试获取锁，成功返回 True，已被占用返回 False"""
        import sys
        try:
            os.makedirs(os.path.dirname(self._path), exist_ok=True)
            if sys.platform == "win32":
                import msvcrt
                self._fd = open(self._path, "w")
                try:
                    msvcrt.locking(self._fd.fileno(), msvcrt.LK_NBLCK, 1)
                    self._fd.write(f"{os.getpid()}\n")
                    self._fd.flush()
                    return True
                except OSError:
                    self._fd.close()
                    self._fd = None
                    return False
            else:
                import fcntl
                self._fd = open(self._path, "w")
                try:
                    fcntl.flock(self._fd.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    self._fd.write(f"{os.getpid()}\n")
                    self._fd.flush()
                    return True
                except OSError:
                    self._fd.close()
                    self._fd = None
                    return False
        except Exception:
            return False

    def release(self) -> None:
        """释放锁"""
        import sys
        try:
            if hasattr(self, "_fd") and self._fd is not None:
                if sys.platform == "win32":
                    import msvcrt
                    try:
                        msvcrt.locking(self._fd.fileno(), msvcrt.LK_UNLCK, 1)
                    except OSError:
                        pass
                else:
                    import fcntl
                    try:
                        fcntl.flock(self._fd.fileno(), fcntl.LOCK_UN)
                    except OSError:
                        pass
                self._fd.close()
                self._fd = None
                try:
                    os.remove(self._path)
                except OSError:
                    pass
        except Exception:
            pass


# ============================================================
# 配置存储主类
# ============================================================

class ChannelConfigStore:
    """JSON 文件渠道配置存储 + 热加载"""

    def __init__(
        self,
        config_path: str | None = None,
        on_change: Callable[[], Any] | None = None,
    ) -> None:
        self._path = config_path or CHANNELS_CONFIG_PATH
        self._on_change = on_change
        self._channels: dict[str, ChannelEntry] = {}
        self._mtime: float = 0.0
        self._lock = asyncio.Lock()
        self._file_locks: dict[str, FileLock] = {}
        self._watch_task: asyncio.Task | None = None

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """加载配置并启动热加载轮询"""
        await self._load_config()
        self._watch_task = asyncio.create_task(self._watch_loop())
        logger.info("ChannelConfigStore: 已启动，配置路径={}", self._path)

    async def stop(self) -> None:
        """停止热加载并释放所有锁"""
        if self._watch_task is not None:
            self._watch_task.cancel()
            try:
                await self._watch_task
            except asyncio.CancelledError:
                pass
        self._release_all_locks()
        logger.info("ChannelConfigStore: 已停止")

    # ------------------------------------------------------------------
    # 读取
    # ------------------------------------------------------------------

    def get_all(self) -> list[ChannelEntry]:
        """返回所有渠道配置（快照）"""
        return list(self._channels.values())

    def get_enabled(self) -> list[ChannelEntry]:
        """返回所有 enabled 的渠道"""
        return [ch for ch in self._channels.values() if ch.enabled]

    def get_by_id(self, channel_id: str) -> ChannelEntry | None:
        """按 ID 获取单条"""
        return self._channels.get(channel_id)

    # ------------------------------------------------------------------
    # 写入（持久化到 JSON 文件，热加载自动感知）
    # ------------------------------------------------------------------

    async def upsert(self, entry: ChannelEntry) -> None:
        """创建或更新一条渠道配置并写回文件"""
        async with self._lock:
            self._channels[entry.id] = entry
            await self._save_config()

    async def delete(self, channel_id: str) -> bool:
        """删除一条渠道配置并写回文件"""
        async with self._lock:
            if channel_id in self._channels:
                del self._channels[channel_id]
                await self._save_config()
                return True
            return False

    # ------------------------------------------------------------------
    # 单实例锁
    # ------------------------------------------------------------------

    def try_acquire_lock(self, channel_id: str) -> bool:
        """尝试为 channel_id 获取文件排他锁"""
        if channel_id in self._file_locks:
            return True  # 已持有
        lock_path = os.path.join(_LOCK_DIR, f"{channel_id}.lock")
        lk = FileLock(lock_path)
        if lk.try_acquire():
            self._file_locks[channel_id] = lk
            return True
        return False

    def release_lock(self, channel_id: str) -> None:
        """释放 channel_id 的文件锁"""
        lk = self._file_locks.pop(channel_id, None)
        if lk is not None:
            lk.release()

    def _release_all_locks(self) -> None:
        for lk in self._file_locks.values():
            lk.release()
        self._file_locks.clear()

    # ------------------------------------------------------------------
    # JSON 文件读写
    # ------------------------------------------------------------------

    async def _load_config(self) -> None:
        """从 JSON 文件加载配置（不存在则自动生成默认）"""
        path = Path(self._path)
        if not path.exists():
            await self._create_default_config(path)
            return

        try:
            raw = path.read_text(encoding="utf-8")
            data = json.loads(raw)
            version = data.get("version", 0)
            if version != 1:
                logger.warning("ChannelConfigStore: 未知 schema version={}，按 v1 解析", version)
            channels_data = data.get("channels", [])
            new_map: dict[str, ChannelEntry] = {}
            for ch in channels_data:
                entry = ChannelEntry.from_dict(ch)
                new_map[entry.id] = entry
            self._channels = new_map
            self._mtime = path.stat().st_mtime
            logger.info(
                "ChannelConfigStore: 加载 {} 条渠道配置（{} 条启用）",
                len(new_map),
                sum(1 for e in new_map.values() if e.enabled),
            )
        except json.JSONDecodeError as e:
            logger.error("ChannelConfigStore: JSON 解析失败 ({})，保留上次有效配置", e)
        except Exception:
            logger.exception("ChannelConfigStore: 加载配置文件失败")

    async def _save_config(self) -> None:
        """将当前配置写回 JSON 文件"""
        path = Path(self._path)
        data = {
            "version": 1,
            "_comment": _DEFAULT_CONFIG.get("_comment", ""),
            "channels": [ch.to_dict() for ch in self._channels.values()],
        }
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
            tmp.replace(path)  # 原子替换，避免部分写入
            self._mtime = path.stat().st_mtime
            logger.debug("ChannelConfigStore: 配置已写回（{} 条）", len(self._channels))
        except Exception:
            logger.exception("ChannelConfigStore: 写入配置文件失败")

    async def _create_default_config(self, path: Path) -> None:
        """配置文件不存在时自动生成默认模板"""
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps(_DEFAULT_CONFIG, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            self._channels = {}
            self._mtime = path.stat().st_mtime
            logger.info("ChannelConfigStore: 已生成默认配置文件 {}", path)
        except Exception:
            logger.exception("ChannelConfigStore: 生成默认配置文件失败")

    # ------------------------------------------------------------------
    # 热加载轮询
    # ------------------------------------------------------------------

    async def _watch_loop(self) -> None:
        """轮询配置文件 mtime，变化时重新加载并通知回调"""
        while True:
            await asyncio.sleep(_POLL_INTERVAL)
            try:
                path = Path(self._path)
                if not path.exists():
                    continue
                current_mtime = path.stat().st_mtime
                if current_mtime > self._mtime:
                    logger.info("ChannelConfigStore: 检测到配置文件变更，热加载中...")
                    await self._load_config()
                    if self._on_change is not None:
                        try:
                            result = self._on_change()
                            if asyncio.iscoroutine(result):
                                await result
                        except Exception:
                            logger.exception("ChannelConfigStore: on_change 回调异常")
            except Exception:
                logger.exception("ChannelConfigStore: 热加载轮询异常")