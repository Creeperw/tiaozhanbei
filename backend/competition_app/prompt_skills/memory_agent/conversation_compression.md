---
skill_id: memory.compress_conversation
version: 1.0.0
agent: memory_agent
task_type: conversation_compression
---

# 触发前提

只有系统在模型调用前根据固定字符预算判定 `memory_required=true` 时才执行本 Skill。模型不得自行计算或修改阈值，也不得把普通短对话描述成需要压缩。

## 压缩目标

在不改变用户原意的前提下，把长会话压缩成后续 Agent 可消费的最小正式对话。`summary` 只允许压缩用户与助教已经正式说出的内容，不允许混入检索证据、工具结果、执行轨迹、模型原始输入输出或编译器结构化结果。

## 写作要求

- `summary` 按 `user：……`、`assistant：……` 的纯文本格式概括与当前任务有关的正式对话；不得写“系统显示”“工具返回”“证据表明”等外部信息。
- `preserved_facts` 只能包含用户明确陈述或输入中已确认的事实。
- `temporary_constraints` 写清约束的适用时间窗口；无法确认时放入未决问题。
- `unresolved_questions` 指出缺少什么，以及缺失会影响哪个后续决策。
- `memory_candidates` 只是待确认候选，不得当作正式长期记忆。
- 不生成学情评分、知识库结论、学习计划或系统状态。

输出必须是且只能是符合给定 JSON Schema 的对象，字段严格限定为：`summary`、`preserved_facts`、`unresolved_questions`、`temporary_constraints`、`memory_candidates`。除上述字段外，严禁输出任何其他字段；系统对输出做严格字段校验，多出的任何字段都会导致本次压缩被判定为失败。用不到的字段一律返回空数组，不要自创字段。
