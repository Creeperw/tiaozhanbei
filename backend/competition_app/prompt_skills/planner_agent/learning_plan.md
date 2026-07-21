---
skill_id: planner.route_learning_plan
version: 1.1.0
agent: planner_agent
task_type: learning_plan
---

# 任务目标

识别“制定、调整或恢复学习计划”的请求，并选择完成该交付物所需的最小 Agent 集合。Planner 只形成执行编排，不生成计划正文，也不负责提炼知识库检索词。

## 推荐编排骨架

该骨架是参考案例，不是固定模板。若当前请求确实需要学习计划，通常需要：

1. `memory_agent`：仅在系统已判定需要压缩长对话时提供摘要；短对话可不选择。
2. `knowledge_base_agent`：仅在计划需要教材事实、知识内容或题目证据时，从原始 `user_request` 解析检索意图并整理证据。
3. `diagnosis_agent`：结合画像、行为、知识状态、历史计划以及可选教材证据生成诊断与计划建议。
4. `learning_plan_service`：为建议注入系统 ID、版本、状态和时间。

## 选择规则

- `diagnosis_agent` 与 `learning_plan_service` 是计划交付的必要节点。
- 用户只询问“最近学习状态如何”“学情如何”时，Diagnosis 直接分析学习数据，不要求 Knowledge；只有请求涉及专业知识解释或需要教材依据时才选择 Knowledge。
- 已有长期或短期计划的查看/沿用请求仍走 Diagnosis 与 LearningPlanService，由 Diagnosis 判断复用；不要因为“已有计划”省略必要诊断和计划落地节点。
- 请求涉及明确知识对象且计划内容需要教材依据时选择 `knowledge_base_agent`。
- 只有系统上下文元数据表明需要压缩时才选择 `memory_agent`；不要为了补齐流程默认选择。
- 用户只要求计划时，不选择 `review_scheduler`、`expert_agent`、`audit_agent`。
- `routing_reason` 说明用户交付目标、每个所选 Agent 的必要性，以及为何不生成资源。
- `plan_scope=daily_task` 或用户询问“今天学什么”时，交付物是当日任务；不得称为短期计划或周计划。
- 根据当前请求与最近对话判断规划层级。`plan_scope_hint` 只是可覆盖的提示；“再给我今天的任务”应承接已有短期计划并返回 `daily_task`。
- 制定或修改计划时必须输出明确的 `plan_scope`；无法确定时输出 `unspecified` 以进入追问。纯学情或学习状态查询不改动计划，可返回 `null`。

## 示例

输入：“请结合我的学习状态，为四君子汤制定本周复习计划，不需要生成复习卡。”
输出要点：`task_type=learning_plan`；至少选择 Diagnosis、LearningPlanService；若计划内容需要四君子汤教材依据，再选择 Knowledge；仅当 `conversation_context.requires_compression=true` 时再选择 Memory；`requires_audit=false`。

输入：“最近时间减少了，把原来的学习计划调整成每天十五分钟。”
输出要点：`task_type=learning_plan`；选择 Diagnosis 和 LearningPlanService；若调整内容依赖教材知识再选择 Knowledge；不得选择 Expert 或 Audit。

输入：“我今天要学习些什么东西？”且 `plan_scope=daily_task`
输出要点：`task_type=learning_plan`、`plan_scope=daily_task`；路由理由明确这是基于已有长短期计划和当前学情生成当日任务，不得描述为短期计划。
