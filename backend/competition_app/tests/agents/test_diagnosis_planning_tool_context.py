import pytest

from competition_app.agents.diagnosis import DiagnosisAgent
from competition_app.runtime.tool_registry import ToolRegistry


@pytest.mark.asyncio
async def test_diagnosis_fetches_bounded_planning_context_after_routing() -> None:
    registry = ToolRegistry()
    registry.register(
        "get_learning_planning_context",
        lambda external_user_id, scope, available_minutes=60: {
            "source": "authorized_tool",
            "learning_profile": {"current_status": {"status_code": "T2"}},
            "learning_monitoring": {"evidence_status": "sufficient"},
            "current_long_term_plan": {"content": "正式长期规划"},
            "current_short_term_plan": {},
            "current_learning_task": {},
            "multi_scale_learning_state": {"macro": {"stage": 1}},
            "path_candidates": {"eligible": [], "blocked": []},
            "task_load_policy": {"recommended_minutes": available_minutes},
        },
        allowed_agents={"diagnosis_agent"},
    )
    original = {
        "learner_id": "USER_1",
        "plan_scope": "short_term",
        "available_minutes": 90,
        "tool_registry": registry,
        "current_long_term_plan": {"content": "root context should be replaced"},
        "learning_monitoring": {"learning_focus_sessions": ["not model input"]},
    }

    result = await DiagnosisAgent()._with_authorized_planning_context(original)

    assert result["planning_context_source"] == "authorized_tool"
    assert result["current_long_term_plan"] == {"content": "正式长期规划"}
    assert result["learning_monitoring"] == {"evidence_status": "sufficient"}
    assert result["task_load_policy"] == {"recommended_minutes": 90}
    assert original["current_long_term_plan"]["content"] == "root context should be replaced"
