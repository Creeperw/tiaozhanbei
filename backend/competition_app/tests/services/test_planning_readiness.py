from competition_app.services.planning_readiness import PlanningReadinessService


def test_long_term_requires_profile_when_profile_is_empty() -> None:
    result = PlanningReadinessService().evaluate(
        {"user_profile": {}, "learning_target": {}}, "long_term", learner_id="U1"
    )

    assert result.status == "needs_profile"
    assert result.can_generate is False
    assert result.next_profile_field == "learning_goal"


def test_short_and_daily_require_their_immediate_parent() -> None:
    service = PlanningReadinessService()
    short = service.evaluate({}, "short_term", learner_id="U1")
    daily = service.evaluate(
        {
            "current_long_term_plan": {
                "plan_id": "LONG_1",
                "learner_id": "U1",
                "version": 1,
                "status": "active",
                "content": "长期规划",
            }
        },
        "daily_task",
        learner_id="U1",
    )

    assert short.status == "needs_long_term_plan"
    assert daily.status == "needs_short_term_plan"


def test_daily_without_any_plan_still_asks_for_immediate_short_term_parent() -> None:
    result = PlanningReadinessService().evaluate({}, "daily_task", learner_id="U1")

    assert result.status == "needs_short_term_plan"
    assert result.requested_scope == "daily_task"


def test_daily_rejects_short_plan_bound_to_old_long_plan() -> None:
    result = PlanningReadinessService().evaluate(
        {
            "current_long_term_plan": {
                "plan_id": "LONG_2",
                "learner_id": "U1",
                "version": 2,
                "status": "active",
                "content": "新长期规划",
            },
            "current_short_term_plan": {
                "plan_id": "SHORT_1",
                "long_term_plan_id": "LONG_1",
                "learner_id": "U1",
                "version": 1,
                "status": "active",
                "content": "旧短期计划",
            },
        },
        "daily_task",
        learner_id="U1",
    )

    assert result.status == "stale_parent_plan"
    assert result.can_generate is False
