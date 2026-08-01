---
skill_id: planner.route_learning_plan
version: 1.2.1
agent: planner_agent
task_type: learning_plan
---

# 任务目标

识别“制定、调整或恢复学习计划”的请求，并选择完成该交付物所需的最小 Agent 集合。Planner 只形成执行编排，不生成计划正文，也不负责提炼知识库检索词。

## 推荐编排骨架

该骨架是参考案例，不是固定模板。若当前请求确实需要学习计划，通常需要：

1. `memory_agent`：规划流程的常规上下文节点，负责读取、提取和治理长短期记忆；上下文压缩仅由系统阈值触发，Planner 无需自行判断。
2. `knowledge_base_agent`：仅在计划需要教材事实、知识内容或题目证据时，从原始 `user_request` 解析检索意图并整理证据。
3. `diagnosis_agent`：结合画像、行为、知识状态、历史计划以及可选教材证据生成诊断与计划建议。
4. `audit_agent`：长期或短期规划必须先审核完整自然语言建议、内部编译合同与父计划约束；当日任务无需此节点。
5. `learning_plan_service`：仅在长期或短期规划审核通过后，为建议注入系统 ID、版本、状态和时间。

## 选择规则

- `diagnosis_agent` 与 `learning_plan_service` 是计划交付的必要节点；长期或短期规划还必须选择 `audit_agent`。
- 用户只询问“最近学习状态如何”“学情如何”时，Diagnosis 直接分析学习数据，不要求 Knowledge；只有请求涉及专业知识解释或需要教材依据时才选择 Knowledge。
- 已有计划且用户只泛化地说“制定一份学习计划”、没有说明长期/短期/当日层级时，先明确告知已有计划，再询问本次要制定或调整哪一层。层级确认前不得默认重做长期规划，也不得进入 Diagnosis 重规划、Compiler 或 Audit。用户选定层级后，再检查该层缺失的基本信息和父计划条件。
- 查看/沿用已明确层级的已有计划时由 LearningPlanService 直接读取正式版本，不强制选择 Diagnosis；明确要求结合最新学情调整某一层时才选择 Diagnosis 判断该层如何更新。
- 请求涉及明确知识对象且计划内容需要教材依据时选择 `knowledge_base_agent`。
- `memory_agent` 是规划流程的常规上下文节点：它负责读取、提取和治理长短期记忆。是否执行上下文压缩由系统固定阈值通过 `memory_required` 决定，不由 Planner 或 Memory Agent 根据提示词自行推断。
- 用户只要求计划时不选择 `review_scheduler`、`expert_agent`；长期或短期规划仍必须选择 `audit_agent`，当日任务可不选择。
- `routing_reason` 说明用户交付目标、每个所选 Agent 的必要性，以及为何不生成资源。
- `plan_scope=daily_task` 或用户询问“今天学什么”时，交付物是当日任务；不得称为短期计划或周计划。
- 根据当前请求与最近对话判断规划层级。`plan_scope_hint` 只是可选弱提示，不能替代语义判断；“再给我今天的任务”通常承接已有短期计划并返回 `daily_task`。
- 制定或修改计划时必须输出明确的 `plan_scope`；无法确定时输出 `unspecified` 以进入追问。纯学情或学习状态查询不改动计划，可返回 `null`。

## 示例

输入：“请结合我的学习状态，为四君子汤制定本周复习计划，不需要生成复习卡。”
输出要点：`task_type=learning_plan`；选择 Diagnosis、Audit、LearningPlanService；若计划内容需要四君子汤教材依据，再选择 Knowledge；长期或短期规划 `requires_audit=true`。

输入：“请结合我的学习状态，为我制定一份学习计划。”且当前已有长期规划和短期计划。
输出要点：`task_type=learning_plan`、`plan_scope=unspecified`、`plan_action=clarify`、`requires_clarification=true`；先说明已有长期规划和短期计划，并询问用户要制定或调整长期规划、短期计划还是当日任务。此轮不生成任何计划，不运行 Compiler 或 Audit；用户选定层级后才检查基本信息并继续。

输入："最近时间减少了，把原来的学习计划调整成每天十五分钟。"
输出要点：`task_type=learning_plan`；选择 Diagnosis 和 LearningPlanService；若调整内容依赖教材知识再选择 Knowledge；若调整的是长期或短期计划，仍需选择 Audit，不得选择 Expert。

输入：“我今天要学习些什么东西？”且 `plan_scope=daily_task`
输出要点：`task_type=learning_plan`、`plan_scope=daily_task`；路由理由明确这是基于已有长短期计划和当前学情生成当日任务，不得描述为短期计划。
