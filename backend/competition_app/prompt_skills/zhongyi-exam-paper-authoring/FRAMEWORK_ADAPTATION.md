# 试卷蓝图生成 · 当前框架映射

当前框架将原 `zhongyi-exam-paper-authoring` 中“先蓝图、按蓝图检索、后组卷、再审核”的原则实现为四阶段工作流。

| 原完整出卷流程 | 当前框架归属 |
| --- | --- |
| 收集考试约束与来源状态 | `user_request`、`exam_constraints` |
| 生成试卷蓝图 | Expert Agent 第一阶段 → `paper_blueprint` Skill |
| 按蓝图单元检索题目 | Knowledge Base Agent → 每单元调用 `get_question_with_content`，合并并全局去重 |
| 选择具体题目并组成完整试卷 | Expert Agent 第二阶段 → `paper_assembly` Skill |
| 派生答案与解析 | 系统从已选题目的题库字段确定性派生 |
| 审核覆盖、算术、唯一性、来源与安全 | Audit Agent → `paper_generation` Skill |

## 当前输入映射

| 蓝图字段 | 框架输入 |
| --- | --- |
| 范围与口径 | `user_request`、`exam_constraints.syllabus_scope` |
| 题目候选范围 | `QuestionCandidatePool` |
| 难度 | 可选偏好；当前题库没有统一难度字段，不作为检索硬过滤条件 |
| 时间预算 | `available_minutes` |
| 长短期目标与当前任务 | `short_term_plan`、`learning_task` |
| 来源、时长、总分、题型等考试约束 | 用户请求或用户数据包；缺失时标记 `待用户确认` |

## 输出边界

当前已使用独立 `PaperBlueprint`、`QuestionCandidatePool` 和 `ExamPaperDraft` 契约。公开资源只包含考生试卷视图；答案、解析、检索通道和内部候选池仅在受控快照与审核边界中使用。
