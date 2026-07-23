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


def test_unspecified_time_does_not_satisfy_long_term_budget() -> None:
    result = ProfileReadinessService().evaluate(
        {
            "user_profile": {
                "learning_goal": "中医执业医师资格考试",
                "learning_background": "零基础",
                "time_constraints": "不固定",
            }
        },
        "long_term",
    )

    assert result.can_proceed is False
    assert result.next_field == "time_constraints"
    assert result.missing_fields == ["time_constraints"]


def test_irregular_time_with_quantified_budget_is_sufficient() -> None:
    result = ProfileReadinessService().evaluate(
        {
            "user_profile": {
                "learning_goal": "中医执业医师资格考试",
                "learning_background": "零基础",
                "time_constraints": "时间段不固定，但每周4天、每次2小时",
            }
        },
        "long_term",
    )

    assert result.can_proceed is True


def test_generic_planning_instruction_is_not_a_learning_goal() -> None:
    result = ProfileReadinessService().evaluate(
        {
            "user_profile": {
                "learning_goal": "请结合我的学习状态，为我制定一份学习计划。",
                "learning_background": "零基础",
                "time_constraints": "每周4天、每次2小时",
            }
        },
        "long_term",
    )

    assert result.can_proceed is False
    assert result.next_field == "learning_goal"
