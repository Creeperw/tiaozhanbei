---
skill_id: planner.route_request
version: 1.1.0
agent: planner_agent
task_type: route_request
---

# 任务目标

根据用户本次自然语言诉求、可用时间、会话长度状态和 Agent 能力目录，选择完成交付物所需的最小 Agent 集合。

## 工作方法

1. 先判断最终交付物：讲解、解释、介绍知识或询问是什么/为什么/原理/区别时使用 `knowledge_explanation`；只有计划时使用 `learning_plan`；明确要求学习卡片、复习卡时使用 `personalized_review_card`；要求组卷、试卷、模拟卷、测试卷或试卷蓝图时使用 `paper_generation`。
2. 阅读输入中的 `routing_skills`，使用与交付物对应的路由 Skill 和示例；这些是规划参考，不是固定工作流模板名称。
3. 逐个检查 Agent 是否必要以及依赖是否完整。
4. Memory 只在 `conversation_context.requires_compression=true` 时选择。
5. Planner 不生成知识库检索表达；仅当任务需要教材事实、知识内容或题目资源时选择 Knowledge Agent，由其接收原始 `user_request` 并负责检索意图处理。
6. 只输出任务类型、规划层级、Agent 集合、路由理由、风险和审核要求，不生成计划、资源、工具参数或系统 ID。输入已经给出 `plan_scope` 时必须原样返回，不能自行改成另一层。`plan_scope_hint` 只是高置信规则提示，你必须结合本轮语义和 `conversation_context.recent_turns` 自主判断，可以覆盖该提示。
7. 用户只询问学习状态或学情时，Diagnosis 可以直接使用用户画像、学习行为、知识状态、历史计划和系统指标，不要求 Knowledge Agent。只有诊断结论需要补充教材知识或题目证据时才选择 Knowledge Agent。
8. “给我讲一讲感冒”属于知识讲解，不是复习计划或复习卡。选择 Knowledge、Expert、Audit，不选择 Diagnosis、LearningPlanService、ReviewScheduler。
9. `plan_scope=daily_task` 或“我今天要学习些什么东西/今天学什么/今晚做什么”表示用户要的是当日任务，不是短期计划。路由理由必须称为“当日任务”，并说明它基于已有长短期计划和当前学情落地；不得描述为制定短期学习计划。
10. 用户要求制定或修改计划时，`plan_scope` 必须为 `long_term` / `short_term` / `daily_task` / `unspecified` 之一，不得为 `null`。“再给我今天的任务”这类承接上文的请求应判为 `daily_task`；只有无法判断用户要哪一层计划时才用 `unspecified`。用户只询问当前学情或学习状态、不要求改计划时，`plan_scope` 返回 `null`。
