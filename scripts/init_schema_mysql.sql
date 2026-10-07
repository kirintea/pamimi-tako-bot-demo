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

-- 3. 用户表（权限 / root 识别，Phase 1）
CREATE TABLE IF NOT EXISTS users (
    `user_id`      VARCHAR(64) PRIMARY KEY,
    `role`         VARCHAR(16) NOT NULL DEFAULT 'normal',
    `display_name` VARCHAR(128) NULL,
    `is_root`      TINYINT(1) NOT NULL DEFAULT 0,
    `created_at`   DATETIME(3) DEFAULT CURRENT_TIMESTAMP(3),
    `last_active`  DATETIME(3) DEFAULT CURRENT_TIMESTAMP(3)
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

-- users 索引
CREATE INDEX idx_users_role ON users(`role`);

-- ============================================================
-- 完成
-- ============================================================
SELECT 'Schema 初始化完成' AS result;