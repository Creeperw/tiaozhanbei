import pytest

from competition_app.contracts.base import AgentEnvelope
from competition_app.contracts.execution import ExecutionPlan, ExecutionStep
from competition_app.contracts.local_repair import RepairIssue
from competition_app.runtime.local_repair import LocalRepairController


def _plan(*steps: ExecutionStep) -> ExecutionPlan:
    return ExecutionPlan(plan_id="PLAN_1", task_type="paper_generation", steps=list(steps))


def paper_or_resource_plan() -> ExecutionPlan:
    return _plan(
        ExecutionStep(step_id="paper_blueprint", agent="paper_blueprint_agent"),
        ExecutionStep(step_id="knowledge", agent="knowledge_base_agent"),
        ExecutionStep(step_id="diagnosis", agent="diagnosis_agent"),
        ExecutionStep(step_id="expert", agent="expert_agent"),
        ExecutionStep(step_id="paper_assembly", agent="paper_assembly_agent"),
        ExecutionStep(step_id="audit", agent="audit_agent"),
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


def test_unresolved_finding_does_not_guess_repair_owner() -> None:
    repair = LocalRepairController().plan_repair(
        plan=resource_plan(),
        audit_step_id="audit",
        audit_findings=["无法确定来源的异常"],
        outputs=existing_outputs(),
    )

    assert repair.status == "needs_human_review"
    assert repair.actions == []


def test_mixed_findings_merge_without_duplicate_reruns() -> None:
    controller = LocalRepairController()
    repair = controller.plan_repair(
        plan=paper_or_resource_plan(),
        audit_step_id="audit",
        audit_findings=["事实缺少教材证据", "题目偏离蓝图", "事实缺少教材证据"],
        outputs=existing_outputs(),
    )

    assert [action.step_id for action in repair.actions].count("knowledge") == 1
    assert [action.step_id for action in repair.actions].count("audit") == 1


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
        outputs=existing_outputs(),
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
