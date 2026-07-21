from pathlib import Path
import json

import pytest

from competition_app.application.container import ApplicationContainer
from competition_app.application.personalized_review_card import ReviewCardRequest
from competition_app.config import Settings
from competition_app.runtime.tool_registry import ToolPermissionError


@pytest.mark.asyncio
async def test_stub_review_card_runs_mastery_review_agents_and_exports_snapshot(tmp_path: Path) -> None:
    container = ApplicationContainer.build(Settings(mode="stub"), snapshot_root=tmp_path)

    tools = container.review_card_use_case.orchestrator.tool_registry
    evidence = await tools.invoke(
        "get_kp_with_content", "knowledge_base_agent", query="四君子汤"
    )
    assert evidence.resolved_kp_ids == ["KP_FJ_001"]
    with pytest.raises(ToolPermissionError):
        await tools.invoke("get_question_with_content", "expert_agent", query="四君子汤")

    result = await container.review_card_use_case.execute(
        ReviewCardRequest(
            learner_id="learner_001",
            user_request="请用对比表生成四君子汤复习卡",
            available_minutes=15,
            messages=[{"message_id": "M1", "role": "user", "content": "请用对比表复习四君子汤。"}],
        )
    )

    assert result.status == "success"
    producers = {output.producer for output in result.agent_outputs}
    assert producers == {
        "planner_agent",
        "knowledge_base_agent",
        "default_route_resolver",
        "diagnosis_agent",
        "review_scheduler",
        "expert_agent",
        "audit_agent",
    }
    assert result.audit.decision == "pass"
    assert result.learning_plan is None
    assert result.resource.title == "四君子汤个性化复习卡"
    assert result.resource_version.status == "published"
    assert result.resource_binding.review_task_id == result.review_task.review_task_id
    assert result.review_schedule.selected_task.review_task_id == result.review_task.review_task_id
    assert result.review_schedule.formula_policy.formula_version == "ebbinghaus-review-v1"
    assert result.review_task.status == "awaiting_attempt"
    assert container.review_service.get_queue("learner_001").entries == []
    assert {intent.effect_type for intent in result.writeback_intents} == {
        "record_audit",
        "publish_resource",
        "upsert_review_task",
        "bind_review_resource",
    }
    assert result.review_task.primary_kp_id == "KP_FJ_001"
    assert result.snapshot_path.exists()
    snapshot_text = result.snapshot_path.read_text(encoding="utf-8")
    assert "DASHSCOPE_API_KEY" not in snapshot_text
    assert "MYSQL_PASSWORD" not in snapshot_text
    planner_output = next(output for output in result.agent_outputs if output.producer == "planner_agent")
    assert not hasattr(planner_output.payload, "knowledge_query")
    assert planner_output.payload.task_type == "personalized_review_card"
    snapshot = json.loads(snapshot_text)
    assert snapshot["review_schedule"]["selected_task"]["primary_kp_id"] == "KP_FJ_001"
    scheduler_output = next(
        output for output in result.agent_outputs if output.producer == "review_scheduler"
    )
    assert {ref.purpose for ref in scheduler_output.input_refs} == {
        "dependency:diagnosis",
        "dependency:knowledge",
    }
    assert "planner" not in [step["step_id"] for step in snapshot["plan"]["steps"]]
    assert [(item["tool_name"], item["agent"], item["status"]) for item in snapshot["tool_trace"]] == [
        ("get_kp_with_content", "knowledge_base_agent", "success"),
        ("get_question_with_content", "knowledge_base_agent", "success"),
    ]


@pytest.mark.asyncio
async def test_stub_practice_request_records_question_search_without_answers(tmp_path: Path) -> None:
    container = ApplicationContainer.build(Settings(mode="stub"), snapshot_root=tmp_path)

    result = await container.review_card_use_case.execute(
        ReviewCardRequest(
            learner_id="learner_002",
            user_request="四君子汤，复习后出三道练习题并生成复习卡",
            available_minutes=15,
        )
    )

    snapshot_text = result.snapshot_path.read_text(encoding="utf-8")
    snapshot = json.loads(snapshot_text)
    assert any(item["tool_name"] == "get_question_with_content" for item in snapshot["tool_trace"])
    assert "题目答案" not in snapshot_text
    assert "题目答案解析" not in snapshot_text
