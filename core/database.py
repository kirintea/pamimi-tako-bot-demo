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
    # 便捷方法（分歧 SQL 已切换至 core/db/statements.py 注册表）
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
        channel: str = "web",
        turn_id: str | None = None,
        turn_seq: int | None = None,
    ) -> int:
        """插入对话记录

        Args:
            user_id: 用户 ID
            session_id: 会话 ID
            role: 角色 (user/assistant/system/tool)
            content: 消息内容
            metadata: 元数据（工具调用、token 用量等）
            channel: 消息渠道（web / feishu / wechat ...，D11）
            turn_id: 一轮对话唯一标识（v3 多行拆分）
            turn_seq: 轮内序号（v3 多行拆分）

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

        # 自增主键：PG 取 RETURNING id，MySQL 取 lastrowid
        return await self.insert_returning_id(
            "insert_conversation",
            user_id,
            session_id,
            role,
            content,
            json.dumps(metadata) if metadata is not None else None,
            channel,
            turn_id,
            turn_seq,
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
                SELECT "id", "role", "content", "metadata", "created_at", "turn_id", "turn_seq"
                FROM conversations
                WHERE "user_id" = $1 AND "session_id" = $2 AND "status" = 'active' AND "id" < $3
                ORDER BY "id" DESC
                LIMIT $4
            """
            rows = await self.fetch(sql, user_id, session_id, before_id, limit)
        else:
            sql = """
                SELECT "id", "role", "content", "metadata", "created_at", "turn_id", "turn_seq"
                FROM conversations
                WHERE "user_id" = $1 AND "session_id" = $2 AND "status" = 'active'
                ORDER BY "id" DESC
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
                    "turn_id": row["turn_id"],
                    "turn_seq": row["turn_seq"],
                }
                for row in messages
            ],
            "has_more": has_more,
            "oldest_id": oldest_id,
        }

    async def copy_conversations_prefix(
        self,
        dst_session_id: str,
        src_session_id: str,
        user_id: str,
        before_id: int | None = None,
        limit: int | None = None,
    ) -> int:
        """将源会话的历史消息（可选前缀）复制为新会话，重映射 session_id/user_id。

        仅复制 ``status='active'`` 的行（跳过 archived/deleted），按 ``id`` 正序，
        保证上下文顺序。用于 fork：让新会话在 PG 中存在可见历史，修复 fork 后空白。

        Args:
            dst_session_id: 目标（新）会话 ID
            src_session_id: 源（父）会话 ID
            user_id: 用户 ID（源与目标同源）
            before_id: 游标，仅复制 ``id <= before_id`` 的前缀（中途 fork）；
                ``None`` 表示全量复制
            limit: 复制行数上限（可选）

        Returns:
            复制的消息行数
        """
        # 参数顺序：$1=user_id, $2=dst_session_id, $3=src_session_id
        # INSERT 列序为 (user_id, session_id, ...) 对应 SELECT ($1, $2, ...)，
        # 故 user_id 取 $1、session_id 取 $2（= 新子会话 id），切勿颠倒入库键。
        #
        # 注意：asyncpg 对同一参数在 INSERT SELECT 和 WHERE 中的类型推断不同
        # （text vs character varying）会抛 AmbiguousParameterError，因此必须
        # 显式 ::varchar 转换以消除歧义。
        params: list = [user_id, dst_session_id, src_session_id]
        where_clauses = [
            '"user_id" = $1::varchar',
            '"session_id" = $3::varchar',
            '"status" = \'active\'',
        ]
        if before_id is not None:
            params.append(before_id)
            where_clauses.append(f'"id" <= ${len(params)}')
        where_sql = " AND ".join(where_clauses)
        order_sql = 'ORDER BY "id" ASC'
        if limit is not None:
            params.append(limit)
            order_sql += f' LIMIT ${len(params)}'

        sql = (
            'INSERT INTO conversations '
            '("user_id", "session_id", "role", "content", "metadata", "status", "channel", "turn_id", "turn_seq", "created_at") '
            f'SELECT $1::varchar, $2::varchar, "role", "content", "metadata", "status", "channel", "turn_id", "turn_seq", "created_at" '
            f'FROM conversations WHERE {where_sql} {order_sql}'
        )
        result = await self.execute(sql, *params)
        # asyncpg 命令标签形如 "INSERT 0 N" / "INSERT N"
        if not result:
            return 0
        try:
            return int(str(result).split()[-1])
        except (ValueError, IndexError):
            return 0

    async def delete_conversations(
        self,
        user_id: str,
        session_id: str,
    ) -> int:
        """删除某会话的全部消息行（用于 fork 失败回滚，避免孤儿数据）。"""
        sql = 'DELETE FROM conversations WHERE "user_id" = $1 AND "session_id" = $2'
        result = await self.execute(sql, user_id, session_id)
        if not result:
            return 0
        try:
            return int(str(result).split()[-1])
        except (ValueError, IndexError):
            return 0

    async def archive_conversation_rows(self, ids: list[int]) -> int:
        """将指定消息行置为 ``archived``（软删，可恢复）。

        用于轮内压缩：一轮结束后把 thinking / tool_call 等内部行归档，
        使其在 UI 与回填中不再出现（等价于 隐藏/内部消息剔除）。
        逐行 UPDATE 以保持 PostgreSQL / MySQL 双后端兼容。
        """
        if not ids:
            return 0
        archived = 0
        for rid in ids:
            res = await self.execute(
                'UPDATE conversations SET "status" = \'archived\' '
                'WHERE "id" = $1 AND "status" = \'active\'',
                rid,
            )
            if res and "UPDATE 1" in str(res):
                archived += 1
        return archived

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
        # （PG `->>` / MySQL JSON_UNQUOTE(JSON_EXTRACT(...)) 分歧由注册表消解）
        rows = await self.fetch_named("get_user_sessions", user_id, limit)

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
        """软删除会话（将 conversations 消息 + sessions 记录均标记为 deleted）

        Args:
            user_id: 用户 ID
            session_id: 会话 ID

        Returns:
            conversations 受影响的行数
        """
        sql = """
            UPDATE conversations
            SET "status" = 'deleted'
            WHERE "user_id" = $1 AND "session_id" = $2 AND "status" = 'active'
        """
        result = await self.execute(sql, user_id, session_id)
        affected = int(result.split()[-1]) if result else 0

        # 同步软删 sessions 表，避免侧栏残留已删除会话
        await self.execute(
            'UPDATE sessions SET "status" = \'deleted\' '
            'WHERE "user_id" = $1 AND "id" = $2 AND "status" = \'active\'',
            user_id, session_id,
        )
        return affected

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
            SET "status" = 'deleted'
            WHERE "id" = $1 AND "status" = 'active'
        """
        result = await self.execute(sql, conversation_id)
        return "UPDATE 1" in result

    async def upsert_session_title(
        self,
        user_id: str,
        session_id: str,
        title: str,
    ) -> None:
        """更新或插入会话标题（同时写入 title 列 + config.title）

        Args:
            user_id: 用户 ID
            session_id: 会话 ID
            title: 会话标题
        """
        import json

        await self.execute_named(
            "upsert_session_title",
            session_id,
            user_id,
            title,
            json.dumps({"title": title}),
        )

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
        # PG `->>` / MySQL JSON_UNQUOTE(JSON_EXTRACT(...)) 分歧由注册表消解；
        # 无匹配行时两侧均返回 NULL → None
        return await self.fetchval_named(
            "get_session_title",
            session_id,
            user_id,
        )

    # ------------------------------------------------------------------
    # 用户（UserService 调用）
    # ------------------------------------------------------------------

    async def upsert_user(
        self,
        user_id: str,
        role: str,
        display_name: str | None = None,
        is_root: bool = False,
    ) -> str:
        """懒注册/更新用户记录（users 表）

        Args:
            user_id: 规范化后的用户标识（主键）
            role: 角色（'normal' / 'root'）
            display_name: 可选展示名
            is_root: 是否 root（与 role 一致）

        Returns:
            user_id
        """
        return await self.insert_returning_id(
            "upsert_user",
            user_id,
            role,
            display_name,
            is_root,
        )

    async def get_user_role(self, user_id: str) -> str | None:
        """查询用户角色（无记录返回 None）"""
        return await self.fetchval_named("get_user_role", user_id)

    async def list_users(self) -> list[dict]:
        """列出所有已登记用户（root 管理视图用）"""
        rows = await self.fetch(
            'SELECT "user_id", "role", "display_name", "is_root", '
            '"created_at", "last_active" FROM users ORDER BY "created_at" ASC'
        )
        return [{k: row[k] for k in row.keys()} for row in rows]

    @property
    def is_initialized(self) -> bool:
        """数据库后端是否已初始化"""
        return self._backend is not None
