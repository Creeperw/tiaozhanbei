# 每日原子任务自动验收实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将“任务完成率”改为只衡量系统事先安排的每日原子任务，并依据绑定题集的完成情况与可验证视频观看证据自动验收任务。

**Architecture:** `competition_app` 主库继续保存计划定义，并通过事务 outbox 将每日任务版本同步到独立的 backend-handoff 业务库；handoff 冻结题目快照、接收练习审核与视频证据、派生原子项和父任务状态。完成率只聚合已发布的每日原子项，完全排除自由练习、自由试卷、案例、资源生成和旧活动投影。

**Tech Stack:** Python 3.10、FastAPI、Pydantic v2、SQLAlchemy、MySQL/SQLite、React 19、Vitest、Playwright。

## Global Constraints

- 每个知识点原子任务只统计今日任务发布时冻结的题目集合；同知识点其他题、后续新增题和自由练习均不计入。
- 冻结题集中每道题都提交且到达终态审核后，知识点原子任务才完成；答错仍算做完，但分数、掌握度、错题和补救任务照常记录。
- `needs_human_review`/`human_review` 不是终态，不能提前完成题目原子项；`pass`、`revise`、`reject` 是终态，后两者记录系统审核异常但不要求学习者重复提交。
- 只有可控 HTML5 视频可按真实播放覆盖率自动完成，阈值为去重观看区间覆盖指定片段的 90%。
- B站、YouTube 等跨域 iframe 必须同时满足有效专注秒数达到片段时长的 90% 和用户点击“确认看完”；任一条件单独满足都不能完成。
- 视频播放跳转、重复播放和多次心跳不能重复累计观看秒数；页面隐藏或空闲超过 300 秒不累计。
- 父级今日任务只由所有有效原子项的状态派生，前端不能无证据手工完成父任务。
- 每日任务完成率为窗口内已完成原子项数除以已发布且未取消原子项数；窗口内无原子项时返回不可用而不是 0%。
- `competition_app` 与 backend-handoff 使用独立数据库和独立 SQLAlchemy engine，禁止跨库直查和不可恢复的同步双写。
- 所有同步操作以 `(host_task_id, host_task_version, event_type)` 幂等；同步失败必须可重试。
- 题目标准答案、解析和 rubric 只能保存在服务端快照中，不得出现在 dashboard、任务进度或下一题接口响应中。
- 保留已经完成的“练习得分率”口径：普通练习与试卷逐题总得分/总分，排除 AI 病患案例。
- 在线测试通过是最终验收标准；Live 验证只能在已启动的前端运行面板中点击 Execute，不从 WSL 命令行执行 Live pytest。
- 修改脏工作区时只暂存本任务文件，不覆盖或回滚其他改动。

---

## File Structure

### 主应用 `backend/competition_app`

- `contracts/learning_plan.py`：每日原子项定义契约。
- `services/learning_plan.py`：将模型的自然语言任务建议确定性拆分、校验并物化原子项。
- `services/daily_task_refresh.py`：24 小时轮换时创建新的任务及原子项身份。
- `services/daily_task_execution.py`：outbox 投递、进度读取、父状态对账。
- `repositories/learning_plan.py`：计划版本与同步 outbox 同事务持久化。
- `integrations/backend_handoff.py`：跨库任务发布和进度读取边界。
- `api/app.py`：dashboard 原子项展示、绑定练习/视频接口和父状态对账。
- `migrations/010_atomic_daily_task_execution.sql`：主库同步 outbox。

### backend-handoff `backend/competition/backend-handoff-20260720/APP/backend`

- `database.py`：任务实例、原子项、题目快照、视频证据及绑定字段。
- `daily_task_progress_service.py`：冻结题集、记录审核、合并观看区间、派生完成状态。
- `routers/daily_task_routes.py`：任务绑定下一题、进度、视频证据和 iframe 确认接口。
- `grading_application_service.py`：普通练习评分写入原子项归属。
- `paper_submission_service.py`：任务绑定试卷逐题进度投递。
- `system_data_service.py`：只按每日原子项计算任务完成率和趋势。
- `learning_governance_service.py`：能力维度的数据源与公式切换到每日原子项。

