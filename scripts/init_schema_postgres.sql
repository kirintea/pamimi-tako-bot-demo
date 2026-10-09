-- ============================================================
-- Pamimi Tako Bot Demo — PostgreSQL 建表脚本
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

-- 3. 用户表（权限 / root 识别，Phase 1）
--    root 身份权威来源为配置 + 环境变量 ROOT_USER_IDS，DB 仅作记录与审计
CREATE TABLE IF NOT EXISTS users (
    "user_id"      VARCHAR(64) PRIMARY KEY,
    "role"         VARCHAR(16) NOT NULL DEFAULT 'normal',
    "display_name" VARCHAR(128),
    "is_root"      BOOLEAN NOT NULL DEFAULT FALSE,
    "created_at"   TIMESTAMPTZ DEFAULT NOW(),
    "last_active"  TIMESTAMPTZ DEFAULT NOW()
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

-- users 索引
CREATE INDEX IF NOT EXISTS idx_users_role ON users("role");

-- ============================================================
-- 完成
-- ============================================================
SELECT 'Schema 初始化完成' AS result;