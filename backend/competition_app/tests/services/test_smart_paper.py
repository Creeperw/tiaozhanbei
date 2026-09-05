import pytest

from competition_app.contracts.knowledge import (
    QuestionBridge,
    QuestionDetail,
    QuestionRetrievalMetadata,
)
from competition_app.contracts.paper import ExamPaperItem
from competition_app.services.smart_paper import (
    DeterministicPaperOrderer,
    build_smart_paper_execution_plan,
    deterministic_candidate_rank,
    validate_smart_paper_constraints,
)


def _item(
    question_id: str,
    question_type: str,
    difficulty: int | None,
    kp_id: str,
    answer: str,
) -> ExamPaperItem:
    return ExamPaperItem(
        sequence=1,
        unit_id=f"U_{kp_id}",
        selection_rationale="测试",
        question=QuestionDetail(
            question_id=question_id,
            question_type=question_type,
            stem=f"{kp_id}的{question_id}题干",
            options=["A. 甲", "B. 乙"] if "选择" in question_type else [],
            reference_answer=answer,
            analysis="解析",
            origin="retrieved",
            source_tier="textbook",
            difficulty=difficulty,
            tags=[kp_id],
            source_metadata={"source": "test"},
            bridges=[
                QuestionBridge(
                    kp_id=kp_id,
                    bridge_layer="strict",
                    relation="tests",
                    confidence=1.0,
                    rank=1,
                    evidence_chunk_uid=f"chunk:{kp_id}",
                    match_method="test",
                )
            ],
            retrieval=QuestionRetrievalMetadata(
                channels=["vector"],
                channel_scores={"vector": 0.9},
                fusion_score=0.9,
            ),
        ),
    )


def test_validate_smart_paper_constraints_closes_question_type_contract() -> None:
    result = validate_smart_paper_constraints(
        {
            "question_count": 6,
            "question_type_distribution": {
                "single_choice": 4,
                "short_answer": 2,
            },
            "answer_mode": "practice",
            "paper_kind": "special",
            "topic": "四君子汤",
        }
    )

    assert result["question_type_distribution"] == {
        "单项选择题": 4,
        "简答题": 2,
    }
    assert result["question_count"] == 6


@pytest.mark.parametrize("question_type", ["case_quiz", "案例分析题", "判断题"])
def test_validate_smart_paper_constraints_rejects_retired_or_unknown_types(
    question_type: str,
) -> None:
    with pytest.raises(ValueError, match="暂不支持题型"):
        validate_smart_paper_constraints(
            {
                "question_type_distribution": {question_type: 1},
                "paper_kind": "special",
                "topic": "四君子汤",
            }
        )


def test_smart_paper_plan_bypasses_only_planner_and_keeps_learner_context() -> None:
    plan = build_smart_paper_execution_plan(memory_required=True)

    assert [step.step_id for step in plan.steps] == [
        "memory",
        "diagnosis",
        "paper_blueprint",
        "question_pool",
        "paper_assembly",
        "audit",
    ]
    assert "planner" not in {step.step_id for step in plan.steps}
    diagnosis = next(step for step in plan.steps if step.step_id == "diagnosis")
    blueprint = next(step for step in plan.steps if step.step_id == "paper_blueprint")
    assembly = next(step for step in plan.steps if step.step_id == "paper_assembly")
    assert diagnosis.depends_on == ["memory"]
    assert blueprint.depends_on == ["memory", "diagnosis"]
    assert "diagnosis" in assembly.depends_on
    plan.validate_dag()


def test_smart_paper_plan_parallelizes_memory_and_diagnosis_without_compression() -> None:
    plan = build_smart_paper_execution_plan(memory_required=False)

    diagnosis = next(step for step in plan.steps if step.step_id == "diagnosis")
    blueprint = next(step for step in plan.steps if step.step_id == "paper_blueprint")
    assert diagnosis.depends_on == []
    assert blueprint.depends_on == ["memory", "diagnosis"]
    plan.validate_dag()


def test_deterministic_order_is_stable_and_preserves_repair_positions() -> None:
    items = [
        _item("Q4", "简答题", 4, "KP2", "要点"),
        _item("Q2", "单项选择题", 3, "KP1", "B"),
        _item("Q3", "单项选择题", 2, "KP2", "A"),
        _item("Q1", "单项选择题", 1, "KP1", "A"),
    ]
    orderer = DeterministicPaperOrderer()

    first = orderer.order(items, answer_mode="practice")
    second = orderer.order(items, answer_mode="practice")
    repaired = orderer.order(
        items,
        answer_mode="practice",
        preserve_positions={"Q2": 2},
    )

    assert [item.question.question_id for item in first.items] == [
        item.question.question_id for item in second.items
    ]
    assert [item.sequence for item in first.items] == [1, 2, 3, 4]
    assert first.items[-1].question.question_type == "简答题"
    assert repaired.items[1].question.question_id == "Q2"
    assert repaired.preserved_positions == 1


def test_candidate_rank_prefers_semantic_relevance_before_rrf() -> None:
    relevant = _item("RELEVANT", "单项选择题", 3, "KP1", "A").question
    irrelevant = _item("IRRELEVANT", "单项选择题", 3, "KP1", "A").question
    relevant = relevant.model_copy(update={
        "retrieval": relevant.retrieval.model_copy(update={
            "semantic_status": "eligible", "semantic_score": 0.8, "fusion_score": 0.4,
        })
    })
    irrelevant = irrelevant.model_copy(update={
        "retrieval": irrelevant.retrieval.model_copy(update={
            "semantic_status": "uncertain", "semantic_score": 0.5, "fusion_score": 0.9,
        })
    })

    assert deterministic_candidate_rank(
        relevant, target_difficulty=3
    ) < deterministic_candidate_rank(irrelevant, target_difficulty=3)
