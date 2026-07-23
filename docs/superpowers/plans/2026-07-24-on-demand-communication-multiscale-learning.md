# 按需通信、局部纠错与多时间尺度学习状态实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在现有六类业务智能体、LangGraph、规划与学习治理链路中加入可审计的按需通信、单轮通用局部纠错，以及带安全硬约束的多时间尺度学习状态与路径候选。

**Architecture:** 运行时新增确定性的认知差距分析、最小通信包和局部修复控制器；现有 Agent 在兼容期同时接收 `agent_handoff` 与 `dependency_outputs`。学习侧从现有持久化计划、行为、掌握度和复习数据派生 `MultiScaleLearningState`，先执行硬约束，再使用公开固定权重生成候选；状态通过桥接层注入 Planner/Diagnosis，并由版本化 API 提供给未来前端。

**Tech Stack:** Python 3.10、Pydantic v2、FastAPI、LangGraph、SQLAlchemy、MySQL/SQLite、React、Vitest、Playwright。

## Global Constraints

- 在线测试通过是最终验收标准。
- Live 验证只能在已经启动的前端运行面板中点击 Execute，不从 WSL 命令行执行 Live pytest。
- 自动化测试必须覆盖正常、边界、故障和极端案例。
- 不实现 Critic Agent、拓扑学习、权重在线学习或实验平台。
- 现有接口不删除字段；新接口和新对象使用 `schema_version="1.0"`。
- 新结构化字段用于执行、校验、持久化和渲染；面向用户的 Agent 正文继续以自然语言为主。
- 局部修复最多一轮，不能跳过 Audit，不能重跑无关节点。
- 候选路径必须先经过前置条件、时间预算、到期复习、可信来源和低数据保护硬约束。
- 缺失指标保持不可用，不伪造成 `0`、`1` 或 `100%`。
- 用户数据按登录用户隔离。
- 所有 Python 命令使用 `torch` 环境。
- 修改现有脏工作区时只暂存本任务文件，不覆盖或回滚其他改动。

---

### Task 1: 定义按需通信契约与认知差距分析器

**Files:**
- Create: `backend/competition_app/contracts/agent_communication.py`
- Create: `backend/competition_app/runtime/agent_communication.py`
- Modify: `backend/competition_app/contracts/__init__.py`
- Create: `backend/competition_app/tests/contracts/test_agent_communication.py`
- Create: `backend/competition_app/tests/runtime/test_agent_communication.py`

**Interfaces:**
- Consumes: `ExecutionStep`, `AgentEnvelope`,根上下文和直接依赖输出。
- Produces: `AgentHandoffBundle`, `CognitiveGapResult`, `CognitiveGapAnalyzer.analyze(step, root_context, dependency_outputs)`.

- [ ] **Step 1: 写通信契约失败测试**

```python
def test_handoff_contract_rejects_cross_user_fact() -> None:
    with pytest.raises(ValueError, match="same learner"):
        AgentHandoffBundle(
            handoff_id="HANDOFF_1",
            trace_id="TRACE_1",
            execution_id="EXE_1",
            learner_id="LEARNER_1",
            target_agent="diagnosis_agent",
            purpose="diagnose",
            confirmed_facts=[
                ConfirmedFact(
                    fact_id="F1",
                    category="profile",
                    content="零基础",
                    learner_id="LEARNER_2",
                    source_step_id="memory",
                )
            ],
        )
```

```python
def test_handoff_contract_keeps_structured_evidence_and_uncertainty() -> None:
    bundle = AgentHandoffBundle.model_validate(valid_bundle_payload())
    assert bundle.schema_version == "1.0"
    assert bundle.evidence[0].source_type == "textbook"
    assert bundle.uncertainties[0].blocking is True
```

- [ ] **Step 2: 运行契约测试并确认因模块不存在而失败**

Run:

```bash
conda run -n torch env PYTHONPATH="$PWD/backend" pytest -q \
  backend/competition_app/tests/contracts/test_agent_communication.py
```

Expected: collection fails with `ModuleNotFoundError: competition_app.contracts.agent_communication`.

- [ ] **Step 3: 实现通信契约**

实现以下类型并添加跨用户校验：

```python
class EvidenceReference(ContractModel):
    evidence_id: str
    source_type: str
    source_id: str
    claim: str
    quality_label: str = "unknown"
    retrieved_at: datetime | None = None


class ConfirmedFact(ContractModel):
    fact_id: str
    category: str
    content: str
    learner_id: str
    evidence_refs: list[str] = Field(default_factory=list)
    source_step_id: str
    freshness: str = "unknown"


class UncertaintyItem(ContractModel):
    uncertainty_id: str
    category: str
    description: str
    blocking: bool = False
    resolution_action: str | None = None


class DownstreamNeed(ContractModel):
    field: str
    reason: str
    required: bool = True
    accepted_source_types: list[str] = Field(default_factory=list)


class AgentHandoffBundle(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    handoff_id: str
    trace_id: str
    execution_id: str
    learner_id: str
    source_steps: list[str] = Field(default_factory=list)
    target_agent: str
    purpose: str
    confirmed_facts: list[ConfirmedFact] = Field(default_factory=list)
    evidence: list[EvidenceReference] = Field(default_factory=list)
    uncertainties: list[UncertaintyItem] = Field(default_factory=list)
    task_constraints: dict[str, Any] = Field(default_factory=dict)
    downstream_needs: list[DownstreamNeed] = Field(default_factory=list)
    omitted_categories: list[str] = Field(default_factory=list)
    generated_at: datetime

    @model_validator(mode="after")
    def facts_belong_to_same_learner(self) -> "AgentHandoffBundle":
        if any(item.learner_id != self.learner_id for item in self.confirmed_facts):
            raise ValueError("handoff facts must belong to the same learner")
        return self


class CognitiveGapResult(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    target_agent: str
    satisfied_fields: list[str] = Field(default_factory=list)
    missing_fields: list[str] = Field(default_factory=list)
    blocking_fields: list[str] = Field(default_factory=list)
    omitted_categories: list[str] = Field(default_factory=list)
```

- [ ] **Step 4: 写认知差距行为失败测试**

```python
def test_analyzer_sends_diagnosis_only_learning_relevant_information() -> None:
    result = CognitiveGapAnalyzer().analyze(
        step=ExecutionStep(
            step_id="diagnosis",
            agent="diagnosis_agent",
            depends_on=["memory", "route_resolution"],
        ),
        root_context={
            "trace_id": "TRACE_1",
            "execution_id": "EXE_1",
            "learner_id": "LEARNER_1",
            "user_request": "制定短期计划",
            "available_minutes": 25,
            "multi_scale_learning_state": {"macro": {}, "meso": {}, "micro": {}},
            "dashscope_api_key": "must-not-leak",
            "unrelated_blob": "x" * 100_000,
        },
        dependency_outputs=diagnosis_dependencies(),
    )

    assert result.bundle.target_agent == "diagnosis_agent"
    assert "dashscope_api_key" not in result.bundle.model_dump_json()
    assert "unrelated_blob" in result.gap.omitted_categories
    assert result.gap.blocking_fields == []
```