### 前端 `frontend/llm/src`

- `components/DashboardPage.jsx`：原子项列表、进度与任务动作。
- `components/PracticePage.jsx`、`components/QuestionTrainingPanel.jsx`、`components/exam-atlas/AtlasPracticePanel.jsx`：透传 `taskItemId` 并只消费冻结题集。
- `components/PaperGenerationPanel.jsx`：打开预绑定试卷，禁止任务上下文重新组卷。
- `components/KnowledgeCardLibrary.jsx`、`components/knowledge-atlas/KnowledgeAtlasDetail.jsx`：HTML5 播放证据与 iframe 混合确认。
- `pageDataLoaders.js`：任务进度、绑定练习和视频证据请求。

---

### Task 1: 定义每日原子项契约并写入主库 outbox

**Files:**

- Modify: `backend/competition_app/contracts/learning_plan.py`
- Modify: `backend/competition_app/services/learning_plan.py`
- Modify: `backend/competition_app/services/daily_task_refresh.py`
- Modify: `backend/competition_app/repositories/learning_plan.py`
- Create: `backend/competition_app/migrations/010_atomic_daily_task_execution.sql`
- Modify: `backend/competition_app/tests/contracts/test_learning_plan.py`
- Modify: `backend/competition_app/tests/services/test_learning_plan_service.py`
- Modify: `backend/competition_app/tests/services/test_daily_task_refresh_service.py`
- Modify: `backend/competition_app/tests/repositories/test_persistence.py`
- Modify: `backend/competition_app/tests/db/test_migrations.py`

**Interfaces:**

- Produces: `DailyTaskItemSpec`, `LearningTask.items`, `learning_task_sync_outbox`。
- Consumes: 现有 `LearningTaskProposal`、`ShortTermLearningPackage.task_blocks`、`SqlLearningPlanRepository.save_current()`。

- [ ] **Step 1: 写契约失败测试**

```python
def test_daily_task_rejects_duplicate_item_ids_and_missing_practice_count() -> None:
    with pytest.raises(ValueError):
        LearningTask.model_validate({
            **valid_learning_task_payload(),
            "items": [
                {
                    "task_item_id": "ITEM_1",
                    "ordinal": 1,
                    "item_type": "knowledge_practice",
                    "title": "完成四君子汤组成练习",
                    "estimated_minutes": 10,
                    "kp_id": "020490",
                    "required_question_count": 0,
                    "completion_policy": {"policy": "frozen_question_set"},
                }
            ],
        })
```

- [ ] **Step 2: 实现最小契约**

```python
class DailyTaskItemSpec(ContractModel):
    task_item_id: str = Field(min_length=1)
    ordinal: int = Field(ge=1)
    item_type: Literal["video_section", "knowledge_practice", "reading", "recall"]
    title: str = Field(min_length=1)
    estimated_minutes: int = Field(gt=0)
    kp_id: str | None = None
    required_question_count: int | None = Field(default=None, ge=1)
    resource_ref: dict[str, Any] = Field(default_factory=dict)
    completion_policy: dict[str, Any] = Field(default_factory=dict)
```

`LearningTask` 增加 `items: list[DailyTaskItemSpec]`，模型校验必须保证：

- `task_item_id` 唯一；
- `ordinal` 从 1 连续；
- `knowledge_practice` 必须有 `kp_id`、正整数 `required_question_count` 和 `policy=frozen_question_set`；
- `video_section` 必须有 `start_seconds < end_seconds`、视频来源和 A 方案策略；
- 原子项预计分钟数之和不超过父任务预算。

- [ ] **Step 3: 写物化失败测试并实现确定性拆分**

