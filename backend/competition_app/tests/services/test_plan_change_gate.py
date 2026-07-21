from competition_app.services.plan_change_gate import PlanChangeGate


ACTIVE_LONG = {"content": "已有长期计划", "status": "active", "version": 3}
ACTIVE_SHORT = {"content": "已有短期计划", "status": "active", "version": 5}


def decide(request: str, **facts):
    return PlanChangeGate().decide(
        user_request=request,
        current_long_term_plan=ACTIVE_LONG,
        current_short_term_plan=ACTIVE_SHORT,
        **facts,
    )


def test_existing_valid_plans_are_reused_by_default() -> None:
    result = decide("请讲解四君子汤的配伍")

    assert result.long_term_action == "reuse"
    assert result.short_term_action == "reuse"
    assert result.daily_task_action == "update"
    assert not result.requires_clarification


def test_single_error_does_not_replan() -> None:
    result = decide("我刚才答错了一题", single_performance_change=True)

    assert (result.long_term_action, result.short_term_action) == ("reuse", "reuse")


def test_explicit_two_week_time_change_only_updates_short_term() -> None:
    result = decide("未来两周每天只有15分钟，请调整短期计划")

    assert result.long_term_action == "reuse"
    assert result.short_term_action == "update"
    assert not result.requires_clarification


def test_vague_replanning_request_requires_clarification() -> None:
    result = decide("这个计划不合适，请重新规划")

    assert result.requires_clarification
    assert result.long_term_action == "reuse"
    assert result.short_term_action == "reuse"
    assert len(result.clarification_questions) >= 4


def test_vague_replanning_synonym_requires_clarification() -> None:
    result = decide("这个长期规划我不满意，重新计划一下")

    assert result.requires_clarification
    assert result.long_term_action == "reuse"
    assert result.short_term_action == "reuse"


def test_plan_dissatisfaction_without_fixed_word_order_requires_clarification() -> None:
    result = decide("这个长期规划我确实不满意")

    assert result.requires_clarification


def test_replan_synonym_with_concrete_short_term_change_updates_short_term() -> None:
    result = decide("未来两周每天只有15分钟，请重新计划短期规划")

    assert not result.requires_clarification
    assert result.long_term_action == "reuse"
    assert result.short_term_action == "update"


def test_confirmed_long_term_change_also_updates_dependent_short_term_plan() -> None:
    result = decide(
        "这个长期规划我不满意，重新计划一下",
        explicit_long_term_change=True,
    )

    assert not result.requires_clarification
    assert result.long_term_action == "update"
    assert result.short_term_action == "update"
    assert result.daily_task_action == "update"