```python
def test_analyzer_blocks_agent_when_required_evidence_is_missing() -> None:
    result = CognitiveGapAnalyzer().analyze(
        step=ExecutionStep(step_id="expert", agent="expert_agent"),
        root_context=base_context(task_type="knowledge_explanation"),
        dependency_outputs={},
    )
    assert "evidence" in result.gap.blocking_fields
```

- [ ] **Step 5: 运行分析器测试并确认失败**

Run:

```bash
conda run -n torch env PYTHONPATH="$PWD/backend" pytest -q \
  backend/competition_app/tests/runtime/test_agent_communication.py
```

Expected: fails because `CognitiveGapAnalyzer` is missing.

- [ ] **Step 6: 实现确定性需求目录和分析器**

实现：

```python
AGENT_NEED_CATALOG: dict[str, tuple[DownstreamNeed, ...]] = {
    "knowledge_base_agent": (
        DownstreamNeed(field="user_request", reason="解析知识对象"),
        DownstreamNeed(field="source_policy", reason="限制可信来源"),
    ),
    "diagnosis_agent": (
        DownstreamNeed(field="learning_goal", reason="确定规划目标"),
        DownstreamNeed(field="time_budget", reason="约束任务量"),
        DownstreamNeed(field="multi_scale_learning_state", reason="依据真实学情"),
    ),
    "learning_plan_service": (
        DownstreamNeed(field="diagnosis_proposal", reason="物化正式计划"),
    ),
    "review_scheduler": (
        DownstreamNeed(field="graded_knowledge_state", reason="只调度已完成练习的知识点"),
    ),
    "expert_agent": (
        DownstreamNeed(field="evidence", reason="生成有证据资源"),
        DownstreamNeed(field="formal_task", reason="对齐正式任务"),
    ),
    "audit_agent": (
        DownstreamNeed(field="artifact", reason="审核目标"),
        DownstreamNeed(field="evidence", reason="核验事实"),
    ),
}
```

`CognitiveGapAnalyzer` 必须：

- 从白名单字段和直接依赖中提取事实。
- 对 `api_key`、`token`、`password`、其他用户字段执行硬过滤。
- 不把未知数值转换为默认满分。
- 返回 `bundle` 和 `gap`。
- 对不在目录中的内部服务节点使用兼容模式：只传直接依赖，不声明阻断。

- [ ] **Step 7: 运行 Task 1 全部测试**

Run:

```bash
conda run -n torch env PYTHONPATH="$PWD/backend" pytest -q \
  backend/competition_app/tests/contracts/test_agent_communication.py \
  backend/competition_app/tests/runtime/test_agent_communication.py
```

Expected: all pass.

- [ ] **Step 8: 提交 Task 1**

```bash
git add \
  backend/competition_app/contracts/agent_communication.py \
  backend/competition_app/contracts/__init__.py \
  backend/competition_app/runtime/agent_communication.py \
  backend/competition_app/tests/contracts/test_agent_communication.py \
  backend/competition_app/tests/runtime/test_agent_communication.py
git commit -m "feat: add cognitive-gap agent handoffs"
```

---

### Task 2: 将最小通信包接入 Legacy 与 LangGraph 执行器

**Files:**
- Modify: `backend/competition_app/runtime/trace.py`
- Modify: `backend/competition_app/runtime/orchestrator.py`
- Modify: `backend/competition_app/runtime/langgraph_orchestrator.py`
- Modify: `backend/competition_app/agents/common.py`
- Modify: `backend/competition_app/application/personalized_review_card.py`
- Modify: `backend/competition_app/tests/runtime/test_orchestrator.py`
- Modify: `backend/competition_app/tests/runtime/test_langgraph_orchestrator.py`
- Create: `backend/competition_app/tests/runtime/test_handoff_trace.py`

**Interfaces:**
- Consumes: `CognitiveGapAnalyzer.analyze(...)`.
- Produces: `CommunicationTrace`, `ExecutionResult.communication_trace`, Agent context fields `agent_handoff` and `cognitive_gap`.

- [ ] **Step 1: 写执行器通信失败测试**

```python
@pytest.mark.asyncio
async def test_orchestrator_passes_handoff_and_direct_dependencies() -> None:
    agent = CapturingAgent()
    orchestrator = Orchestrator(registry_with("diagnosis_agent", agent))
    result = await orchestrator.execute(plan_for_diagnosis(), root_context())

    assert result.status == "success"
    assert agent.context["agent_handoff"]["target_agent"] == "diagnosis_agent"
    assert set(agent.context["dependency_outputs"]) == {"memory", "route_resolution"}
    assert result.communication_trace[-1].target_agent == "diagnosis_agent"
```

```python
@pytest.mark.asyncio
async def test_orchestrator_does_not_call_agent_on_blocking_gap() -> None:
    agent = CountingAgent()
    result = await Orchestrator(registry_with("expert_agent", agent)).execute(
        plan_with_expert_without_evidence(),
        root_context(task_type="knowledge_explanation"),
    )
    assert result.status == "failed"
    assert result.error_type == "AgentHandoffBlocked"
    assert agent.calls == 0
```

- [ ] **Step 2: 运行测试并确认缺少通信轨迹而失败**

Run:

```bash
conda run -n torch env PYTHONPATH="$PWD/backend" pytest -q \
  backend/competition_app/tests/runtime/test_orchestrator.py \
  backend/competition_app/tests/runtime/test_handoff_trace.py
```

Expected: new assertions fail because execution contexts and results lack handoff fields.

- [ ] **Step 3: 扩展运行轨迹**

新增：

```python
class CommunicationTrace(BaseModel):
    handoff_id: str
    step_id: str
    target_agent: str
    fact_count: int = Field(ge=0)
    evidence_count: int = Field(ge=0)
    blocking_field_count: int = Field(ge=0)
    omitted_categories: list[str] = Field(default_factory=list)
    status: Literal["prepared", "blocked", "consumed"]
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
```

`TraceRecorder` 增加 `communication_items` 和 `record_communication(...)`。  
`ExecutionResult` 增加：

```python
communication_trace: list[CommunicationTrace] = Field(default_factory=list)
```

- [ ] **Step 4: 在 `_run_step` 中构建通信包**

在取得直接依赖输出后调用分析器：

```python
analysis = self.communication_analyzer.analyze(
    step=step,
    root_context=root_context,
    dependency_outputs=dependency_outputs,
)
step_context["dependency_outputs"] = dependency_outputs
step_context["agent_handoff"] = analysis.bundle.model_dump(mode="json")
step_context["cognitive_gap"] = analysis.gap.model_dump(mode="json")
```

