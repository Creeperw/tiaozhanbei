from competition_app.contracts.agent_context import ModelAgentContext, build_model_context
from competition_app.llm.prompt_skills import prompt_skill_registry


def test_model_agent_context_has_uniform_metadata_and_business_payload() -> None:
    context = {
        "trace_id": "TRACE_1",
        "request_id": "REQ_1",
        "learner_id": "USER_1",
        "original_user_request": "请结合我的学习状态制定计划",
        "user_request": "请制定长期规划",
        "messages": [
            {"role": "user", "content": "你好"},
            {
                "role": "assistant",
                "content": '<<EV:{"raw":"trace"}>><think>推理</think>你好，请问想学习什么？',
            },
        ],
        "current_long_term_plan": {"content": "不应自动注入"},
        "learning_monitoring": {"learning_focus_sessions": [1, 2, 3]},
        "multi_scale_learning_state": {"macro": {"secret": True}},
        "compressed_conversation_summary": "user：更早前询问过方剂学。",
        "user_profile": {"learning_background": "零基础"},
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
    assert parsed.prompt_skill_version == "1.7.0"
    assert parsed.payload["user_profile"] == {}
    assert parsed.payload["original_user_request"] == "请结合我的学习状态制定计划"
    assert parsed.payload["request_context"] == {
        "original_user_request": "请结合我的学习状态制定计划",
        "current_user_request": "请制定长期规划",
    }
    assert parsed.payload["shared_context"]["original_user_request"] == (
        "请结合我的学习状态制定计划"
    )
    assert parsed.payload["shared_context"]["current_user_request"] == "请制定长期规划"
    shared = parsed.payload["shared_context"]
    assert shared["recent_conversation"] == [
        {"role": "user", "content": "你好"},
        {"role": "assistant", "content": "你好，请问想学习什么？"},
    ]
    assert shared["compressed_conversation"] == "user：更早前询问过方剂学。"
    assert shared["user_profile"] == {"learning_background": "零基础"}
    assert shared["external_information"] == []
    assert "current_long_term_plan" not in shared
    assert "learning_monitoring" not in shared
    assert "multi_scale_learning_state" not in shared
    assert "prompt_skill" not in parsed.payload
