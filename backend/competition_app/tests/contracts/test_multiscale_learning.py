from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from competition_app.contracts.multiscale_learning import (
    MetricValue,
    MultiScaleLearningState,
    PathCandidate,
)


def valid_candidate_payload() -> dict[str, object]:
    return {
        "candidate_id": "CANDIDATE_1",
        "scope": "daily_task",
        "stage": {"stage_id": "PHASE_1", "name": "中医基础"},
        "books": [{"book_id": "BOOK_1", "name": "中医基础理论"}],
        "knowledge_points": [{"kp_id": "KP_1", "name": "阴阳学说"}],
        "estimated_minutes": 25,
        "eligible": True,
        "hard_constraint_results": [
            {
                "key": "trusted_source",
                "passed": True,
                "reason": "approved route source",
                "source_refs": ["route:ROUTE_1"],
            }
        ],
        "score": 0.72,
        "score_components": {
            "learning_gain": {
                "available": True,
                "value": 0.8,
                "unit": "ratio_0_1",
                "source_refs": ["mastery:MASTER_1"],
            },
            "difficulty_fit": {
                "available": False,
                "value": None,
                "unit": "ratio_0_1",
                "source_refs": [],
                "unavailable_reason": "resource_difficulty_missing",
            },
        },
        "evidence_refs": ["evidence:TB_1"],
        "source_refs": ["task:TASK_1"],
        "recommended_action": "learn",
    }


def test_missing_metric_requires_unavailable_reason() -> None:
    with pytest.raises(ValueError, match="unavailable metric requires reason"):
        MetricValue(available=False, value=None)


def test_candidate_score_and_components_are_bounded() -> None:
    candidate = PathCandidate.model_validate(valid_candidate_payload())

    assert 0 <= candidate.score <= 1
    assert all(
        item.value is None or 0 <= item.value <= 1
        for item in candidate.score_components.values()
    )


def test_candidate_rejects_out_of_range_score_component() -> None:
    payload = valid_candidate_payload()
    payload["score_components"]["learning_gain"]["value"] = 1.01

    with pytest.raises(ValidationError, match="score component values must be between 0 and 1"):
        PathCandidate.model_validate(payload)


def test_unavailable_metric_cannot_carry_a_value() -> None:
    with pytest.raises(ValueError, match="unavailable metric value must be null"):
        MetricValue(
            available=False,
            value=0,
            unavailable_reason="no_attempts",
        )


def test_multiscale_state_requires_canonical_digest() -> None:
    payload = {
        "state_id": "STATE_1",
        "learner_id": "1",
        "generated_at": datetime(2026, 7, 24, tzinfo=timezone.utc),
        "macro": {},
        "meso": {},
        "micro": {},
        "data_quality": {},
        "hard_constraints": [],
        "source_refs": [],
        "state_digest": "a" * 24,
    }
    state = MultiScaleLearningState.model_validate(payload)
    assert state.schema_version == "1.0"

    payload["state_digest"] = "not-a-digest"
    with pytest.raises(ValidationError):
        MultiScaleLearningState.model_validate(payload)
