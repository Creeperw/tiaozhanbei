import pytest

from competition_app.services.plan_scope import (
    infer_continued_plan_scope,
    infer_plan_scope,
)


@pytest.mark.parametrize(
    ("user_text", "expected"),
    [
        ("我今天要学习些什么东西？", "daily_task"),
        ("今天学什么？", "daily_task"),
        ("今晚我该学点什么", "daily_task"),
        ("请根据短期计划安排今天的任务", "daily_task"),
        ("再给我今天的任务", "daily_task"),
        ("请结合长期规划制定本周计划", "short_term"),
        ("请制定中医执业医师长期规划", "long_term"),
        ("请制定长期和本周学习计划", "unspecified"),
        ("给我安排一下学习", None),
        ("我今天有点累", None),
    ],
)
def test_infer_plan_scope_preserves_explicit_layers_and_ambiguity(
    user_text: str, expected: str | None
) -> None:
    assert infer_plan_scope(user_text) == expected


@pytest.mark.parametrize(
    "answer",
    [
        "目前是零基础，我是计算机专业的",
        "每周大概4天，一天4小时",
        "不对，我要考执业医师资格证",
    ],
)
def test_infer_continued_long_term_scope_for_profile_answers(answer: str) -> None:
    messages = [
        {"role": "user", "content": "请结合我的学习状态，为我制定一份学习计划。"},
        {"role": "assistant", "content": "请确认需要哪一层计划。"},
        {"role": "user", "content": "长期计划吧"},
        {"role": "user", "content": answer},
    ]

    assert infer_continued_plan_scope(answer, messages) == "long_term"


def test_explicit_non_planning_request_can_switch_away_from_plan_context() -> None:
    messages = [
        {"role": "user", "content": "长期计划吧"},
        {"role": "user", "content": "请解释阴阳是什么"},
    ]

    assert infer_continued_plan_scope("请解释阴阳是什么", messages) is None
