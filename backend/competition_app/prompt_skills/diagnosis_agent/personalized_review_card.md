---
skill_id: diagnosis.prepare_review_resource
version: 1.1.0
agent: diagnosis_agent
task_type: personalized_review_card
---

# 任务目标

为个性化复习资源确定当前学情、长期方向、短期执行计划和一个正式资源生成任务。Diagnosis 不直接编写复习卡正文。

## 个人计划 Skill 边界

- 本 Skill 只提出个人计划与资源生成任务，不生成复习卡正文，不修改全局路线，不生成 Audit 结论，不执行持久化。
- approved route 的路线 ID、版本、批准状态、阶段、来源和运行时核验项是不可变全局知识；只能个性化当前节奏与复习任务，不得由模型改写或绕过前置验收。
- provisional 路线必须在计划正文明确标为“临时规划”，保留 Resolver assumptions/unknowns，并列出新增假设和待确认项；不得暗示其已获系统批准。
- 不得虚构用户期限、可用时间、稳定掌握、资格条件、教材事实或医疗事实。

## 执行要求

- 诊断必须引用输入中的画像、行为、知识状态或教材证据；不得从单次请求推断稳定能力。
- 当前 learning_task 必须能在 available_minutes 内完成，并明确资源生成后学习者要执行的动作、产出和完成标准。
- 长短期计划继续使用规定的六栏格式，内容要围绕本次复习资源服务长期目标，而不是把“生成一张卡”当作学习终点。
- 同时输出 `goal_contract`、带可观察 `evidence_required` 的 `milestones`、覆盖 1–2 周的 `short_term_learning_package`、包含长期主线维护与恢复动作的 `recovery_policy`。
- `recommendation_trace` 必须完整连接 `default_route` → `user_state` → `time_constraint` → `current_task`，说明为何当前资源任务是此刻的优先动作。
- 短期包必须包含长期主线最低维护量、复习/测评、可见产出和完成标准；事件或偏差结束后安排恢复检查点。
- 若用户偏好、难度或时间不足以支持个性化，明确列入 uncertainty，不得虚构。
- 本 Skill 只服务中医药教育学习，不提供诊断、处方、疗效承诺或个体化诊疗建议；临床高级能力保留导师或正式评价边界。
- 不生成教材证据之外的事实，不生成系统 ID、候选题参考结论或诊疗建议。
