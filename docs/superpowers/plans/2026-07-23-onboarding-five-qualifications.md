# 注册学情群体与五类资格考试 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 新注册用户只可选择跨专业进阶或学历教育群体，并从系统批准的五类教材型资格考试中选择学习方向。

**Architecture:** 后端 `GROUP_TEMPLATES` 只暴露两个可选群体，同时保留对历史 `public_interest` 数据的读取兼容。主后端提供 `/api/v1/qualification-targets` 作为唯一注册资格目录，将官方考试身份和可复用教材路线一起写入学情调查及活动学习目标，供规划链路直接复用。

**Tech Stack:** FastAPI、Pydantic、React、Vitest、pytest、Playwright。

## Global Constraints

- 五类资格目标的唯一权威来源为 `backend/competition_app/data/qualification_targets/tcm_qualification_targets.v1.json`。
- 新增调查必须原样提交目录中的 `exam_track_id` 与 `textbook_route_id`。
- 历史 `public_interest` 记录可读取，但接口和前端不得再把它作为可选项返回。
- Live 验收只通过已启动页面执行，不从 WSL 运行 Live pytest。

---

### Task 1: 收敛学情群体

**Files:**
- Modify: `backend/competition/backend-handoff-20260720/APP/backend/onboarding_template_service.py`
- Test: `backend/competition/backend-handoff-20260720/APP/backend/tests/test_training_routes.py`

**Interfaces:**
- Consumes: `GET /training/onboarding/group-templates`
- Produces: `groups=[cross_professional, academic]`；历史 `public_interest` 可继续规范化。

- [ ] 写失败测试，断言模板接口只返回两个群体，并验证历史值仍能读取。
- [ ] 在 `torch` 环境运行指定测试，确认因仍返回 `public_interest` 而失败。
- [ ] 将大众模板移到仅供历史解析的兼容表，公开模板和默认问题不再包含大众项。
- [ ] 重跑测试并确认通过。

### Task 2: 注册调查改用五类资格考试

**Files:**
- Modify: `frontend/llm/src/components/OnboardingSurveyPanel.jsx`
- Test: `frontend/llm/src/components/OnboardingSurveyPanel.test.jsx`

**Interfaces:**
- Consumes: `GET /api/v1/qualification-targets`
- Produces: `goals.target_type`、`goals.exam_track_id`、`goals.textbook_route_id`、`goals.textbook_route_version`、`goals.target_exam_or_course`。

- [x] 写失败测试，模拟五类资格考试并断言所有官方名称出现。
- [x] 把选项请求切换到 `/api/v1/qualification-targets`。
- [x] 移除长期目标、短期目标和自由“规划输入”字段。
- [x] 保存时把资格身份、教材路线与官方名称合并进 `goals`。
- [ ] 重跑组件测试并确认通过。

### Task 3: 后端保存稳定路线信息

**Files:**
- Modify: `backend/competition/backend-handoff-20260720/APP/backend/diagnosis_agent_service.py`
- Modify: `backend/competition/backend-handoff-20260720/APP/backend/learning_plan_service.py`
- Test: `backend/competition/backend-handoff-20260720/APP/backend/tests/test_training_routes.py`

**Interfaces:**
- Consumes: 调查中的资格身份与教材路线字段
- Produces: 活动学习目标、学情状态和规划上下文中的同名稳定字段。

- [x] 写失败测试，提交资格目标后检查学习目标与调查数据。
- [x] 在规范化逻辑中保留考试轨道、路线 ID 与版本。
- [x] 使规划解析优先采用已保存活动目标并消费中断恢复答案。
- [x] 重跑定向测试并确认通过。

### Task 4: 文档和完整验证

**Files:**
- Modify: `docs/frontend-api-reference.md`
- Modify: `docs/deployment.md`

**Interfaces:**
- Produces: 注册调查前端与后端字段契约说明。

- [x] 记录两类群体、五类资格来源和调查请求示例。
- [ ] 运行相关 pytest、前端 Vitest、lint、build 和 `git diff --check`。
- [ ] 在已启动页面注册新用户，确认页面仅显示两个群体和五类资格考试。
- [ ] 保存后读取学情状态，确认路线 ID、版本和名称均存在，且浏览器控制台无错误。