测试输入包含一个视频片段和三个知识点，断言输出 4 个稳定原子项；同一任务重用时 ID 不变，新任务版本或24小时轮换时生成新 ID。

确定性 ID 使用：

```python
task_item_id = f"DTI_{uuid4().hex}"
```

不得让模型直接提供内部 ID。模型只提供自然语言任务和知识点/资源语义，服务层负责校验正式知识点和视频引用。

- [ ] **Step 4: 写 outbox 迁移**

`010_atomic_daily_task_execution.sql` 创建：

```sql
CREATE TABLE IF NOT EXISTS learning_task_sync_outbox (
    event_id VARCHAR(128) PRIMARY KEY,
    learner_id VARCHAR(128) NOT NULL,
    task_id VARCHAR(128) NOT NULL,
    task_version INT NOT NULL,
    event_type VARCHAR(32) NOT NULL,
    payload_json JSON NOT NULL,
    status VARCHAR(32) NOT NULL DEFAULT 'pending',
    attempt_count INT NOT NULL DEFAULT 0,
    last_error TEXT NULL,
    created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    delivered_at TIMESTAMP(6) NULL,
    UNIQUE KEY uq_task_sync_event (task_id, task_version, event_type),
    INDEX idx_task_sync_pending (learner_id, status, created_at)
);
```

- [ ] **Step 5: 让计划版本与 outbox 同事务写入**

`SqlLearningPlanRepository.save_current()` 在保存包含 `learning_task` 的新版本时，同一 `engine.begin()` 中插入 `publish` 事件；24小时轮换产生新任务时写 `replace`，旧任务不删除。

- [ ] **Step 6: 运行 Task 1 测试**

```bash
cd /mnt/d/code/AI/deeplearning/tiaozhanbei/backend
/home/wangjl/miniconda3/envs/torch/bin/python -m pytest -q \
  competition_app/tests/contracts/test_learning_plan.py \
  competition_app/tests/services/test_learning_plan_service.py \
  competition_app/tests/services/test_daily_task_refresh_service.py \
  competition_app/tests/repositories/test_persistence.py \
  competition_app/tests/db/test_migrations.py
```

Expected: all pass；迁移重复执行不新增重复 outbox 表或事件。

---

### Task 2: 建立 handoff 原子任务执行状态机

**Files:**

- Modify: `backend/competition/backend-handoff-20260720/APP/backend/database.py`
- Create: `backend/competition/backend-handoff-20260720/APP/backend/daily_task_progress_service.py`
- Create: `backend/competition/backend-handoff-20260720/APP/backend/tests/test_daily_task_progress_service.py`
- Modify: `backend/competition/backend-handoff-20260720/APP/backend/tests/test_core_learning_schema.py`

**Interfaces:**

- Produces: `upsert_daily_task_snapshot()`, `record_reviewed_question()`, `record_video_evidence()`, `confirm_iframe_video()`, `daily_task_progress()`。
- Consumes: Task 1 的任务同步 payload。

- [ ] **Step 1: 写 schema 与状态机失败测试**

必须覆盖：

```python
def test_same_kp_unbound_question_does_not_complete_item(): ...
def test_all_frozen_questions_terminally_reviewed_complete_item(): ...
def test_needs_human_review_is_not_terminal(): ...
def test_wrong_answer_still_completes_frozen_question(): ...
def test_sync_replay_does_not_reselect_questions(): ...
```

- [ ] **Step 2: 新增四张 ORM 表**

- `DailyTaskInstanceRecord`：`host_task_id/version/user_id/status/refresh_started_at/refresh_due_at`。
- `DailyTaskItemRecord`：原子项定义、状态、完成时间。
- `DailyTaskQuestionSnapshotRecord`：冻结题目版本与服务端答案快照、对应 attempt/audit 状态。
- `DailyTaskVideoEvidenceRecord`：观看区间、专注秒数、确认状态。

