import pytest

from competition_app.contracts.base import AgentEnvelope
from competition_app.contracts.execution import ExecutionPlan, ExecutionStep
from competition_app.contracts.local_repair import RepairIssue
from competition_app.contracts.audit_compilation import AuditLocation
from competition_app.runtime.local_repair import LocalRepairController


def _plan(*steps: ExecutionStep) -> ExecutionPlan:
    return ExecutionPlan(plan_id="PLAN_1", task_type="paper_generation", steps=list(steps))


def paper_or_resource_plan() -> ExecutionPlan:
    return _plan(
        ExecutionStep(step_id="paper_blueprint", agent="paper_blueprint_agent"),
        ExecutionStep(
            step_id="question_pool",
            agent="knowledge_base_agent",
            depends_on=["paper_blueprint"],
        ),
        ExecutionStep(
            step_id="paper_assembly",
            agent="paper_assembly_agent",
            depends_on=["paper_blueprint", "question_pool"],
        ),
        ExecutionStep(
            step_id="audit",
            agent="audit_agent",
            depends_on=["paper_blueprint", "question_pool", "paper_assembly"],
        ),
    )


def resource_plan() -> ExecutionPlan:
    return _plan(
        ExecutionStep(step_id="knowledge", agent="knowledge_base_agent"),
        ExecutionStep(step_id="expert", agent="expert_agent", depends_on=["knowledge"]),
        ExecutionStep(step_id="audit", agent="audit_agent", depends_on=["expert"]),
    )


def existing_outputs() -> dict[str, AgentEnvelope[dict[str, str]]]:
    return {
        "knowledge": AgentEnvelope(
            artifact_id="ART_1", artifact_type="test", case_id="CASE_1", trace_id="TRACE_1",
            request_id="REQ_1", execution_id="EXE_1", step_id="knowledge", producer="test",
            task_type="paper_generation", learner_id="LEARNER_1", payload={},
        )
    }


def paper_outputs() -> dict[str, AgentEnvelope[dict[str, str]]]:
    return {
        step_id: AgentEnvelope(
            artifact_id=f"ART_{step_id}", artifact_type="test", case_id="CASE_1",
            trace_id="TRACE_1", request_id="REQ_1", execution_id="EXE_1",
            step_id=step_id, producer="test", task_type="paper_generation",
            learner_id="LEARNER_1", payload={},
        )
        for step_id in ("paper_blueprint", "question_pool", "paper_assembly")
    }


@pytest.mark.parametrize(
    ("finding", "issue_type", "expected_steps"),
    [
        ("题目内容表达不清", "content_quality", ["paper_assembly", "audit"]),
        (
            "蓝图要求25道填空题，成卷只有10道",
            "paper_blueprint_mismatch",
            ["paper_blueprint", "question_pool", "paper_assembly", "audit"],
        ),
    ],
)
def test_repair_controller_selects_smallest_whitelisted_chain(
    finding: str, issue_type: str, expected_steps: list[str]
) -> None:
    repair = LocalRepairController().plan_repair(
        plan=paper_or_resource_plan(),
        audit_step_id="audit",
        audit_findings=[finding],
        structured_findings=[RepairIssue(issue_id="I1", issue_type=issue_type,
                         message=finding, owner_step_id="paper_assembly")],
        outputs=paper_outputs(),
    )

    assert [item.step_id for item in repair.actions] == expected_steps


@pytest.mark.parametrize("finding", ["无法确定来源的异常", "不是证据缺失，不要重查", "没有蓝图冲突", "题干不存在表达不清"])
def test_unresolved_finding_does_not_guess_repair_owner(finding) -> None:
    repair = LocalRepairController().plan_repair(
        plan=resource_plan(),
        audit_step_id="audit",
        audit_findings=[finding],
        outputs=existing_outputs(),
    )

    assert repair.status == "needs_human_review"
    assert repair.actions == []


def test_mixed_findings_merge_without_duplicate_reruns() -> None:
    controller = LocalRepairController()
    repair = controller.plan_repair(
        plan=paper_or_resource_plan(),
        audit_step_id="audit",
        audit_findings=["题目内容表达不清", "题目偏离蓝图", "题目内容表达不清"],
        structured_findings=[
            RepairIssue(issue_id="I1", issue_type="content_quality", message="题目内容表达不清", owner_step_id="paper_assembly"),
            RepairIssue(issue_id="I2", issue_type="paper_blueprint_mismatch", message="题目偏离蓝图", owner_step_id="paper_assembly"),
        ],
        outputs=paper_outputs(),
    )

    assert [action.step_id for action in repair.actions].count("question_pool") == 1
    assert [action.step_id for action in repair.actions].count("audit") == 1


