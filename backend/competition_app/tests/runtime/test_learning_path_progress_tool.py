"""Container-level tests for the bounded get_learning_path_progress tool."""

from pathlib import Path

import pytest

from competition_app.agents.diagnosis import DiagnosisAgent
from competition_app.application.container import ApplicationContainer
from competition_app.config import Settings


@pytest.mark.asyncio
async def test_stub_container_learning_path_progress_degrades_gracefully(
    tmp_path: Path,
) -> None:
    """Without a persisted long-term plan the tool reports requires_long_term_plan
    instead of failing, so Diagnosis can still route to the right planning task."""

    container = ApplicationContainer.build(Settings(mode="stub"), snapshot_root=tmp_path)
    tools = container.review_card_use_case.orchestrator.tool_registry

    result = await tools.invoke(
        "get_learning_path_progress",
        "diagnosis_agent",
        external_user_id="learner_001",
    )

    assert result["schema_version"] == "1.0"
    assert result["learner_id"] == "learner_001"
    assert result["availability"] == "requires_long_term_plan"
    assert result["stages"] == []
    assert result["books"] == []
    assert result["current_section"] is None


@pytest.mark.asyncio
async def test_stub_container_learning_path_progress_denies_other_agents(
    tmp_path: Path,
) -> None:
    from competition_app.runtime.tool_registry import ToolPermissionError

    container = ApplicationContainer.build(Settings(mode="stub"), snapshot_root=tmp_path)
    tools = container.review_card_use_case.orchestrator.tool_registry

    with pytest.raises(ToolPermissionError):
        await tools.invoke(
            "get_learning_path_progress",
            "planner_agent",
            external_user_id="learner_001",
        )


def test_diagnosis_compresses_learning_path_progress_for_the_model() -> None:
    path_progress = {
        "schema_version": "1.0",
        "learner_id": "USER_1",
        "availability": "available",
        "current_stage_name": "第一阶段",
        "stages": [
            {"name": "第一阶段", "status": "in_progress", "order": 1, "books": [{"book": "中医学基础"}]},
            {"name": "第二阶段", "status": "locked", "order": 2, "books": [{"book": "方剂学"}]},
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
                            "topic": "知识讲解",
                            "transcript": "很长的字幕内容",
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
                "topic": "知识讲解",
                "transcript": "很长的字幕内容",
            },
        },
    }

    compact = DiagnosisAgent._model_learning_path_progress(path_progress)

    assert compact["availability"] == "available"
    assert compact["current_stage_name"] == "第一阶段"
    assert compact["stages"][0]["name"] == "第一阶段"
    assert compact["books"][0]["sections"][0]["name"] == "阴阳学说"
    video = compact["current_section"]["video"]
    assert video["bvid"] == "BV1xx"
    assert video["video_title"] == "中医学基础（上）"
    assert "transcript" not in video
    assert "topic" not in video


def test_diagnosis_compresses_missing_or_empty_path_progress() -> None:
    assert DiagnosisAgent._model_learning_path_progress(None) == {}
    assert DiagnosisAgent._model_learning_path_progress({}) == {}
    compact = DiagnosisAgent._model_learning_path_progress(
        {"availability": "requires_long_term_plan"}
    )
    assert compact["availability"] == "requires_long_term_plan"
    assert compact["books"] == []
