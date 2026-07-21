# Three-Layer Learning Planning Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让模型综合默认路线生成彼此独立的长期计划、1–2 周短期计划和当日任务，并以宽松但确定性的系统校验保障主流程稳定。

**Architecture:** DefaultRouteResolver 提供最小路线事实；Diagnosis 模型输出三个自然语言正文和少量执行字段；独立 PlanningValidator 检查基本格式、路线教材边界、三层一致性和时间预算，并仅修订一次；LearningPlanService 只持久化，不拼接正文。已有有效计划默认复用，模糊重规划请求进入澄清状态。

**Tech Stack:** Python 3.10、Pydantic v2、FastAPI、pytest、原生 JavaScript；所有 Python 命令使用 `/home/wangjl/miniconda3/envs/torch/bin/python`。

## Global Constraints

- 长期计划、短期计划、当日任务是三个独立自然语言产物。
- 模型综合默认路线生成正文；系统不得把路线文本拼接到正文。
- 已有有效长短期计划默认原样复用。
- 模糊重规划请求必须先澄清，澄清前不覆盖计划。
- 校验宽松，以主流程跑通为主；只阻断缺失核心区域、非法教材、严重超时和三层完全失配。
- 首次校验失败只允许模型修订一次。
- 自动化和子智能体验证统一使用 torch Python 环境。
- 最终验收必须在已启动的 Live 前端运行台点击完成。

---

### Task 1: 最小三层模型协议

**Files:**
- Modify: `competition_app/llm/schemas.py`
- Modify: `competition_app/agents/diagnosis.py`
- Modify: `competition_app/llm/stub.py`
- Test: `competition_app/tests/validation/test_diagnosis_plan_schema_boundary.py`

**Interfaces:**
- Produces: `ThreeLayerPlanningModelOutput`，包含 `long_term_plan_content`、`short_term_plan_content`、`daily_task_content`、三层 action、`priority_mode`、`estimated_minutes`、`expected_output`、`completion_criteria`。
- Consumes: `ResolvedPlanningRoute` 和精简用户学习事实。

- [ ] 写失败测试：模型边界只接受三段正文和少量执行字段，不要求嵌套路线对象。
- [ ] 使用 torch 环境运行目标测试并确认失败。
- [ ] 实现 `ThreeLayerPlanningModelOutput`，给正文设置必要标题检查，action 使用枚举。
- [ ] 修改 Diagnosis 输入，仅发送目标、时间、学习证据摘要、路线阶段和已有计划。
- [ ] 修改 Stub 返回三个独立正文。
- [ ] 运行目标测试并确认通过。

### Task 2: 复用与重规划澄清门禁

**Files:**
- Create: `competition_app/services/plan_change_gate.py`
- Modify: `competition_app/agents/diagnosis.py`
- Modify: `competition_app/contracts/learning_plan.py`
- Test: `competition_app/tests/services/test_plan_change_gate.py`
- Test: `competition_app/tests/integration/test_learning_plan_stub_flow.py`

**Interfaces:**
- Produces: `PlanChangeDecision`，字段为 `long_term_action`、`short_term_action`、`daily_task_action`、`requires_clarification`、`clarification_questions`、`reason`。
- Consumes: 用户请求、已有计划、明确目标/时间变化和持续学习证据。

- [ ] 写失败测试：已有有效计划默认复用；单次错误不更新；明确时间变化只更新短期；模糊“重新规划”要求澄清。
- [ ] 使用 torch 环境运行并确认失败。
- [ ] 实现确定性门禁，不让模型决定是否覆盖有效计划。
- [ ] 将澄清状态纳入规划结果，澄清前不调用规划模型、不产生新版本。
- [ ] 运行目标测试并确认通过。

### Task 3: 宽松三层规划校验与一次修订

**Files:**
- Create: `competition_app/services/planning_validator.py`
- Modify: `competition_app/agents/diagnosis.py`
- Test: `competition_app/tests/services/test_planning_validator.py`
- Test: `competition_app/tests/validation/test_diagnosis_plan_agent_flow.py`

**Interfaces:**
- Produces: `PlanningValidationResult(valid: bool, issues: list[str])`。
- Consumes: 三层模型输出、默认路线教材白名单、当日预算、复用动作。

- [ ] 写失败测试：缺少核心区域、出现路线外教材、严重超时、当日任务与短期计划完全无关时失败；轻微格式差异和措辞变化通过。
- [ ] 使用 torch 环境运行并确认失败。
- [ ] 实现宽松校验器，不追求逐字段或逐句一致。
- [ ] Diagnosis 首次失败时附带 issues 调模型修订一次；第二次失败抛出清晰错误。
- [ ] 运行目标测试并确认通过。

### Task 4: 三层计划持久化

**Files:**
- Modify: `competition_app/contracts/learning_plan.py`
- Modify: `competition_app/services/learning_plan.py`
- Test: `competition_app/tests/services/test_learning_plan_service.py`

**Interfaces:**
- `LongTermPlan.content` 保存长期正文。
- `ShortTermPlan.content` 保存短期正文。
- `LearningTask.task_content` 保存当日任务正文。
- Service 只创建 ID、版本、状态和时间，不修改正文。

- [ ] 写失败测试：三个正文分别原样持久化；reuse 原样保留且版本不增加；update 只更新目标层级。
- [ ] 使用 torch 环境运行并确认失败。
- [ ] 删除 Diagnosis 中 `_append_route_phases` 等正文拼接路径。
- [ ] 调整 LearningPlanService 仅持久化已校验正文。
- [ ] 运行目标测试并确认通过。

### Task 5: 页面三层展示

**Files:**
- Modify: `competition_app/static/app.js`
- Modify: `competition_app/static/index.html`
- Test: `competition_app/tests/api/test_api.py`

**Interfaces:**
- 正式结果区只展示“长期学习计划”“未来 1–2 周计划”“今日学习任务”。
- 调试详情仍可展示路线和校验信息，但不得混入正文。

- [ ] 写失败测试：页面模板包含三个独立区域，不把 route ID 当正式正文。
- [ ] 使用 torch 环境运行 API/UI 静态测试并确认失败。
- [ ] 更新渲染逻辑和标题。
- [ ] 运行目标测试并确认通过。

### Task 6: 集成回归与 Live 验收

**Files:**
- Modify: `competition_app/tests/integration/test_learning_plan_stub_flow.py`
- Modify: `competition_app/tests/validation/test_diagnosis_plan_agent_flow.py`

**Interfaces:**
- 验证完整 Planner → Route → Knowledge → Diagnosis → LearningPlanService 流程。

- [ ] 新增集成场景：新计划生成、已有计划复用、明确短期变化、模糊重规划澄清、到期复习保留主线维护。
- [ ] 运行计划相关测试：`/home/wangjl/miniconda3/envs/torch/bin/python -m pytest -q competition_app/tests/services/test_plan_change_gate.py competition_app/tests/services/test_planning_validator.py competition_app/tests/services/test_learning_plan_service.py competition_app/tests/integration/test_learning_plan_stub_flow.py competition_app/tests/validation/test_diagnosis_plan_agent_flow.py`。
- [ ] 运行核心框架回归，不追逐无关细节失败。
- [ ] 使用 torch 环境启动 Live 服务。
- [ ] 在前端运行台点击“学习计划”场景，确认真实模型完成三层规划。
- [ ] 点击已有计划复用和模糊重规划场景，确认复用与澄清门禁。
