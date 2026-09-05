from __future__ import annotations

import pytest

from competition_app.evaluation.d1_semantic_judge import (
    D1LearnerVisibleSemanticJudge,
    JUDGE_VERSION,
)


class CapturingChatModel:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    async def complete_json(self, agent: str, payload: dict) -> dict:
        self.calls.append((agent, payload))
        return {
            "status": "judged",
            "acceptable": True,
            "request_fulfilled": True,
            "material_a_handled_correctly": True,
            "material_b_handled_correctly": True,
            "relationship_handled_correctly": True,
            "no_unsupported_resolution": True,
            "rationale_code": "acceptable",
        }


@pytest.mark.asyncio
async def test_semantic_judge_request_excludes_labels_and_process_metadata():
    model = CapturingChatModel()
    judge = D1LearnerVisibleSemanticJudge(model)

    verdict = await judge.judge(
        question="两份材料的说法应该怎样理解？",
        material_a="材料甲正文",
        material_b="材料乙正文",
        answer="候选学习讲解",
    )

    assert verdict["judge_version"] == JUDGE_VERSION
    assert verdict["acceptable"] is True
    assert len(model.calls) == 1
    agent, request = model.calls[0]
    assert agent == "learner_visible_semantic_judge"
    assert set(request["payload"]) == {
        "question",
        "material_a",
        "material_b",
        "candidate_answer",
        "output_schema",
    }
    serialized = str(request).lower()
    for forbidden in (
        "claim_text",
        "gold_relation",
        "case_group",
        "pair_order",
        "rule_id",
        "audit_decision",
        "repair_count",
        "closure_allowed",
    ):
        assert forbidden not in serialized