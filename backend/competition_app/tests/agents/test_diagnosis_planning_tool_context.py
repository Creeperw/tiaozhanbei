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
    registry.register(
        "get_learning_path_progress",
        lambda external_user_id: {
            "schema_version": "1.0",
            "learner_id": external_user_id,
            "availability": "available",
            "current_stage_name": "第一阶段",
            "stages": [
                {
                    "name": "第一阶段",
                    "status": "in_progress",
                    "order": 1,
                    "books": [{"book": "中医学基础"}],
                }
            ],
            "books": [
                {
                    "book": "中医学基础",
                    "status": "in_progress",
                    "sections": [
                        {
                            "name": "阴阳学说",
                            "chapter": "第一章 绪论",
                            "status": "in_progress",
                            "mastery": 0.3,
                            "video": {
                                "bvid": "BV1xx",
                                "video_title": "中医学基础（上）",
                                "part_title": "阴阳学说",
                                "start_seconds": 10,
                                "end_seconds": 120,
                            },
                        }
                    ],
                }
            ],
            "current_section": {
                "name": "阴阳学说",
                "chapter": "第一章 绪论",
                "status": "in_progress",
                "mastery": 0.3,
                "video": {
                    "bvid": "BV1xx",
                    "video_title": "中医学基础（上）",
                    "part_title": "阴阳学说",
                    "start_seconds": 10,
                    "end_seconds": 120,
                },
            },
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
    assert result["learning_path_progress"]["availability"] == "available"
    assert result["learning_path_progress"]["current_section"]["name"] == "阴阳学说"
    assert result["learning_path_progress"]["current_section"]["video"]["bvid"] == "BV1xx"


@pytest.mark.asyncio
async def test_diagnosis_keeps_planning_context_without_path_progress_tool() -> None:
    """Older deployments or unit callers without the path tool still get the
    enriched planning context; the new projection is simply omitted."""

    registry = ToolRegistry()
    registry.register(
        "get_learning_planning_context",
        lambda external_user_id, scope, available_minutes=60: {
            "source": "authorized_tool",
            "learning_profile": {"current_status": {"status_code": "T2"}},
            "current_long_term_plan": {"content": "正式长期规划"},
        },
        allowed_agents={"diagnosis_agent"},
    )
    original = {
        "learner_id": "USER_1",
        "plan_scope": "daily_task",
        "available_minutes": 30,
        "tool_registry": registry,
    }

    result = await DiagnosisAgent()._with_authorized_planning_context(original)

    assert result["planning_context_source"] == "authorized_tool"
    assert result["current_long_term_plan"] == {"content": "正式长期规划"}
    assert "learning_path_progress" not in result
