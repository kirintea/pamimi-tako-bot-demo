# -*- coding: utf-8 -*-

"""语句注册表 — 收敛 PostgreSQL / MySQL 分歧的 SQL

所有变体统一用 $N 占位符书写：
- PostgresBackend 直接执行 stmt.pg（asyncpg 原生 $N）
- MySQLBackend 执行 stmt.mysql，内部经 translate_placeholders 转为 %s，
  再经 translate_identifiers 将双引号标识符转为反引号。

列名统一使用 PG 双引号风格（如 "role", "source"），MySQL 侧自动翻译。

ID 返回语义（对齐 PG 的 RETURNING id）：
- 自增主键表（insert_conversation）→ MySQL 用 cur.lastrowid
- 客户端主键 upsert（agent / session / schedule）→ 调用方直接返回传入的 record.id
- (user_id, name) 冲突表（mcp / skill）→ mysql_id_sql 回查现有行 id
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Statement:
    """一条分歧 SQL 的双方言变体"""

    pg: str
    mysql: str
    #: MySQL 侧插入后回查 ID 的补充 SQL（None 则用 lastrowid / 由调用方返回 record.id）
    mysql_id_sql: str | None = None
    #: mysql_id_sql 的参数取自原语句的哪些 $N（1-based），如 (2, 3) 表示 $2 $3
    mysql_id_args: tuple[int, ...] = ()

    def pick(self, dialect: str) -> str:
        """按方言挑选语句变体（"postgres" / "mysql"）"""
        if dialect == "postgres":
            return self.pg
        if dialect == "mysql":
            return self.mysql
        raise ValueError(f"未知方言: {dialect!r}")


STATEMENTS: dict[str, Statement] = {
    # ---------------------------------------------------------------
    # storage.upsert_agent — 客户端主键 (id) 冲突更新
    # ---------------------------------------------------------------
    "upsert_agent": Statement(
        pg="""
            INSERT INTO agents ("id", "user_id", "source", "data", "created_at", "updated_at")
            VALUES ($1, $2, $3, $4, $5, $6)
            ON CONFLICT ("id") DO UPDATE SET
                "data" = EXCLUDED."data",
                "source" = EXCLUDED."source",
                "updated_at" = EXCLUDED."updated_at"
            RETURNING "id"
        """,
        mysql="""
            INSERT INTO agents ("id", "user_id", "source", "data", "created_at", "updated_at")
            VALUES ($1, $2, $3, $4, $5, $6)
            ON DUPLICATE KEY UPDATE
                "data" = VALUES("data"),
                "source" = VALUES("source"),
                "updated_at" = VALUES("updated_at")
        """,
    ),
    # ---------------------------------------------------------------
    # storage.upsert_session — 冲突时保留原有 parent/depth
    # ---------------------------------------------------------------
    "upsert_session": Statement(
        pg="""
            INSERT INTO sessions ("id", "user_id", "agent_id", "source", "config", "state_json",
                                  "parent_session_id", "depth", "created_at", "updated_at")
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
            ON CONFLICT ("id") DO UPDATE SET
                "config" = EXCLUDED."config",
                "state_json" = EXCLUDED."state_json",
                "source" = EXCLUDED."source",
                "parent_session_id" = COALESCE(EXCLUDED."parent_session_id", sessions."parent_session_id"),
                "depth" = COALESCE(NULLIF(EXCLUDED."depth", 0), sessions."depth"),
                "updated_at" = EXCLUDED."updated_at"
            RETURNING "id"
        """,
        mysql="""
            INSERT INTO sessions ("id", "user_id", "agent_id", "source", "config", "state_json",
                                  "parent_session_id", "depth", "created_at", "updated_at")
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
            ON DUPLICATE KEY UPDATE
                "config" = VALUES("config"),
                "state_json" = VALUES("state_json"),
                "source" = VALUES("source"),
                "parent_session_id" = COALESCE(VALUES("parent_session_id"), "parent_session_id"),
                "depth" = COALESCE(NULLIF(VALUES("depth"), 0), "depth"),
                "updated_at" = VALUES("updated_at")
        """,
    ),
    # ---------------------------------------------------------------
    # storage.upsert_mcp — (user_id, name) 唯一冲突，回查现有行 id
    # ---------------------------------------------------------------
    "upsert_mcp": Statement(
        pg="""
            INSERT INTO mcps ("id", "user_id", "name", "transport", "config", "enabled", "created_at", "updated_at")
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
            ON CONFLICT ("user_id", "name") DO UPDATE SET
                "transport" = EXCLUDED."transport",
                "config" = EXCLUDED."config",
                "enabled" = EXCLUDED."enabled",
                "updated_at" = EXCLUDED."updated_at"
            RETURNING "id"
        """,
        mysql="""
            INSERT INTO mcps ("id", "user_id", "name", "transport", "config", "enabled", "created_at", "updated_at")
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
            ON DUPLICATE KEY UPDATE
                "transport" = VALUES("transport"),
                "config" = VALUES("config"),
                "enabled" = VALUES("enabled"),
                "updated_at" = VALUES("updated_at")
        """,
        mysql_id_sql='SELECT "id" FROM mcps WHERE "user_id" = $1 AND "name" = $2',
        mysql_id_args=(2, 3),
    ),
    # ---------------------------------------------------------------
    # storage.upsert_skill — 同 (user_id, name) 冲突
    # ---------------------------------------------------------------
    "upsert_skill": Statement(
        pg="""
            INSERT INTO skills ("id", "user_id", "name", "data", "enabled", "created_at", "updated_at")
            VALUES ($1, $2, $3, $4, $5, $6, $7)
            ON CONFLICT ("user_id", "name") DO UPDATE SET
                "data" = EXCLUDED."data",
                "enabled" = EXCLUDED."enabled",
                "updated_at" = EXCLUDED."updated_at"
            RETURNING "id"
        """,
        mysql="""
            INSERT INTO skills ("id", "user_id", "name", "data", "enabled", "created_at", "updated_at")
            VALUES ($1, $2, $3, $4, $5, $6, $7)
            ON DUPLICATE KEY UPDATE
                "data" = VALUES("data"),
                "enabled" = VALUES("enabled"),
                "updated_at" = VALUES("updated_at")
        """,
        mysql_id_sql='SELECT "id" FROM skills WHERE "user_id" = $1 AND "name" = $2',
        mysql_id_args=(2, 3),
    ),
    # ---------------------------------------------------------------
    # storage.upsert_schedule — 客户端主键
    # ---------------------------------------------------------------
    "upsert_schedule": Statement(
        pg="""
            INSERT INTO schedules ("id", "user_id", "agent_id", "session_id", "name",
                                   "cron_expr", "prompt", "source", "enabled",
                                   "last_run_at", "next_run_at", "created_at", "updated_at")
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13)
            ON CONFLICT ("id") DO UPDATE SET
                "name" = EXCLUDED."name",
                "cron_expr" = EXCLUDED."cron_expr",
                "prompt" = EXCLUDED."prompt",
                "enabled" = EXCLUDED."enabled",
                "last_run_at" = EXCLUDED."last_run_at",
                "next_run_at" = EXCLUDED."next_run_at",
                "updated_at" = EXCLUDED."updated_at"
            RETURNING "id"
        """,
        mysql="""
            INSERT INTO schedules ("id", "user_id", "agent_id", "session_id", "name",
                                   "cron_expr", "prompt", "source", "enabled",
                                   "last_run_at", "next_run_at", "created_at", "updated_at")
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13)
            ON DUPLICATE KEY UPDATE
                "name" = VALUES("name"),
                "cron_expr" = VALUES("cron_expr"),
                "prompt" = VALUES("prompt"),
                "enabled" = VALUES("enabled"),
                "last_run_at" = VALUES("last_run_at"),
                "next_run_at" = VALUES("next_run_at"),
                "updated_at" = VALUES("updated_at")
        """,
    ),
    # ---------------------------------------------------------------
    # database.insert_conversation — 自增主键纯 INSERT
    # ---------------------------------------------------------------
    "insert_conversation": Statement(
        pg="""
            INSERT INTO conversations ("user_id", "session_id", "role", "content", "metadata", "channel", "turn_id", "turn_seq")
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
            RETURNING "id"
        """,
        mysql="""
            INSERT INTO conversations ("user_id", "session_id", "role", "content", "metadata", "channel", "turn_id", "turn_seq")
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
        """,
    ),
    # ---------------------------------------------------------------
    # database.upsert_session_title — config JSON 合并（PG || 拼接 vs MySQL JSON_MERGE_PATCH）
    # ---------------------------------------------------------------
    "upsert_session_title": Statement(
        pg="""
            INSERT INTO sessions ("id", "user_id", "agent_id", "config", "status")
            VALUES ($1, $2, 'default', $3, 'active')
            ON CONFLICT ("id") DO UPDATE SET
                "config" = COALESCE(sessions."config", '{}'::jsonb) || EXCLUDED."config",
                "updated_at" = NOW()
        """,
        mysql="""
            INSERT INTO sessions ("id", "user_id", "agent_id", "config", "status")
            VALUES ($1, $2, 'default', $3, 'active')
            ON DUPLICATE KEY UPDATE
                "config" = JSON_MERGE_PATCH(COALESCE("config", JSON_OBJECT()), VALUES("config")),
                "updated_at" = NOW()
        """,
    ),
    # ---------------------------------------------------------------
    # database.get_session_title — JSON 取字段
    # ---------------------------------------------------------------
    "get_session_title": Statement(
        pg="""
            SELECT "config"->>'title' AS title
            FROM sessions
            WHERE "id" = $1 AND "user_id" = $2 AND "status" = 'active'
        """,
        mysql="""
            SELECT JSON_UNQUOTE(JSON_EXTRACT("config", '$.title')) AS title
            FROM sessions
            WHERE "id" = $1 AND "user_id" = $2 AND "status" = 'active'
        """,
    ),
    # ---------------------------------------------------------------
    # database.get_user_sessions — 大聚合查询
    # ---------------------------------------------------------------
    "get_user_sessions": Statement(
        pg="""
            SELECT
                c."session_id",
                MIN(c."created_at") AS created_at,
                MAX(c."created_at") AS last_active,
                COUNT(*) FILTER (WHERE c."role" = 'user') AS message_count,
                s."config"->>'title' AS custom_title,
                (
                    SELECT LEFT(x."content", 30)
                    FROM conversations x
                    WHERE x."user_id" = $1
                      AND x."session_id" = c."session_id"
                      AND x."role" = 'user'
                      AND x."status" = 'active'
                    ORDER BY x."id" ASC
                    LIMIT 1
                ) AS first_message
            FROM conversations c
            LEFT JOIN sessions s ON s."id" = c."session_id" AND s."user_id" = c."user_id" AND s."status" = 'active'
            WHERE c."user_id" = $1 AND c."status" = 'active'
            GROUP BY c."session_id", s."config"
            ORDER BY last_active DESC
            LIMIT $2
        """,
        mysql="""
            SELECT
                c."session_id",
                MIN(c."created_at") AS created_at,
                MAX(c."created_at") AS last_active,
                SUM(CASE WHEN c."role" = 'user' THEN 1 ELSE 0 END) AS message_count,
                JSON_UNQUOTE(JSON_EXTRACT(s."config", '$.title')) AS custom_title,
                (
                    SELECT LEFT(x."content", 30)
                    FROM conversations x
                    WHERE x."user_id" = $1
                      AND x."session_id" = c."session_id"
                      AND x."role" = 'user'
                      AND x."status" = 'active'
                    ORDER BY x."id" ASC
                    LIMIT 1
                ) AS first_message
            FROM conversations c
            LEFT JOIN sessions s ON s."id" = c."session_id" AND s."user_id" = c."user_id" AND s."status" = 'active'
            WHERE c."user_id" = $1 AND c."status" = 'active'
            GROUP BY c."session_id", s."config"
            ORDER BY last_active DESC
            LIMIT $2
        """,
    ),
}
