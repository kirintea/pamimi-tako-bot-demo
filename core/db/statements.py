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
    # [已禁用] upsert_agent — agents 表已从 DDL 移除（建而不用）
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
            AS new_values
            ON DUPLICATE KEY UPDATE
                "config" = new_values."config",
                "state_json" = new_values."state_json",
                "source" = new_values."source",
                "parent_session_id" = COALESCE(new_values."parent_session_id", sessions."parent_session_id"),
                "depth" = COALESCE(NULLIF(new_values."depth", 0), sessions."depth"),
                "updated_at" = new_values."updated_at"
        """,
    ),
    # [已禁用] upsert_mcp — mcps 表已从 DDL 移除（改 configs/mcps.json 文件驱动，运行时单一真源）
    # [已禁用] upsert_skill — skills 表已从 DDL 移除（运行时目录加载 + configs/skills.json 元数据）
    # [已禁用] upsert_channel — channels 表已从 DDL 移除（已改 JSON 文件驱动）
    # [已禁用] upsert_schedule — schedules 表已从 DDL 移除（建而不用）
    # ---------------------------------------------------------------
    # user_service.upsert_user — 客户端主键 (user_id) 冲突更新
    # ---------------------------------------------------------------
    # 时间列（created_at / last_active）由应用侧按 database.timezone 生成传参
    # （$5/$6）：DB 服务端时区可能是 UTC，NOW()/DEFAULT 落库会差 8 小时。
    "upsert_user": Statement(
        pg="""
            INSERT INTO users ("user_id", "role", "display_name", "is_root", "channel", "created_at", "last_active")
            VALUES ($1, $2, $3, $4, $5, $6, $7)
            ON CONFLICT ("user_id") DO UPDATE SET
                "role" = EXCLUDED."role",
                "display_name" = COALESCE(EXCLUDED."display_name", users."display_name"),
                "is_root" = EXCLUDED."is_root",
                "channel" = EXCLUDED."channel",
                "last_active" = EXCLUDED."last_active"
            RETURNING "user_id"
        """,
        mysql="""
            INSERT INTO users ("user_id", "role", "display_name", "is_root", "channel", "created_at", "last_active")
            VALUES ($1, $2, $3, $4, $5, $6, $7)
            AS new_values
            ON DUPLICATE KEY UPDATE
                "role" = new_values."role",
                "display_name" = COALESCE(new_values."display_name", users."display_name"),
                "is_root" = new_values."is_root",
                "channel" = new_values."channel",
                "last_active" = new_values."last_active"
        """,
        # upsert 在 MySQL 侧 update 时 lastrowid 为 0，故回查现有行 user_id
        mysql_id_sql='SELECT "user_id" FROM users WHERE "user_id" = $1',
        mysql_id_args=(1,),
    ),
    # ---------------------------------------------------------------
    # user_service.get_user_role — 单值查询
    # ---------------------------------------------------------------
    "get_user_role": Statement(
        pg="""
            SELECT "role" FROM users WHERE "user_id" = $1
        """,
        mysql="""
            SELECT "role" FROM users WHERE "user_id" = $1
        """,
    ),
    # ---------------------------------------------------------------
    # database.insert_conversation — 自增主键纯 INSERT
    # ---------------------------------------------------------------
    # created_at（$9）由应用侧按 database.timezone 生成传参，不依赖
    # DATETIME DEFAULT CURRENT_TIMESTAMP(3)（DB 服务端跑 UTC 时会差 8 小时）
    "insert_conversation": Statement(
        pg="""
            INSERT INTO conversations ("user_id", "session_id", "role", "content", "metadata", "channel", "turn_id", "turn_seq", "created_at")
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
            RETURNING "id"
        """,
        mysql="""
            INSERT INTO conversations ("user_id", "session_id", "role", "content", "metadata", "channel", "turn_id", "turn_seq", "created_at")
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
        """,
    ),
    # ---------------------------------------------------------------
    # database.upsert_session_title — 写入 title 列 + config.title（双写兼容）
    # 参数：$1=session_id, $2=user_id, $3=title(VARCHAR), $4=config(JSON)
    # ---------------------------------------------------------------
    # created_at/updated_at（$5/$6）由应用侧按 database.timezone 生成传参（同上，避免 DB NOW() UTC）
    "upsert_session_title": Statement(
        pg="""
            INSERT INTO sessions ("id", "user_id", "agent_id", "title", "config", "status", "created_at", "updated_at")
            VALUES ($1, $2, 'default', $3, $4::jsonb, 'active', $5, $6)
            ON CONFLICT ("id") DO UPDATE SET
                "title" = EXCLUDED."title",
                "config" = COALESCE(sessions."config", '{}'::jsonb) || EXCLUDED."config",
                "updated_at" = EXCLUDED."updated_at"
        """,
        mysql="""
            INSERT INTO sessions ("id", "user_id", "agent_id", "title", "config", "status", "created_at", "updated_at")
            VALUES ($1, $2, 'default', $3, CAST($4 AS JSON), 'active', $5, $6)
            AS new_values
            ON DUPLICATE KEY UPDATE
                "title" = new_values."title",
                "config" = JSON_MERGE_PATCH(COALESCE(sessions."config", JSON_OBJECT()), new_values."config"),
                "updated_at" = new_values."updated_at"
        """,
    ),
    # ---------------------------------------------------------------
    # database.get_session_title — 优先读 title 列，降级到 config->>'title'
    # ---------------------------------------------------------------
    "get_session_title": Statement(
        pg="""
            SELECT COALESCE("title", "config"->>'title') AS title
            FROM sessions
            WHERE "id" = $1 AND "user_id" = $2 AND "status" = 'active'
        """,
        mysql="""
            SELECT COALESCE("title", JSON_UNQUOTE(JSON_EXTRACT("config", '$.title'))) AS title
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
                COALESCE(s."title", s."config"->>'title') AS custom_title,
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
            GROUP BY c."session_id", s."title", s."config"
            ORDER BY last_active DESC
            LIMIT $2
        """,
        mysql="""
            SELECT
                c."session_id",
                MIN(c."created_at") AS created_at,
                MAX(c."created_at") AS last_active,
                SUM(CASE WHEN c."role" = 'user' THEN 1 ELSE 0 END) AS message_count,
                COALESCE(s."title", JSON_UNQUOTE(JSON_EXTRACT(s."config", '$.title'))) AS custom_title,
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
            GROUP BY c."session_id", s."title", s."config"
            ORDER BY last_active DESC
            LIMIT $2
        """,
    ),
    # ---------------------------------------------------------------
    # database.get_all_sessions — root 管理视图：跨用户查询所有会话
    # ---------------------------------------------------------------
    "get_all_sessions": Statement(
        pg="""
            SELECT
                c."user_id",
                c."session_id",
                MIN(c."created_at") AS created_at,
                MAX(c."created_at") AS last_active,
                COUNT(*) FILTER (WHERE c."role" = 'user') AS message_count,
                COALESCE(s."title", s."config"->>'title') AS custom_title,
                (
                    SELECT LEFT(x."content", 30)
                    FROM conversations x
                    WHERE x."user_id" = c."user_id"
                      AND x."session_id" = c."session_id"
                      AND x."role" = 'user'
                      AND x."status" = 'active'
                    ORDER BY x."id" ASC
                    LIMIT 1
                ) AS first_message,
                MAX(c."channel") AS channel,
                u."display_name"
            FROM conversations c
            LEFT JOIN sessions s ON s."id" = c."session_id" AND s."user_id" = c."user_id" AND s."status" = 'active'
            LEFT JOIN users u ON u."user_id" = c."user_id"
            WHERE c."status" = 'active'
            GROUP BY c."user_id", c."session_id", s."title", s."config", u."display_name"
            ORDER BY last_active DESC
            LIMIT $1
        """,
        mysql="""
            SELECT
                c."user_id",
                c."session_id",
                MIN(c."created_at") AS created_at,
                MAX(c."created_at") AS last_active,
                SUM(CASE WHEN c."role" = 'user' THEN 1 ELSE 0 END) AS message_count,
                COALESCE(s."title", JSON_UNQUOTE(JSON_EXTRACT(s."config", '$.title'))) AS custom_title,
                (
                    SELECT LEFT(x."content", 30)
                    FROM conversations x
                    WHERE x."user_id" = c."user_id"
                      AND x."session_id" = c."session_id"
                      AND x."role" = 'user'
                      AND x."status" = 'active'
                    ORDER BY x."id" ASC
                    LIMIT 1
                ) AS first_message,
                MAX(c."channel") AS channel,
                u."display_name"
            FROM conversations c
            LEFT JOIN sessions s ON s."id" = c."session_id" AND s."user_id" = c."user_id" AND s."status" = 'active'
            LEFT JOIN users u ON u."user_id" = c."user_id"
            WHERE c."status" = 'active'
            GROUP BY c."user_id", c."session_id", s."title", s."config", u."display_name"
            ORDER BY last_active DESC
            LIMIT $1
        """,
    ),
    # ============================================================
    # 运营统计聚合（core/stats.py 消费）
    # 口径见 .draft/2026-10-22-ops-stats-dashboard.md
    #
    # 占位符约定（各语句自成一体，见下方注释）：
    #   *_series: $1=granularity $2=tz $3=start $4=end $5=channel（NULL=全部）
    #   *_total / stats_channel_*: $1=start $2=end [$3=channel]
    #
    # MySQL 侧 created_at 是按 database.timezone 落的 naive 墙钟，分桶无需
    # 时区转换，故 mysql 变体不引用 $2。translate_placeholders 按出现顺序
    # 重排参数、多余参数安全忽略，因此双方言可共用同一 args 元组。
    # ============================================================

    # 使用人数(UV) + 对话轮次(Turns) —— 按桶
    # turns = COUNT(DISTINCT turn_id)（NULL 自动忽略）
    #       + turn_id IS NULL 且 role='user' 的旧行数
    "stats_conv_series": Statement(
        pg="""
            SELECT date_trunc($1::text, "created_at" AT TIME ZONE $2::text) AS "bucket",
                   COUNT(DISTINCT "user_id") AS "uv",
                   COUNT(DISTINCT "turn_id") AS "turns_v3",
                   SUM(CASE WHEN "turn_id" IS NULL AND "role" = 'user'
                            THEN 1 ELSE 0 END) AS "turns_legacy"
            FROM "conversations"
            WHERE "status" = 'active'
              AND "created_at" >= $3 AND "created_at" < $4
              AND ($5::text IS NULL OR "channel" = $5)
            GROUP BY 1
            ORDER BY 1
        """,
        mysql="""
            SELECT CASE $1
                     WHEN 'day' THEN DATE("created_at")
                     WHEN 'week' THEN DATE_SUB(DATE("created_at"),
                                               INTERVAL WEEKDAY("created_at") DAY)
                     WHEN 'month' THEN STR_TO_DATE(DATE_FORMAT("created_at", '%Y-%m-01'),
                                                   '%Y-%m-%d')
                   END AS "bucket",
                   COUNT(DISTINCT "user_id") AS "uv",
                   COUNT(DISTINCT "turn_id") AS "turns_v3",
                   SUM(CASE WHEN "turn_id" IS NULL AND "role" = 'user'
                            THEN 1 ELSE 0 END) AS "turns_legacy"
            FROM "conversations"
            WHERE "status" = 'active'
              AND "created_at" >= $3 AND "created_at" < $4
              AND ($5 IS NULL OR "channel" = $5)
            GROUP BY 1
            ORDER BY 1
        """,
    ),

    # 使用人数 + 对话轮次 —— 整窗合计（uv/turns 跨桶不可加，必须单独算）
    "stats_conv_total": Statement(
        pg="""
            SELECT COUNT(DISTINCT "user_id") AS "uv",
                   COUNT(DISTINCT "turn_id") AS "turns_v3",
                   SUM(CASE WHEN "turn_id" IS NULL AND "role" = 'user'
                            THEN 1 ELSE 0 END) AS "turns_legacy"
            FROM "conversations"
            WHERE "status" = 'active'
              AND "created_at" >= $1 AND "created_at" < $2
              AND ($3::text IS NULL OR "channel" = $3)
        """,
        mysql="""
            SELECT COUNT(DISTINCT "user_id") AS "uv",
                   COUNT(DISTINCT "turn_id") AS "turns_v3",
                   SUM(CASE WHEN "turn_id" IS NULL AND "role" = 'user'
                            THEN 1 ELSE 0 END) AS "turns_legacy"
            FROM "conversations"
            WHERE "status" = 'active'
              AND "created_at" >= $1 AND "created_at" < $2
              AND ($3 IS NULL OR "channel" = $3)
        """,
    ),

    # 活跃会话数 —— 按桶（活动日归属，同会话跨天各计一次）
    # INNER JOIN sessions：两口径分母一致（都以 sessions 表为会话真源）
    "stats_active_series": Statement(
        pg="""
            SELECT date_trunc($1::text, c."created_at" AT TIME ZONE $2::text) AS "bucket",
                   COUNT(DISTINCT c."session_id") AS "sessions_active"
            FROM "conversations" c
            JOIN "sessions" s ON s."id" = c."session_id" AND s."status" = 'active'
            WHERE c."status" = 'active'
              AND c."created_at" >= $3 AND c."created_at" < $4
              AND ($5::text IS NULL OR c."channel" = $5)
            GROUP BY 1
            ORDER BY 1
        """,
        mysql="""
            SELECT CASE $1
                     WHEN 'day' THEN DATE(c."created_at")
                     WHEN 'week' THEN DATE_SUB(DATE(c."created_at"),
                                               INTERVAL WEEKDAY(c."created_at") DAY)
                     WHEN 'month' THEN STR_TO_DATE(DATE_FORMAT(c."created_at", '%Y-%m-01'),
                                                   '%Y-%m-%d')
                   END AS "bucket",
                   COUNT(DISTINCT c."session_id") AS "sessions_active"
            FROM "conversations" c
            JOIN "sessions" s ON s."id" = c."session_id" AND s."status" = 'active'
            WHERE c."status" = 'active'
              AND c."created_at" >= $3 AND c."created_at" < $4
              AND ($5 IS NULL OR c."channel" = $5)
            GROUP BY 1
            ORDER BY 1
        """,
    ),

    # 活跃会话数 —— 整窗去重合计
    "stats_active_total": Statement(
        pg="""
            SELECT COUNT(DISTINCT c."session_id") AS "sessions_active"
            FROM "conversations" c
            JOIN "sessions" s ON s."id" = c."session_id" AND s."status" = 'active'
            WHERE c."status" = 'active'
              AND c."created_at" >= $1 AND c."created_at" < $2
              AND ($3::text IS NULL OR c."channel" = $3)
        """,
        mysql="""
            SELECT COUNT(DISTINCT c."session_id") AS "sessions_active"
            FROM "conversations" c
            JOIN "sessions" s ON s."id" = c."session_id" AND s."status" = 'active'
            WHERE c."status" = 'active'
              AND c."created_at" >= $1 AND c."created_at" < $2
              AND ($3 IS NULL OR c."channel" = $3)
        """,
    ),

    # 新增会话数 —— 按桶（创建日归属，每会话只计一次）
    # sessions.channel 不可信（upsert_session_title 不写，默认 'web'），
    # 渠道过滤走派生渠道：该会话 active 消息的 MIN(channel)。
    "stats_new_series": Statement(
        pg="""
            SELECT date_trunc($1::text, t."created_at" AT TIME ZONE $2::text) AS "bucket",
                   COUNT(*) AS "sessions_new"
            FROM (
                SELECT s."id" AS "sid", s."created_at" AS "created_at",
                       COALESCE((SELECT MIN(c."channel") FROM "conversations" c
                                 WHERE c."session_id" = s."id"
                                   AND c."status" = 'active'), '') AS "channel"
                FROM "sessions" s
                WHERE s."status" = 'active'
                  AND s."created_at" >= $3 AND s."created_at" < $4
            ) t
            WHERE ($5::text IS NULL OR t."channel" = $5)
            GROUP BY 1
            ORDER BY 1
        """,
        mysql="""
            SELECT CASE $1
                     WHEN 'day' THEN DATE(t."created_at")
                     WHEN 'week' THEN DATE_SUB(DATE(t."created_at"),
                                               INTERVAL WEEKDAY(t."created_at") DAY)
                     WHEN 'month' THEN STR_TO_DATE(DATE_FORMAT(t."created_at", '%Y-%m-01'),
                                                   '%Y-%m-%d')
                   END AS "bucket",
                   COUNT(*) AS "sessions_new"
            FROM (
                SELECT s."id" AS "sid", s."created_at" AS "created_at",
                       COALESCE((SELECT MIN(c."channel") FROM "conversations" c
                                 WHERE c."session_id" = s."id"
                                   AND c."status" = 'active'), '') AS "channel"
                FROM "sessions" s
                WHERE s."status" = 'active'
                  AND s."created_at" >= $3 AND s."created_at" < $4
            ) t
            WHERE ($5 IS NULL OR t."channel" = $5)
            GROUP BY 1
            ORDER BY 1
        """,
    ),

    # 新增会话数 —— 整窗合计（等于 sessions 表行数）
    "stats_new_total": Statement(
        pg="""
            SELECT COUNT(*) AS "sessions_new"
            FROM (
                SELECT s."id" AS "sid",
                       COALESCE((SELECT MIN(c."channel") FROM "conversations" c
                                 WHERE c."session_id" = s."id"
                                   AND c."status" = 'active'), '') AS "channel"
                FROM "sessions" s
                WHERE s."status" = 'active'
                  AND s."created_at" >= $1 AND s."created_at" < $2
            ) t
            WHERE ($3::text IS NULL OR t."channel" = $3)
        """,
        mysql="""
            SELECT COUNT(*) AS "sessions_new"
            FROM (
                SELECT s."id" AS "sid",
                       COALESCE((SELECT MIN(c."channel") FROM "conversations" c
                                 WHERE c."session_id" = s."id"
                                   AND c."status" = 'active'), '') AS "channel"
                FROM "sessions" s
                WHERE s."status" = 'active'
                  AND s."created_at" >= $1 AND s."created_at" < $2
            ) t
            WHERE ($3 IS NULL OR t."channel" = $3)
        """,
    ),

    # 渠道维度 —— UV / 轮次（按 conversations.channel 分组）
    "stats_channel_conv": Statement(
        pg="""
            SELECT "channel" AS "channel",
                   COUNT(DISTINCT "user_id") AS "uv",
                   COUNT(DISTINCT "turn_id") AS "turns_v3",
                   SUM(CASE WHEN "turn_id" IS NULL AND "role" = 'user'
                            THEN 1 ELSE 0 END) AS "turns_legacy"
            FROM "conversations"
            WHERE "status" = 'active'
              AND "created_at" >= $1 AND "created_at" < $2
            GROUP BY 1
            ORDER BY 1
        """,
        mysql="""
            SELECT "channel" AS "channel",
                   COUNT(DISTINCT "user_id") AS "uv",
                   COUNT(DISTINCT "turn_id") AS "turns_v3",
                   SUM(CASE WHEN "turn_id" IS NULL AND "role" = 'user'
                            THEN 1 ELSE 0 END) AS "turns_legacy"
            FROM "conversations"
            WHERE "status" = 'active'
              AND "created_at" >= $1 AND "created_at" < $2
            GROUP BY 1
            ORDER BY 1
        """,
    ),

    # 渠道维度 —— 活跃会话（按消息行 channel 分组；同会话内 channel 恒定）
    "stats_channel_active": Statement(
        pg="""
            SELECT c."channel" AS "channel",
                   COUNT(DISTINCT c."session_id") AS "sessions_active"
            FROM "conversations" c
            JOIN "sessions" s ON s."id" = c."session_id" AND s."status" = 'active'
            WHERE c."status" = 'active'
              AND c."created_at" >= $1 AND c."created_at" < $2
            GROUP BY 1
            ORDER BY 1
        """,
        mysql="""
            SELECT c."channel" AS "channel",
                   COUNT(DISTINCT c."session_id") AS "sessions_active"
            FROM "conversations" c
            JOIN "sessions" s ON s."id" = c."session_id" AND s."status" = 'active'
            WHERE c."status" = 'active'
              AND c."created_at" >= $1 AND c."created_at" < $2
            GROUP BY 1
            ORDER BY 1
        """,
    ),

    # 渠道维度 —— 新增会话（按派生会话渠道分组）
    "stats_channel_new": Statement(
        pg="""
            SELECT t."channel" AS "channel", COUNT(*) AS "sessions_new"
            FROM (
                SELECT s."id" AS "sid",
                       COALESCE((SELECT MIN(c."channel") FROM "conversations" c
                                 WHERE c."session_id" = s."id"
                                   AND c."status" = 'active'), '') AS "channel"
                FROM "sessions" s
                WHERE s."status" = 'active'
                  AND s."created_at" >= $1 AND s."created_at" < $2
            ) t
            GROUP BY 1
            ORDER BY 1
        """,
        mysql="""
            SELECT t."channel" AS "channel", COUNT(*) AS "sessions_new"
            FROM (
                SELECT s."id" AS "sid",
                       COALESCE((SELECT MIN(c."channel") FROM "conversations" c
                                 WHERE c."session_id" = s."id"
                                   AND c."status" = 'active'), '') AS "channel"
                FROM "sessions" s
                WHERE s."status" = 'active'
                  AND s."created_at" >= $1 AND s."created_at" < $2
            ) t
            GROUP BY 1
            ORDER BY 1
        """,
    ),

    # 分桶 × 渠道 —— 按天×渠道明细表（口径同 *_series，GROUP BY 再加 channel）
    # series 式占位符：$1=granularity $2=tz $3=start $4=end $5=channel（NULL=全部）
    "stats_bucket_channel_conv": Statement(
        pg="""
            SELECT date_trunc($1::text, "created_at" AT TIME ZONE $2::text) AS "bucket",
                   "channel" AS "channel",
                   COUNT(DISTINCT "user_id") AS "uv",
                   COUNT(DISTINCT "turn_id") AS "turns_v3",
                   SUM(CASE WHEN "turn_id" IS NULL AND "role" = 'user'
                            THEN 1 ELSE 0 END) AS "turns_legacy"
            FROM "conversations"
            WHERE "status" = 'active'
              AND "created_at" >= $3 AND "created_at" < $4
              AND ($5::text IS NULL OR "channel" = $5)
            GROUP BY 1, 2
            ORDER BY 1, 2
        """,
        mysql="""
            SELECT CASE $1
                     WHEN 'day' THEN DATE("created_at")
                     WHEN 'week' THEN DATE_SUB(DATE("created_at"),
                                               INTERVAL WEEKDAY("created_at") DAY)
                     WHEN 'month' THEN STR_TO_DATE(DATE_FORMAT("created_at", '%Y-%m-01'),
                                                   '%Y-%m-%d')
                   END AS "bucket",
                   "channel" AS "channel",
                   COUNT(DISTINCT "user_id") AS "uv",
                   COUNT(DISTINCT "turn_id") AS "turns_v3",
                   SUM(CASE WHEN "turn_id" IS NULL AND "role" = 'user'
                            THEN 1 ELSE 0 END) AS "turns_legacy"
            FROM "conversations"
            WHERE "status" = 'active'
              AND "created_at" >= $3 AND "created_at" < $4
              AND ($5 IS NULL OR "channel" = $5)
            GROUP BY 1, 2
            ORDER BY 1, 2
        """,
    ),

    "stats_bucket_channel_active": Statement(
        pg="""
            SELECT date_trunc($1::text, c."created_at" AT TIME ZONE $2::text) AS "bucket",
                   c."channel" AS "channel",
                   COUNT(DISTINCT c."session_id") AS "sessions_active"
            FROM "conversations" c
            JOIN "sessions" s ON s."id" = c."session_id" AND s."status" = 'active'
            WHERE c."status" = 'active'
              AND c."created_at" >= $3 AND c."created_at" < $4
              AND ($5::text IS NULL OR c."channel" = $5)
            GROUP BY 1, 2
            ORDER BY 1, 2
        """,
        mysql="""
            SELECT CASE $1
                     WHEN 'day' THEN DATE(c."created_at")
                     WHEN 'week' THEN DATE_SUB(DATE(c."created_at"),
                                               INTERVAL WEEKDAY(c."created_at") DAY)
                     WHEN 'month' THEN STR_TO_DATE(DATE_FORMAT(c."created_at", '%Y-%m-01'),
                                                   '%Y-%m-%d')
                   END AS "bucket",
                   c."channel" AS "channel",
                   COUNT(DISTINCT c."session_id") AS "sessions_active"
            FROM "conversations" c
            JOIN "sessions" s ON s."id" = c."session_id" AND s."status" = 'active'
            WHERE c."status" = 'active'
              AND c."created_at" >= $3 AND c."created_at" < $4
              AND ($5 IS NULL OR c."channel" = $5)
            GROUP BY 1, 2
            ORDER BY 1, 2
        """,
    ),

    "stats_bucket_channel_new": Statement(
        pg="""
            SELECT date_trunc($1::text, t."created_at" AT TIME ZONE $2::text) AS "bucket",
                   t."channel" AS "channel",
                   COUNT(*) AS "sessions_new"
            FROM (
                SELECT s."id" AS "sid", s."created_at" AS "created_at",
                       COALESCE((SELECT MIN(c."channel") FROM "conversations" c
                                 WHERE c."session_id" = s."id"
                                   AND c."status" = 'active'), '') AS "channel"
                FROM "sessions" s
                WHERE s."status" = 'active'
                  AND s."created_at" >= $3 AND s."created_at" < $4
            ) t
            WHERE ($5::text IS NULL OR t."channel" = $5)
            GROUP BY 1, 2
            ORDER BY 1, 2
        """,
        mysql="""
            SELECT CASE $1
                     WHEN 'day' THEN DATE(t."created_at")
                     WHEN 'week' THEN DATE_SUB(DATE(t."created_at"),
                                               INTERVAL WEEKDAY(t."created_at") DAY)
                     WHEN 'month' THEN STR_TO_DATE(DATE_FORMAT(t."created_at", '%Y-%m-01'),
                                                   '%Y-%m-%d')
                   END AS "bucket",
                   t."channel" AS "channel",
                   COUNT(*) AS "sessions_new"
            FROM (
                SELECT s."id" AS "sid", s."created_at" AS "created_at",
                       COALESCE((SELECT MIN(c."channel") FROM "conversations" c
                                 WHERE c."session_id" = s."id"
                                   AND c."status" = 'active'), '') AS "channel"
                FROM "sessions" s
                WHERE s."status" = 'active'
                  AND s."created_at" >= $3 AND s."created_at" < $4
            ) t
            WHERE ($5 IS NULL OR t."channel" = $5)
            GROUP BY 1, 2
            ORDER BY 1, 2
        """,
    ),

    # 渠道筛选器选项 —— 窗口内出现过的 channel（动态，不写死）
    "stats_channel_options": Statement(
        pg="""
            SELECT DISTINCT "channel" AS "channel"
            FROM "conversations"
            WHERE "status" = 'active'
              AND "created_at" >= $1 AND "created_at" < $2
            ORDER BY 1
        """,
        mysql="""
            SELECT DISTINCT "channel" AS "channel"
            FROM "conversations"
            WHERE "status" = 'active'
              AND "created_at" >= $1 AND "created_at" < $2
            ORDER BY 1
        """,
    ),
}