若存在阻断字段：

- 记录 `handoff_blocked` 事件。
- 不调用 Agent。
- 抛出 `AgentHandoffBlocked`，错误信息列出缺失字段，不输出敏感数据。

正常时记录 `handoff_prepared` 和 `handoff_consumed` 摘要事件。

- [ ] **Step 5: 对 LangGraph 执行相同测试**

新增测试：

```python
@pytest.mark.asyncio
async def test_langgraph_uses_same_handoff_contract_as_legacy() -> None:
    result = await LangGraphOrchestrator(registry).execute(plan, context)
    assert result.status == "success"
    assert result.communication_trace
    assert captured_context["agent_handoff"]["schema_version"] == "1.0"
```

Run:

```bash
conda run -n torch env PYTHONPATH="$PWD/backend" pytest -q \
  backend/competition_app/tests/runtime/test_langgraph_orchestrator.py::test_langgraph_uses_same_handoff_contract_as_legacy
```

Expected before implementation: fails because LangGraph result omits communication trace.

- [ ] **Step 6: 在 LangGraph 状态和结果中保留通信轨迹**

扩展 `LangGraphExecutionState` 的合并字段，确保：

- 并行节点通信记录不会互相覆盖。
- 中断恢复后已完成节点的通信记录不会重复。
- `_result_from_state` 在成功、失败、人工复核和中断结果中都返回通信轨迹。

- [ ] **Step 7: 将通信摘要持久化到工作流运行状态**

`ReviewCardResult` 和 `WorkflowInterruptedResult` 增加：

```python
coordination: dict[str, Any] = Field(default_factory=dict)
```

`_remember_run` 保存：

```python
"coordination": {
    "communication_trace": [item.model_dump(mode="json") for item in execution.communication_trace],
}
```

- [ ] **Step 8: 运行 Task 2 回归测试**

Run:

```bash
conda run -n torch env PYTHONPATH="$PWD/backend" pytest -q \
  backend/competition_app/tests/runtime/test_orchestrator.py \
  backend/competition_app/tests/runtime/test_langgraph_orchestrator.py \
  backend/competition_app/tests/runtime/test_handoff_trace.py \
  backend/competition_app/tests/application/test_workflow_presentation.py
```

Expected: all pass.

- [ ] **Step 9: 提交 Task 2**

```bash
git add \
  backend/competition_app/runtime/trace.py \
  backend/competition_app/runtime/orchestrator.py \
  backend/competition_app/runtime/langgraph_orchestrator.py \
  backend/competition_app/agents/common.py \
  backend/competition_app/application/personalized_review_card.py \
  backend/competition_app/tests/runtime/test_orchestrator.py \
  backend/competition_app/tests/runtime/test_langgraph_orchestrator.py \
  backend/competition_app/tests/runtime/test_handoff_trace.py \
  backend/competition_app/tests/application/test_workflow_presentation.py
git commit -m "feat: pass auditable minimal agent handoffs"
```

---

### Task 3: 实现通用局部修复控制器

**Files:**
- Create: `backend/competition_app/contracts/local_repair.py`
- Create: `backend/competition_app/runtime/local_repair.py`
- Modify: `backend/competition_app/contracts/resource.py`
- Modify: `backend/competition_app/contracts/__init__.py`
- Create: `backend/competition_app/tests/contracts/test_local_repair.py`
- Create: `backend/competition_app/tests/runtime/test_local_repair.py`

**Interfaces:**
- Consumes: `ExecutionPlan`, Audit `findings`,当前 outputs 和触发 Audit 步骤。
- Produces: `LocalRepairPlan`, `LocalRepairController.plan_repair(...)`.

- [ ] **Step 1: 写修复分类和白名单失败测试**

```python
@pytest.mark.parametrize(
    ("finding", "expected_steps"),
    [
        ("事实缺少教材证据", ["knowledge", "expert", "audit"]),
        ("资源未结合用户掌握状态", ["diagnosis", "expert", "audit"]),
        ("题目内容表达不清", ["paper_assembly", "audit"]),
        ("蓝图要求25道填空题，成卷只有10道", ["paper_blueprint", "knowledge", "paper_assembly", "audit"]),
    ],
)
def test_repair_controller_selects_smallest_whitelisted_chain(
    finding: str, expected_steps: list[str]
) -> None:
    repair = LocalRepairController().plan_repair(
        plan=paper_or_resource_plan(),
        audit_step_id="audit",
        audit_findings=[finding],
        outputs=existing_outputs(),
    )
    assert [item.step_id for item in repair.actions] == expected_steps
```

```python
def test_unresolved_finding_does_not_guess_repair_owner() -> None:
    repair = LocalRepairController().plan_repair(
        plan=resource_plan(),
        audit_step_id="audit",
        audit_findings=["无法确定来源的异常"],
        outputs=existing_outputs(),
    )
    assert repair.status == "needs_human_review"
    assert repair.actions == []
```

- [ ] **Step 2: 运行测试并确认模块缺失**

Run:

```bash
conda run -n torch env PYTHONPATH="$PWD/backend" pytest -q \
  backend/competition_app/tests/contracts/test_local_repair.py \
  backend/competition_app/tests/runtime/test_local_repair.py
```

Expected: fails with missing contract/controller.

- [ ] **Step 3: 实现修复契约**

```python
class RepairIssue(ContractModel):
    issue_id: str
    issue_type: Literal[
        "missing_evidence",
        "conflicting_evidence",
        "learner_mismatch",
        "route_or_prerequisite_error",
        "content_quality",
        "paper_blueprint_mismatch",
        "unresolved",
    ]
    message: str
    claim_ref: str | None = None
    evidence_ref: str | None = None
    owner_step_id: str | None = None
    affected_step_ids: list[str] = Field(default_factory=list)
    severity: Literal["low", "medium", "high"] = "medium"


class RepairAction(ContractModel):
    action_id: str
    action_type: Literal["rerun"]
    step_id: str
    reason: str
    depends_on: list[str] = Field(default_factory=list)
    preserve_outputs: list[str] = Field(default_factory=list)


class LocalRepairPlan(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    repair_id: str
    execution_id: str
    trigger_step_id: str
    issues: list[RepairIssue]
    actions: list[RepairAction]
    max_rounds: Literal[1] = 1
    requires_reaudit: bool = True
    status: Literal["planned", "needs_human_review"]
```

在 `AuditResult` 中增加兼容字段：

```python
structured_findings: list[RepairIssue] = Field(default_factory=list)
```

- [ ] **Step 4: 实现确定性问题分类和最小链生成**

控制器优先使用 `structured_findings`；旧字符串 findings 使用固定关键词分类。  
生成动作前必须验证：

