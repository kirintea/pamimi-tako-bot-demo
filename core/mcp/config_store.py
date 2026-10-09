# -*- coding: utf-8 -*-

"""MCP 配置文件存储 — JSON 文件驱动（热加载）

设计目标（与 channels.json 同构）：
- 替代数据库存储 MCP 安装配置，改为 JSON 文件（指定路径）
- 单一真源：运行时 AgentFactory 加载 MCP 客户端直接读此文件，
  REST 端点 /mcp 也读写此文件，不再使用数据库
- YAML 不参与 MCP 导入（AppConfig 无 mcp_servers 字段，写入即启动失败）
- 配置文件不存在时生成空默认文件，由前端 / REST /mcp 填充
- 配置文件路径可通过环境变量 MCPS_CONFIG_PATH 覆盖

注意：MCP 为按需连接（stdio 子进程每实例独立 / HTTP 无状态），无 channels 那样的多实例
长连接单例冲突，故无需文件锁；保存用 tmp+rename 保证原子性即可。
"""

from __future__ import annotations

import asyncio
import copy
import json
import os
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from loguru import logger

from core.config.schemas import MCPConfig


# ============================================================
# 配置路径
# ============================================================

_DEFAULT_CONFIG_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "configs",
)

MCPS_CONFIG_PATH = os.environ.get(
    "MCPS_CONFIG_PATH",
    os.path.join(_DEFAULT_CONFIG_DIR, "mcps.json"),
)

# 热加载轮询间隔（秒）
_POLL_INTERVAL = 2.0


# ============================================================
# 数据模型
# ============================================================

@dataclass
class MCPConfigEntry:
    """单条 MCP 配置（文件中的一条记录）"""

    name: str
    transport: str = "stdio"
    command: str | None = None
    args: list[str] = field(default_factory=list)
    url: str | None = None
    headers: dict[str, str] = field(default_factory=dict)
    display_name: str | None = None
    description: str = ""
    enabled: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "transport": self.transport,
            "command": self.command,
            "args": list(self.args),
            "url": self.url,
            "headers": dict(self.headers),
            "display_name": self.display_name,
            "description": self.description,
            "enabled": self.enabled,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "MCPConfigEntry":
        return cls(
            name=data["name"],
            transport=data.get("transport", "stdio"),
            command=data.get("command"),
            args=list(data.get("args", []) or []),
            url=data.get("url"),
            headers=dict(data.get("headers", {}) or {}),
            display_name=data.get("display_name"),
            description=data.get("description", ""),
            enabled=bool(data.get("enabled", True)),
        )

    def to_mcp_config(self) -> MCPConfig:
        """转换为运行时 MCPConfig（仅含运行时所需字段）"""
        return MCPConfig(
            name=self.name,
            transport=self.transport,
            command=self.command,
            args=list(self.args),
            url=self.url,
            headers=dict(self.headers),
        )


# ============================================================
# 连接字段校验（API / 前端共用的判定规则）
# ============================================================

_HTTP_FAMILY = frozenset({"http", "https", "sse", "streamable_http", "streamablehttp"})


def normalize_transport(transport: str | None) -> str:
    """归一传输别名：大小写不敏感，连字符视作下划线。"""
    return (transport or "").strip().lower().replace("-", "_")


def validate_connection_fields(
    transport: str | None,
    command: str | None = None,
    url: str | None = None,
) -> str | None:
    """校验连接字段完整性，返回错误信息；None 表示通过。

    - stdio 必须有 command
    - http 族（http / https / sse / streamable_http / streamableHttp）必须有 url
    - 未知 transport 拒绝

    用于阻止"只存名字、连接字段全空"的空壳记录（该记录会静默影子掉
    YAML 中的同名有效配置，导致 Agent 永远拿不到 MCP 工具）。
    """
    t = normalize_transport(transport)
    if t == "stdio":
        if not (command or "").strip():
            return "stdio 类型必须提供 command（启动命令）"
        return None
    if t in _HTTP_FAMILY:
        if not (url or "").strip():
            return f"{transport} 类型必须提供 url（服务地址）"
        return None
    return f"未知 transport '{transport}'（支持 stdio / http / sse / streamable_http）"


# ============================================================
# 模块级同步读取（供 ConfigManager 在启动时同步加载）
# ============================================================

def load_mcp_configs(path: str | None = None) -> list[MCPConfig]:
    """从 JSON 文件同步读取启用的 MCP 配置（运行时使用）

    文件不存在或解析失败时返回空列表。仅返回 enabled=True 的条目。
    """
    p = Path(path or MCPS_CONFIG_PATH)
    if not p.exists():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        logger.warning("MCPConfigStore: 读取配置文件失败（返回空）: {}", e)
        return []
    entries = [MCPConfigEntry.from_dict(d) for d in data.get("mcp_servers", [])]
    return [e.to_mcp_config() for e in entries if e.enabled]


