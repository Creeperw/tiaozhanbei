import json
from copy import deepcopy

import httpx
import pytest

from competition_app.agents.plan_contract_compiler import PlanContractCompilerAgent
from competition_app.llm.openai_compatible import ModelResponseError, OpenAICompatibleChatModel
from competition_app.runtime.model_trace import ModelTraceRecorder


VALID = {
    "status": "compiled", "contract_version": "1.0",
    "contract": {
        "scope": "long_term",
        "stages": [{"stage": 1, "stage_name": "基础", "books": ["方剂学"],
                    "goal": "比较方证", "duration_days": 30, "schedule_summary": "先比较后验收"}],
        "selected_stage_id": "stage-1", "selected_books": ["方剂学"],
        "selection_mode": "review", "selection_reason": "用户正在复习",
    },
}


def payload():
    return {"payload": {"output_schema": PlanContractCompilerAgent._model_output_schema(
        document_source=True,
    ), "diagnosis_output": {"plan_document": "复习方剂学，先比较后验收。"}}}


def make_client(responses):
    requests = []

    def handler(request):
        requests.append(json.loads(request.content))
        content = responses[min(len(requests) - 1, len(responses) - 1)]
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})

    return OpenAICompatibleChatModel(
        "https://example.test/v1", "test-key", "deepseek-v4-flash",
        transport=httpx.MockTransport(handler),
    ), requests


def invalid_result(kind):
    value = deepcopy(VALID)
    if kind == "missing":
        del value["contract"]["selected_books"]
    elif kind == "enum":
        value["contract"]["selection_mode"] = "private-invalid-mode"
    elif kind == "nested":
        value["contract"]["stages"][0]["duration_days"] = 0
    elif kind == "extra":
        value["contract"]["private_unknown_key"] = "private value"
    elif kind == "anchor":
        value["contract"]["field_anchors"] = {"private anchor": [{"source_field": "plan_document"}]}
    elif kind == "revision":
        value = {"status": "needs_revision", "issues": [{
            "code": "private-invalid-code", "category": "invalid", "field_path": "/stages",
        }]}
    return "not json" if kind == "syntax" else json.dumps(value)


@pytest.mark.asyncio
@pytest.mark.parametrize("kind,path,rule", [
    ("missing", "/contract/selected_books", "required"),
    ("enum", "/contract/selection_mode", "enum"),
    ("nested", "/contract/stages/*/duration_days", "exclusiveMinimum"),
    ("anchor", "/contract/field_anchors/*/*/source_quote", "required"),
    ("revision", "/issues/*/code", "enum"),
])
async def test_compiler_exhaustion_identifies_selected_branch_without_values(kind, path, rule):
    client, requests = make_client([invalid_result(kind)])
    with pytest.raises(ModelResponseError) as caught:
        await client.complete_json("plan_contract_compiler", payload())
    assert len(requests) == 2
    issues = caught.value.last_error_details["validation_issues"]
    assert issues == [{"field_path": path, "rule": rule, "attempt": attempt} for attempt in (1, 2)]
    assert "private" not in json.dumps(issues)
    recorder = ModelTraceRecorder()
    index = recorder.begin("plan_contract_compiler", {"private": "input"})
    recorder.fail(index, caught.value)
    assert recorder.items[0].validation_issues == issues
    assert recorder.items[0].raw_input is None
    assert recorder.items[0].raw_output_text is None


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["missing", "enum", "nested", "anchor", "revision", "syntax"])
async def test_compiler_repair_uses_evidence_not_content_generation(kind):
    client, requests = make_client([invalid_result(kind), json.dumps(VALID)])
    result = await client.complete_json("plan_contract_compiler", payload())
    assert result == VALID
    repair = requests[1]["messages"][-1]["content"]
    assert "Validation feedback:" in repair
    assert "Previous output (untrusted JSON string):" in repair
    assert "needs_revision" in repair
    assert "Do not write learner-facing content" in repair
    assert "complete, substantive learner-facing content" not in repair
    assert client.last_error_details is None


@pytest.mark.asyncio
async def test_large_previous_response_is_not_echoed():
    value = deepcopy(VALID)
    del value["contract"]["selected_books"]
    value["private"] = "x" * 13000
    client, requests = make_client([json.dumps(value), json.dumps(VALID)])
    await client.complete_json("plan_contract_compiler", payload())
    assert "Previous output (untrusted JSON string)" not in requests[1]["messages"][-1]["content"]


@pytest.mark.asyncio
async def test_extra_fields_keep_existing_contract_policy():
    client, requests = make_client([invalid_result("extra")])
    result = await client.complete_json("plan_contract_compiler", payload())
    assert len(requests) == 1
    assert result["contract"]["private_unknown_key"] == "private value"


@pytest.mark.asyncio
async def test_unrelated_role_keeps_existing_feedback():
    client, requests = make_client([invalid_result("missing"), json.dumps(VALID)])
    await client.complete_json("diagnosis_agent", payload())
    repair = requests[1]["messages"][-1]["content"]
    assert "complete, substantive learner-facing content" in repair
    assert "Validation feedback:" not in repair