def test_paper_missing_evidence_uses_real_question_pool_producer() -> None:
    repair = LocalRepairController().plan_repair(
        plan=paper_or_resource_plan(),
        audit_step_id="audit",
        audit_findings=[],
        structured_findings=[
            RepairIssue(
                issue_id="ISSUE_EVIDENCE",
                issue_type="missing_evidence",
                message="入卷题目缺少候选池依据",
                owner_step_id="paper_assembly",
                affected_step_ids=["paper_assembly"],
            )
        ],
        outputs=paper_outputs(),
    )

    assert repair.status == "planned"
    assert [action.step_id for action in repair.actions] == [
        "question_pool", "paper_assembly", "audit"
    ]


def test_located_paper_item_issue_reruns_only_assembly_and_audit() -> None:
    repair = LocalRepairController().plan_repair(
        plan=paper_or_resource_plan(),
        audit_step_id="audit",
        audit_findings=[],
        structured_findings=[
            RepairIssue(
                issue_id="ISSUE_Q1_EXPLANATION",
                issue_type="answer_or_explanation_invalid",
                message="题目Q1缺少解析",
                owner_step_id="paper_assembly",
                affected_step_ids=["paper_assembly"],
                origin="deterministic",
                locations=[
                    AuditLocation(
                        location_key="paper:explanation:Q1",
                        subject_type="exam_paper",
                        location_type="explanation",
                        display_label="题目Q1的解析",
                    )
                ],
            )
        ],
        outputs=paper_outputs(),
    )

    assert [action.step_id for action in repair.actions] == [
        "paper_assembly", "audit"
    ]
    assert repair.actions[0].issue_ids == ["ISSUE_Q1_EXPLANATION"]
    assert repair.actions[0].locations[0].display_label == "题目Q1的解析"
    assert repair.actions[0].operation == "repair_explanation"
    assert repair.actions[0].scope_question_ids == ["Q1"]
    assert repair.actions[0].scope_field_paths == ["explanations.Q1"]
    assert "只修正以下已定位问题" in repair.actions[0].repair_instruction
    assert repair.actions[0].previous_output_digest is not None


def test_located_blueprint_mismatch_replaces_only_the_question_then_reaudits() -> None:
    repair = LocalRepairController().plan_repair(
        plan=paper_or_resource_plan(),
        audit_step_id="audit",
        audit_findings=[],
        structured_findings=[
            RepairIssue(
                issue_id="ISSUE_OFF_TOPIC_Q1",
                issue_type="paper_blueprint_mismatch",
                message="题目Q1明显偏离当前蓝图单元。",
                owner_step_id="paper_assembly",
                affected_step_ids=["paper_assembly"],
                origin="audit_model",
                locations=[
                    AuditLocation(
                        location_key="paper:question:Q1",
                        subject_type="exam_paper",
                        location_type="question",
                        display_label="试卷题目Q1",
                    )
                ],
            )
        ],
        outputs=paper_outputs(),
    )

    assert [action.step_id for action in repair.actions] == [
        "paper_assembly",
        "audit",
    ]
    assert repair.actions[0].operation == "replace_question"
    assert repair.actions[0].scope_question_ids == ["Q1"]


def test_structured_findings_take_priority_over_legacy_strings() -> None:
    repair = LocalRepairController().plan_repair(
        plan=paper_or_resource_plan(),
        audit_step_id="audit",
        audit_findings=["无法确定来源的异常"],
        structured_findings=[
            RepairIssue(
                issue_id="ISSUE_1",
                issue_type="content_quality",
                message="题目内容表达不清",
                owner_step_id="paper_assembly",
            )
        ],
        outputs=paper_outputs(),
    )

    assert repair.status == "planned"
    assert [action.step_id for action in repair.actions] == ["paper_assembly", "audit"]


def test_controller_fails_closed_when_whitelist_step_is_missing_from_plan() -> None:
    repair = LocalRepairController().plan_repair(
        plan=resource_plan(),
        audit_step_id="audit",
        audit_findings=["题目内容表达不清"],
        outputs=existing_outputs(),
    )

    assert repair.status == "needs_human_review"
    assert repair.actions == []


