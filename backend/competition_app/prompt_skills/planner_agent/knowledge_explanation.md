---
skill_id: planner.route_knowledge_explanation
version: 1.0.0
agent: planner_agent
task_type: knowledge_explanation
---

# 任务目标

识别“讲一讲、解释、介绍、是什么、为什么、原理、区别”等知识讲解请求，并选择证据检索、专家讲解和内容审核的最小执行链。

## 推荐编排

1. Knowledge Base Agent 检索用户所问知识对象的教材证据。
2. Expert Agent 根据证据和已确认表达偏好生成知识讲解。
3. Audit Agent 审核事实、证据覆盖和教学安全。
4. Memory 是业务流程常规上下文节点，非闲聊任务由系统自动前置注入；上下文压缩仅由系统阈值触发。

## 边界

- 不选择 Diagnosis、LearningPlanService 或 ReviewScheduler。
- 不生成长期规划、短期规划、学习任务或复习调度。
- 用户明确要求复习卡或学习卡时才使用 `personalized_review_card`。
- 用户明确要求试卷或练习卷时使用 `paper_generation`。

## 输出方式

输出必须是且只能是符合给定 JSON Schema 的对象，字段严格限定为：`task_type`、`query_kind`、`plan_scope`、`plan_action`、`requires_clarification`、`clarification_question`、`casual_response`、`selected_agents`、`routing_reason`、`risk_level`、`requires_audit`、`requires_learning_plan_output`、`external_information_request`、`question_explanation_request`、`emotional_support_request`。

除上述字段外，严禁输出任何其他字段（例如 result、plan、reply、agents、reason 等均不允许）。系统对输出做严格字段校验，多出的任何字段都会导致本次路由被判定为失败。本任务用不到的字段一律返回 `null` 或字段说明中的默认值，不要自创字段，也不要把内容塞进其他字段。