约束必须包括：

```text
UNIQUE(host_task_id, host_task_version, user_id)
UNIQUE(task_item_id, user_id)
UNIQUE(task_item_id, ordinal)
UNIQUE(task_item_id, question_version_id)
```

`ensure_runtime_schema_for()` 必须同时支持新库和既有 SQLite/MySQL 库，不依赖 `create_all()` 给旧表补列。

- [ ] **Step 3: 实现冻结题集**

`upsert_daily_task_snapshot(db, user_id, payload)` 对每个 `knowledge_practice` item 只在首次发布时按正式 `kp_id` 选择 `required_question_count` 道题，并复制：

- `question_id/question_version_id`；
- 题型、题干、选项；
- 标准答案、rubric、知识点快照；
- 来源与快照哈希。

重放相同 `(task_id, version)` 必须复用原快照。候选不足时同步返回阻断错误，不能悄悄降低题量。

- [ ] **Step 4: 实现题目完成状态机**

```python
TERMINAL_AUDIT_DECISIONS = {"pass", "revise", "reject"}
NON_TERMINAL_AUDIT_DECISIONS = {"pending", "needs_human_review", "human_review"}
```

知识点 item 完成条件：冻结题数大于0，并且每条快照都有当前用户提交和终态审核。父任务完成条件：所有非取消 item 完成。

- [ ] **Step 5: 实现视频 A 方案状态机**

HTML5：服务端合并 `[start, end]` 观看区间并计算并集；达到片段90%自动完成。

iframe：仅记录有效专注秒数；`confirm_iframe_video()` 只有在：

```text
active_seconds >= 0.9 * (segment_end_seconds - segment_start_seconds)
```

时接受确认并完成，否则返回 409。单纯确认或单纯计时都不能完成。

- [ ] **Step 6: 运行 Task 2 测试**

```bash
cd /mnt/d/code/AI/deeplearning/tiaozhanbei/backend/competition/backend-handoff-20260720
/home/wangjl/miniconda3/envs/torch/bin/python -m pytest -q \
  APP/backend/tests/test_core_learning_schema.py \
  APP/backend/tests/test_daily_task_progress_service.py
```

Expected: all pass，且快照响应不含标准答案。

---

### Task 3: 建立可靠跨库同步与进度 API

**Files:**

- Create: `backend/competition_app/services/daily_task_execution.py`
- Modify: `backend/competition_app/integrations/backend_handoff.py`
- Modify: `backend/competition_app/application/container.py`
- Modify: `backend/competition_app/api/app.py`
- Create: `backend/competition_app/tests/services/test_daily_task_execution.py`
- Modify: `backend/competition_app/tests/integrations/test_backend_handoff.py`
- Modify: `backend/competition_app/tests/api/test_dashboard.py`

**Interfaces:**

- Produces: `DailyTaskExecutionCoordinator.dispatch_pending()`, `reconcile_parent_status()`；`BackendHandoffRuntime.upsert_daily_task_execution()`、`load_daily_task_progress()`。
- Consumes: 主库 outbox 与 Task 2 状态机。

- [ ] **Step 1: 写 outbox 故障恢复失败测试**

测试顺序：主计划和 outbox 已提交 → handoff 第一次不可用 → outbox 保持 `pending` 且 `attempt_count=1` → dashboard 第二次读取触发重试 → handoff 幂等落库 → outbox `delivered`。

- [ ] **Step 2: 实现协调器**

`dispatch_pending(learner_id, limit=20)`：

- 按创建时间投递当前用户 pending/failed 事件；
- 错误信息脱敏后写 `last_error`；
- 成功写 `delivered_at`；
- 不因 handoff 暂时故障回滚已发布计划。

`reconcile_parent_status()` 读取 handoff 父状态；只有所有 item 完成时才生成主库 `LearningTask` 的新 `completed` 版本。

- [ ] **Step 3: 扩展 dashboard**

