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
4. Memory 仅在系统判定长对话需要压缩时参与。

## 边界

- 不选择 Diagnosis、LearningPlanService 或 ReviewScheduler。
- 不生成长期规划、短期规划、学习任务或复习调度。
- 用户明确要求复习卡或学习卡时才使用 `personalized_review_card`。
- 用户明确要求试卷或练习卷时使用 `paper_generation`。
