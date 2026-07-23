import pytest

from competition_app.contracts.local_repair import LocalRepairPlan, RepairAction, RepairIssue
from competition_app.contracts.resource import AuditResult


def test_repair_contracts_preserve_bounded_single_round_plan() -> None:
    issue = RepairIssue(
        issue_id="ISSUE_1",
        issue_type="missing_evidence",
        message="事实缺少教材证据",
        owner_step_id="knowledge",
    )
    repair = LocalRepairPlan(
        repair_id="REPAIR_1",
        execution_id="EXE_1",
        trigger_step_id="audit",
        issues=[issue],
        actions=[
            RepairAction(
                action_id="rerun:knowledge",
                action_type="rerun",
                step_id="knowledge",
                reason=issue.message,
            )
        ],
        status="planned",
    )

    assert repair.schema_version == "1.0"
    assert repair.max_rounds == 1
    assert repair.requires_reaudit is True


def test_audit_result_accepts_structured_findings_without_breaking_strings() -> None:
    audit = AuditResult(
        audit_result_id="AUDIT_1",
        decision="revise",
        findings=["事实缺少教材证据"],
        structured_findings=[
            RepairIssue(
                issue_id="ISSUE_1",
                issue_type="missing_evidence",
                message="事实缺少教材证据",
                owner_step_id="knowledge",
            )
        ],
    )

    assert audit.findings == ["事实缺少教材证据"]
    assert audit.structured_findings[0].issue_type == "missing_evidence"


def test_controller_never_exceeds_one_round() -> None:
    assert LocalRepairPlan.model_fields["max_rounds"].default == 1


@pytest.mark.parametrize("issue_type", ["unresolved", "missing_evidence"])
def test_repair_issue_accepts_declared_issue_types(issue_type: str) -> None:
    assert RepairIssue(
        issue_id="ISSUE_1", issue_type=issue_type, message="测试问题"
    ).issue_type == issue_type
