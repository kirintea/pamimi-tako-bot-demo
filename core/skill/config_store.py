# -*- coding: utf-8 -*-

"""Skill 配置文件存储 — JSON 文件驱动（热加载 + 目录自动同步）

设计目标（与 channels.json / mcps.json 同构）：
- 替代数据库存储 Skill 安装元数据，改为 JSON 文件（指定路径）
- 本文件是管理/展示用的元数据目录，供前端展示技能列表与 enabled 状态
- 后端启动及运行时定期扫描 agent_space/skills 目录（LocalSkillLoader），
  自动把磁盘上的 SKILL.md 同步进 skills.json：磁盘为"是否存在的真相源"，
  JSON 中保留用户编辑的 enabled / 元数据；目录增删 SKILL.md 会被自动感知

注意：运行时 Skill 实际由 agent_space/skills 目录加载（LocalSkillLoader 直接扫目录），
skills.json 不参与运行时加载（enabled 仅作前端展示标记，不影响实际加载）。
"""

from __future__ import annotations

import asyncio
import json
import os
from dataclasses import dataclass, field
from datetime import datetime
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

SKILLS_CONFIG_PATH = os.environ.get(
    "SKILLS_CONFIG_PATH",
    os.path.join(_DEFAULT_CONFIG_DIR, "skills.json"),
)

# 热加载轮询间隔（秒）
_POLL_INTERVAL = 2.0


# ============================================================
# 数据模型
# ============================================================

def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


