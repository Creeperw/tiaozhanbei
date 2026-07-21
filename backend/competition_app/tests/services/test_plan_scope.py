import pytest

from competition_app.services.plan_scope import infer_plan_scope


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
