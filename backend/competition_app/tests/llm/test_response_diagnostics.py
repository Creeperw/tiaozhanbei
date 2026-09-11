import asyncio
import json

import httpx
import pytest

from competition_app.agents.diagnosis import DiagnosisAgent
from competition_app.application.container import StreamingChatModel
from competition_app.contracts.agent_context import build_model_context
from competition_app.llm.openai_compatible import OpenAICompatibleChatModel
from competition_app.llm.prompt_skills import prompt_skill_registry
from competition_app.llm.response_diagnostics import PLAN_HEADINGS, safe_response_diagnostics
from competition_app.runtime.model_trace import ModelTraceRecorder


def payload():
    skill = prompt_skill_registry.load("diagnosis_agent", "learning_plan")
    return build_model_context(
        {"trace_id": "offline", "request_id": "offline", "learner_id": "offline",
         "task_type": "learning_plan", "user_request": "private learner request"},
        target_agent="diagnosis_agent", prompt_skill=skill,
        payload={"plan_scope": "long_term", "output_schema": DiagnosisAgent._planning_draft_schema("long_term")},
        permission_note="只输出当前长期层完整文档",
    )


def model(handler):
    return OpenAICompatibleChatModel("https://offline.invalid", "secret-test-key", "deepseek-v4-flash", transport=httpx.MockTransport(handler))


def encoded(document):
    return json.dumps({"plan_document": document, "selected_path_candidate_id": None}, ensure_ascii=False)


@pytest.mark.asyncio
@pytest.mark.parametrize("stream,reason,done", [(True, "stop", True), (True, "length", True), (True, None, False), (False, "length", False)])
async def test_metadata_distinguishes_endings_without_changing_response(stream, reason, done):
    document = "\n\n".join(f"## 【{h}】\n完整测试内容" for h in PLAN_HEADINGS)
    raw = encoded(document)
    requests = []

    def handler(request):
        requests.append(json.loads(request.content))
        if not stream:
            return httpx.Response(200, json={"choices": [{"message": {"content": raw}, "finish_reason": reason}], "usage": {"completion_tokens": 42}})
        events = [
            {"choices": [{"delta": {"content": raw[:10]}}]},
            {"choices": [{"delta": {"content": raw[10:]}, "finish_reason": reason}]},
            {"choices": [], "usage": {"prompt_tokens": 100, "completion_tokens": 42, "total_tokens": 142, "private": "secret"}},
        ]
        body = "".join("data: " + json.dumps(e) + "\n\n" for e in events)
        return httpx.Response(200, text=body + ("data: [DONE]\n\n" if done else ""))

    client = model(handler)
    recorder = ModelTraceRecorder()
    wrapper = StreamingChatModel(client, model_trace_recorder=recorder, stream=False)
    result = await wrapper.complete_json("diagnosis_agent", payload(), on_reasoning=(lambda _: None) if stream else None)
    assert result["plan_document"] == document
    details = recorder.items[0].response_diagnostics
    assert details["skill_in_system"] is True
    assert details["skill_version"] == "1.17.0"
    attempt = details["attempts"][0]
    assert all(attempt["required_headings_present"].values())
    assert attempt["finish_reason"] == reason if reason else "finish_reason" not in attempt
    assert attempt["content_chars"] == len(raw)
    assert attempt["completion_tokens"] == 42
    assert attempt["body_read_completed"] is True
    assert attempt["done_received"] is done if stream else "done_received" not in attempt
    assert attempt["response_format"] == "json_schema"
    assert attempt["stop_configured"] is False
    assert attempt["max_tokens_configured"] is False
    assert attempt["max_completion_tokens_configured"] is False
    assert all(recorder.items[0].raw_output["plan_document_headings_present"].values())
    assert "max_tokens" not in requests[0] and "stop" not in requests[0]
    assert recorder.items[0].raw_input is None
    assert "private" not in str(details) and "secret" not in str(details)


@pytest.mark.asyncio
async def test_empty_retry_preserves_each_attempt_and_resets_next_call():
    requests = []

    def handler(request):
        requests.append(json.loads(request.content))
        raw = "" if len(requests) == 1 else encoded("## 【最终目标】\n正文")
        return httpx.Response(200, json={"choices": [{"message": {"content": raw}, "finish_reason": "length" if not raw else "stop"}]})

    client = model(handler)
    await client.complete_json("diagnosis_agent", payload())
    attempts = client.last_timing_details["response_diagnostics"]["attempts"]
    assert [a["finish_reason"] for a in attempts] == ["length", "stop"]
    assert [a["thinking"] for a in attempts] == ["enabled", "disabled"]
    await client.complete_json("diagnosis_agent", payload())
    assert len(client.last_timing_details["response_diagnostics"]["attempts"]) == 1


@pytest.mark.asyncio
async def test_wrapper_failure_preserves_partial_stream_metadata():
    from competition_app.llm.openai_compatible import ModelResponseError

    def handler(request):
        return httpx.Response(200, text="data: " + json.dumps({"choices": [{"delta": {"content": "partial"}}]}) + "\n\ndata: invalid-json\n\n")

    recorder = ModelTraceRecorder()
    wrapper = StreamingChatModel(model(handler), model_trace_recorder=recorder, stream=False)
    with pytest.raises(ModelResponseError):
        await wrapper.complete_json("diagnosis_agent", payload(), on_reasoning=lambda _: None)
    attempt = recorder.items[0].response_diagnostics["attempts"][0]
    assert attempt["content_chars"] == 7
    assert attempt["done_received"] is False
    assert attempt["body_read_completed"] is False


@pytest.mark.asyncio
async def test_concurrent_diagnostics_are_isolated():
    async def handler(request):
        await asyncio.sleep(0)
        reason = "length" if "alpha" in request.content.decode() else "stop"
        return httpx.Response(200, json={"choices": [{"message": {"content": encoded("正文")}, "finish_reason": reason}]})

    client = model(handler)

    async def call(text):
        await client.complete_json("diagnosis_agent", {"user_request": text})
        return client.last_timing_details["response_diagnostics"]["attempts"]

    first, second = await asyncio.gather(call("alpha"), call("beta"))
    assert len(first) == len(second) == 1
    assert first[0]["finish_reason"] == "length"
    assert second[0]["finish_reason"] == "stop"


def test_untrusted_metadata_is_bounded_and_allowlisted():
    result = safe_response_diagnostics({
        "skill_id": "secret", "skill_version": "secret", "messages_digest": "secret",
        "attempts": [{"finish_reason": "secret", "completion_tokens": -1, "prompt_tokens": True,
                      "total_tokens": 100_000_001, "done_received": "secret", "body": "secret",
                      "attempts": [{"secret": "secret"}], "content_chars": 4}] * 100,
    })
    assert len(result["attempts"]) == 20
    assert result["attempts"][0] == {"content_chars": 4}
    assert "secret" not in str(result)