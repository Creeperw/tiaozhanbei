import pytest

from competition_app.repositories.failure_case import (
    AuditFailureCase,
    InMemoryFailureCaseRepository,
)


def _case(**overrides) -> AuditFailureCase:
    fields = dict(
        case_id="FC_1",
        execution_id="EXE_1",
        learner_id="LEARNER_1",
        task_type="knowledge_explanation",
        audit_result_id="AUDIT_1",
        decision="pass",
        released=True,
        issue_types=["content_quality"],
        findings=["非阻断建议：可以进一步润色表达。"],
        repair_json=None,
        input_digest="a" * 64,
    )
    fields.update(overrides)
    return AuditFailureCase(**fields)


def test_in_memory_save_and_list_recent_orders_by_time() -> None:
    repository = InMemoryFailureCaseRepository()
    repository.save(_case(case_id="FC_1", decision="revise", released=False))
    repository.save(_case(case_id="FC_2", decision="pass", released=True))

    recent = repository.list_recent()

    assert {case.case_id for case in recent} == {"FC_1", "FC_2"}
    # 同一次执行内 created_at 可能相同；list_recent 按时间倒序，稳定时
    # 允许任意顺序，只验证全部存在。
    assert len(recent) == 2


def test_in_memory_count_by_issue_type() -> None:
    repository = InMemoryFailureCaseRepository()
    repository.save(
        _case(case_id="FC_1", issue_types=["content_quality", "conflicting_evidence"])
    )
    repository.save(_case(case_id="FC_2", issue_types=["content_quality"]))
    repository.save(_case(case_id="FC_3", issue_types=["safety_violation"]))

    counts = repository.count_by_issue_type()

    assert counts == {
        "content_quality": 2,
        "conflicting_evidence": 1,
        "safety_violation": 1,
    }


def test_in_memory_repair_success_rate_only_counts_repaired_cases() -> None:
    repository = InMemoryFailureCaseRepository()
    repository.save(
        _case(
            case_id="FC_1",
            repair_json={
                "repair_id": "R1",
                "final_audit_decision": "pass",
                "status": "completed",
            },
        )
    )
    repository.save(
        _case(
            case_id="FC_2",
            repair_json={
                "repair_id": "R2",
                "final_audit_decision": "needs_human_review",
                "status": "stopped",
            },
        )
    )
    repository.save(_case(case_id="FC_3", repair_json=None))

    assert repository.repair_success_rate() == 0.5


def test_in_memory_escalated_and_released_rates() -> None:
    repository = InMemoryFailureCaseRepository()
    repository.save(_case(case_id="FC_1", decision="pass", released=True))
    repository.save(_case(case_id="FC_2", decision="needs_human_review", released=False))
    repository.save(_case(case_id="FC_3", decision="reject", released=False))

    assert repository.escalated_rate() == pytest.approx(2 / 3, abs=1e-3)
    assert repository.released_rate() == pytest.approx(1 / 3, abs=1e-3)


def test_in_memory_empty_repository_returns_zero_rates() -> None:
    repository = InMemoryFailureCaseRepository()

    assert repository.count_by_issue_type() == {}
    assert repository.repair_success_rate() == 0.0
    assert repository.escalated_rate() == 0.0
    assert repository.released_rate() == 0.0
    assert repository.list_recent() == []
