---
skill_id: planner.route_personalized_review_card
version: 1.0.0
agent: planner_agent
task_type: personalized_review_card
---

# 任务目标

识别用户是否要求生成可直接学习的复习卡或教学资源，并形成从证据、学情、正式任务到资源审核的动态执行链。

## 推荐编排骨架

该骨架用于说明能力依赖，不是让 Planner 输出固定模板名称：

1. Knowledge 解析原始请求、调用向量检索工具并整理教材证据。
2. Diagnosis 依据学习数据和证据提出计划与当前学习任务。
3. LearningPlanService 将建议转成正式计划和任务。
4. ReviewScheduler 为立即生成的复习资源建立调度壳。
5. Expert 依据正式任务和证据生成资源。
6. Audit 审核资源事实、适配性和教学安全。
7. Memory 只在系统明确标记长对话需要压缩时加入。

## 选择规则

- 生成可发布教学资源时必须选择 `expert_agent` 和 `audit_agent`。
- 必须满足 Agent 能力目录中的上游依赖，不能让 Expert 绕过证据、诊断或正式任务。
- `requires_audit` 必须为 true。
- 不输出检索词、题目、计划正文、工具名、工具参数或系统 ID。

## 示例

输入：“请生成一张可以直接学习的四君子汤复习卡。”
输出要点：`task_type=personalized_review_card`；选择 Knowledge、Diagnosis、LearningPlanService、ReviewScheduler、Expert、Audit；短对话无需 Memory。

输入：“结合前面对话给我做一张理中丸错题复习卡。”且 `conversation_context.requires_compression=true`
输出要点：在完整资源链基础上选择 Memory；理由中说明长对话摘要将为后续学情和资源适配提供约束。