- 步骤存在于当前执行计划。
- 最终动作包含当前 Audit。
- 动作顺序满足原 DAG 依赖或修复白名单定义。
- 不引入当前任务不存在的能力。
- 混合 findings 合并成去重后的最小传递闭包。

- [ ] **Step 5: 增加极端分类测试**

```python
def test_mixed_findings_merge_without_duplicate_reruns() -> None:
    repair = controller.plan_repair(
        plan=paper_plan(),
        audit_step_id="audit",
        audit_findings=[
            "事实缺少教材证据",
            "题目偏离蓝图",
            "事实缺少教材证据",
        ],
        outputs=existing_outputs(),
    )
    assert [a.step_id for a in repair.actions].count("knowledge") == 1
    assert [a.step_id for a in repair.actions].count("audit") == 1
```

```python
def test_controller_never_exceeds_one_round() -> None:
    assert LocalRepairPlan.model_fields["max_rounds"].default == 1
```

- [ ] **Step 6: 运行 Task 3 测试**

Run:

```bash
conda run -n torch env PYTHONPATH="$PWD/backend" pytest -q \
  backend/competition_app/tests/contracts/test_local_repair.py \
  backend/competition_app/tests/runtime/test_local_repair.py \
  backend/competition_app/tests/agents/test_audit_paper.py
```

Expected: all pass.

- [ ] **Step 7: 提交 Task 3**

```bash
git add \
  backend/competition_app/contracts/local_repair.py \
  backend/competition_app/contracts/resource.py \
  backend/competition_app/contracts/__init__.py \
  backend/competition_app/runtime/local_repair.py \
  backend/competition_app/tests/contracts/test_local_repair.py \
  backend/competition_app/tests/runtime/test_local_repair.py \
  backend/competition_app/tests/agents/test_audit_paper.py
git commit -m "feat: plan bounded local workflow repairs"
```

---

### Task 4: 在 Legacy 和 LangGraph 中执行并恢复局部修复

**Files:**
- Modify: `backend/competition_app/runtime/trace.py`
- Modify: `backend/competition_app/runtime/orchestrator.py`
- Modify: `backend/competition_app/runtime/langgraph_orchestrator.py`
- Modify: `backend/competition_app/application/personalized_review_card.py`
- Modify: `backend/competition_app/tests/runtime/test_orchestrator.py`
- Modify: `backend/competition_app/tests/runtime/test_langgraph_orchestrator.py`
- Create: `backend/competition_app/tests/integration/test_local_repair_flow.py`

**Interfaces:**
- Consumes: `LocalRepairController.plan_repair(...)`.
- Produces: `RepairTrace`, `ExecutionResult.repair_trace`, `_execute_local_repair(...)`,检查点可恢复修复子图。

- [ ] **Step 1: 写通用修复执行失败测试**

```python
@pytest.mark.asyncio
async def test_missing_evidence_reruns_only_knowledge_expert_and_audit() -> None:
    calls = []
    result = await Orchestrator(repair_registry(calls)).execute(
        resource_plan_with_audit_revision(),
        root_context(),
    )
    assert result.status == "success"
    assert calls == [
        "knowledge", "expert", "audit",
        "knowledge", "expert", "audit",
    ]
    assert result.repair_trace[0].rerun_step_ids == ["knowledge", "expert", "audit"]
```

```python
@pytest.mark.asyncio
async def test_second_failed_audit_stops_without_third_round() -> None:
    result = await Orchestrator(always_revise_registry()).execute(plan, context)
    assert result.status == "waiting_human_review"
    assert len(result.repair_trace) == 1
    assert audit_agent.calls == 2
```

- [ ] **Step 2: 运行测试并确认现有 `_revise_once` 不满足通用链**

Run:

```bash
conda run -n torch env PYTHONPATH="$PWD/backend" pytest -q \
  backend/competition_app/tests/integration/test_local_repair_flow.py
```

Expected: fails because current code only reruns Expert/Paper Assembly and Audit.

- [ ] **Step 3: 实现 Legacy `_execute_local_repair`**

替换固定 `_revise_once` 的调用路径，但保留方法作为兼容包装。新执行逻辑：

```python
repair_plan = self.repair_controller.plan_repair(
    plan=plan,
    audit_step_id=audit_step_id,
    audit_findings=findings,
    outputs=outputs,
)
if repair_plan.status == "needs_human_review":
    return RepairExecutionOutcome.waiting(repair_plan)

for action in repair_plan.actions:
    if action.step_id == audit_step_id:
        continue
    rerun_step = steps_by_id[action.step_id]
    repaired_outputs[action.step_id] = await self._run_step(
        rerun_step,
        repair_context,
        repaired_outputs,
        trace,
    )
repaired_outputs[audit_step_id] = await self._run_step(
    audit_step,
    audit_context,
    repaired_outputs,
    trace,
)
```

必须把原 Audit 结果放入 `audit_feedback`，并保留未列入 actions 的 outputs。

同时在 `runtime/trace.py` 定义：

```python
class RepairTrace(BaseModel):
    repair_id: str
    trigger_step_id: str
    issue_types: list[str] = Field(default_factory=list)
    rerun_step_ids: list[str] = Field(default_factory=list)
    preserved_step_ids: list[str] = Field(default_factory=list)
    round: Literal[1] = 1
    status: Literal["planned", "running", "completed", "stopped"]
    final_audit_decision: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
```

并给 `ExecutionResult` 增加：

```python
repair_trace: list[RepairTrace] = Field(default_factory=list)
```

- [ ] **Step 4: 写 LangGraph 修复恢复失败测试**

```python
@pytest.mark.asyncio
async def test_process_restart_during_repair_preserves_completed_repair_nodes(
    tmp_path: Path,
) -> None:
    first = durable_orchestrator(tmp_path, interrupt_in_repair=True)
    interrupted = await first.execute(plan, context, thread_id="THREAD_REPAIR_1")
    assert interrupted.status == "interrupted"
    assert "knowledge" in interrupted.outputs

    second = durable_orchestrator(tmp_path, interrupt_in_repair=False)
    resumed = await second.resume(
        "THREAD_REPAIR_1",
        {"answer": "继续"},
        plan=plan,
        context=context,
    )
    assert resumed.status == "success"
    assert knowledge_agent.calls == 1
```

- [ ] **Step 5: 运行该测试并确认失败**

Run:

```bash
conda run -n torch env PYTHONPATH="$PWD/backend" pytest -q \
  backend/competition_app/tests/runtime/test_langgraph_orchestrator.py::test_process_restart_during_repair_preserves_completed_repair_nodes
```

Expected: fails because repair progress is not part of LangGraph state.

- [ ] **Step 6: 将修复计划和进度写入 LangGraph 状态**

`LangGraphExecutionState` 增加：

