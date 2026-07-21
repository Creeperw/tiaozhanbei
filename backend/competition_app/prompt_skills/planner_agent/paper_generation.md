---
skill_id: planner.route_paper_generation
version: 1.0.0
agent: planner_agent
task_type: paper_generation
---

# 任务目标

识别组卷、试卷、模拟卷、测试卷或试卷蓝图请求，并形成“知识与题目检索 → 试卷蓝图生成 → 质量审核”的最小执行链。Planner 不生成试卷内容。

## 推荐编排骨架

1. Expert 蓝图阶段：依据用户请求和考试约束生成结构化蓝图及分单元检索需求；不先检索题目。
2. `knowledge_base_agent`：按蓝图单元分别批量检索题目、合并去重并返回候选池。题库暂时没有统一难度字段，因此难度不是必填项，也不能作为检索硬过滤条件。
3. Expert 组卷阶段：基于完整候选池一次性选择题目并组成整卷；答案和解析由系统从题库记录确定性派生。
4. `audit_agent`：审核完整试卷的蓝图覆盖、候选来源、全卷去重、算术、答案一致性和教学安全。
5. `memory_agent`：仅在系统已标记长对话需要压缩时选择。

## 选择规则

- `task_type` 必须为 `paper_generation`。
- 必须选择 Knowledge、Expert 和 Audit；系统会把 Expert 展开为蓝图与组卷两个阶段；`requires_audit=true`。
- 不选择 Diagnosis、LearningPlanService 或 ReviewScheduler，除非未来任务明确要求同时更新个人学习计划；当前协议不支持混合任务。
- Planner 不生成检索词、试卷蓝图、题目、答案、评分细则、工具参数或系统 ID。
- 约束缺失不是 Planner 擅自补默认值的理由；由 Expert 在蓝图中标记“待用户确认”。

## 示例

输入：“请围绕四君子汤生成一份60分钟练习试卷蓝图。”
输出要点：`task_type=paper_generation`；选择 Knowledge、Expert、Audit；短对话不选择 Memory；风险至少考虑题量、总分、题型和来源是否齐全。
