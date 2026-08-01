---
skill_id: planner.learner_data_query
version: 1.1.0
agent: planner_agent
task_type: learner_data_query
---

# 学习者数据查询路由

## 适用范围

用户在询问自己的近期学习、下一步学习重点、统计进度、掌握情况、复习状态或既有计划进展，
并没有要求生成或修改学习内容。

## 路由要求

1. 使用 `learner_data_query`，并选择一个 `query_kind`：
   - `recent_learning`：最近学了什么、这周学习内容；
   - `next_learning`：最近需要学什么、接下来或下一步应该重点学什么；
   - `progress_summary`：题量、正确情况、专注时长、任务完成情况；
   - `mastery_status`：掌握度、薄弱点、待验证知识点；
   - `review_status`：复习队列、到期复习、复习完成情况；
   - `plan_progress`：长期规划、短期计划和当日任务的实际进展。
2. 只选择 Diagnosis；Memory 是业务流程常规上下文节点，非闲聊任务由系统自动前置注入，不需要 Planner 自行选择或移除。
3. 不选择 Knowledge、Expert、ReviewScheduler、LearningPlanService 或 Audit。
4. 本任务只读，不创建复习卡、不生成题目、不改写计划。
5. “最近学了什么”查询已完成内容；“最近需要学什么”查询下一步重点，两者不要混淆。
6. “给我生成复习卡”“制定/生成/安排/调整学习计划”“讲解某知识点”不是本任务。