```python
repair_plans: Annotated[dict[str, dict[str, Any]], _merge_mappings]
repair_progress: Annotated[dict[str, dict[str, Any]], _merge_mappings]
repair_trace: Annotated[list[dict[str, Any]], _append_unique_repair_trace]
```

Audit 节点进入 `revise` 时执行同一修复控制器。每完成一个修复动作就写入 state/checkpoint。恢复时跳过 `completed_step_ids`。

- [ ] **Step 7: 确保 UI 事件完整**

发送：

- `repair_planned`
- `repair_step_started`
- `repair_step_completed`
- `repair_reaudit_started`
- `repair_completed`
- `repair_stopped`

事件只包含步骤、分类、状态和摘要，不包含用户敏感原文。

`PersonalizedReviewCardUseCase` 将 repair trace 加入已存在的 `coordination`：

```python
"repair_trace": [
    item.model_dump(mode="json") for item in execution.repair_trace
],
```

- [ ] **Step 8: 运行 Task 4 回归**

Run:

```bash
conda run -n torch env PYTHONPATH="$PWD/backend" pytest -q \
  backend/competition_app/tests/runtime/test_orchestrator.py \
  backend/competition_app/tests/runtime/test_langgraph_orchestrator.py \
  backend/competition_app/tests/integration/test_local_repair_flow.py \
  backend/competition_app/tests/runtime/test_sqlalchemy_checkpointer.py
```

Expected: all pass.

- [ ] **Step 9: 提交 Task 4**

```bash
git add \
  backend/competition_app/runtime/trace.py \
  backend/competition_app/runtime/orchestrator.py \
  backend/competition_app/runtime/langgraph_orchestrator.py \
  backend/competition_app/application/personalized_review_card.py \
  backend/competition_app/tests/runtime/test_orchestrator.py \
  backend/competition_app/tests/runtime/test_langgraph_orchestrator.py \
  backend/competition_app/tests/integration/test_local_repair_flow.py
git commit -m "feat: execute bounded local repairs in langgraph"
```

---

### Task 5: 构建多时间尺度学习状态和安全路径候选

**Files:**
- Create: `backend/competition_app/contracts/multiscale_learning.py`
- Modify: `backend/competition_app/contracts/__init__.py`
- Create: `backend/competition/backend-handoff-20260720/APP/backend/multiscale_learning_service.py`
- Create: `backend/competition_app/tests/contracts/test_multiscale_learning.py`
- Create: `backend/competition/backend-handoff-20260720/APP/backend/tests/test_multiscale_learning_service.py`

**Interfaces:**
- Consumes: 现有用户画像、长期/短期/当日计划、行为、答题、掌握度、复习、错题和默认路线。
- Produces: `MultiScaleLearningState`, `PathCandidate`, `build_multiscale_state(db, user_id, plan_context, window_days)`, `build_path_candidates(...)`.

- [ ] **Step 1: 写契约失败测试**

```python
def test_missing_metric_requires_unavailable_reason() -> None:
    with pytest.raises(ValueError, match="unavailable metric requires reason"):
        MetricValue(available=False, value=None)
```

```python
def test_candidate_score_and_components_are_bounded() -> None:
    candidate = PathCandidate.model_validate(valid_candidate_payload())
    assert 0 <= candidate.score <= 1
    assert all(
        item.value is None or 0 <= item.value <= 1
        for item in candidate.score_components.values()
    )
```

- [ ] **Step 2: 运行契约测试并确认模块缺失**

Run:

```bash
conda run -n torch env PYTHONPATH="$PWD/backend:$PWD/backend/competition/backend-handoff-20260720" pytest -q \
  backend/competition_app/tests/contracts/test_multiscale_learning.py
```

Expected: missing module failure.

- [ ] **Step 3: 实现多尺度契约**

至少定义：

```python
class MetricValue(ContractModel):
    available: bool
    value: float | int | None = None
    unit: str | None = None
    source_refs: list[str] = Field(default_factory=list)
    unavailable_reason: str | None = None


class HardConstraintResult(ContractModel):
    key: str
    passed: bool
    reason: str
    source_refs: list[str] = Field(default_factory=list)


class PathCandidate(ContractModel):
    candidate_id: str
    scope: Literal["long_term", "short_term", "daily_task"]
    stage: dict[str, Any] = Field(default_factory=dict)
    books: list[dict[str, str]] = Field(default_factory=list)
    knowledge_points: list[dict[str, str]] = Field(default_factory=list)
    estimated_minutes: int = Field(ge=0, le=1440)
    eligible: bool
    blocked_reasons: list[str] = Field(default_factory=list)
    hard_constraint_results: list[HardConstraintResult]
    score: float = Field(ge=0, le=1)
    score_components: dict[str, MetricValue]
    evidence_refs: list[str] = Field(default_factory=list)
    source_refs: list[str] = Field(default_factory=list)
    recommended_action: str


class MultiScaleLearningState(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    state_id: str
    learner_id: str
    generated_at: datetime
    macro: dict[str, Any]
    meso: dict[str, Any]
    micro: dict[str, Any]
    data_quality: dict[str, Any]
    hard_constraints: list[HardConstraintResult]
    source_refs: list[dict[str, Any]]
    state_digest: str
```

- [ ] **Step 4: 写状态构建和极端案例失败测试**

覆盖：

```python
def test_new_user_state_does_not_invent_mastery_or_accuracy(self):
    state = build_multiscale_state(self.db, self.new_user_id, plan_context={})
    self.assertFalse(state["micro"]["question_accuracy"]["available"])
    self.assertIsNone(state["micro"]["question_accuracy"]["value"])
    self.assertIn("no_question_attempts", state["micro"]["question_accuracy"]["unavailable_reason"])
```

```python
def test_daily_candidates_are_blocked_without_short_term_plan(self):
    state = build_multiscale_state(self.db, self.user_id, plan_context=long_only())
    candidates = build_path_candidates(
        self.db, self.user_id, state=state, scope="daily_task"
    )
    self.assertTrue(all(not item["eligible"] for item in candidates["items"]))
    self.assertTrue(any("short_term_plan_required" in item["blocked_reasons"] for item in candidates["items"]))
```

```python
def test_1440_minute_budget_is_a_cap_not_a_fill_target(self):
    candidates = build_candidates_with_budget(1440)
    self.assertTrue(all(item["estimated_minutes"] < 1440 for item in candidates["items"]))
```

```python
def test_one_hundred_due_reviews_are_prioritized_but_capacity_limited(self):
    candidates = build_candidates_with_due_reviews(100, daily_capacity=12)
    due_items = [item for item in candidates["items"] if item["recommended_action"] == "review"]
    self.assertLessEqual(len(due_items), 12)
    self.assertTrue(all(item["score_components"]["retention_benefit"]["value"] > 0 for item in due_items))
```

