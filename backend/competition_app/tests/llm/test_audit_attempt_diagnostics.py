"""Offline HTTP evidence: observation must not alter compiler behaviour."""

import json
from copy import deepcopy

import httpx
import pytest
from pydantic import TypeAdapter

from competition_app.application.container import StreamingChatModel
from competition_app.contracts.audit_compilation import AuditFindingsCompilerResult
from competition_app.llm.openai_compatible import OpenAICompatibleChatModel, ModelResponseError
from competition_app.llm.response_diagnostics import safe_response_diagnostics
from competition_app.runtime.model_trace import ModelTraceRecorder


SCHEMA = TypeAdapter(AuditFindingsCompilerResult).json_schema()
GOOD = {"status": "compiled", "issues": [{
    "issue_type": "plan_contract_invalid", "message": "offline private finding",
    "blocking": True, "location_keys": ["plan:whole"],
    "source_anchors": [{"source_field": "findings", "source_quote": "offline private finding"}],
}]}
BAD = deepcopy(GOOD)
del BAD["issues"][0]["source_anchors"]


async def run_responses(values, *, observe=True, role="audit_findings_compiler", stream=False):
    requests = []

    def handler(request):
        requests.append(json.loads(request.content))
        value = values[len(requests) - 1]
        content = value if isinstance(value, str) else json.dumps(value)
        if stream:
            event = {"choices": [{"delta": {"content": content}, "finish_reason": "stop"}]}
            return httpx.Response(200, text="data: " + json.dumps(event) + "\n\ndata: [DONE]\n\n")
        return httpx.Response(200, json={"choices": [{"message": {"content": content}, "finish_reason": "stop"}]})

    model = OpenAICompatibleChatModel(
        "https://opencode.ai/zen/go/v1", "offline-key", "deepseek-v4-flash",
        transport=httpx.MockTransport(handler),
    )
    if not observe:
        model._finish_debug_attempt = lambda **kwargs: None
    recorder = ModelTraceRecorder()
    wrapper = StreamingChatModel(model, model_trace_recorder=recorder, stream=False)
    outcome = None
    try:
        outcome = await wrapper.complete_json(
            role, {"payload": {"output_schema": SCHEMA}},
            on_reasoning=(lambda _: None) if stream else None,
        )
    except ModelResponseError as error:
        outcome = error.reason
    return outcome, requests, recorder.items[0], model.last_error_details


@pytest.mark.asyncio
@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("first,reason", [
    (BAD, "business_schema_invalid"),
    ("not-json private text", "invalid_json"),
    ('{"a":1}\n{"b":2}', "ambiguous_json"),
])
async def test_first_failure_survives_success_without_changing_requests(first, reason, stream):
    observed = await run_responses([first, GOOD], stream=stream)
    baseline = await run_responses([first, GOOD], observe=False, stream=stream)
    assert observed[:2] == baseline[:2]
    assert observed[0] == GOOD
    assert len(observed[1]) == 2
    trace = observed[2]
    attempts = trace.response_diagnostics["structured_attempts"]
    assert attempts[0]["failure_reason"] == reason
    assert attempts[1] == {"attempt": 2, "status": "succeeded"}
    if reason == "business_schema_invalid":
        assert attempts[0]["validation_issues"] == [
            {"field_path": "/issues/*/source_anchors", "rule": "required"}
        ]
    assert "private" not in json.dumps(trace.response_diagnostics)
    assert trace.raw_input is None and trace.raw_output_text is None
    assert observed[3] is None


@pytest.mark.asyncio
async def test_exhaustion_preserves_both_failures_without_changing_error():
    observed = await run_responses([BAD, BAD])
    baseline = await run_responses([BAD, BAD], observe=False)
    assert observed[:2] == baseline[:2]
    assert observed[3] == baseline[3]
    assert observed[0] == "business_schema_invalid"
    assert [r["attempt"] for r in observed[2].response_diagnostics["structured_attempts"]] == [1, 2]


@pytest.mark.asyncio
async def test_other_roles_are_unchanged():
    result = await run_responses([GOOD], role="paper_audit_findings_compiler")
    assert "structured_attempts" not in result[2].response_diagnostics


def test_diagnostic_input_is_bounded_and_does_not_copy_values():
    item = {"attempt": 1, "status": "validation_failed", "failure_reason": "business_schema_invalid",
            "response": "private", "reasoning": "private", "validation_issues": [
                {"field_path": "/issues/*/source_anchors", "rule": "required", "message": "private"},
                {"field_path": "/敏感信息", "rule": "required"},
            ]}
    safe = safe_response_diagnostics({"structured_attempts": [item] * 100})
    assert len(safe["structured_attempts"]) == 2
    assert "private" not in json.dumps(safe) and "敏感" not in json.dumps(safe, ensure_ascii=False)
    assert len(safe["structured_attempts"][0]["validation_issues"]) == 1
    assert safe_response_diagnostics({"structured_attempts": [{"attempt": True, "status": "succeeded"}]}) == {"structured_attempts": []}