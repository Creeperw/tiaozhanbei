import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from competition_app.application.personalized_review_card import (
    CoordinationSummary,
    PersonalizedReviewCardUseCase,
    ReviewCardResult,
)
from competition_app.application.workflow_presentation import workflow_result_to_markdown
from competition_app.repositories.runtime import InMemoryRunStateRepository
from competition_app.runtime.orchestrator import ExecutionResult
from competition_app.runtime.trace import CommunicationTrace


def test_long_term_plan_message_includes_system_owned_stage_data() -> None:
    message = workflow_result_to_markdown({
        "status": "success",
        "learning_plan": {
            "generated_scope": "long_term",
            "long_term_plan": {
                "content": "【最终目标】建立中医基础。",
                "stages": [
                    {
                        "stage": 1,
                        "book": ["《中医学基础》"],
                        "goal": "建立基础理论框架。",
                    }
                ],
            },
        },
    })

    assert "【阶段路线数据】" in message
    encoded = message.split("```json\n", 1)[1].split("\n```", 1)[0]
    assert json.loads(encoded) == {
        "long_term_plan_stages": [
            {
                "stage": 1,
                "book": ["《中医学基础》"],
                "goal": "建立基础理论框架。",
            }
        ]
    }


def test_short_term_message_does_not_repeat_stale_long_term_stages() -> None:
    message = workflow_result_to_markdown({
        "status": "success",
        "learning_plan": {
            "generated_scope": "short_term",
            "long_term_plan": None,
            "short_term_plan": {"content": "【当前主目标】完成本周学习。"},
        },
    })

    assert "long_term_plan_stages" not in message


def test_daily_task_message_names_chapter_and_focus_knowledge_points() -> None:
    message = workflow_result_to_markdown({
        "status": "success",
        "learning_plan": {
            "generated_scope": "daily_task",
            "learning_task": {
                "task_content": "精读阴阳学说并整理笔记。",
                "learning_chapter": "《中医学基础》阴阳学说",
                "focus_knowledge_points": ["阴阳对立制约", "阴阳互根互用"],
                "estimated_minutes": 45,
                "completion_criteria": "能够闭卷解释两个概念。",
            },
        },
    })

    assert "今日章节：《中医学基础》阴阳学说" in message
    assert "重点知识点：阴阳对立制约、阴阳互根互用" in message
    assert "预计用时：45 分钟" in message


def test_paper_message_keeps_exam_body_in_workspace() -> None:
    message = workflow_result_to_markdown({
        "status": "success",
        "task_type": "paper_generation",
        "resource": {
            "title": "四君子汤试卷",
            "content": {"试卷正文": [{"题干": "不应出现在对话里"}]},
        },
        "ui_actions": [
            {
                "label": "开始答题",
                "destination": "workshop.paper",
                "params": {"paper_id": "PAPER_1"},
            }
        ],
    })

    assert "不应出现在对话里" not in message
    assert "开始答题" in message
    assert "通过审核" in message


def test_workflow_run_state_persists_communication_trace_summary() -> None:
    execution = ExecutionResult(
        status="success",
        communication_trace=[
            CommunicationTrace(
                handoff_id="HANDOFF_EXE_1_diagnosis",
                step_id="diagnosis",
                target_agent="diagnosis_agent",
                fact_count=2,
                evidence_count=0,
                blocking_field_count=0,
                status="consumed",
            )
        ],
    )
    use_case = object.__new__(PersonalizedReviewCardUseCase)
    use_case.run_state_repository = InMemoryRunStateRepository()
    coordination = use_case._execution_coordination(execution)

    use_case._remember_run(
        "THREAD_COORDINATION",
        {
            "status": "completed",
            "result": ReviewCardResult(
                status="success",
                execution_id="EXE_1",
                task_type="learning_plan",
                agent_outputs=[],
                snapshot_path=Path("snapshot.json"),
                writeback_intents=[],
                coordination=coordination,
            ),
        },
    )

    saved = use_case.get_run_state("THREAD_COORDINATION")
    assert saved is not None
    assert coordination.schema_version == "1.0"
    assert saved["coordination"]["schema_version"] == "1.0"
    assert saved["coordination"]["communication_trace"][0]["schema_version"] == "1.0"
    assert saved["coordination"]["communication_trace"][0]["handoff_id"] == (
        "HANDOFF_EXE_1_diagnosis"
    )


def test_review_card_rejects_invalid_coordination_shape() -> None:
    with pytest.raises(ValidationError):
        ReviewCardResult(
            status="success",
            execution_id="EXE_INVALID_COORDINATION",
            task_type="learning_plan",
            agent_outputs=[],
            snapshot_path=Path("snapshot.json"),
            writeback_intents=[],
            coordination={
                "schema_version": "1.0",
                "communication_trace": [],
                "unexpected": "not allowed",
            },
        )

    coordination = CoordinationSummary()
    assert coordination.model_dump(mode="json") == {
        "schema_version": "1.0",
        "communication_trace": [],
        "repair_trace": [],
    }