`GET /api/v1/dashboard/home` 在读取当前任务前执行 outbox 重试和状态对账，并返回：

```json
{
  "current_learning_task": {
    "task_id": "TASK_1",
    "version": 2,
    "progress": {"completed": 2, "total": 4, "rate": 0.5},
    "items": [
      {
        "task_item_id": "DTI_1",
        "item_type": "knowledge_practice",
        "status": "in_progress",
        "progress": {"reviewed_questions": 2, "required_questions": 3},
        "action": {"destination": "workshop.practice", "params": {"taskItemId": "DTI_1"}}
      }
    ]
  }
}
```

不得返回答案、rubric、内部用户 ID。

- [ ] **Step 4: 封堵人工父任务完成**

`POST /api/v1/learning-tasks/current/complete` 改为只触发 `reconcile_parent_status()`；存在未完成 item 时返回 409 和当前进度，不得直接写 `completed`。

- [ ] **Step 5: 运行 Task 3 测试**

```bash
cd /mnt/d/code/AI/deeplearning/tiaozhanbei/backend
/home/wangjl/miniconda3/envs/torch/bin/python -m pytest -q \
  competition_app/tests/services/test_daily_task_execution.py \
  competition_app/tests/integrations/test_backend_handoff.py \
  competition_app/tests/api/test_dashboard.py
```

Expected: all pass。

---

### Task 4: 将普通练习与任务冻结题集绑定

**Files:**

- Create: `backend/competition/backend-handoff-20260720/APP/backend/routers/daily_task_routes.py`
- Modify: `backend/competition/backend-handoff-20260720/APP/backend/main.py`
- Modify: `backend/competition/backend-handoff-20260720/APP/backend/database.py`
- Modify: `backend/competition/backend-handoff-20260720/APP/backend/grading_application_service.py`
- Modify: `backend/competition/backend-handoff-20260720/APP/backend/routers/training_routes.py`
- Create: `backend/competition/backend-handoff-20260720/APP/backend/tests/test_daily_task_routes.py`
- Modify: `backend/competition/backend-handoff-20260720/APP/backend/tests/test_training_routes.py`

**Interfaces:**

- Produces: `GET /daily-task-items/{task_item_id}/practice/next` 和服务端 claim 绑定。
- Consumes: Task 2 快照与 `apply_practice_grading()`。

- [ ] **Step 1: 写安全绑定失败测试**

覆盖：他人 item 返回404；自由 `question_id` 伪装成绑定题返回422；任务下一题只来自未完成快照；响应不含答案；同一 claim 重放不重复完成。

- [ ] **Step 2: 扩展 claim 与评分命令**

给 `CorePracticeSubmissionClaim`、`LearningAttemptRecord` 增加可空 `daily_task_item_id`；给 `GradePracticeCommand` 增加：

```python
daily_task_item_id: str | None = None
```

绑定关系必须来自服务端签发 claim，不信任请求 body。

- [ ] **Step 3: 实现绑定下一题**

接口按 `ordinal` 返回首个没有终态审核的快照；全部完成时返回：

```json
{"available": false, "reason": "daily_task_item_completed", "progress": {"reviewed": 3, "required": 3}}
```

- [ ] **Step 4: 在审核落库后投递进度**

`apply_practice_grading()` 在 Audit 持久化且事务提交前调用 `record_reviewed_question()`。私有题绕过统一服务的分支不能用于每日冻结题集；每日任务发布只能选择正式题库版本。

- [ ] **Step 5: 运行 Task 4 测试**

```bash
cd /mnt/d/code/AI/deeplearning/tiaozhanbei/backend/competition/backend-handoff-20260720
/home/wangjl/miniconda3/envs/torch/bin/python -m pytest -q \
  APP/backend/tests/test_daily_task_routes.py \
  APP/backend/tests/test_training_routes.py
```

Expected: all pass。

---

