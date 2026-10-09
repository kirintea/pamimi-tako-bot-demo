-- Fork 诊断脚本
-- 用法：在 PostgreSQL 中执行，替换下面的 user_id 为你实际的用户 id

-- 1. 查看所有会话及其消息数（验证 conversations 表是否有数据）
SELECT
    "session_id",
    "user_id",
    COUNT(*) AS total_rows,
    COUNT(*) FILTER (WHERE "status" = 'active') AS active_rows,
    COUNT(*) FILTER (WHERE "role" = 'user') AS user_rows,
    MIN("id") AS min_id,
    MAX("id") AS max_id
FROM conversations
GROUP BY "session_id", "user_id"
ORDER BY MAX("created_at") DESC
LIMIT 20;

-- 2. 查看最近被 fork 出的会话（通过 storage.fork_session 创建的 sessions 行）
--    注意：sessions 表的 id 就是 session_id
SELECT
    s."id" AS session_id,
    s."user_id",
    s."parent_session_id",
    s."depth",
    s."source",
    (SELECT COUNT(*) FROM conversations c
     WHERE c."session_id" = s."id" AND c."user_id" = s."user_id" AND c."status" = 'active'
    ) AS conv_count
FROM sessions s
WHERE s."source" = 'FORK'
ORDER BY s."created_at" DESC
LIMIT 10;

-- 3. 对比：查看某个用户的所有会话（替换 'your_user_id'）
-- SELECT "session_id", COUNT(*) FILTER (WHERE "status"='active') AS active
-- FROM conversations
-- WHERE "user_id" = 'your_user_id'
-- GROUP BY "session_id";