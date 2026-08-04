---
skill_id: planner.route_personalized_review_card
version: 1.0.0
agent: planner_agent
task_type: personalized_review_card
---

# 任务目标

识别用户是否要求生成可直接学习的复习卡、个性化练习或教学资源，并形成从证据、学情到资源审核的动态执行链。

## 推荐编排骨架

该骨架用于说明能力依赖，不是让 Planner 输出固定模板名称：

1. Knowledge 解析原始请求、调用向量检索工具并整理教材证据。
2. Diagnosis 依据已有学习数据和证据判断当前资源适配重点；资源请求本身不创建或修改计划。
3. ReviewScheduler 在已有正式任务可用时读取调度信息；没有正式任务时只形成资源选择所需的只读调度壳。
4. Expert 依据用户画像、当前学情、已有任务（如有）和证据选择并生成资源。
5. Audit 审核资源事实、适配性和教学安全。
6. 只有用户本轮明确同时要求创建/调整计划和生成资源时，才额外进入计划编译、计划审核与 LearningPlanService 发布链路。
7. Memory 是业务流程常规上下文节点，非闲聊任务由系统自动前置注入；上下文压缩仅由系统阈值触发，Planner 无需自行选择。

## 选择规则

- 生成可发布教学资源时必须选择 `expert_agent` 和 `audit_agent`。
- “我有哪些核心薄弱点，需要做哪些题目”属于个性化练习资源：必须读取真实掌握证据并检索匹配题目，不能退化成只读学情回答，也不能因此重建学习计划。
- 必须满足 Agent 能力目录中的上游依赖，不能让 Expert 绕过证据、诊断或正式任务。
- `requires_audit` 必须为 true。
- 若完整语义同时要求创建/调整计划并生成学习卡、复习卡或学习资源，返回 `requires_learning_plan_output=true`；只生成资源时为 false。必须依据用户整句、近期对话和已确认补充语义判断，禁止使用固定关键词清单。
- `requires_learning_plan_output` 以**当前用户消息的交付物**为准。历史对话里曾讨论、制定或追问过计划，不能把当前单纯的资源推荐请求升级为“计划 + 资源”；只有当前消息明确承接并要求继续完成两种交付物时才可为 true。
- `external_information_request`、`question_explanation_request`、`emotional_support_request` 也必须由完整语义判断；本资源任务通常均为 false，不得由后端关键词补判。
- 不输出检索词、题目、计划正文、工具名、工具参数或系统 ID。

## 示例

输入：“请生成一张可以直接学习的四君子汤复习卡。”
输出要点：`task_type=personalized_review_card`；`requires_learning_plan_output=false`；选择 Knowledge、Diagnosis、ReviewScheduler、Expert、Audit，不选择 LearningPlanService，不创建或修改计划；Memory 仍按系统上下文要求参与。

输入：“结合前面对话给我做一张理中丸错题复习卡。”且 `conversation_context.requires_compression=true`
输出要点：在完整资源链基础上选择 Memory；理由中说明长对话摘要将为后续学情和资源适配提供约束。