### Task 5: 支持任务绑定试卷逐题完成

**Files:**

- Modify: `backend/competition/backend-handoff-20260720/APP/backend/database.py`
- Modify: `backend/competition/backend-handoff-20260720/APP/backend/paper_generation_service.py`
- Modify: `backend/competition/backend-handoff-20260720/APP/backend/paper_submission_service.py`
- Modify: `backend/competition/backend-handoff-20260720/APP/backend/tests/test_paper_submission_service.py`

**Interfaces:**

- Produces: `PaperInstanceRecord.daily_task_item_id`，逐题审核对原子项的幂等投递。
- Consumes: Task 2 冻结题集和现有试卷评分服务。

- [ ] **Step 1: 写试卷集合一致性失败测试**

断言绑定试卷的 `paper_items.question_version_id` 集合必须与原子项快照集合完全一致；缺题、多题或替换题都拒绝发布/提交。

- [ ] **Step 2: 持久化试卷绑定**

给 `PaperInstanceRecord` 增加可空 `daily_task_item_id`。从每日任务进入时只能创建/打开预绑定试卷，不允许重新生成另一套题。

- [ ] **Step 3: 逐题投递审核状态**

`submit_paper()` 每题 `apply_practice_grading()` 返回后，同事务更新对应快照；交卷活动日志继续保留，但不再直接影响任务完成率。

- [ ] **Step 4: 运行 Task 5 测试**

```bash
cd /mnt/d/code/AI/deeplearning/tiaozhanbei/backend/competition/backend-handoff-20260720
/home/wangjl/miniconda3/envs/torch/bin/python -m pytest -q \
  APP/backend/tests/test_paper_submission_service.py
```

Expected: all pass。

---

### Task 6: 将完成率切换为每日原子项

**Files:**

- Modify: `backend/competition/backend-handoff-20260720/APP/backend/system_data_service.py`
- Modify: `backend/competition/backend-handoff-20260720/APP/backend/learning_governance_service.py`
- Modify: `backend/competition/backend-handoff-20260720/APP/backend/tests/test_system_data_service.py`
- Modify: `backend/competition/backend-handoff-20260720/APP/backend/tests/test_learning_governance_service.py`
- Modify: `docs/learning-monitoring-methodology.md`

**Interfaces:**

- Produces: `daily_atomic_task_completion_rate`。
- Consumes: `daily_task_instances` 与 `daily_task_items`。

- [ ] **Step 1: 写污染隔离失败测试**

测试同一窗口内存在：2个每日原子项（1完成）、20个已完成自由练习活动、1个已完成自由试卷和1个案例。预期完成率仍为：

```text
1 / 2 = 0.5
```

- [ ] **Step 2: 改造窗口聚合和趋势**

`rebuild_system_data()`、`build_learning_window_metrics()`、`build_learning_trends()` 只查询已发布每日原子项；取消项排除，过期未完成项保留在分母。

无原子项返回：

```json
{"available": false, "value": null, "unit": "ratio", "unavailable_reason": "no_planned_daily_task_items"}
```

计算版本升级为 `system-data-v3-daily-atomic`。

- [ ] **Step 3: 更新学情能力维度**

“任务执行”数据源改为：

```text
daily_task_instances,daily_task_items
```

公式改为：

```text
completed_non_cancelled_daily_items/non_cancelled_published_daily_items
```

不得再声明 `learning_task` 泛任务是该维度来源。

- [ ] **Step 4: 更新方法文档并运行测试**

```bash
cd /mnt/d/code/AI/deeplearning/tiaozhanbei/backend/competition/backend-handoff-20260720
/home/wangjl/miniconda3/envs/torch/bin/python -m pytest -q \
  APP/backend/tests/test_system_data_service.py \
  APP/backend/tests/test_learning_governance_service.py
```

Expected: all pass。

---

### Task 7: 前端展示原子项并完成绑定练习导航

**Files:**