```python
def test_missing_difficulty_is_not_scored_as_perfect(self):
    candidate = candidate_for_resource_without_difficulty()
    self.assertFalse(candidate["score_components"]["difficulty_fit"]["available"])
    self.assertIsNone(candidate["score_components"]["difficulty_fit"]["value"])
```

- [ ] **Step 5: 运行服务测试并确认缺少实现**

Run:

```bash
conda run -n torch env PYTHONPATH="$PWD/backend:$PWD/backend/competition/backend-handoff-20260720" pytest -q \
  backend/competition/backend-handoff-20260720/APP/backend/tests/test_multiscale_learning_service.py
```

Expected: module/function missing failures.

- [ ] **Step 6: 实现状态构建**

`build_multiscale_state` 必须：

- 使用 7/30/90 日窗口过滤行为数据。
- 从 `LongTermPlan`、`ShortTermPlan`、`LearningTask` 构建 macro/meso。
- 从 `LearningQuestionAttempt`、`KnowledgeMasteryState`、`LearnerKPReviewState`、`MistakeRecord`、`LearningFocusSession` 构建 micro。
- 给每个指标返回来源、单位和不可用原因。
- 使用规范化 JSON 的 SHA-256 前 24 位生成 `state_digest`。
- 不把派生状态写入第二套状态表。

- [ ] **Step 7: 实现硬约束门和透明评分**

硬约束顺序：

```python
HARD_CONSTRAINT_ORDER = (
    "goal_route_alignment",
    "parent_plan_exists",
    "prerequisite_satisfied",
    "time_budget",
    "due_review_priority",
    "trusted_source",
    "low_data_protection",
    "approved_stage_mapping",
)
```

评分：

```python
POSITIVE_WEIGHTS = {
    "learning_gain": 0.30,
    "retention_benefit": 0.20,
    "knowledge_coverage": 0.20,
    "time_fit": 0.10,
    "difficulty_fit": 0.10,
    "autonomy_support": 0.10,
}
REPETITION_WEIGHT = 0.10
UNCERTAINTY_WEIGHT = 0.15
```

对可用正向分项重新归一化；风险项独立扣减。任何硬约束失败都令 `eligible=False`，无论评分多高。

- [ ] **Step 8: 运行 Task 5 测试**

Run:

```bash
conda run -n torch env PYTHONPATH="$PWD/backend:$PWD/backend/competition/backend-handoff-20260720" pytest -q \
  backend/competition_app/tests/contracts/test_multiscale_learning.py \
  backend/competition/backend-handoff-20260720/APP/backend/tests/test_multiscale_learning_service.py \
  backend/competition/backend-handoff-20260720/APP/backend/tests/test_learning_governance_service.py
```

Expected: all pass.

- [ ] **Step 9: 提交 Task 5**

```bash
git add \
  backend/competition_app/contracts/multiscale_learning.py \
  backend/competition_app/contracts/__init__.py \
  backend/competition/backend-handoff-20260720/APP/backend/multiscale_learning_service.py \
  backend/competition_app/tests/contracts/test_multiscale_learning.py \
  backend/competition/backend-handoff-20260720/APP/backend/tests/test_multiscale_learning_service.py
git commit -m "feat: build safe multiscale learning state"
```

---

### Task 6: 接入桥接层、规划上下文和稳定 API

**Files:**
- Modify: `backend/competition_app/integrations/backend_handoff.py`
- Modify: `backend/competition_app/application/personalized_review_card.py`
- Modify: `backend/competition_app/application/container.py`
- Modify: `backend/competition_app/repositories/runtime.py`
- Modify: `backend/competition_app/api/app.py`
- Modify: `backend/competition_app/agents/planner.py`
- Modify: `backend/competition_app/agents/diagnosis.py`
- Modify: `backend/competition_app/services/planning_validator.py`
- Modify: `backend/competition_app/tests/integrations/test_backend_handoff.py`
- Modify: `backend/competition_app/tests/api/test_learning_governance_api.py`
- Create: `backend/competition_app/tests/api/test_multiscale_learning_api.py`
- Create: `backend/competition_app/tests/integration/test_multiscale_planning_flow.py`

**Interfaces:**
- Consumes: Task 5 service and Task 2/4 coordination traces.
- Produces:
  - `GET /api/v1/learning-state/multiscale`
  - `GET /api/v1/learning-state/path-candidates`
  - `GET /api/v1/executions/{execution_id}/coordination`
  - Planner/Diagnosis context field `multi_scale_learning_state`.

- [ ] **Step 1: 写 API 契约与隔离失败测试**

```python
def test_multiscale_endpoint_returns_versioned_contract(client, logged_in_user):
    response = client.get("/api/v1/learning-state/multiscale?window_days=30")
    assert response.status_code == 200
    payload = response.json()
    assert payload["schema_version"] == "1.0"
    assert set(payload) >= {
        "state_id", "learner_id", "macro", "meso", "micro",
        "data_quality", "hard_constraints", "source_refs", "state_digest",
    }
```

```python
def test_coordination_endpoint_rejects_other_users_execution(
    client, other_user_execution,
):
    response = client.get(
        f"/api/v1/executions/{other_user_execution}/coordination"
    )
    assert response.status_code == 404
```

```python
def test_path_candidates_validate_scope_and_limit(client):
    assert client.get("/api/v1/learning-state/path-candidates?scope=unknown").status_code == 422
    assert client.get("/api/v1/learning-state/path-candidates?scope=daily_task&limit=31").status_code == 422
```

- [ ] **Step 2: 运行 API 测试并确认 404/缺少方法**

Run:

```bash
conda run -n torch env PYTHONPATH="$PWD/backend:$PWD/backend/competition/backend-handoff-20260720" pytest -q \
  backend/competition_app/tests/api/test_multiscale_learning_api.py
```

Expected: endpoints return 404 before implementation.

- [ ] **Step 3: 扩展桥接运行时**

新增：

```python
def load_multiscale_learning_state(
    self,
    external_user_id: str,
    *,
    plan_context: dict[str, Any],
    window_days: int = 30,
) -> dict[str, Any]: ...


def load_path_candidates(
    self,
    external_user_id: str,
    *,
    plan_context: dict[str, Any],
    scope: str,
    limit: int = 10,
    include_blocked: bool = True,
) -> dict[str, Any]: ...
```

两者都必须通过 `_workshop_user` 将外部用户映射为交接数据库用户，并在同一事务中读取。

- [ ] **Step 4: 扩展运行状态仓库的按执行 ID 查询**

协议新增：

```python
def get_by_execution_id(
    self,
    execution_id: str,
    learner_id: str,
) -> dict[str, Any] | None: ...
```

SQL 查询必须同时包含：

```sql
WHERE execution_id=:execution_id AND learner_id=:learner_id
```

