from pathlib import Path
from types import SimpleNamespace

import pytest

from competition_app.application.personalized_review_card import (
    PersonalizedReviewCardUseCase,
)
from competition_app.contracts.base import AgentEnvelope
from competition_app.contracts.resource import AuditResult
from competition_app.contracts.local_repair import RepairIssue
from competition_app.repositories.failure_case import (
    AuditFailureCase,
    InMemoryFailureCaseRepository,
)
from competition_app.repositories.evolution import InMemoryEvolutionRepository
from competition_app.services.failure_signature import FailureSignatureService
from competition_app.services.feedback_governance import FeedbackGovernanceService


class AuditEnvelope:
    def __init__(self, audit: AuditResult) -> None:
        self.payload = audit


class _Execution:
    """最小 execution 替身：只暴露 _record_failure_case 需要访问的字段。"""

    def __init__(
        self,
        audit: AuditResult | None,
        repair_trace: list | None = None,
    ) -> None:
        self.outputs = {"audit": AuditEnvelope(audit)} if audit else {}
        self.repair_trace = repair_trace or []

    @property
    def status(self) -> str:
        return "success"


def _audit(decision: str = "pass", issue_types: list[str] | None = None) -> AuditResult:
    return AuditResult(
        audit_result_id="AUDIT_TEST",
        decision=decision,
        audit_report="测试报告",
        findings=["非阻断建议：可以进一步润色。"],
        structured_findings=[
            RepairIssue(
                issue_id=f"I_{index}",
                issue_type=issue_type,
                message=f"问题{index}",
                owner_step_id="expert",
                affected_step_ids=["expert"],
                origin="audit_model",
            )
            for index, issue_type in enumerate(issue_types or [])
        ],
    )


def _use_case() -> tuple[PersonalizedReviewCardUseCase, InMemoryFailureCaseRepository]:
    repository = InMemoryFailureCaseRepository()
    use_case = PersonalizedReviewCardUseCase(
        orchestrator=SimpleNamespace(execute=None),
        snapshot_exporter=SimpleNamespace(export=lambda *args, **kwargs: Path("x")),
    )
    use_case.failure_case_repository = repository
    return use_case, repository


def test_red_line_escalation_is_recorded_as_unreleased() -> None:
    use_case, repository = _use_case()
    execution = _Execution(
        audit=_audit("needs_human_review", issue_types=["safety_violation"])
    )

    use_case._record_failure_case(
        execution_id="EXE_1",
        learner_id="LEARNER_1",
        task_type="knowledge_explanation",
        execution=execution,
        request_text="竹沥水题目讲解",
    )

    cases = repository.list_recent()
    assert len(cases) == 1
    case = cases[0]
    assert isinstance(case, AuditFailureCase)
    assert case.execution_id == "EXE_1"
    assert case.decision == "needs_human_review"
    assert case.released is False
    assert case.issue_types == ["safety_violation"]
    assert case.input_digest != ""
    assert len(case.input_digest) == 64


def test_non_red_line_release_is_recorded_as_released() -> None:
    use_case, repository = _use_case()
    execution = _Execution(
        audit=_audit("pass", issue_types=["content_quality", "conflicting_evidence"])
    )

    use_case._record_failure_case(
        execution_id="EXE_2",
        learner_id="LEARNER_2",
        task_type="knowledge_explanation",
        execution=execution,
        request_text="四君子汤复习卡",
    )

    case = repository.list_recent()[0]
    assert case.decision == "pass"
    assert case.released is True
    assert case.issue_types == ["content_quality", "conflicting_evidence"]


def test_clean_pass_without_findings_is_not_recorded() -> None:
    use_case, repository = _use_case()
    execution = _Execution(
        audit=AuditResult(
            audit_result_id="AUDIT_OK",
            decision="pass",
            audit_report="全部通过",
            findings=[],
        )
    )

    use_case._record_failure_case(
        execution_id="EXE_3",
        learner_id="LEARNER_3",
        task_type="knowledge_explanation",
        execution=execution,
        request_text="简单问题",
    )

    assert repository.list_recent() == []


def test_repair_trace_is_compacted_into_repair_json() -> None:
    use_case, repository = _use_case()
    trace = SimpleNamespace(
        repair_id="REPAIR_1",
        trigger_step_id="audit",
        rerun_step_ids=["expert", "audit"],
        preserved_step_ids=["knowledge"],
        status="completed",
        final_audit_decision="pass",
        issue_types=["content_quality"],
    )
    execution = _Execution(
        audit=_audit("revise", issue_types=["content_quality"]),
        repair_trace=[trace],
    )

    use_case._record_failure_case(
        execution_id="EXE_4",
        learner_id="LEARNER_4",
        task_type="knowledge_explanation",
        execution=execution,
        request_text="需要修复的问题",
    )

    case = repository.list_recent()[0]
    assert case.repair_json is not None
    assert case.repair_json["repair_id"] == "REPAIR_1"
    assert case.repair_json["rerun_step_ids"] == ["expert", "audit"]
    assert case.repair_json["final_audit_decision"] == "pass"
    assert case.repair_json["rounds"] == 1


def test_model_audit_finding_waits_for_admin_before_signature_aggregation() -> None:
    use_case, _ = _use_case()
    evolution = InMemoryEvolutionRepository()
    use_case.feedback_governance_service = FeedbackGovernanceService(
        evolution, enabled=True
    )
    use_case.failure_signature_service = FailureSignatureService(evolution)

    use_case._record_failure_case(
        execution_id="EXE_MODEL_FINDING",
        learner_id="LEARNER_MODEL_FINDING",
        task_type="knowledge_explanation",
        execution=_Execution(
            audit=_audit("pass", issue_types=["content_quality"])
        ),
        request_text="讲解一道题",
    )

    feedback = evolution.list_feedback(limit=10)
    assert len(feedback) == 1
    assert feedback[0].status == "pending"
    assert feedback[0].trust_level == "medium"
    assert evolution.list_signatures(limit=10) == []


def test_deterministic_finding_is_aggregated_without_model_self_approval() -> None:
    use_case, _ = _use_case()
    evolution = InMemoryEvolutionRepository()
    use_case.feedback_governance_service = FeedbackGovernanceService(
        evolution, enabled=True
    )
    use_case.failure_signature_service = FailureSignatureService(evolution)
    audit = _audit("revise", issue_types=["missing_evidence"])
    audit.structured_findings[0] = audit.structured_findings[0].model_copy(
        update={
            "origin": "deterministic",
            "blocking": True,
            "policy_id": "resource:missing_evidence",
        }
    )

    use_case._record_failure_case(
        execution_id="EXE_DETERMINISTIC_FINDING",
        learner_id="LEARNER_DETERMINISTIC_FINDING",
        task_type="personalized_review_card",
        execution=_Execution(audit=audit),
        request_text="生成复习卡",
    )

    feedback = evolution.list_feedback(limit=10)
    assert len(feedback) == 1
    assert feedback[0].status == "validated"
    assert feedback[0].trust_level == "high"
    signatures = evolution.list_signatures(limit=10)
    assert len(signatures) == 1
    assert signatures[0].candidate_ready is False
