# -*- coding: utf-8 -*-

"""数据库管理器门面 — 后端分派 + DDL + 查询接口

设计：
- 底层通过 DatabaseBackend 抽象分派 PostgreSQL(asyncpg) / MySQL(aiomysql)
- 方言差异由 core/db/statements.py 语句注册表 + $N→%s 翻译消解
- 初始化失败固定 fail-fast：ERROR 日志后抛出，启动中止（见 Task 7）

使用方式：
    db = DatabaseManager(config)
    await db.initialize()   # 初始化连接池 + 建表
    await db.execute("INSERT INTO ...")
    rows = await db.fetch("SELECT * FROM ...")
    await db.shutdown()     # 关闭连接池
"""

from __future__ import annotations

from loguru import logger

from core.config.schemas import DatabaseConfig
from core.db.base import (
    DatabaseBackend,
    DatabaseUnavailableError,
    SchemaMissingError,
)
from core.db.ddl import REQUIRED_TABLES, get_ddl
from core.db.factory import create_backend
from core.db.statements import STATEMENTS


class DatabaseManager:
    """数据库管理器门面 — 连接池生命周期 + 查询接口

    Args:
        config: 数据库配置
    """

    def __init__(self, config: DatabaseConfig) -> None:
        self._config = config
        self._backend: DatabaseBackend | None = None

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------

    async def initialize(self) -> None:
        """初始化数据库连接

        失败行为固定 fail-fast（D3 评审定案）：
            连接失败 / DDL 失败 / 缺表（verify_tables=true）
            → ERROR 日志（含根因与缺表清单）→ 关闭已建立的连接 → 抛出
            → lifespan 异常 → uvicorn 启动失败退出（非零码）。
        未配置 URL 时跳过初始化（有意的"无库运行"逃生口，/health 显示 not_configured）。
        """
        if not self._config.url:
            logger.warning("DatabaseManager: 数据库 URL 未配置，跳过初始化")
            return

        backend: DatabaseBackend | None = None
        try:
            backend = create_backend(self._config)
            await backend.connect()
            logger.info(
                "DatabaseManager: 连接池已创建 (dialect={}, pool_size={})",
                backend.dialect, self._config.pool_size,
            )
            if self._config.auto_create_tables:
                await backend.run_ddl(get_ddl(backend.dialect))
                logger.info(
                    "DatabaseManager: DDL 建表完成 (dialect={})", backend.dialect
                )
            if self._config.verify_tables:
                missing = await backend.missing_tables(REQUIRED_TABLES)
                if missing:
                    raise SchemaMissingError(f"必需表缺失: {missing}")
            self._backend = backend
            logger.info(
                "DatabaseManager: 初始化成功 (auto_create_tables={})",
                self._config.auto_create_tables,
            )
        except Exception as e:
            # fail-fast：关闭已建立的连接（不留半开连接），记 ERROR 日志后原样抛出
            if backend is not None:
                try:
                    await backend.close()
                except Exception as close_err:  # noqa: BLE001
                    logger.warning("DatabaseManager: 关闭失败连接时出错: {}", close_err)
            logger.error(
                "DatabaseManager: 初始化失败，启动中止（进程退出）: {}: {}",
                type(e).__name__, e,
            )
            raise

    async def shutdown(self) -> None:
        """关闭连接池"""
        if self._backend is not None:
            await self._backend.close()
            self._backend = None
            logger.info("DatabaseManager: 连接池已关闭")

    def _require_backend(self) -> DatabaseBackend:
        """取已初始化的后端；未初始化时抛 503 语义异常"""
        if self._backend is None:
            raise DatabaseUnavailableError("数据库未初始化")
        return self._backend

    # ------------------------------------------------------------------
    # 查询接口（通用 SQL：PostgreSQL 原生 $N；MySQL 由后端翻译为 %s）
    # ------------------------------------------------------------------

    async def execute(self, sql: str, *args) -> str:
        """执行 SQL 语句（INSERT/UPDATE/DELETE），返回状态串（如 "INSERT 0 1"）"""
        return await self._require_backend().execute(sql, *args)

    async def fetch(self, sql: str, *args) -> list:
        """查询多行数据"""
        return await self._require_backend().fetch(sql, *args)

    async def fetchrow(self, sql: str, *args):
        """查询单行数据（无匹配返回 None）"""
        return await self._require_backend().fetchrow(sql, *args)

    async def fetchval(self, sql: str, *args):
        """查询单个值（无匹配返回 None）"""
        return await self._require_backend().fetchval(sql, *args)

    # ------------------------------------------------------------------
    # 具名语句分派（core/db/statements.py 注册表）
    # ------------------------------------------------------------------

    async def execute_named(self, name: str, *args) -> str:
        """执行注册表中的写语句（自动挑选方言变体）"""
        return await self._require_backend().execute_named(name, *args)

    async def fetchval_named(self, name: str, *args):
        """执行注册表中的单值查询"""
        return await self._require_backend().fetchval_named(name, *args)

    async def fetch_named(self, name: str, *args) -> list:
        """执行注册表中的多行查询"""
        return await self._require_backend().fetch_named(name, *args)

    async def insert_returning_id(self, name: str, *args):
        """执行注册表中的插入语句并返回生成 ID

        MySQL 侧：自增表用 lastrowid；(user_id,name) 冲突表回查现有行 id。
        """
        stmt = STATEMENTS[name]
        return await self._require_backend().insert_returning_id(stmt, *args)

    # ------------------------------------------------------------------
    # 便捷方法（SQL 原样保留，Task 9 切换到注册表）
    # ------------------------------------------------------------------

    @staticmethod
    def _sanitize_text(text: str) -> str:
        """移除 PostgreSQL 无法存储的字符（NUL 等）

        PostgreSQL text 类型不允许 \\u0000 (NUL)，会抛出
        UntranslatableCharacterError。此方法在写入前剥离这些字符。
        """
        return text.replace("\x00", "") if text else text

    async def insert_conversation(
        self,
        user_id: str,
        session_id: str,
        role: str,
        content: str,
        metadata: dict | None = None,
    ) -> int:
        """插入对话记录

        Args:
            user_id: 用户 ID
            session_id: 会话 ID
            role: 角色 (user/assistant/system/tool)
            content: 消息内容
            metadata: 元数据（工具调用、token 用量等）

        Returns:
            插入记录的 ID
        """
        import json

        # 剥离 NUL 字符，避免 asyncpg.exceptions.UntranslatableCharacterError
        content = self._sanitize_text(content)
        if metadata is not None:
            metadata_str = json.dumps(metadata, ensure_ascii=False)
            metadata_str = self._sanitize_text(metadata_str)
            metadata = json.loads(metadata_str)

        sql = """
            INSERT INTO conversations (user_id, session_id, role, content, metadata)
            VALUES ($1, $2, $3, $4, $5)
            RETURNING id
        """
        return await self.fetchval(
            sql,
            user_id,
            session_id,
            role,
            content,
            json.dumps(metadata) if metadata is not None else None,
        )

    async def get_conversation_history(
        self,
        user_id: str,
        session_id: str,
        before_id: int | None = None,
        limit: int = 50,
    ) -> dict:
        """获取对话历史（游标分页，正序）

        Args:
            user_id: 用户 ID
            session_id: 会话 ID
            before_id: 游标，获取此 ID 之前的消息（用于加载更多）
            limit: 返回记录数上限

        Returns:
            {
                "messages": [...],
                "has_more": bool,
                "oldest_id": int | None
            }
        """
        if before_id:
            sql = """
                SELECT id, role, content, metadata, created_at
                FROM conversations
                WHERE user_id = $1 AND session_id = $2 AND status = 'active' AND id < $3
                ORDER BY id DESC
                LIMIT $4
            """
            rows = await self.fetch(sql, user_id, session_id, before_id, limit)
        else:
            sql = """
                SELECT id, role, content, metadata, created_at
                FROM conversations
                WHERE user_id = $1 AND session_id = $2 AND status = 'active'
                ORDER BY id DESC
                LIMIT $3
            """
            rows = await self.fetch(sql, user_id, session_id, limit)

        import json as _json

        # 转为正序
        messages = list(reversed(rows))

        # 判断是否还有更多
        has_more = len(rows) == limit
        oldest_id = messages[0]["id"] if messages else None

        return {
            "messages": [
                {
                    "id": row["id"],
                    "role": row["role"],
                    "content": row["content"],
                    "metadata": _json.loads(row["metadata"]) if row["metadata"] else None,
                    "created_at": row["created_at"].isoformat() if row["created_at"] else None,
                }
                for row in messages
            ],
            "has_more": has_more,
            "oldest_id": oldest_id,
        }

    async def get_user_sessions(
        self,
        user_id: str,
        limit: int = 50,
    ) -> list[dict]:
        """获取用户的会话列表（从 conversations 聚合 + sessions 获取自定义标题）

        Args:
            user_id: 用户 ID
            limit: 返回会话数上限

        Returns:
            会话列表（按最后活跃时间倒序）
        """
        # 从 conversations 聚合会话信息，同时 left join sessions 获取自定义标题
        sql = """
            SELECT
                c.session_id,
                MIN(c.created_at) AS created_at,
                MAX(c.created_at) AS last_active,
                COUNT(*) AS message_count,
                s.config->>'title' AS custom_title,
                (
                    SELECT LEFT(x.content, 30)
                    FROM conversations x
                    WHERE x.user_id = $1
                      AND x.session_id = c.session_id
                      AND x.role = 'user'
                      AND x.status = 'active'
                    ORDER BY x.id ASC
                    LIMIT 1
                ) AS first_message
            FROM conversations c
            LEFT JOIN sessions s ON s.id = c.session_id AND s.user_id = c.user_id AND s.status = 'active'
            WHERE c.user_id = $1 AND c.status = 'active'
            GROUP BY c.session_id, s.config
            ORDER BY last_active DESC
            LIMIT $2
        """
        rows = await self.fetch(sql, user_id, limit)

        # 转换为字典列表，优先使用自定义标题
        sessions = []
        for row in rows:
            title = row["custom_title"] or row["first_message"] or "新会话"
            sessions.append({
                "session_id": row["session_id"],
                "title": title,
                "created_at": row["created_at"].isoformat() if row["created_at"] else None,
                "last_active": row["last_active"].isoformat() if row["last_active"] else None,
                "message_count": row["message_count"],
            })

        return sessions

    async def soft_delete_session(
        self,
        user_id: str,
        session_id: str,
    ) -> int:
        """软删除会话（将所有消息标记为 deleted）

        Args:
            user_id: 用户 ID
            session_id: 会话 ID

        Returns:
            受影响的行数
        """
        sql = """
            UPDATE conversations
            SET status = 'deleted'
            WHERE user_id = $1 AND session_id = $2 AND status = 'active'
        """
        result = await self.execute(sql, user_id, session_id)
        # 解析 "UPDATE N" 获取受影响行数
        return int(result.split()[-1]) if result else 0

    async def soft_delete_conversation(
        self,
        conversation_id: int,
    ) -> bool:
        """软删除单条对话记录

        Args:
            conversation_id: 对话记录 ID

        Returns:
            是否成功
        """
        sql = """
            UPDATE conversations
            SET status = 'deleted'
            WHERE id = $1 AND status = 'active'
        """
        result = await self.execute(sql, conversation_id)
        return "UPDATE 1" in result

    async def upsert_session_title(
        self,
        user_id: str,
        session_id: str,
        title: str,
    ) -> None:
        """更新或插入会话标题（存储在 sessions 表 config 字段）

        Args:
            user_id: 用户 ID
            session_id: 会话 ID
            title: 会话标题
        """
        import json

        sql = """
            INSERT INTO sessions (id, user_id, agent_id, config, status)
            VALUES ($1, $2, 'default', $3, 'active')
            ON CONFLICT (id) DO UPDATE SET
                config = COALESCE(sessions.config, '{}'::jsonb) || EXCLUDED.config,
                updated_at = NOW()
        """
        await self.execute(sql, session_id, user_id, json.dumps({"title": title}))

    async def get_session_title(
        self,
        user_id: str,
        session_id: str,
    ) -> str | None:
        """获取会话标题

        Args:
            user_id: 用户 ID
            session_id: 会话 ID

        Returns:
            会话标题或 None
        """
        import json

        sql = """
            SELECT config->>'title' AS title
            FROM sessions
            WHERE id = $1 AND user_id = $2 AND status = 'active'
        """
        row = await self.fetchrow(sql, session_id, user_id)
        return row["title"] if row else None

    @property
    def is_initialized(self) -> bool:
        """数据库后端是否已初始化"""
        return self._backend is not None
