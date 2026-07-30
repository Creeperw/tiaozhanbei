---
skill_id: planner.general_learning_support
version: 1.0.0
agent: planner_agent
task_type: general_learning_support
---

# 综合学习支持路由

## 适用范围

用户希望围绕教材、章节或一组内容获得开放式学习支持，例如学习要点、阅读重点、
知识梳理、学习方法、复习思路或比较总结，但没有要求创建正式计划，也不是只问
某个概念的定义、原理或事实。

## 路由要求

1. 使用 `general_learning_support`，选择 Knowledge、Expert 和 Audit。
2. Expert 可以根据用户原话和表达偏好自然组织正文，不要求固定栏目或固定段数。
3. Knowledge 仍负责教材与必要补充证据；Audit 只检查事实、相关性和教学安全。
4. 不选择 Diagnosis、LearningPlanService 或 ReviewScheduler，不创建或修改计划。
5. 用户明确要求制定计划、推荐具体题目、生成卡片或组卷时，使用对应专门任务。

## 边界示例

- “讲讲阴阳的含义和相互关系”属于 `knowledge_explanation`。
- “梳理《中医学基础》阴阳学说章节的学习要点”属于 `general_learning_support`。
- “给阴阳学说章节安排本周计划”属于 `learning_plan`。
