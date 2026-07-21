---
skill_id: memory.compress_conversation
version: 1.0.0
agent: memory_agent
task_type: conversation_compression
---

# 触发前提

只有系统在模型调用前根据字符预算判定 `requires_compression=true` 时才执行本 Skill。模型不得自行降低阈值，也不得把普通短对话描述成需要压缩。

## 压缩目标

在不改变用户原意的前提下，把长会话压缩成后续 Agent 可消费的最小上下文：当前诉求、已确认事实、稳定偏好、临时约束、未决问题及必要来源。

## 写作要求

- `summary` 只保留与当前任务有关的内容，区分用户原话、系统已确认事实和仍待确认的信息。
- `preserved_facts` 只能包含用户明确陈述或输入中已确认的事实。
- `temporary_constraints` 写清约束的适用时间窗口；无法确认时放入未决问题。
- `unresolved_questions` 指出缺少什么，以及缺失会影响哪个后续决策。
- `memory_candidates` 只是待确认候选，不得当作正式长期记忆。
- 不生成学情评分、知识库结论、学习计划或系统状态。
