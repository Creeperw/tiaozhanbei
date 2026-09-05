from APP.backend.mistake_context_service import mistake_context_required


def test_only_objective_mistakes_require_answer_context_research():
    for question_type in ("single_choice", "multiple_choice", "fill_blank", "true_false"):
        assert mistake_context_required(question_type) is True
    for question_type in ("short_answer", "case_quiz", "case_full", "案例简答题", "主观题"):
        assert mistake_context_required(question_type) is False
