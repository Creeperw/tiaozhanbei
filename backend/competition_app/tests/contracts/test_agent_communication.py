from datetime import datetime, timezone

import pytest

from competition_app.contracts.agent_communication import (
    AgentHandoffBundle,
    ConfirmedFact,
)


def valid_bundle_payload() -> dict[str, object]:
    return {
        "handoff_id": "HANDOFF_1",
        "trace_id": "TRACE_1",
        "execution_id": "EXE_1",
        "learner_id": "LEARNER_1",
        "target_agent": "diagnosis_agent",
        "purpose": "diagnose",
        "evidence": [
            {
                "evidence_id": "E1",
                "source_type": "textbook",
                "source_id": "TB_1",
                "claim": "基础概念定义",
            }
        ],
        "uncertainties": [
            {
                "uncertainty_id": "U1",
                "category": "learning_state",
                "description": "尚未完成诊断",
                "blocking": True,
            }
        ],
        "generated_at": datetime(2026, 7, 24, tzinfo=timezone.utc),
    }


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
            generated_at=datetime(2026, 7, 24, tzinfo=timezone.utc),
        )


def test_handoff_contract_keeps_structured_evidence_and_uncertainty() -> None:
    bundle = AgentHandoffBundle.model_validate(valid_bundle_payload())

    assert bundle.schema_version == "1.0"
    assert bundle.evidence[0].source_type == "textbook"
    assert bundle.uncertainties[0].blocking is True
