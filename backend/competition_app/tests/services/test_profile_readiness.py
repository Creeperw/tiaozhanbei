from competition_app.services.profile_readiness import ProfileReadinessService


def test_long_term_profile_gate_asks_one_field_at_a_time() -> None:
    result = ProfileReadinessService().evaluate(
        {
            "user_profile": {"learning_goal": "中医执业医师资格考试"},
            "learning_target": {},
        },
        "long_term",
    )

    assert result.status == "incomplete"
    assert result.next_field == "learning_background"
    assert result.missing_fields == ["learning_background", "time_constraints"]
    assert len(result.questions) == 1


def test_complete_profile_can_generate_long_term_plan() -> None:
    result = ProfileReadinessService().evaluate(
        {
            "user_profile": {
                "learning_goal": "中医执业医师资格考试",
                "learning_background": "零基础",
                "time_constraints": "每周5天，每天2小时",
            }
        },
        "long_term",
    )

    assert result.can_proceed is True
    assert result.missing_fields == []