- Modify: `frontend/llm/src/components/DashboardPage.jsx`
- Modify: `frontend/llm/src/components/dashboard/dashboardDailyModel.js`
- Modify: `frontend/llm/src/components/PracticePage.jsx`
- Modify: `frontend/llm/src/components/QuestionTrainingPanel.jsx`
- Modify: `frontend/llm/src/components/exam-atlas/AtlasPracticePanel.jsx`
- Modify: `frontend/llm/src/components/PaperGenerationPanel.jsx`
- Modify: `frontend/llm/src/pageDataLoaders.js`
- Modify: corresponding `*.test.jsx` and `pageDataLoaders.test.js`

**Interfaces:**

- Produces: 带 `taskItemId` 的任务动作和冻结题集练习体验。
- Consumes: Task 3 dashboard、Task 4 next-practice、Task 5 bound paper。

- [ ] **Step 1: 写 Dashboard 原子项失败测试**

断言4个 item 独立显示状态，父进度为 `2/4`；操作按钮保留 `taskItemId`；不存在“直接完成整个任务”按钮。

- [ ] **Step 2: 改造 TodayTaskRail**

每项显示：标题、类型、预计时长、`reviewed/required` 或视频覆盖率、状态和动作。父任务只展示派生进度。

- [ ] **Step 3: 透传 taskItemId**

```text
navigationContext.taskItemId
→ QuestionTrainingPanel
→ AtlasPracticePanel
→ loadDailyTaskPracticeQuestion
→ submitPracticeAnswer
```

绑定任务上下文禁用“正式题库/我的题目/全部题目”自由切换；完成全部快照后显示“今日知识点练习已完成”。

- [ ] **Step 4: 改造试卷入口**

有 `taskItemId` 时直接打开预绑定试卷，并隐藏组卷约束编辑入口；服务端仍校验绑定，不信任前端参数。

- [ ] **Step 5: 运行 Task 7 测试和构建**

```bash
cd /mnt/d/code/AI/deeplearning/tiaozhanbei/frontend/llm
npm run test:unit -- \
  src/components/DashboardPage.test.jsx \
  src/components/PracticePage.test.jsx \
  src/components/exam-atlas/AtlasPracticePanel.test.jsx \
  src/components/PaperGenerationPanel.test.jsx \
  src/pageDataLoaders.test.js
npm run build
```

Expected: unit tests and build pass。

---

### Task 8: 实现视频 A 方案前端证据

**Files:**

- Modify: `frontend/llm/src/components/KnowledgeCardLibrary.jsx`
- Modify: `frontend/llm/src/components/knowledge-atlas/KnowledgeAtlasDetail.jsx`
- Create: `frontend/llm/src/videoTaskEvidence.js`
- Create: `frontend/llm/src/videoTaskEvidence.test.js`
- Modify: `frontend/llm/src/components/KnowledgeCardLibrary.test.jsx`
- Create: `frontend/llm/src/components/knowledge-atlas/KnowledgeAtlasDetail.test.jsx`
- Modify: `frontend/llm/src/pageDataLoaders.js`
- Create: `backend/competition/backend-handoff-20260720/APP/backend/tests/test_daily_task_video_evidence.py`

**Interfaces:**

- Produces: HTML5 去重观看区间上报；iframe 专注证据加用户确认。
- Consumes: Task 2 视频状态机和 Task 3 item ID。

- [ ] **Step 1: 写区间合并和伪进度失败测试**

覆盖：重复播放不重复累计、seek 跳到结尾不补齐中间区间、隐藏页不发送观看证据、89%不完成、90%完成。

- [ ] **Step 2: 实现 HTML5 证据收集器**

监听 `play/pause/timeupdate/seeking/ratechange/ended/visibilitychange`；每次只上报连续真实播放区间。服务端负责最终合并和阈值判断，前端百分比只用于展示。

- [ ] **Step 3: 实现 iframe 混合确认**