def test_controller_fails_closed_when_execution_plan_has_invalid_dag() -> None:
    repair = LocalRepairController().plan_repair(
        plan=_plan(
            ExecutionStep(step_id="paper_blueprint", agent="paper_blueprint_agent"),
            ExecutionStep(step_id="knowledge", agent="knowledge_base_agent"),
            ExecutionStep(step_id="paper_assembly", agent="paper_assembly_agent"),
            ExecutionStep(
                step_id="audit", agent="audit_agent", depends_on=["not_in_this_plan"]
            ),
        ),
        audit_step_id="audit",
        audit_findings=["蓝图要求25道填空题，成卷只有10道"],
        outputs=existing_outputs(),
    )

    assert repair.status == "needs_human_review"
    assert repair.actions == []


def test_repair_actions_reuse_completed_upstream_dependency() -> None:
    plan = _plan(
        ExecutionStep(
            step_id="diagnosis", agent="diagnosis_agent", depends_on=["knowledge"]
        ),
        ExecutionStep(step_id="knowledge", agent="knowledge_base_agent"),
        ExecutionStep(step_id="expert", agent="expert_agent", depends_on=["diagnosis"]),
        ExecutionStep(step_id="audit", agent="audit_agent", depends_on=["expert"]),
    )

    repair = LocalRepairController().plan_repair(
        plan=plan,
        audit_step_id="audit",
        audit_findings=["资源未结合用户掌握状态"],
        structured_findings=[RepairIssue(issue_id="I1", issue_type="learner_mismatch", message="资源未结合用户掌握状态", owner_step_id="expert")],
        outputs=existing_outputs(),
    )

    assert [action.step_id for action in repair.actions] == [
        "diagnosis", "expert", "audit"
    ]
    assert repair.actions[0].depends_on == []


def test_mixed_repair_closes_over_source_dag_without_duplicate_actions() -> None:
    plan = _plan(
        ExecutionStep(
            step_id="paper_assembly", agent="paper_assembly_agent", depends_on=["expert"]
        ),
        ExecutionStep(step_id="expert", agent="expert_agent", depends_on=["diagnosis"]),
        ExecutionStep(
            step_id="diagnosis", agent="diagnosis_agent", depends_on=["knowledge"]
        ),
        ExecutionStep(step_id="knowledge", agent="knowledge_base_agent"),
        ExecutionStep(step_id="audit", agent="audit_agent", depends_on=["paper_assembly"]),
    )

    repair = LocalRepairController().plan_repair(
        plan=plan,
        audit_step_id="audit",
        audit_findings=["事实缺少教材证据", "题目内容表达不清"],
        structured_findings=[
            RepairIssue(issue_id="I1", issue_type="missing_evidence", message="事实缺少教材证据", owner_step_id="expert"),
            RepairIssue(issue_id="I2", issue_type="content_quality", message="题目内容表达不清", owner_step_id="paper_assembly"),
        ],
        outputs=existing_outputs(),
    )

    assert [action.step_id for action in repair.actions] == [
        "knowledge", "diagnosis", "expert", "paper_assembly", "audit"
    ]
    assert len({action.step_id for action in repair.actions}) == len(repair.actions)


def test_non_audit_trigger_fails_closed() -> None:
    plan = _plan(
        ExecutionStep(step_id="paper_assembly", agent="paper_assembly_agent"),
        ExecutionStep(step_id="review", agent="review_agent", action="manual_review"),
    )

    repair = LocalRepairController().plan_repair(
        plan=plan,
        audit_step_id="review",
        audit_findings=["题目内容表达不清"],
        outputs=existing_outputs(),
    )

    assert repair.status == "needs_human_review"
    assert repair.actions == []


@pytest.mark.parametrize("ancestor_step_id", ["critic", "topology", "unrelated"])
def test_repair_rejects_non_whitelisted_source_ancestor(ancestor_step_id: str) -> None:
    plan = _plan(
        ExecutionStep(step_id="knowledge", agent="knowledge_base_agent"),
        ExecutionStep(step_id=ancestor_step_id, agent=f"{ancestor_step_id}_agent"),
        ExecutionStep(
            step_id="expert",
            agent="expert_agent",
            depends_on=["knowledge", ancestor_step_id],
        ),
        ExecutionStep(step_id="audit", agent="audit_agent", depends_on=["expert"]),
    )

    repair = LocalRepairController().plan_repair(
        plan=plan,
        audit_step_id="audit",
        audit_findings=["事实缺少教材证据"],
        outputs=existing_outputs(),
    )

    assert repair.status == "needs_human_review"
    assert repair.actions == []


