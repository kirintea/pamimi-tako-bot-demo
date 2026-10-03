-- ============================================================
-- AgentScope Platform Server — MySQL 建表脚本
-- 数据库: dmx_agent_db (MySQL >= 5.7.22)
-- 执行方式: mysql -h <host> -u <user> -p dmx_agent_db < init_schema_mysql.sql
-- 说明: CREATE TABLE 带 IF NOT EXISTS；索引重复创建会报 1061 可忽略
--       所有列名统一反引号，避免与 SQL 保留字冲突
-- ============================================================

-- 1. 对话历史表（核心）
CREATE TABLE IF NOT EXISTS conversations (
    `id`          BIGINT AUTO_INCREMENT PRIMARY KEY,
    `user_id`     VARCHAR(64) NOT NULL,
    `session_id`  VARCHAR(64) NOT NULL,
    `role`        VARCHAR(16) NOT NULL,
    `content`     TEXT NOT NULL,
    `metadata`    JSON NULL,
    `status`      VARCHAR(16) NOT NULL DEFAULT 'active',
    `channel`     VARCHAR(16) NOT NULL DEFAULT 'web',
    `created_at`  DATETIME(3) DEFAULT CURRENT_TIMESTAMP(3)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 2. 会话表（含 fork 血缘）
CREATE TABLE IF NOT EXISTS sessions (
    `id`                VARCHAR(64) PRIMARY KEY,
    `user_id`           VARCHAR(64) NOT NULL,
    `agent_id`          VARCHAR(32) NOT NULL,
    `source`            VARCHAR(16) NOT NULL DEFAULT 'user',
    `team_id`           VARCHAR(32) NULL,
    `config`            JSON NULL,
    `state_json`        TEXT NULL,
    `status`            VARCHAR(16) NOT NULL DEFAULT 'active',
    `parent_session_id` VARCHAR(64) NULL,
    `depth`             INTEGER NOT NULL DEFAULT 0,
    `channel`           VARCHAR(16) NOT NULL DEFAULT 'web',
    `created_at`        DATETIME(3) DEFAULT CURRENT_TIMESTAMP(3),
    `updated_at`        DATETIME(3) DEFAULT CURRENT_TIMESTAMP(3)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 3. MCP 已安装表
CREATE TABLE IF NOT EXISTS mcps (
    `id`          VARCHAR(32) PRIMARY KEY,
    `user_id`     VARCHAR(64) NOT NULL,
    `name`        VARCHAR(128) NOT NULL,
    `transport`   VARCHAR(16) NOT NULL DEFAULT 'stdio',
    `config`      JSON NULL,
    `enabled`     TINYINT(1) NOT NULL DEFAULT 1,
    `created_at`  DATETIME(3) DEFAULT CURRENT_TIMESTAMP(3),
    `updated_at`  DATETIME(3) DEFAULT CURRENT_TIMESTAMP(3),
    UNIQUE KEY uniq_mcps_user_name (`user_id`, `name`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 4. Skill 已安装表
CREATE TABLE IF NOT EXISTS skills (
    `id`          VARCHAR(32) PRIMARY KEY,
    `user_id`     VARCHAR(64) NOT NULL,
    `name`        VARCHAR(128) NOT NULL,
    `data`        JSON NULL,
    `enabled`     TINYINT(1) NOT NULL DEFAULT 1,
    `created_at`  DATETIME(3) DEFAULT CURRENT_TIMESTAMP(3),
    `updated_at`  DATETIME(3) DEFAULT CURRENT_TIMESTAMP(3),
    UNIQUE KEY uniq_skills_user_name (`user_id`, `name`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 5. 定时任务表
CREATE TABLE IF NOT EXISTS schedules (
    `id`          VARCHAR(32) PRIMARY KEY,
    `user_id`     VARCHAR(64) NOT NULL,
    `agent_id`    VARCHAR(32) NOT NULL,
    `session_id`  VARCHAR(32) NULL,
    `name`        VARCHAR(256) NOT NULL,
    `cron_expr`   VARCHAR(64) NOT NULL,
    `prompt`      TEXT NOT NULL,
    `source`      VARCHAR(16) NOT NULL DEFAULT 'user',
    `enabled`     TINYINT(1) NOT NULL DEFAULT 1,
    `last_run_at` DATETIME(3) NULL,
    `next_run_at` DATETIME(3) NULL,
    `created_at`  DATETIME(3) DEFAULT CURRENT_TIMESTAMP(3),
    `updated_at`  DATETIME(3) DEFAULT CURRENT_TIMESTAMP(3)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 6. Agent 记录表
CREATE TABLE IF NOT EXISTS agents (
    `id`          VARCHAR(32) PRIMARY KEY,
    `user_id`     VARCHAR(64) NOT NULL,
    `source`      VARCHAR(16) NOT NULL DEFAULT 'user',
    `data`        JSON NOT NULL,
    `created_at`  DATETIME(3) DEFAULT CURRENT_TIMESTAMP(3),
    `updated_at`  DATETIME(3) DEFAULT CURRENT_TIMESTAMP(3)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ============================================================
-- 索引（MySQL 无 IF NOT EXISTS，重复创建报 1061 可忽略）
-- ============================================================

-- conversations 索引
CREATE INDEX idx_conv_user_session ON conversations(`user_id`, `session_id`);
CREATE INDEX idx_conv_created ON conversations(`created_at`);
CREATE INDEX idx_conv_user_time ON conversations(`user_id`, `created_at`);
CREATE INDEX idx_conv_status ON conversations(`status`);

-- sessions 索引
CREATE INDEX idx_sessions_user_agent ON sessions(`user_id`, `agent_id`);
CREATE INDEX idx_sessions_team ON sessions(`team_id`);
CREATE INDEX idx_sessions_status ON sessions(`status`);
CREATE INDEX idx_sessions_parent ON sessions(`parent_session_id`);

-- mcps 索引
CREATE INDEX idx_mcps_user ON mcps(`user_id`);

-- skills 索引
CREATE INDEX idx_skills_user ON skills(`user_id`);

-- schedules 索引
CREATE INDEX idx_schedules_user ON schedules(`user_id`);

-- agents 索引
CREATE INDEX idx_agents_user ON agents(`user_id`);

-- ============================================================
-- 完成
-- ============================================================
SELECT 'Schema 初始化完成' AS result;