B站/YouTube iframe 显示：

- “第三方播放器无法读取真实进度”的透明说明；
- 有效专注时长进度；
- 未达90%时禁用“确认看完”；
- 达到90%后允许确认；
- 服务端再次校验阈值。

- [ ] **Step 4: 运行 Task 8 测试**

```bash
cd /mnt/d/code/AI/deeplearning/tiaozhanbei/backend/competition/backend-handoff-20260720
/home/wangjl/miniconda3/envs/torch/bin/python -m pytest -q \
  APP/backend/tests/test_daily_task_video_evidence.py

cd /mnt/d/code/AI/deeplearning/tiaozhanbei/frontend/llm
npm run test:unit -- \
  src/videoTaskEvidence.test.js \
  src/components/KnowledgeCardLibrary.test.jsx \
  src/components/knowledge-atlas/KnowledgeAtlasDetail.test.jsx
npm run build
```

Expected: all pass。

---

### Task 9: 完整回归与在线验收

**Files:**

- Modify: `docs/online-validation/2026-07-24-atomic-daily-task-extreme-cases.md`
- Modify only if failures prove a defect: files already named in Tasks 1–8。

**Interfaces:**

- Produces: 可复核的正常、边界、故障和极端场景证据。

- [ ] **Step 1: 运行主应用回归**

```bash
cd /mnt/d/code/AI/deeplearning/tiaozhanbei/backend
/home/wangjl/miniconda3/envs/torch/bin/python -m pytest -q competition_app/tests
```

Expected: all pass。

- [ ] **Step 2: 运行 handoff 相关回归**

```bash
cd /mnt/d/code/AI/deeplearning/tiaozhanbei/backend/competition/backend-handoff-20260720
/home/wangjl/miniconda3/envs/torch/bin/python -m pytest -q \
  APP/backend/tests/test_daily_task_progress_service.py \
  APP/backend/tests/test_daily_task_routes.py \
  APP/backend/tests/test_training_routes.py \
  APP/backend/tests/test_paper_submission_service.py \
  APP/backend/tests/test_system_data_service.py \
  APP/backend/tests/test_learning_governance_service.py
```

Expected: all pass。

- [ ] **Step 3: 运行前端单测与构建**

```bash
cd /mnt/d/code/AI/deeplearning/tiaozhanbei/frontend/llm
npm run test:unit
npm run build
```

Expected: all pass。如果 Vitest worker 在 WSL 启动超时，记录环境故障，并在现有前端运行面板中执行同一测试，不得把未执行写成通过。

- [ ] **Step 4: 在前端运行台执行在线场景**

必须覆盖：

1. 今日任务含1个HTML5视频与3个知识点题集；逐项完成后父进度从0/4到4/4。
2. 每个知识点冻结3题；完成2题仍未完成；做同知识点自由第4题仍未完成；完成冻结第3题后完成。
3. 冻结题答错但审核终态，任务完成、得分率下降并产生错题/补救记录。
4. 一题 `needs_human_review` 时任务保持进行中；转终态后完成。
5. 自由完成20道练习和1份试卷不能改变每日任务完成率分母或分子。
6. HTML5视频重复播放和跳播不能伪造90%；真实覆盖90%后自动完成。
7. B站 iframe 达到专注阈值但未确认不完成；确认但阈值不足被服务端拒绝；二者满足后完成。
8. handoff 临时不可用后恢复，outbox 重试不重抽题、不重复原子项。
9. 24小时轮换后旧任务保留历史，新任务拥有新 item ID 与新冻结题集。
10. 其他用户不能读取、提交或确认当前用户的 item。

- [ ] **Step 5: 写在线验证报告**

报告逐项记录：输入、任务/原子项 ID、冻结题数、操作、API状态、最终父进度、完成率、练习得分率、证据截图或响应摘要。任何未出现最终状态或服务端证据的场景不得标记通过。
