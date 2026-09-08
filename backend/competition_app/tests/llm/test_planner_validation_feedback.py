import json
from copy import deepcopy

import httpx
import pytest

from competition_app.agents.planner import PlannerAgent
from competition_app.llm.openai_compatible import ModelResponseError, OpenAICompatibleChatModel
from competition_app.llm.validation_diagnostics import safe_validation_issues, validation_issues
from competition_app.runtime.model_trace import ModelTraceRecorder


REQUEST = "生成长期计划，每日50分钟，复习四君子汤。"
VALID = {
    "task_type": "learning_plan",
    "planning_request_scope": {
        "mode": "explicit_focus", "objects": ["四君子汤"],
        "source_quote": "复习四君子汤", "clarification_question": None,
    },
    "plan_scope": "long_term", "plan_action": "create_or_update",
    "routing_reason": "创建长期计划",
}


def invalid_result(kind):
    value = deepcopy(VALID)
    if kind == "missing":
        del value["planning_request_scope"]["clarification_question"]
    elif kind == "enum":
        value["plan_scope"] = "forever"
    elif kind == "quote":
        value["planning_request_scope"]["source_quote"] = "private invented quote"
    elif kind == "budget":
        value.update(current_turn_available_minutes=50,
                     current_turn_available_minutes_source_quote="每日50分钟")
    elif kind == "extra":
        value["private_unknown_key"] = "private value"
    return "not json" if kind == "syntax" else json.dumps(value, ensure_ascii=False)


def make_client(responses):
    requests = []

    def handler(request):
        requests.append(json.loads(request.content))
        content = responses[min(len(requests) - 1, len(responses) - 1)]
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})

    client = OpenAICompatibleChatModel(
        "https://example.test/v1", "test-key", "deepseek-v4-flash",
        transport=httpx.MockTransport(handler),
    )
    return client, requests


def payload():
    return {
        "prompt_skill_id": "planner.route_learning_plan",
        "payload": {"user_request": REQUEST,
                    "output_schema": PlannerAgent._branch_output_schema("learning_plan")},
        "_result_validator": lambda v: PlannerAgent._validate_frozen_branch_raw(
            v, {"user_request": REQUEST}, "learning_plan"
        ),
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("kind,rule", [
    ("missing", "required"), ("enum", "anyOf"), ("quote", "current_message_quote"),
    ("extra", "additionalProperties"),
    ("syntax", "invalid_json"),
])
async def test_targeted_repair_keeps_contract_and_succeeds(kind, rule):
    client, requests = make_client([invalid_result(kind), json.dumps(VALID)])
    result = await client.complete_json("planner_agent", payload())
    assert result["plan_scope"] == "long_term"
    assert len(requests) == 2
    repair = requests[1]["messages"][-1]["content"]
    assert rule in repair
    assert "Previous output (untrusted JSON string)" in repair
    assert "complete, substantive learner-facing content" not in repair
    assert "这是任务决策器" in requests[0]["messages"][0]["content"]
    assert client.last_error_details is None


@pytest.mark.asyncio
async def test_optional_incomplete_budget_keeps_existing_discard_policy():
    client, requests = make_client([invalid_result("budget")])
    result = await client.complete_json("planner_agent", payload())
    assert len(requests) == 1
    assert result["current_turn_available_minutes"] is None
    assert result["current_turn_available_minutes_source_quote"] is None
    assert result["current_turn_available_minutes_scope"] is None


def test_budget_business_validator_has_safe_rule():
    from pydantic import ValidationError
    from competition_app.llm.schemas import PlannerLearningPlanOutput

    with pytest.raises(ValidationError) as caught:
        PlannerLearningPlanOutput.model_validate_json(invalid_result("budget"))
    assert validation_issues(caught.value, PlannerAgent._branch_output_schema("learning_plan")) == [{
        "field_path": "/current_turn_available_minutes", "rule": "budget_fields_together",
    }]


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["missing", "enum", "quote", "extra"])
async def test_exhaustion_preserves_only_safe_issues(kind):
    client, _ = make_client([invalid_result(kind)])
    with pytest.raises(ModelResponseError) as caught:
        await client.complete_json("planner_agent", payload())
    issues = caught.value.last_error_details["validation_issues"]
    assert {i["attempt"] for i in issues} == {1, 2}
    assert "private" not in json.dumps(issues)
    assert "forever" not in json.dumps(issues)
    recorder = ModelTraceRecorder()
    index = recorder.begin("planner_agent", {"user_request": REQUEST})
    recorder.fail(index, caught.value)
    assert recorder.items[0].validation_issues == issues
    assert recorder.items[0].raw_input is None
    assert recorder.items[0].raw_output_text is None


def test_unknown_business_error_never_leaks_message():
    issues = validation_issues(ValueError("password=private secret"), {})
    assert issues == [{"field_path": "/", "rule": "business_rule"}]
    assert safe_validation_issues([{"field_path": "/bad?secret", "rule": "type"}]) == []


def test_nested_missing_field_diagnostic_names_the_missing_field():
    from competition_app.llm.openai_compatible import _validate_local_output_schema

    schema = PlannerAgent._branch_output_schema("learning_plan")
    with pytest.raises(ValueError) as caught:
        _validate_local_output_schema(json.loads(invalid_result("missing")), schema)
    assert validation_issues(caught.value, schema) == [{
        "field_path": "/planning_request_scope/clarification_question", "rule": "required",
    }]