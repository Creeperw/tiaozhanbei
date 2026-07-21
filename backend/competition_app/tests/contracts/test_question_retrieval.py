from competition_app.contracts.knowledge import (
    LearnerQuestionView,
    QuestionDetail,
    QuestionRetrievalMetadata,
    to_learner_view,
)


def test_question_detail_retains_internal_answer_but_learner_view_excludes_it() -> None:
    question = QuestionDetail(
        question_id="Q_1",
        question_type="单项选择题",
        stem="四君子汤的功效是？",
        reference_answer="益气健脾",
        analysis="教材解析",
        tags=["方剂学"],
        source_metadata={},
        bridges=[],
        retrieval=QuestionRetrievalMetadata(
            channels=["bridge"],
            channel_scores={"bridge": 1.0},
            fusion_score=1.0,
        ),
    )

    learner_view = to_learner_view(question)

    assert isinstance(learner_view, LearnerQuestionView)
    assert learner_view.question_id == "Q_1"
    assert "reference_answer" not in learner_view.model_dump()
    assert "analysis" not in learner_view.model_dump()
