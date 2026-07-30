---
skill_id: planner.route_request
version: 1.4.0
agent: planner_agent
task_type: route_request
---

# 任务目标

根据用户本次自然语言诉求、可用时间、会话长度状态和 Agent 能力目录，选择完成交付物所需的最小 Agent 集合。

## 工作方法

1. 先判断最终交付物：纯问候、感谢、告别或询问助教能力且没有学习任务时使用 `casual_conversation`，不选择下游 Agent，并在 `casual_response` 中结合本轮话语和最近对话生成自然、完整的用户回复；如果用户表达考试焦虑、紧张、挫败或需要鼓励，也可使用 `casual_conversation`，但回复应先共情，再给出可执行的当下建议和继续求助的入口，不要只返回一句固定欢迎语。天气、考试日期、报名时间、截止日期等时效性事实使用 `general_learning_support`，由 Knowledge 调用网络检索工具并标注来源；讲解单个概念、原理或区别时使用 `knowledge_explanation`；围绕教材或章节梳理学习要点、阅读重点、学习方法等开放式支持时使用 `general_learning_support`；查询用户本人近期学习、下一步学习重点、进度、掌握、复习或计划进展时使用 `learner_data_query`；只有明确要求创建、安排或修改计划时使用 `learning_plan`；明确要求学习卡片、复习卡时使用 `personalized_review_card`；要求组卷、试卷、模拟卷、测试卷或试卷蓝图时使用 `paper_generation`。问候语和真实任务同时出现时，以真实任务为准。
2. 阅读输入中的 `routing_skills`，使用与交付物对应的路由 Skill 和示例；这些是规划参考，不是固定工作流模板名称。
3. 逐个检查 Agent 是否必要以及依赖是否完整。
4. Memory 只在 `conversation_context.requires_compression=true` 时选择。
5. Planner 不生成知识库检索表达；仅当任务需要教材事实、知识内容或题目资源时选择 Knowledge Agent，由其接收原始 `user_request` 并负责检索意图处理。
6. 只输出任务类型、规划层级、Agent 集合、路由理由、风险和审核要求，不生成计划、资源、工具参数或系统 ID。输入已经给出 `plan_scope` 时必须原样返回，不能自行改成另一层。`plan_scope_hint` 只是高置信规则提示，你必须结合本轮语义和 `conversation_context.recent_turns` 自主判断，可以覆盖该提示。
7. 用户只询问学习状态或学情时使用 `learner_data_query`，并选择对应 `query_kind`。Diagnosis 通过系统授权的只读工具读取当前用户数据，不要求 Knowledge Agent，也不得创建学习计划或复习卡。
   - “我最近学了些什么”是 `recent_learning`，回答已经发生的学习；
   - “我最近需要学习些什么”“接下来该学什么”是 `next_learning`，综合现有计划、薄弱点、到期复习和近期完成记录给出下一步重点；
   - 后一类仍是只读建议，不发布短期计划。只有用户明确说“制定、生成、安排、调整计划/任务”时才进入 `learning_plan`。
   - 若用户不仅询问薄弱点，还明确要求“需要做哪些题、推荐哪些练习或资源”，这已经包含可交付资源：使用 `personalized_review_card`，让 Diagnosis 提供真实学情，Knowledge 检索匹配内容和题目，Expert 选择或补充资源，Audit 审核。不要只回答掌握状态，也不要创建新计划。
8. “给我讲一讲感冒”属于知识讲解，不是复习计划或复习卡。选择 Knowledge、Expert、Audit，不选择 Diagnosis、LearningPlanService、ReviewScheduler。
9. `plan_scope=daily_task` 或“我今天要学习些什么东西/今天学什么/今晚做什么”表示用户要的是当日任务，不是短期计划。路由理由必须称为“当日任务”，并说明它基于已有长短期计划和当前学情落地；不得描述为制定短期学习计划。
10. 用户要求制定或修改计划时，`plan_scope` 必须为 `long_term` / `short_term` / `daily_task` / `unspecified` 之一，不得为 `null`。“再给我今天的任务”这类承接上文的请求应判为 `daily_task`；只有无法判断用户要哪一层计划时才用 `unspecified`。用户只询问当前学情或学习状态、不要求改计划时，`plan_scope` 返回 `null`。
11. 是否需要追问由你结合本轮语义、最近对话和现有规划状态判断。明确时直接选择层级并返回 `requires_clarification=false`；确实无法判断时返回 `plan_scope=unspecified`、`requires_clarification=true`，并在 `clarification_question` 中给出一条自然、可直接回答的问题。不要机械复述固定模板，也不要询问上下文中已经明确的信息。
