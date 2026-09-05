from competition_app.application.personalized_review_card import PersonalizedReviewCardUseCase
from competition_app.runtime.orchestrator import ExecutionResult


class AuditPayload:
    decision = "reject"
    findings = ["教学内容缺少必要证据", "请补充教材引用"]


class AuditEnvelope:
    payload = AuditPayload()


def test_failure_detail_exposes_audit_decision_without_hiding_the_gate() -> None:
    execution = ExecutionResult(status="failed", outputs={"audit": AuditEnvelope()})

    detail = PersonalizedReviewCardUseCase._execution_failure_detail(execution)

    assert detail == "audit decision=reject; findings=教学内容缺少必要证据; 请补充教材引用"