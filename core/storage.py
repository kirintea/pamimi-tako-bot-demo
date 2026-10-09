# -*- coding: utf-8 -*-

"""PostgreSQL 存储层 — 平台资源的持久化 CRUD

提供 Session / Conversation 等资源的数据库操作。
底层复用 DatabaseManager 的 asyncpg 连接池。
注意：Agent / Schedule / Channel 的 DB 存储已废弃（对应表已从 DDL 移除）；
MCP / Skill 改由 JSON 配置文件驱动（见 core/mcp/config_store.py、core/skill/config_store.py）；
Channel 改由 JSON 文件驱动（core/channels/config_store.py）。本存储层仅保留运行时仍在使用的
Session / Conversation 相关 CRUD。
"""

from __future__ import annotations

import json
from datetime import datetime

from core.database import DatabaseManager
from core.storage_models import (
    SessionConfig,
    SessionRecord,
    SessionSource,
    _generate_id,
)

from loguru import logger


class PostgresStorage:
    """PostgreSQL 存储层 — 实现平台资源的 CRUD 操作

    Args:
        db: DatabaseManager 实例（需已初始化）
    """

    def __init__(self, db: DatabaseManager) -> None:
        self._db = db


    # ------------------------------------------------------------------
    # Session CRUD
    # ------------------------------------------------------------------

    async def upsert_session(
        self,
        user_id: str,
        agent_id: str,
        config: SessionConfig,
        state_json: str = "",
        session_id: str | None = None,
        source: SessionSource = SessionSource.USER,
        parent_session_id: str | None = None,
        depth: int | None = None,
    ) -> SessionRecord:
        """创建或更新 Session 记录

        Args:
            user_id: 用户 ID
            agent_id: Agent ID
            config: 会话配置
            state_json: AgentState 序列化 JSON
            session_id: 更新已有会话时传入，None 则创建新会话
            source: 会话来源
            parent_session_id: 父会话 ID（Fork 时传入），更新已有会话时为 None 表示保留原值
            depth: Fork 深度（None 则默认 0）

        Returns:
            SessionRecord
        """
        now = datetime.now()
        sid = session_id or _generate_id()
        final_depth = 0 if depth is None else depth

        # 客户端主键 (id)：无需取回 id，直接构造记录返回
        await self._db.execute_named(
            "upsert_session",
            sid,
            user_id,
            agent_id,
            source.value,
            config.model_dump_json(),
            state_json,
            parent_session_id,
            final_depth,
            now,
            now,
        )

        return SessionRecord(
            id=sid,
            user_id=user_id,
            agent_id=agent_id,
            source=source,
            config=config,
            state_json=state_json,
            parent_session_id=parent_session_id,
            depth=final_depth,
            created_at=now,
            updated_at=now,
        )

    async def fork_session(
        self,
        src_session_id: str,
        user_id: str,
        new_name: str | None = None,
        new_session_id: str | None = None,
        branch_after_message_id: int | None = None,
    ) -> SessionRecord:
        """基于已有会话创建分支（Fork）

        1. 读取源会话的 agent_id / config / state_json / depth
        2. 新会话 parent_session_id = src_session_id, depth = src.depth + 1
        3. source = FORK
        4. new_name 非空时覆盖 config.name，其余 config 字段保留

        Args:
            src_session_id: 源会话 ID
            user_id: 用户 ID（必须与源会话一致）
            new_name: 新会话名称，None 则沿用源会话名称
            new_session_id: 指定新会话 ID（与 Redis 中的 child_session_id 对齐），
                None 则由 upsert_session 内部生成

        Returns:
            新创建的 SessionRecord

        Raises:
            ValueError: 源会话不存在
        """
        row = await self._db.fetchrow(
            """SELECT "id", "user_id", "agent_id", "config", "state_json",
                      "parent_session_id", "depth"
               FROM sessions WHERE "id" = $1 AND "user_id" = $2""",
            src_session_id,
            user_id,
        )
        if row is None:
            raise ValueError(f"源会话不存在: {src_session_id}")

        src_cfg_json = row["config"]
        cfg_data = (
            json.loads(src_cfg_json)
            if isinstance(src_cfg_json, str)
            else src_cfg_json
        )
        cfg = SessionConfig(**cfg_data)
        if new_name:
            cfg.name = new_name

        src_depth = int(row["depth"] or 0)

        return await self.upsert_session(
            user_id=user_id,
            agent_id=row["agent_id"],
            config=cfg,
            state_json=row["state_json"] or "",
            session_id=new_session_id,
            source=SessionSource.FORK,
            parent_session_id=row["id"],
            depth=src_depth + 1,
        )

    async def list_sessions(
        self,
        user_id: str,
        agent_id: str | None = None,
    ) -> list[SessionRecord]:
        """列出用户的会话"""
        if agent_id:
            sql = """
                SELECT * FROM sessions
                WHERE "user_id" = $1 AND "agent_id" = $2
                ORDER BY "updated_at" DESC
            """
            rows = await self._db.fetch(sql, user_id, agent_id)
        else:
            sql = """
                SELECT * FROM sessions
                WHERE "user_id" = $1
                ORDER BY "updated_at" DESC
            """
            rows = await self._db.fetch(sql, user_id)
        return [self._row_to_session(r) for r in rows]

    async def get_session(
        self,
        user_id: str,
        agent_id: str,
        session_id: str,
    ) -> SessionRecord | None:
        """获取单个 Session"""
        sql = """
            SELECT * FROM sessions
            WHERE "user_id" = $1 AND "id" = $2
        """
        row = await self._db.fetchrow(sql, user_id, session_id)
        return self._row_to_session(row) if row else None

    async def update_session_state(
        self,
        user_id: str,
        agent_id: str,
        session_id: str,
        state_json: str,
    ) -> None:
        """更新 Session 的 AgentState（热路径）"""
        sql = """
            UPDATE sessions SET "state_json" = $1, "updated_at" = NOW()
            WHERE "user_id" = $2 AND "id" = $3
        """
        await self._db.execute(sql, state_json, user_id, session_id)

    async def delete_session(
        self,
        user_id: str,
        agent_id: str,
        session_id: str,
    ) -> bool:
        """删除 Session"""
        sql = 'DELETE FROM sessions WHERE "user_id" = $1 AND "id" = $2'
        result = await self._db.execute(sql, user_id, session_id)
        return "DELETE 1" in result

    def _row_to_session(self, row) -> SessionRecord:
        """将数据库行转换为 SessionRecord"""
        config_data = json.loads(row["config"]) if isinstance(row["config"], str) else row["config"]
        return SessionRecord(
            id=row["id"],
            user_id=row["user_id"],
            agent_id=row["agent_id"],
            source=SessionSource(row["source"]),
            team_id=row.get("team_id"),
            config=SessionConfig(**config_data),
            state_json=row.get("state_json") or "",  # MySQL TEXT 列可为 NULL
            parent_session_id=row.get("parent_session_id"),
            depth=int(row.get("depth") or 0),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )
