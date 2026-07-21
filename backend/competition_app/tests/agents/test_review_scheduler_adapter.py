from datetime import datetime, timedelta, timezone

import pytest

from competition_app.agents.review_scheduler import ReviewSchedulerAdapter
from competition_app.contracts.base import AgentEnvelope
from competition_app.contracts.knowledge import EvidencePack
from competition_app.contracts.review import DailyReviewPolicy
from competition_app.agents.common import envelope


NOW = datetime(2026, 7, 16, 12, 0, tzinfo=timezone.utc)


class DiagnosisPayload:
    daily_review_policy = DailyReviewPolicy(capacity=1).model_dump(mode="json")
    weak_kp_ids = ["KP_FJ_001"]


def context() -> dict:
    base = {
        "case_id": "CASE_1",
        "trace_id": "TRACE_1",
        "request_id": "REQ_1",
        "execution_id": "EXE_1",
        "learner_id": "learner_001",
        "task_type": "personalized_review_card",
        "now": NOW,
    }
    knowledge_context = {**base, "step_id": "knowledge", "dependency_outputs": {}}
    diagnosis_context = {**base, "step_id": "diagnosis", "dependency_outputs": {}}
    knowledge = envelope(
        knowledge_context,
        "knowledge_base_agent",
        "evidence_pack",
        EvidencePack(
            evidence_pack_id="EP_1",
            query="四君子汤",
            resolved_kp_ids=["KP_FJ_001"],
        ),
    )
    diagnosis = envelope(
        diagnosis_context,
        "diagnosis_agent",
        "diagnosis_result",
        DiagnosisPayload(),
    )
    return {
        **base,
        "step_id": "schedule",
        "dependency_outputs": {"knowledge": knowledge, "diagnosis": diagnosis},
        "user_knowledge_states": [
            {
                "user_id": "learner_001",
                "kp_id": "KP_FJ_001",
                "knowledge_mastery": 0.6,
                "answer_accuracy": 0.7,
                "forgetting_coefficient": 0.08,
                "kp_review_status": "需要继续复习",
                "calculated_at": (NOW - timedelta(days=1)).isoformat(),
            }
        ],
    }


@pytest.mark.asyncio
async def test_adapter_returns_traceable_deterministic_schedule_envelope() -> None:
    result = await ReviewSchedulerAdapter().run(context())

    assert isinstance(result, AgentEnvelope)
    assert result.producer == "review_scheduler"
    assert result.payload.formula_policy.formula_version == "ebbinghaus-review-v1"
    assert result.payload.selected_task.primary_kp_id == "KP_FJ_001"
    assert {ref.purpose for ref in result.input_refs} == {
        "dependency:knowledge",
        "dependency:diagnosis",
    }