找不到或属于其他用户都返回 `None`，API 统一返回 404，避免泄露执行 ID 是否存在。

- [ ] **Step 5: 实现三个 API**

FastAPI 参数：

```python
window_days: int = Query(default=30)
include_recent_events: bool = Query(default=False)
scope: Literal["long_term", "short_term", "daily_task"]
limit: int = Query(default=10, ge=1, le=30)
include_blocked: bool = Query(default=True)
```

所有接口先执行 `current_user(request)`，再调用用户隔离的服务。

- [ ] **Step 6: 写规划注入失败测试**

```python
@pytest.mark.asyncio
async def test_diagnosis_receives_eligible_candidates_and_blocked_reasons() -> None:
    result = await use_case.execute(short_term_request())
    model_payload = diagnosis_model.last_payload
    state = model_payload["payload"]["learning_state"]
    assert state["state_digest"]
    assert model_payload["payload"]["path_candidates"]["eligible"]
    assert model_payload["payload"]["path_candidates"]["blocked"]
```

```python
@pytest.mark.asyncio
async def test_model_cannot_select_blocked_candidate() -> None:
    with pytest.raises(ValueError, match="blocked path candidate"):
        await use_case.execute(
            diagnosis_selecting_unmet_prerequisite_candidate()
        )
```

- [ ] **Step 7: 运行规划注入测试并确认失败**

Run:

```bash
conda run -n torch env PYTHONPATH="$PWD/backend:$PWD/backend/competition/backend-handoff-20260720" pytest -q \
  backend/competition_app/tests/integration/test_multiscale_planning_flow.py
```

Expected: fails because application context lacks multiscale state/candidates.

- [ ] **Step 8: 注入 Planner/Diagnosis 并二次校验**

`PersonalizedReviewCardUseCase` 新增可选 loader，构建上下文时加入：

```python
"multi_scale_learning_state": state,
"path_candidates": {
    "eligible": [item for item in candidates if item["eligible"]],
    "blocked": [item for item in candidates if not item["eligible"]],
},
```

Planner 只接收摘要：

- `has_long_term_plan`
- `has_short_term_plan`
- `due_review_count`
- `data_quality`
- `hard_constraint_summary`

Diagnosis 接收完整候选。`PlanningValidator` 根据候选 ID、阶段、教材、知识点和前置条件再次验证模型选择。

- [ ] **Step 9: 运行 Task 6 回归**

Run:

```bash
conda run -n torch env PYTHONPATH="$PWD/backend:$PWD/backend/competition/backend-handoff-20260720" pytest -q \
  backend/competition_app/tests/integrations/test_backend_handoff.py \
  backend/competition_app/tests/api/test_learning_governance_api.py \
  backend/competition_app/tests/api/test_multiscale_learning_api.py \
  backend/competition_app/tests/integration/test_multiscale_planning_flow.py \
  backend/competition_app/tests/services/test_planning_validator.py \
  backend/competition_app/tests/agents/test_planner_dynamic_routing.py \
  backend/competition_app/tests/agents/test_diagnosis_learning_plan.py
```

Expected: all pass.

- [ ] **Step 10: 提交 Task 6**

```bash
git add \
  backend/competition_app/integrations/backend_handoff.py \
  backend/competition_app/application/personalized_review_card.py \
  backend/competition_app/application/container.py \
  backend/competition_app/repositories/runtime.py \
  backend/competition_app/api/app.py \
  backend/competition_app/agents/planner.py \
  backend/competition_app/agents/diagnosis.py \
  backend/competition_app/services/planning_validator.py \
  backend/competition_app/tests/integrations/test_backend_handoff.py \
  backend/competition_app/tests/api/test_learning_governance_api.py \
  backend/competition_app/tests/api/test_multiscale_learning_api.py \
  backend/competition_app/tests/integration/test_multiscale_planning_flow.py
git commit -m "feat: expose multiscale planning APIs"
```

---

### Task 7: 为现有前端增加非绑定式验证展示

**Files:**
- Modify: `frontend/llm/src/pageDataLoaders.js`
- Modify: `frontend/llm/src/pageDataLoaders.test.js`
- Modify: `frontend/llm/src/components/ReportsPage.jsx`
- Modify: `frontend/llm/src/components/ReportsPage.test.jsx`
- Modify: `frontend/llm/src/components/PlanningPage.jsx`
- Modify: `frontend/llm/src/components/PlanningPage.test.jsx`
- Modify: `frontend/llm/src/components/CompactAssistant.jsx`
- Modify: `frontend/llm/src/components/CompactAssistant.test.jsx`

**Interfaces:**
- Consumes: Task 6 三个稳定 API 和实时事件。
- Produces: 多尺度摘要、候选解释、通信/修复执行边的验证性 UI。

- [ ] **Step 1: 写数据加载器失败测试**

```javascript
it('loads versioned multiscale state without parsing plan prose', async () => {
  const result = await loadReportsData({ fetcher });
  expect(result.report.multiscale.schema_version).toBe('1.0');
  expect(result.report.multiscale.macro.current_stage.name).toBe('中医基础与文化语言');
});
```

```javascript
it('preserves unavailable metrics instead of coercing them to zero', async () => {
  const result = await loadReportsData({ fetcher });
  expect(result.report.multiscale.micro.question_accuracy.available).toBe(false);
  expect(result.report.multiscale.micro.question_accuracy.value).toBeNull();
});
```

- [ ] **Step 2: 运行测试并确认新字段缺失**

Run:

```bash
npm run test:unit -- \
  src/pageDataLoaders.test.js \
  src/components/ReportsPage.test.jsx \
  src/components/PlanningPage.test.jsx \
  src/components/CompactAssistant.test.jsx
```

Working directory: `frontend/llm`

Expected: new assertions fail.

- [ ] **Step 3: 扩展空数据契约和加载器**

新增：

```javascript
export const emptyMultiscaleState = {
  schema_version: '1.0',
  state_id: '',
  generated_at: null,
  macro: {},
  meso: {},
  micro: {},
  data_quality: {},
  hard_constraints: [],
  source_refs: [],
  state_digest: '',
};

export const emptyPathCandidates = {
  schema_version: '1.0',
  scope: '',
  items: [],
};
```

加载器直接使用结构化字段，不解析长期或短期规划正文。

- [ ] **Step 4: 在报告和规划页增加验证性展示**

Reports：

- 宏观/中观/微观三栏摘要。
- 数据来源和不可用原因。
- 不显示裸知识点 ID。

Planning：

- 可用候选。
- 被阻断候选及硬约束原因。
- 评分分解；不可用分项显示“未纳入”，不显示 `0%`。

组件使用 API 字段名作为稳定输入，不把当前布局写进后端契约。

- [ ] **Step 5: 展示通信和修复事件**

`CompactAssistant` 的执行图事件映射新增：