# ============================================================
# 配置存储主类
# ============================================================

class MCPConfigStore:
    """JSON 文件 MCP 配置存储 + 热加载"""

    def __init__(
        self,
        config_path: str | None = None,
        on_change: Callable[[], Any] | None = None,
    ) -> None:
        self._path = config_path or MCPS_CONFIG_PATH
        self._on_change = on_change
        self._entries: dict[str, MCPConfigEntry] = {}
        self._mtime: float = 0.0
        self._lock = asyncio.Lock()
        self._watch_task: asyncio.Task | None = None

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """加载配置（不存在则按 seed 生成默认）并启动热加载轮询"""
        if not Path(self._path).exists():
            await self._create_default_config()
        else:
            await self._load_config()
        self._watch_task = asyncio.create_task(self._watch_loop())
        logger.info("MCPConfigStore: 已启动，配置路径={}", self._path)

    async def stop(self) -> None:
        """停止热加载轮询"""
        if self._watch_task is not None:
            self._watch_task.cancel()
            try:
                await self._watch_task
            except asyncio.CancelledError:
                pass
        logger.info("MCPConfigStore: 已停止")

    # ------------------------------------------------------------------
    # 读取
    # ------------------------------------------------------------------

    def get_all(self) -> list[MCPConfigEntry]:
        """返回所有 MCP 配置（快照）"""
        return list(self._entries.values())

    def get_by_name(self, name: str) -> MCPConfigEntry | None:
        """按名称获取单条"""
        return self._entries.get(name)

    def get_mcp_configs(self) -> list[MCPConfig]:
        """返回运行时使用的 MCPConfig 列表（仅 enabled=True）"""
        return [e.to_mcp_config() for e in self._entries.values() if e.enabled]

    # ------------------------------------------------------------------
    # 写入（持久化到 JSON 文件，热加载自动感知）
    # ------------------------------------------------------------------

    async def upsert(self, entry: MCPConfigEntry) -> None:
        """创建或更新一条 MCP 配置并写回文件（按 name 唯一）"""
        async with self._lock:
            self._entries[entry.name] = entry
            await self._save_config()

    async def delete(self, name: str) -> bool:
        """删除一条 MCP 配置并写回文件"""
        async with self._lock:
            if name in self._entries:
                del self._entries[name]
                await self._save_config()
                return True
            return False

    # ------------------------------------------------------------------
    # JSON 文件读写
    # ------------------------------------------------------------------

    async def _load_config(self) -> None:
        """从 JSON 文件加载配置"""
        path = Path(self._path)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            new_map: dict[str, MCPConfigEntry] = {}
            for d in data.get("mcp_servers", []):
                entry = MCPConfigEntry.from_dict(d)
                new_map[entry.name] = entry
            self._entries = new_map
            self._mtime = path.stat().st_mtime
            logger.info(
                "MCPConfigStore: 加载 {} 条 MCP 配置（{} 条启用）",
                len(new_map),
                sum(1 for e in new_map.values() if e.enabled),
            )
        except Exception:  # noqa: BLE001
            logger.exception("MCPConfigStore: 加载配置文件失败")

    async def _save_config(self) -> None:
        """将当前配置写回 JSON 文件（tmp+rename 原子替换）"""
        path = Path(self._path)
        data = {
            "version": 1,
            "_comment": (
                "MCP 服务配置文件（热加载）。修改后保存即生效，无需重启。"
                "运行时 AgentFactory 直接读取此处作为 MCP 客户端来源（单一真源）。"
                "headers 中可能含 token，请确保此文件权限受限且已加入 .gitignore。"
            ),
            "mcp_servers": [e.to_dict() for e in self._entries.values()],
        }
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
            tmp.replace(path)  # 原子替换，避免部分写入
            self._mtime = path.stat().st_mtime
            logger.debug("MCPConfigStore: 配置已写回（{} 条）", len(self._entries))
        except Exception:  # noqa: BLE001
            logger.exception("MCPConfigStore: 写入配置文件失败")

    async def _create_default_config(self) -> None:
        """配置文件不存在时生成空默认文件（由前端 / REST /mcp 填充）"""
        try:
            self._entries = {}
            await self._save_config()
            logger.info("MCPConfigStore: 已生成默认空配置文件 {}", self._path)
        except Exception:  # noqa: BLE001
            logger.exception("MCPConfigStore: 生成默认配置文件失败")

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
                    logger.info("MCPConfigStore: 检测到配置文件变更，热加载中...")
                    await self._load_config()
                    if self._on_change is not None:
                        try:
                            result = self._on_change()
                            if asyncio.iscoroutine(result):
                                await result
                        except Exception:  # noqa: BLE001
                            logger.exception("MCPConfigStore: on_change 回调异常")
            except Exception:  # noqa: BLE001
                logger.exception("MCPConfigStore: 热加载轮询异常")