@pytest.mark.parametrize("action", ["audit_log", "preaudit_cleanup"])
def test_audit_action_substrings_are_not_valid_triggers(action: str) -> None:
    plan = _plan(
        ExecutionStep(step_id="paper_assembly", agent="paper_assembly_agent"),
        ExecutionStep(step_id="review", agent="review_agent", action=action),
    )

    repair = LocalRepairController().plan_repair(
        plan=plan,
        audit_step_id="review",
        audit_findings=["题目内容表达不清"],
        outputs=existing_outputs(),
    )

    assert repair.status == "needs_human_review"
    assert repair.actions == []


def test_factual_error_issue_is_registered_and_repairable() -> None:
    """factual_error 红线必须能走返修规划，而不是触发 KeyError 崩溃。

    回归：cmb_0006 因 _DEFAULT_TARGETS/_ALLOWED_TARGETS 未注册
    factual_error，_affected_target() 字典索引抛 KeyError: 'factual_error'。
    """
    plan = resource_plan()
    issue = RepairIssue(
        issue_id="FACT-1",
        issue_type="factual_error",
        message="专家判定存在知识性错误，必须返修",
        owner_step_id="expert",
        affected_step_ids=["expert"],
        severity="high",
        blocking=True,
        origin="audit_model",
        locations=[],
        source_anchors=[],
    )

    repair = LocalRepairController().plan_repair(
        plan=plan,
        audit_step_id="audit",
        audit_findings=[],
        outputs=existing_outputs(),
        structured_findings=[issue],
    )

    assert repair.status == "planned"
    assert repair.actions, "factual_error 必须产出可执行的返修动作"
    assert repair.actions[-1].step_id == "audit"
    assert any(action.step_id == "expert" for action in repair.actions)
    assert any(action.step_id == "knowledge" for action in repair.actions)


def test_located_expert_origin_uses_the_minimal_expert_repair_chain() -> None:
    issue = RepairIssue(
        issue_id="EXPERT-ORIGIN-1",
        issue_type="missing_evidence",
        message="专家引用了本次证据包中不存在的证据编号",
        origin_step_id="expert",
        owner_step_id="expert",
        affected_step_ids=["expert"],
        severity="high",
        blocking=True,
        origin="deterministic",
    )

    repair = LocalRepairController().plan_repair(
        plan=resource_plan(),
        audit_step_id="audit",
        audit_findings=[],
        outputs=existing_outputs(),
        structured_findings=[issue],
    )

    assert repair.status == "planned"
    assert [action.step_id for action in repair.actions] == ["expert", "audit"]


def test_located_knowledge_origin_reruns_knowledge_then_downstream_expert() -> None:
    issue = RepairIssue(
        issue_id="KNOWLEDGE-ORIGIN-1",
        issue_type="missing_evidence",
        message="知识检索未返回任务所需的有效证据",
        origin_step_id="knowledge",
        owner_step_id="knowledge",
        affected_step_ids=["knowledge", "expert"],
        severity="high",
        blocking=True,
        origin="deterministic",
    )

    repair = LocalRepairController().plan_repair(
        plan=resource_plan(),
        audit_step_id="audit",
        audit_findings=[],
        outputs=existing_outputs(),
        structured_findings=[issue],
    )

    assert repair.status == "planned"
    assert [action.step_id for action in repair.actions] == [
        "knowledge", "expert", "audit"
    ]


def planning_plan() -> ExecutionPlan:
    """Mirror the live short-term replanning DAG: memory → route → diagnosis → audit."""

    return _plan(
        ExecutionStep(step_id="memory", agent="memory_agent"),
        ExecutionStep(
            step_id="route_resolution",
            agent="route_resolution_agent",
            depends_on=["memory"],
        ),
        ExecutionStep(
            step_id="diagnosis",
            agent="diagnosis_agent",
            depends_on=["route_resolution"],
        ),
        ExecutionStep(
            step_id="audit",
            agent="audit_agent",
            depends_on=["diagnosis"],
        ),
    )


def planning_outputs() -> dict[str, AgentEnvelope[dict[str, str]]]:
    return {
        step_id: AgentEnvelope(
            artifact_id=f"ART_{step_id}", artifact_type="test", case_id="CASE_1",
            trace_id="TRACE_1", request_id="REQ_1", execution_id="EXE_1",
            step_id=step_id, producer="test", task_type="short_term_planning",
            learner_id="LEARNER_1", payload={},
        )
        for step_id in ("memory", "route_resolution", "diagnosis")
    }


def _plan_owned_issue(issue_type: str) -> RepairIssue:
    """Build the shape AuditIssueResolver emits for a plan-local finding."""

    return RepairIssue(
        issue_id=f"PLAN-{issue_type}",
        issue_type=issue_type,
        message="短期计划正文与考纲路线不一致",
        origin_step_id="diagnosis",
        owner_step_id="diagnosis",
        affected_step_ids=["diagnosis"],
        severity="medium",
        blocking=True,
        origin="audit_model",
        locations=[
            AuditLocation(
                location_key="plan:natural_language",
                display_label="自然语言规划正文",
                subject_type="short_term_plan",
                location_type="section",
            )
        ],
    )


