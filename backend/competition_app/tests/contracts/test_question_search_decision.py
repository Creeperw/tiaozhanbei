import pytest

from competition_app.contracts.knowledge import QuestionSearchDecision
from competition_app.llm.schemas import KnowledgeRetrievalPlanModelOutput


def test_question_search_decision_requires_conservative_union_metadata() -> None:
    decision = QuestionSearchDecision(
        rule_question_search_needed=False,
        rule_reasons=[],
        model_question_search_needed=True,
        model_question_search_reason="用户要求出题。",
        final_question_search_needed=True,
        merge_strategy="conservative_union",
    )

    assert decision.final_question_search_needed is True


def test_knowledge_model_output_rejects_tool_name_and_question_id() -> None:
    with pytest.raises(ValueError, match="Extra inputs"):
        KnowledgeRetrievalPlanModelOutput.model_validate(
            {
                "kp_query": "四君子汤",
                "question_query": "四君子汤练习题",
                "retrieval_reason": "用户要求练习。",
                "tool_name": "get_question_with_content",
                "question_id": "Q_1",
            }
        )


def test_knowledge_retrieval_plan_always_requires_question_query() -> None:
    with pytest.raises(ValueError, match="question_query"):
        KnowledgeRetrievalPlanModelOutput.model_validate(
            {
                "kp_query": "四君子汤",
                "question_query": "",
                "retrieval_reason": "用户要求练习。",
            }
        )