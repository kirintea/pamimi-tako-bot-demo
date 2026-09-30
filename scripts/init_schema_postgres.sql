-- ============================================================
-- AgentScope Platform Server — PostgreSQL 建表脚本
-- 数据库: dmx_agent_db
-- 执行方式: psql -h <host> -U <user> -d dmx_agent_db -f init_schema_postgres.sql
-- 说明: 所有 CREATE TABLE 均带 IF NOT EXISTS，可重复执行
--       所有列名统一双引号，避免与 SQL 保留字冲突
-- ============================================================

-- 1. 对话历史表（核心）
CREATE TABLE IF NOT EXISTS conversations (
    "id"          BIGSERIAL PRIMARY KEY,
    "user_id"     VARCHAR(64) NOT NULL,
    "session_id"  VARCHAR(64) NOT NULL,
    "role"        VARCHAR(16) NOT NULL,
    "content"     TEXT NOT NULL,
    "metadata"    JSONB DEFAULT NULL,
    "status"      VARCHAR(16) NOT NULL DEFAULT 'active',
    "channel"     VARCHAR(16) NOT NULL DEFAULT 'web',
    "created_at"  TIMESTAMPTZ DEFAULT NOW()
);

-- 2. 会话表（含 fork 血缘）
CREATE TABLE IF NOT EXISTS sessions (
    "id"                VARCHAR(64) PRIMARY KEY,
    "user_id"           VARCHAR(64) NOT NULL,
    "agent_id"          VARCHAR(32) NOT NULL,
    "source"            VARCHAR(16) NOT NULL DEFAULT 'user',
    "team_id"           VARCHAR(32),
    "config"            JSONB DEFAULT NULL,
    "state_json"        TEXT NOT NULL DEFAULT '',
    "status"            VARCHAR(16) NOT NULL DEFAULT 'active',
    "parent_session_id" VARCHAR(64),
    "depth"             INTEGER NOT NULL DEFAULT 0,
    "channel"           VARCHAR(16) NOT NULL DEFAULT 'web',
    "created_at"        TIMESTAMPTZ DEFAULT NOW(),
    "updated_at"        TIMESTAMPTZ DEFAULT NOW()
);

-- 3. MCP 已安装表
CREATE TABLE IF NOT EXISTS mcps (
    "id"          VARCHAR(32) PRIMARY KEY,
    "user_id"     VARCHAR(64) NOT NULL,
    "name"        VARCHAR(128) NOT NULL,
    "transport"   VARCHAR(16) NOT NULL DEFAULT 'stdio',
    "config"      JSONB DEFAULT NULL,
    "enabled"     BOOLEAN NOT NULL DEFAULT TRUE,
    "created_at"  TIMESTAMPTZ DEFAULT NOW(),
    "updated_at"  TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE("user_id", "name")
);

-- 4. Skill 已安装表
CREATE TABLE IF NOT EXISTS skills (
    "id"          VARCHAR(32) PRIMARY KEY,
    "user_id"     VARCHAR(64) NOT NULL,
    "name"        VARCHAR(128) NOT NULL,
    "data"        JSONB DEFAULT NULL,
    "enabled"     BOOLEAN NOT NULL DEFAULT TRUE,
    "created_at"  TIMESTAMPTZ DEFAULT NOW(),
    "updated_at"  TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE("user_id", "name")
);

-- 5. 定时任务表
CREATE TABLE IF NOT EXISTS schedules (
    "id"          VARCHAR(32) PRIMARY KEY,
    "user_id"     VARCHAR(64) NOT NULL,
    "agent_id"    VARCHAR(32) NOT NULL,
    "session_id"  VARCHAR(32),
    "name"        VARCHAR(256) NOT NULL,
    "cron_expr"   VARCHAR(64) NOT NULL,
    "prompt"      TEXT NOT NULL DEFAULT '',
    "source"      VARCHAR(16) NOT NULL DEFAULT 'user',
    "enabled"     BOOLEAN NOT NULL DEFAULT TRUE,
    "last_run_at" TIMESTAMPTZ,
    "next_run_at" TIMESTAMPTZ,
    "created_at"  TIMESTAMPTZ DEFAULT NOW(),
    "updated_at"  TIMESTAMPTZ DEFAULT NOW()
);

-- 6. Agent 记录表
CREATE TABLE IF NOT EXISTS agents (
    "id"          VARCHAR(32) PRIMARY KEY,
    "user_id"     VARCHAR(64) NOT NULL,
    "source"      VARCHAR(16) NOT NULL DEFAULT 'user',
    "data"        JSONB NOT NULL,
    "created_at"  TIMESTAMPTZ DEFAULT NOW(),
    "updated_at"  TIMESTAMPTZ DEFAULT NOW()
);

-- ============================================================
-- 索引（全部 IF NOT EXISTS，可重复执行）
-- ============================================================

-- conversations 索引
CREATE INDEX IF NOT EXISTS idx_conv_user_session
    ON conversations("user_id", "session_id") WHERE "status" = 'active';
CREATE INDEX IF NOT EXISTS idx_conv_created
    ON conversations("created_at");
CREATE INDEX IF NOT EXISTS idx_conv_user_time
    ON conversations("user_id", "created_at" DESC) WHERE "status" = 'active';
CREATE INDEX IF NOT EXISTS idx_conv_status
    ON conversations("status") WHERE "status" != 'active';

-- sessions 索引
CREATE INDEX IF NOT EXISTS idx_sessions_user_agent
    ON sessions("user_id", "agent_id") WHERE "status" = 'active';
CREATE INDEX IF NOT EXISTS idx_sessions_team
    ON sessions("team_id") WHERE "team_id" IS NOT NULL AND "status" = 'active';
CREATE INDEX IF NOT EXISTS idx_sessions_status
    ON sessions("status") WHERE "status" != 'active';
CREATE INDEX IF NOT EXISTS idx_sessions_parent
    ON sessions("parent_session_id") WHERE "parent_session_id" IS NOT NULL;

-- mcps 索引
CREATE INDEX IF NOT EXISTS idx_mcps_user ON mcps("user_id");

-- skills 索引
CREATE INDEX IF NOT EXISTS idx_skills_user ON skills("user_id");

-- schedules 索引
CREATE INDEX IF NOT EXISTS idx_schedules_user ON schedules("user_id");

-- agents 索引
CREATE INDEX IF NOT EXISTS idx_agents_user ON agents("user_id");

-- ============================================================
-- 完成
-- ============================================================
SELECT 'Schema 初始化完成' AS result;