```javascript
handoff_prepared: '按需通信',
handoff_blocked: '通信信息不足',
repair_planned: '已生成局部修复链',
repair_step_started: '局部修复执行中',
repair_completed: '局部修复完成',
repair_stopped: '局部修复已停止',
```

只显示摘要、步骤和状态。

- [ ] **Step 6: 运行 Task 7 测试和构建**

Run:

```bash
npm run test:unit -- \
  src/pageDataLoaders.test.js \
  src/components/ReportsPage.test.jsx \
  src/components/PlanningPage.test.jsx \
  src/components/CompactAssistant.test.jsx
npm run build
```

Working directory: `frontend/llm`

Expected: tests and build pass.

- [ ] **Step 7: 提交 Task 7**

```bash
git add \
  frontend/llm/src/pageDataLoaders.js \
  frontend/llm/src/pageDataLoaders.test.js \
  frontend/llm/src/components/ReportsPage.jsx \
  frontend/llm/src/components/ReportsPage.test.jsx \
  frontend/llm/src/components/PlanningPage.jsx \
  frontend/llm/src/components/PlanningPage.test.jsx \
  frontend/llm/src/components/CompactAssistant.jsx \
  frontend/llm/src/components/CompactAssistant.test.jsx
git commit -m "feat: show multiscale state and repair traces"
```

---

### Task 8: 同步文档、执行完整回归和在线极端验收

**Files:**
- Modify: `README.md`
- Modify: `backend/competition_app/README.md`
- Modify: `docs/frontend-api-reference.md`
- Modify: `docs/database-operations.md`
- Modify: `docs/deployment.md`
- Modify: `docs/learning-monitoring-methodology.md`
- Create: `docs/online-validation/2026-07-24-communication-multiscale-extreme-cases.md`

**Interfaces:**
- Consumes: Tasks 1–7 的最终契约、接口和行为。
- Produces: 可部署文档、接口文档、监测方法和在线验收记录。

- [ ] **Step 1: 更新接口文档**

为三个接口写明：

- 方法和路径。
- 鉴权。
- 查询参数。
- 完整响应示例。
- 字段类型、单位和空值语义。
- `schema_version`。
- 用户隔离规则。
- 前端兼容建议。

- [ ] **Step 2: 更新数据库和部署文档**

明确：

- 通信和修复轨迹复用 `workflow_run_states.payload_json`。
- LangGraph 检查点只保存中断/修复中的运行状态。
- 完成后的协调摘要保存在工作流结果。
- 不新增第二套学习状态表。
- 清理工作流状态时不能删除仍处于 interrupted/running 的线程。

- [ ] **Step 3: 更新监测方法文档**

写明：

- macro/meso/micro 数据来源。
- 7/30/90 日窗口。
- 硬约束顺序。
- 固定评分权重和缺失分项重归一化。
- 数据不足和过期数据的限制。
- 当前权重尚未通过真实学习增益校准。

- [ ] **Step 4: 运行后端非 Live 全量测试**

Run:

```bash
conda run -n torch env \
  PYTHONPATH="$PWD/backend:$PWD/backend/competition/backend-handoff-20260720" \
  pytest -q \
  backend/competition_app/tests \
  backend/competition/backend-handoff-20260720/APP/backend/tests
```

Expected: all collected non-Live tests pass.若已有与本任务无关的环境型失败，必须记录失败测试、原始错误和排除依据，不能声称全量通过。

- [ ] **Step 5: 运行前端全量单元/组件测试和构建**

Run:

```bash
npm run test:unit
npm run build
```

Working directory: `frontend/llm`

Expected: all pass.

- [ ] **Step 6: 在已启动前端运行面板执行在线常规案例**

点击 Execute，依次验证：

1. 新用户制定长期规划。
2. 已有长期规划后制定短期规划。
3. 已有短期规划后制定今日任务。
4. 到期复习影响候选顺序。
5. Audit 缺证据触发最小修复链。
6. 页面刷新后恢复中断或修复状态。

每个案例记录：

- 输入。
- 用户 ID。
- execution ID/thread ID。
- 预期节点。
- 实际节点。
- 最终结果。
- 截图或响应摘要。

- [ ] **Step 7: 在已启动前端运行面板执行在线极端案例**

点击 Execute，依次验证：

1. 只有注册调查、没有任何学习数据。
2. 只有长期计划，没有短期计划。
3. 父长期计划失效。
4. 可用时间 0 分钟。
5. 可用时间 1440 分钟但不强制排满。
6. 100 个到期复习点按容量截断。
7. 全部资源缺少难度。
8. 证据互相冲突。
9. Audit 同时报三类问题。
10. 修复后第二次 Audit 仍失败。
11. 修复过程中刷新页面。
12. 尝试读取另一个测试用户 execution ID。

在线测试未全部通过时继续修复并重复对应案例，不能以单元测试替代。

- [ ] **Step 8: 写在线验收记录**

在 `docs/online-validation/2026-07-24-communication-multiscale-extreme-cases.md` 中为每个案例填写：

- `PASS` 或 `FAIL`。
- 失败原因和修复提交。
- 最后一次执行时间。
- 仍存在的限制。

- [ ] **Step 9: 检查文档和差异**

Run:

```bash
git diff --check
rg -n "T[B]D|T[O]DO|待[补]|待[定]" \
  README.md \
  backend/competition_app/README.md \
  docs/frontend-api-reference.md \
  docs/database-operations.md \
  docs/deployment.md \
  docs/learning-monitoring-methodology.md \
  docs/online-validation/2026-07-24-communication-multiscale-extreme-cases.md
```

Expected: `git diff --check` succeeds; placeholder scan has no matches.

- [ ] **Step 10: 提交文档和最终验证记录**

```bash
git add \
  README.md \
  backend/competition_app/README.md \
  docs/frontend-api-reference.md \
  docs/database-operations.md \
  docs/deployment.md \
  docs/learning-monitoring-methodology.md \
  docs/online-validation/2026-07-24-communication-multiscale-extreme-cases.md
git commit -m "docs: document communication and multiscale learning"
```

---

## 计划自审

- 规格覆盖：按需通信、认知差距、单轮局部修复、多时间尺度状态、硬约束、透明评分、稳定 API、未来前端适配、文档和极端在线测试均有对应任务。
- 类型一致：通信、修复、多尺度、候选、运行轨迹均先定义契约，再被后续任务引用。
- 兼容性：保留 `dependency_outputs` 和现有接口；新字段均为加法修改。
- 持久化：复用 `workflow_run_states` 和现有学习源数据，不引入重复状态表。
- 安全性：修复白名单、一轮上限、Audit 强制、低数据保护和用户隔离均有测试。
- 验收：自动化测试是回归门，已启动前端运行面板的在线常规与极端案例是最终门。
