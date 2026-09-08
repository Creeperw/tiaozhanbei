import asyncio

import pytest

from competition_app.runtime.model_trace import ModelTraceRecorder


def test_planning_failure_evidence_is_bounded_without_prompt_or_reasoning():
    from competition_app.application.personalized_review_card import PersonalizedReviewCardUseCase

    recorder = ModelTraceRecorder()
    index = recorder.begin("diagnosis_agent", {"user_request": "private prompt"})
    recorder.succeed(index, {"plan_document": "规划正文" * 4000, "reasoning": "private reasoning"})
    item = recorder.items[0]
    summary = PersonalizedReviewCardUseCase._failure_model_output_summary(item)
    assert len(summary["plan_document_excerpt"]) == 12000
    assert summary["plan_document_chars"] == 16000
    assert summary["plan_document_truncated"] is True
    assert item.raw_input is None
    assert "private" not in str(summary)


def test_model_trace_defaults_to_digest_only_without_raw_payloads() -> None:
    recorder = ModelTraceRecorder()
    index = recorder.begin("diagnosis_agent", {"api_key": "secret", "topic": "感冒"})
    recorder.succeed(index, {"summary": "学习建议", "authorization": "Bearer secret"})

    item = recorder.items[0]
    assert item.sequence == 1
    assert item.raw_input is None
    assert item.raw_output is None
    assert item.input_chars > 0
    assert len(item.input_digest or "") == 64
    assert item.output_chars > 0
    assert len(item.output_digest or "") == 64


def test_model_trace_full_capture_is_explicit_and_secret_safe() -> None:
    recorder = ModelTraceRecorder()
    with recorder.capture_full():
        index = recorder.begin(
            "diagnosis_agent", {"api_key": "secret", "topic": "感冒"}
        )
        recorder.succeed(
            index, {"summary": "学习建议", "authorization": "Bearer secret"}
        )

    item = recorder.items[0]
    assert item.full_capture is True
    assert item.raw_input["api_key"] == "[REDACTED]"
    assert item.raw_output["authorization"] == "[REDACTED]"


def test_model_trace_reset_removes_previous_request_data() -> None:
    recorder = ModelTraceRecorder()
    recorder.begin("planner_agent", {"topic": "旧请求"})

    recorder.reset()

    assert recorder.items == []


def test_model_trace_records_transport_text_separately_from_parsed_json() -> None:
    recorder = ModelTraceRecorder(full_capture=True)
    index = recorder.begin("expert_agent", {"topic": "四君子汤"})
    recorder.record_transport(
        index,
        request_payload={
            "url": "https://example.test/v1/chat/completions",
            "body": {"messages": [{"role": "user", "content": "真实输入"}]},
        },
        response_text='  {"learning_tip":"真实原文"}\n',
        reasoning_text="先核对证据",
        timing_details={
            "queue_wait_ms": 12,
            "provider_duration_ms": 34,
            "request_attempt_count": 1,
            "last_reasoning_at_monotonic": 123.5,
            "reasoning_delta_count": 4,
            "response_chars": 25,
        },
    )
    recorder.succeed(index, {"learning_tip": "真实原文"})

    item = recorder.items[0]
    assert item.transport_input["body"]["messages"][0]["content"] == "真实输入"
    assert item.raw_output_text == '  {"learning_tip":"真实原文"}\n'
    assert item.reasoning_text == "先核对证据"
    assert item.raw_output == {"learning_tip": "真实原文"}
    assert item.queue_wait_ms == 12
    assert item.provider_duration_ms == 34
    assert item.request_attempt_count == 1
    assert item.last_reasoning_at_monotonic == 123.5
    assert item.reasoning_delta_count == 4
    assert item.response_chars == 25


def test_model_trace_failure_keeps_only_bounded_transport_diagnostics() -> None:
    recorder = ModelTraceRecorder()
    index = recorder.begin("planner_agent", {"topic": "教材证据"})
    error = RuntimeError("Chat model request failed: ConnectError")
    error.reason = "transport_error"
    error.status_code = 200
    error.last_error_details = {
        "retry_count": 2,
        "transport_stage": "connect",
        "url": "https://provider.invalid/chat/completions",
        "authorization": "Bearer secret",
        "response_body": "private provider response",
    }
    error.last_timing_details = {
        "queue_wait_ms": 12,
        "provider_duration_ms": 345,
        "request_attempt_count": 3,
        "last_reasoning_at_monotonic": 456.75,
        "reasoning_delta_count": 9,
        "response_chars": 17,
    }

    recorder.fail(index, error)

    item = recorder.items[0]
    assert item.error_type == "RuntimeError"
    assert item.error_reason == "transport_error"
    assert item.error_status_code == 200
    assert item.error_retry_count == 2
    assert item.error_transport_stage == "connect"
    assert item.queue_wait_ms == 12
    assert item.provider_duration_ms == 345
    assert item.request_attempt_count == 3
    assert item.last_reasoning_at_monotonic == 456.75
    assert item.reasoning_delta_count == 9
    assert item.response_chars == 17
    assert item.model_dump(mode="json").get("url") is None
    assert "secret" not in str(item.model_dump(mode="json"))


def test_model_trace_failure_rejects_unbounded_diagnostic_values() -> None:
    recorder = ModelTraceRecorder()
    index = recorder.begin("planner_agent", {"topic": "教材证据"})
    error = RuntimeError("transport failed")
    error.reason = "transport_error; https://provider.invalid"
    error.last_error_details = {
        "retry_count": 999999,
        "transport_stage": "https://provider.invalid",
    }

    recorder.fail(index, error)

    item = recorder.items[0]
    assert item.error_reason is None
    assert item.error_retry_count is None
    assert item.error_transport_stage is None


@pytest.mark.asyncio
async def test_model_trace_isolated_between_concurrent_tasks() -> None:
    recorder = ModelTraceRecorder(full_capture=True)
    ready = asyncio.Event()
    release = asyncio.Event()

    async def first_request():
        recorder.reset()
        index = recorder.begin("planner_agent", {"topic": "请求A"})
        ready.set()
        await release.wait()
        recorder.succeed(index, {"result": "A"})
        return recorder.items

    async def second_request():
        await ready.wait()
        recorder.reset()
        index = recorder.begin("planner_agent", {"topic": "请求B"})
        recorder.succeed(index, {"result": "B"})
        release.set()
        return recorder.items

    first, second = await asyncio.gather(first_request(), second_request())

    assert first[0].raw_input["topic"] == "请求A"
    assert first[0].raw_output == {"result": "A"}
    assert second[0].raw_input["topic"] == "请求B"
    assert second[0].raw_output == {"result": "B"}


@pytest.mark.asyncio
async def test_full_capture_scope_does_not_leak_to_concurrent_request() -> None:
    recorder = ModelTraceRecorder()
    ready = asyncio.Event()
    release = asyncio.Event()

    async def internal_evaluation():
        recorder.reset()
        with recorder.capture_full():
            index = recorder.begin("expert_agent", {"topic": "评测输入"})
            ready.set()
            await release.wait()
            recorder.succeed(index, {"result": "评测输出"})
            return recorder.items

    async def learner_request():
        await ready.wait()
        recorder.reset()
        index = recorder.begin("planner_agent", {"topic": "学习者输入"})
        recorder.succeed(index, {"result": "学习者输出"})
        release.set()
        return recorder.items

    evaluation_items, learner_items = await asyncio.gather(
        internal_evaluation(), learner_request()
    )

    assert evaluation_items[0].raw_input["topic"] == "评测输入"
    assert learner_items[0].raw_input is None
    assert learner_items[0].raw_output is None
