from competition_app.contracts.agent_context import ModelAgentContext, build_model_context
from competition_app.llm.prompt_skills import prompt_skill_registry


def test_model_agent_context_has_uniform_metadata_and_business_payload() -> None:
    context = {
        "trace_id": "TRACE_1",
        "request_id": "REQ_1",
        "learner_id": "USER_1",
    }

    value = build_model_context(
        context,
        target_agent="diagnosis_agent",
        prompt_skill=prompt_skill_registry.load("diagnosis_agent", "learning_plan"),
        payload={"user_profile": {}, "system_data": {}},
        permission_note="只读最小数据切片",
    )

    parsed = ModelAgentContext.model_validate(value)
    assert parsed.trace_id == "TRACE_1"
    assert parsed.task_id == "REQ_1"
    assert parsed.workflow_step_id == "diagnosis_agent"
    assert parsed.user_id == "USER_1"
    assert parsed.source_agent == "orchestrator"
    assert parsed.target_agent == "diagnosis_agent"
    assert parsed.purpose == "执行受控任务 diagnosis.create_learning_plan"
    assert "任务目标" in parsed.task_instructions
    assert parsed.permission_note == "只读最小数据切片"
    assert parsed.prompt_skill_id == "diagnosis.create_learning_plan"
    assert parsed.prompt_skill_version == "1.3.0"
    assert parsed.payload["user_profile"] == {}
    assert "prompt_skill" not in parsed.payload