@pytest.mark.parametrize(
    "issue_type",
    [
        "factual_error",
        "missing_evidence",
        "conflicting_evidence",
        "learner_mismatch",
        "content_quality",
    ],
)
def test_plan_owned_issue_is_repaired_by_diagnosis(issue_type: str) -> None:
    """规划类问题必须回到 Diagnosis 返修。

    回归护栏：这些标签不在 _ALLOWED_TARGETS 的 Diagnosis 里时，归属会被
    静默改判到 Expert/PaperAssembly，而规划 DAG 没有这些步骤，于是可返修的
    revise 被升级成人工复核，且没有任何返修步骤真正执行。
    """

    repair = LocalRepairController().plan_repair(
        plan=planning_plan(),
        audit_step_id="audit",
        audit_findings=[],
        outputs=planning_outputs(),
        structured_findings=[_plan_owned_issue(issue_type)],
    )

    assert repair.status == "planned", repair.issues
    assert [action.step_id for action in repair.actions] == ["diagnosis", "audit"]
    assert repair.actions[0].operation == "rerun_step"
    assert repair.actions[-1].operation == "reaudit"


def test_plan_route_mismatch_reruns_route_resolution_before_diagnosis() -> None:
    """路线类问题先重读已冻结的路线判定，再重写计划正文。"""

    repair = LocalRepairController().plan_repair(
        plan=planning_plan(),
        audit_step_id="audit",
        audit_findings=[],
        outputs=planning_outputs(),
        structured_findings=[_plan_owned_issue("route_or_prerequisite_error")],
    )

    assert repair.status == "planned", repair.issues
    assert [action.step_id for action in repair.actions] == [
        "route_resolution",
        "diagnosis",
        "audit",
    ]


def test_plan_owned_issue_keeps_repair_chain_inside_planning_steps() -> None:
    """返修链不得引入规划 DAG 之外的知识库/专家/组卷步骤。"""

    repair = LocalRepairController().plan_repair(
        plan=planning_plan(),
        audit_step_id="audit",
        audit_findings=[],
        outputs=planning_outputs(),
        structured_findings=[_plan_owned_issue("factual_error")],
    )

    assert repair.status == "planned", repair.issues
    assert {
        action.step_id for action in repair.actions
    } <= {"memory", "route_resolution", "diagnosis", "audit"}


def test_resource_findings_keep_their_default_targets() -> None:
    """规划归属是附加项，资源/组卷流程的默认目标保持不变。"""

    controller = LocalRepairController()
    for issue_type, default in (
        ("factual_error", "expert"),
        ("missing_evidence", "expert"),
        ("content_quality", "paper_assembly"),
        ("learner_mismatch", "expert"),
        ("route_or_prerequisite_error", "expert"),
    ):
        assert controller._DEFAULT_TARGETS[issue_type] == default


def test_all_audit_issue_types_are_registered_across_repair_layer() -> None:
    """AuditIssueType 全集必须在返修层所有分发点注册，防止漏注册 KeyError。

    新增 issue_type 时若忘记同步 local_repair 字典或 audit_issue_resolver
    的 _OWNER_BY_TYPE，此测试会立即失败（而不是等线上 KeyError）。
    """
    from typing import get_args

    from competition_app.contracts.audit_compilation import AuditIssueType
    from competition_app.runtime.audit_issue_resolver import AuditIssueResolver
    from competition_app.runtime.local_repair import LocalRepairController

    all_types = set(get_args(AuditIssueType))
    controller = LocalRepairController()
    # 人工复核类型允许空目标，但必须在字典中显式注册
    assert all_types == set(controller._DEFAULT_TARGETS), (
        f"_DEFAULT_TARGETS 缺失/多余注册: "
        f"{all_types ^ set(controller._DEFAULT_TARGETS)}"
    )
    assert all_types == set(controller._ALLOWED_TARGETS), (
        f"_ALLOWED_TARGETS 缺失/多余注册: "
        f"{all_types ^ set(controller._ALLOWED_TARGETS)}"
    )
    assert all_types == set(AuditIssueResolver._OWNER_BY_TYPE), (
        f"_OWNER_BY_TYPE 缺失/多余注册: "
        f"{all_types ^ set(AuditIssueResolver._OWNER_BY_TYPE)}"
    )
