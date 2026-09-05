-- 会话来源标记：user=用户发起（侧边栏可见），system=系统自动任务（如到期复习卡
-- 自动调度，侧边栏隐藏但保留会话供排查）。
-- SQLite 与 MySQL 均兼容：ALTER 增加带默认值的列，存量行自动归为 user。
ALTER TABLE conversation_sessions
    ADD COLUMN source VARCHAR(16) NOT NULL DEFAULT 'user';

-- 存量回填：后台调度任务（due_review_dispatch）以 THREAD_ 开头作为会话标识，
-- 这些会话从未出现在用户侧边栏操作中，统一标记为 system。
-- 注意：不能用 LIKE 'THREAD%' 通配——pymysql 会把迁移 SQL 中的裸 % 当格式化
-- 符，导致 "unsupported format character"；INSTR 在 MySQL 与 SQLite 均可用。
UPDATE conversation_sessions
SET source = 'system'
WHERE INSTR(session_id, 'THREAD') = 1;