@dataclass
class SkillConfigEntry:
    """单条 Skill 元数据配置（文件中的一条记录）"""

    name: str
    id: str = ""
    display_name: str | None = None
    description: str = ""
    markdown: str = ""
    tags: list[str] = field(default_factory=list)
    author: str | None = None
    enabled: bool = True
    version: str | None = None
    icon_url: str | None = None
    hub_id: str | None = None
    card_id: str | None = None
    created_at: str = ""
    updated_at: str = ""

    def __post_init__(self) -> None:
        if not self.id:
            self.id = self.name
        if not self.created_at:
            self.created_at = _now_iso()
        if not self.updated_at:
            self.updated_at = self.created_at

    def to_dict(self) -> dict[str, Any]:
        """序列化为字典（不含 markdown，仅保留元数据）"""
        return {
            "id": self.id,
            "name": self.name,
            "display_name": self.display_name,
            "description": self.description,
            "tags": list(self.tags),
            "author": self.author,
            "enabled": self.enabled,
            "version": self.version,
            "icon_url": self.icon_url,
            "hub_id": self.hub_id,
            "card_id": self.card_id,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SkillConfigEntry":
        return cls(
            name=data["name"],
            id=data.get("id") or data["name"],
            display_name=data.get("display_name"),
            description=data.get("description", ""),
            markdown=data.get("markdown", ""),
            tags=list(data.get("tags", []) or []),
            author=data.get("author"),
            enabled=bool(data.get("enabled", True)),
            version=data.get("version"),
            icon_url=data.get("icon_url"),
            hub_id=data.get("hub_id"),
            card_id=data.get("card_id"),
            created_at=data.get("created_at", ""),
            updated_at=data.get("updated_at", ""),
        )


# ============================================================
# 模块级同步读取
# ============================================================

def load_skill_entries(path: str | None = None) -> list[SkillConfigEntry]:
    """从 JSON 文件同步读取 Skill 元数据列表（供测试/启动用）"""
    p = Path(path or SKILLS_CONFIG_PATH)
    if not p.exists():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        logger.warning("SkillConfigStore: 读取配置文件失败（返回空）: {}", e)
        return []
    return [SkillConfigEntry.from_dict(d) for d in data.get("skills", [])]


# ============================================================
# 配置存储主类
# ============================================================

class SkillConfigStore:
    """JSON 文件 Skill 元数据配置存储 + 热加载"""

    def __init__(
        self,
        config_path: str | None = None,
        on_change: Callable[[], Any] | None = None,
        skills_dir: str | None = None,
    ) -> None:
        self._path = config_path or SKILLS_CONFIG_PATH
        self._on_change = on_change
        self._skills_dir = skills_dir
        self._entries: dict[str, SkillConfigEntry] = {}
        self._mtime: float = 0.0
        self._dir_mtime: float = 0.0
        self._lock = asyncio.Lock()
        self._watch_task: asyncio.Task | None = None

    @property
    def skills_dir(self) -> str | None:
        """返回 skills 目录路径（如 agent_space/skills），用于创建/删除实际的 SKILL.md 文件"""
        return self._skills_dir

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """加载配置（不存在则生成空默认）并启动热加载轮询；启动即与磁盘目录同步一次"""
        if not Path(self._path).exists():
            await self._create_default_config()
        else:
            await self._load_config()
        # 后端扫描 skills 目录 → 自动更新 json，供前端展示
        await self._rescan_and_merge()
        self._watch_task = asyncio.create_task(self._watch_loop())
        logger.info("SkillConfigStore: 已启动，配置路径={}", self._path)

    async def stop(self) -> None:
        """停止热加载轮询"""
        if self._watch_task is not None:
            self._watch_task.cancel()
            try:
                await self._watch_task
            except asyncio.CancelledError:
                pass
        logger.info("SkillConfigStore: 已停止")

    # ------------------------------------------------------------------
    # 读取
    # ------------------------------------------------------------------

    def get_all(self) -> list[SkillConfigEntry]:
        """返回所有 Skill 元数据（快照）"""
        return list(self._entries.values())

    def get_by_name(self, name: str) -> SkillConfigEntry | None:
        """按名称获取单条"""
        return self._entries.get(name)

    # ------------------------------------------------------------------
    # 目录自动同步：扫描 agent_space/skills，把磁盘 SKILL.md 合并进内存
    # ------------------------------------------------------------------

    async def _rescan_and_merge(self) -> None:
        """扫描 skills 目录（agent_space/skills），将磁盘 SKILL.md 同步进内存与 json。

        磁盘为"是否存在的真相源"：目录里有哪些 SKILL.md，json 就有哪些条目；
        json 中已存在的条目保留用户编辑的 enabled / 元数据 / tags 等；
        目录中已删除的条目会从 json 移除。扫描后写回 skills.json。
        """
        if not self._skills_dir:
            return
        try:
            from agentscope.skill import LocalSkillLoader

            loader = LocalSkillLoader(directory=self._skills_dir, scan_subdir=True)
            skills = await loader.list_skills()
        except Exception:  # noqa: BLE001
            logger.exception("SkillConfigStore: 扫描 skills 目录失败（跳过同步）")
            return

        new_map: dict[str, SkillConfigEntry] = {}
        for sk in skills:
            prev = self._entries.get(sk.name)
            entry = SkillConfigEntry(
                name=sk.name,
                display_name=(prev.display_name if prev else sk.name),
                description=getattr(sk, "description", "") or "",
                markdown="",  # 不存储完整内容到元数据（运行时从文件读取）
                tags=list(prev.tags) if prev else [],
                author=prev.author if prev else None,
                enabled=prev.enabled if prev else True,
                version=prev.version if prev else None,
                icon_url=prev.icon_url if prev else None,
                hub_id=prev.hub_id if prev else None,
                card_id=prev.card_id if prev else None,
                created_at=prev.created_at if prev else _now_iso(),
                updated_at=_now_iso(),
            )
            new_map[entry.name] = entry
        self._entries = new_map
        logger.info("SkillConfigStore: 扫描到 {} 个 skill（来自目录）", len(new_map))
        await self._save_config()

    # ------------------------------------------------------------------
    # 写入（持久化到 JSON 文件，热加载自动感知）
    # ------------------------------------------------------------------

    async def upsert(self, entry: SkillConfigEntry) -> None:
        """创建或更新一条 Skill 元数据并写回文件（按 name 唯一）"""
        async with self._lock:
            entry.updated_at = _now_iso()
            self._entries[entry.name] = entry
            await self._save_config()

    async def delete(self, name: str) -> bool:
        """删除一条 Skill 元数据并写回文件"""
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
            new_map: dict[str, SkillConfigEntry] = {}
            for d in data.get("skills", []):
                entry = SkillConfigEntry.from_dict(d)
                new_map[entry.name] = entry
            self._entries = new_map
            self._mtime = path.stat().st_mtime
            logger.info("SkillConfigStore: 加载 {} 条 Skill 元数据", len(new_map))
        except Exception:  # noqa: BLE001
            logger.exception("SkillConfigStore: 加载配置文件失败")

    async def _save_config(self) -> None:
        """将当前配置写回 JSON 文件（tmp+rename 原子替换）"""
        path = Path(self._path)
        data = {
            "version": 1,
            "_comment": (
                "Skill 元数据配置文件（热加载）。修改后保存即生效，无需重启。"
                "注意：运行时 Skill 由 agent_space/skills 目录（文件系统）加载，"
                "本文件仅为管理/展示用的元数据目录。"
            ),
            "skills": [e.to_dict() for e in self._entries.values()],
        }
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
            tmp.replace(path)  # 原子替换，避免部分写入
            self._mtime = path.stat().st_mtime
            logger.debug("SkillConfigStore: 配置已写回（{} 条）", len(self._entries))
        except Exception:  # noqa: BLE001
            logger.exception("SkillConfigStore: 写入配置文件失败")

    async def _create_default_config(self) -> None:
        """配置文件不存在时生成默认空文件"""
        try:
            self._entries = {}
            await self._save_config()
            logger.info("SkillConfigStore: 已生成默认空配置文件 {}", self._path)
        except Exception:  # noqa: BLE001
            logger.exception("SkillConfigStore: 生成默认配置文件失败")

    # ------------------------------------------------------------------
    # 热加载轮询
    # ------------------------------------------------------------------

    async def _watch_loop(self) -> None:
        """轮询：skills.json 外部变更则重载；skills 目录增删 SKILL.md 则重新扫描合并"""
        while True:
            await asyncio.sleep(_POLL_INTERVAL)
            try:
                # 1) skills.json 被外部编辑（enabled / 元数据）→ 重载内存
                path = Path(self._path)
                if path.exists():
                    current_mtime = path.stat().st_mtime
                    if current_mtime > self._mtime:
                        logger.info("SkillConfigStore: 检测到配置文件变更，热加载中...")
                        await self._load_config()
                        if self._on_change is not None:
                            try:
                                result = self._on_change()
                                if asyncio.iscoroutine(result):
                                    await result
                            except Exception:  # noqa: BLE001
                                logger.exception("SkillConfigStore: on_change 回调异常")
                # 2) skills 目录新增/删除 SKILL.md → 重新扫描合并（保留 enabled 等标记）
                if self._skills_dir and Path(self._skills_dir).exists():
                    dir_mtime = Path(self._skills_dir).stat().st_mtime
                    if dir_mtime != self._dir_mtime:
                        logger.info("SkillConfigStore: 检测到 skills 目录变更，重新扫描中...")
                        await self._rescan_and_merge()
                        self._dir_mtime = dir_mtime
            except Exception:  # noqa: BLE001
                logger.exception("SkillConfigStore: 热加载轮询异常")
