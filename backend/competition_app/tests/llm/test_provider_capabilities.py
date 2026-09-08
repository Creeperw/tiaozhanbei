import json

import httpx
import pytest

from competition_app.llm.openai_compatible import ModelResponseError, OpenAICompatibleChatModel
from competition_app.llm.provider_capabilities import structured_output_mode
from competition_app.llm.response_diagnostics import provider_error_diagnostics, safe_response_diagnostics

URL = "https://opencode.ai/zen/go/v1"
SCHEMA = {"type": "object", "properties": {"plan_document": {"type": "string", "minLength": 1}},
          "required": ["plan_document"], "additionalProperties": False}


@pytest.mark.parametrize("url,model,expected", [
    (URL, "deepseek-v4-flash", "json_object"),
    (URL + "/", "deepseek-v4-flash", "json_object"),
    ("https://opencode.ai:443/zen/go/v1", "deepseek-v4-flash", "json_object"),
    ("https://opencode.ai/zen/v1", "deepseek-v4-flash", "json_schema"),
    (URL, "deepseek-v4-pro", "json_schema"),
    ("https://other.invalid/zen/go/v1", "deepseek-v4-flash", "json_schema"),
    ("https://opencode.ai.evil.invalid/zen/go/v1", "deepseek-v4-flash", "json_schema"),
])
def test_exact_provider_model_scope(url, model, expected):
    assert structured_output_mode(url, model) == expected


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["diagnosis_agent", "planner_agent", "memory_agent", "plan_contract_compiler"])
@pytest.mark.parametrize("stream", [True, False])
async def test_all_roles_use_supported_mode_without_schema_retry(role, stream):
    requests = []
    def handler(request):
        requests.append(json.loads(request.content))
        raw = '{"plan_document":"complete"}'
        if stream:
            return httpx.Response(200, text='data: ' + json.dumps({"choices": [{"delta": {"content": raw}, "finish_reason": "stop"}]}) + '\n\ndata: [DONE]\n\n')
        return httpx.Response(200, json={"choices": [{"message": {"content": raw}}]})
    client = OpenAICompatibleChatModel(URL, "secret", "deepseek-v4-flash", transport=httpx.MockTransport(handler))
    for _ in range(2):
        result = await client.complete_json(role, {"payload": {"output_schema": SCHEMA}}, on_delta=(lambda _: None) if stream else None)
        assert result["plan_document"] == "complete"
    assert len(requests) == 2
    assert all(b["response_format"] == {"type": "json_object"} for b in requests)


@pytest.mark.asyncio
@pytest.mark.parametrize("raw", ['{}', '{"plan_document":12}', '{"plan_document":""}', '{"plan_document":"ok","extra":true}'])
async def test_local_schema_still_rejects_invalid_output(raw):
    requests = []
    def handler(request):
        requests.append(request)
        return httpx.Response(200, json={"choices": [{"message": {"content": raw}}]})
    client = OpenAICompatibleChatModel(URL, "secret", "deepseek-v4-flash", transport=httpx.MockTransport(handler))
    with pytest.raises(ModelResponseError) as error:
        await client.complete_json("diagnosis_agent", {"payload": {"output_schema": SCHEMA}})
    assert error.value.reason == "business_schema_invalid"
    assert len(requests) == 2


@pytest.mark.asyncio
async def test_json_object_400_is_not_retried_as_schema_fallback():
    requests = []
    message = "Error from provider (Console Go): Upstream request failed: [invalid_request_error] This response_format type is unavailable now"
    def handler(request):
        requests.append(request)
        return httpx.Response(400, json={"error": {"type": "invalid_request_error", "message": message}})
    client = OpenAICompatibleChatModel(URL, "secret", "deepseek-v4-flash", transport=httpx.MockTransport(handler))
    with pytest.raises(ModelResponseError):
        await client.complete_json("diagnosis_agent", {"payload": {"output_schema": SCHEMA}})
    assert len(requests) == 1
    diag = client.last_timing_details["response_diagnostics"]["attempts"][0]
    assert diag["provider_error_message"] == message
    assert diag["provider_error_type"] == "invalid_request_error"


def test_error_persistence_rejects_unknown_prose_and_secret_fields():
    raw = json.dumps({"error": {"type": "invalid_request_error", "code": "secret", "message": "private learner secret-key", "param": "private"}}).encode()
    diag = safe_response_diagnostics(provider_error_diagnostics(raw))
    assert diag["provider_error_type"] == "invalid_request_error"
    assert "provider_error_digest" in diag
    assert "secret" not in str(diag) and "private" not in str(diag)
    assert not safe_response_diagnostics({"provider_error_message": "private secret-key"})