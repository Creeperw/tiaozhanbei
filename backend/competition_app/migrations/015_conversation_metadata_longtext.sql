-- conversation_messages.metadata_json 升级为 LONGTEXT
--
-- 原因：持久化协作回执（trace_events）虽然已截断，但模型调用轨迹在
-- 多智能体长链路下仍可能接近 TEXT 的 64KB 上限。LONGTEXT 提供双保险，
-- 避免 DataError 1406 导致正式对话保存失败。
ALTER TABLE conversation_messages
    MODIFY COLUMN metadata_json LONGTEXT NULL;
