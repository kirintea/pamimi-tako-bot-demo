# -*- coding: utf-8 -*-

"""DDL 建表语句 — PostgreSQL / MySQL 双方言（幂等，可重复执行）"""

from __future__ import annotations

DDL_POSTGRES = [
    # 对话历史表（核心）
    """
    CREATE TABLE IF NOT EXISTS conversations (
        id          BIGSERIAL PRIMARY KEY,
        user_id     VARCHAR(64) NOT NULL,
        session_id  VARCHAR(64) NOT NULL,
        role        VARCHAR(16) NOT NULL,
        content     TEXT NOT NULL,
        metadata    JSONB DEFAULT NULL,
        status      VARCHAR(16) NOT NULL DEFAULT 'active',
        created_at  TIMESTAMPTZ DEFAULT NOW()
    )
    """,

    # 状态字段（软删除：active / deleted）
    """
    ALTER TABLE conversations ADD COLUMN IF NOT EXISTS status VARCHAR(16) NOT NULL DEFAULT 'active'
    """,

    # JSONB 字段默认值改为 NULL（已有表）
    """
    ALTER TABLE conversations ALTER COLUMN metadata SET DEFAULT NULL
    """,

    # 索引：用户+会话查询（过滤状态）
    """
    CREATE INDEX IF NOT EXISTS idx_conv_user_session
    ON conversations(user_id, session_id) WHERE status = 'active'
    """,

    # 索引：时间范围查询
    """
    CREATE INDEX IF NOT EXISTS idx_conv_created
    ON conversations(created_at)
    """,

    # 索引：用户最近对话（过滤状态）
    """
    CREATE INDEX IF NOT EXISTS idx_conv_user_time
    ON conversations(user_id, created_at DESC) WHERE status = 'active'
    """,

    # 索引：按状态查询（供数据部门清理 deleted 记录）
    """
    CREATE INDEX IF NOT EXISTS idx_conv_status
    ON conversations(status) WHERE status != 'active'
    """,

    # ============================================================
    # 服务层新表（Phase 1+）
    # ============================================================

    # Session 会话表
    """
    CREATE TABLE IF NOT EXISTS sessions (
        id          VARCHAR(32) PRIMARY KEY,
        user_id     VARCHAR(64) NOT NULL,
        agent_id    VARCHAR(32) NOT NULL,
        source      VARCHAR(16) NOT NULL DEFAULT 'user',
        team_id     VARCHAR(32),
        config      JSONB DEFAULT NULL,
        state_json  TEXT NOT NULL DEFAULT '',
        status      VARCHAR(16) NOT NULL DEFAULT 'active',
        created_at  TIMESTAMPTZ DEFAULT NOW(),
        updated_at  TIMESTAMPTZ DEFAULT NOW()
    )
    """,
    """
    ALTER TABLE sessions ALTER COLUMN config SET DEFAULT NULL
    """,

    # 状态字段（软删除：active / deleted）
    """
    ALTER TABLE sessions ADD COLUMN IF NOT EXISTS status VARCHAR(16) NOT NULL DEFAULT 'active'
    """,

    """
    CREATE INDEX IF NOT EXISTS idx_sessions_user_agent
    ON sessions(user_id, agent_id) WHERE status = 'active'
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_sessions_team
    ON sessions(team_id) WHERE team_id IS NOT NULL AND status = 'active'
    """,

    # 索引：按状态查询（供数据清理 deleted 记录）
    """
    CREATE INDEX IF NOT EXISTS idx_sessions_status
    ON sessions(status) WHERE status != 'active'
    """,

    # MCP 已安装表
    """
    CREATE TABLE IF NOT EXISTS mcps (
        id          VARCHAR(32) PRIMARY KEY,
        user_id     VARCHAR(64) NOT NULL,
        name        VARCHAR(128) NOT NULL,
        transport   VARCHAR(16) NOT NULL DEFAULT 'stdio',
        config      JSONB DEFAULT NULL,
        enabled     BOOLEAN NOT NULL DEFAULT TRUE,
        created_at  TIMESTAMPTZ DEFAULT NOW(),
        updated_at  TIMESTAMPTZ DEFAULT NOW(),
        UNIQUE(user_id, name)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_mcps_user
    ON mcps(user_id)
    """,

    # Skill 已安装表
    """
    CREATE TABLE IF NOT EXISTS skills (
        id          VARCHAR(32) PRIMARY KEY,
        user_id     VARCHAR(64) NOT NULL,
        name        VARCHAR(128) NOT NULL,
        data        JSONB DEFAULT NULL,
        enabled     BOOLEAN NOT NULL DEFAULT TRUE,
        created_at  TIMESTAMPTZ DEFAULT NOW(),
        updated_at  TIMESTAMPTZ DEFAULT NOW(),
        UNIQUE(user_id, name)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_skills_user
    ON skills(user_id)
    """,

    # 定时任务表
    """
    CREATE TABLE IF NOT EXISTS schedules (
        id          VARCHAR(32) PRIMARY KEY,
        user_id     VARCHAR(64) NOT NULL,
        agent_id    VARCHAR(32) NOT NULL,
        session_id  VARCHAR(32),
        name        VARCHAR(256) NOT NULL,
        cron_expr   VARCHAR(64) NOT NULL,
        prompt      TEXT NOT NULL DEFAULT '',
        source      VARCHAR(16) NOT NULL DEFAULT 'user',
        enabled     BOOLEAN NOT NULL DEFAULT TRUE,
        last_run_at TIMESTAMPTZ,
        next_run_at TIMESTAMPTZ,
        created_at  TIMESTAMPTZ DEFAULT NOW(),
        updated_at  TIMESTAMPTZ DEFAULT NOW()
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_schedules_user
    ON schedules(user_id)
    """,

    # ============================================================
    # Agent / Message 持久化表（storage.py 的 Agent/Message CRUD 使用）
    # 列定义与 core/storage_models.py 的 AgentRecord / MessageRecord 对齐，
    # 并匹配 storage.py 中 upsert_agent / upsert_message 的 INSERT 列顺序。
    # ============================================================

    # Agent 记录表
    # 列: id(VARCHAR(32) PK, 对应 AgentRecord._generate_id 的 16 位 hex)
    #     user_id / source / data(JSONB, 存 AgentData) / created_at / updated_at
    # upsert_agent 使用 ON CONFLICT (id) 更新，故 id 为主键。
    """
    CREATE TABLE IF NOT EXISTS agents (
        id          VARCHAR(32) PRIMARY KEY,
        user_id     VARCHAR(64) NOT NULL,
        source      VARCHAR(16) NOT NULL DEFAULT 'user',
        data        JSONB NOT NULL,
        created_at  TIMESTAMPTZ DEFAULT NOW(),
        updated_at  TIMESTAMPTZ DEFAULT NOW()
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_agents_user
    ON agents(user_id)
    """,

    # Message 记录表
    # 列: id(BIGSERIAL PK, 对应 _row_to_message 中 str(row["id"]))
    #     user_id / session_id / msg_id / role / content / metadata(JSONB) / created_at
    # upsert_message 通过「同 session 末条 msg_id 相同则更新」逻辑去重，不依赖唯一约束。
    """
    CREATE TABLE IF NOT EXISTS messages (
        id          BIGSERIAL PRIMARY KEY,
        user_id     VARCHAR(64) NOT NULL,
        session_id  VARCHAR(64) NOT NULL,
        msg_id      VARCHAR(64) NOT NULL,
        role        VARCHAR(16) NOT NULL,
        content     TEXT NOT NULL,
        metadata    JSONB DEFAULT NULL,
        created_at  TIMESTAMPTZ DEFAULT NOW()
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_messages_user_session
    ON messages(user_id, session_id)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_messages_user_session_msg
    ON messages(user_id, session_id, msg_id)
    """,

    # ============================================================
    # 会话分支血缘（Fork 特性）
    # ============================================================

    # parent_session_id：父会话 ID（Fork 血缘），根会话为 NULL
    """
    ALTER TABLE sessions ADD COLUMN IF NOT EXISTS parent_session_id VARCHAR(32)
    """,
    # depth：Fork 深度，根会话=0，每 fork 一次 +1
    """
    ALTER TABLE sessions ADD COLUMN IF NOT EXISTS depth INTEGER NOT NULL DEFAULT 0
    """,
    # 索引：按父会话查询子分支（仅对非根会话生效）
    """
    CREATE INDEX IF NOT EXISTS idx_sessions_parent
    ON sessions(parent_session_id) WHERE parent_session_id IS NOT NULL
    """,

    # ============================================================
    # 列宽迁移 — sessions.id / parent_session_id 扩容至 VARCHAR(64)
    # 原始 DDL 用 VARCHAR(32)，但 8090 API 层用 str(uuid.uuid4())（36 字符）
    # 生成 session_id，超出 32 字符上限。conversations.session_id 已是 VARCHAR(64)，
    # 此处对齐。ALTER ... TYPE 是幂等的，列宽不变时 PostgreSQL 不报错。
    # ============================================================
    """
    ALTER TABLE sessions ALTER COLUMN id TYPE VARCHAR(64)
    """,
    """
    ALTER TABLE sessions ALTER COLUMN parent_session_id TYPE VARCHAR(64)
    """,
]

# 服务端依赖的 7 张核心表（建表/校验/健康检查共用，单一事实来源）
REQUIRED_TABLES = [
    "conversations",
    "sessions",
    "mcps",
    "skills",
    "schedules",
    "agents",
    "messages",
]
