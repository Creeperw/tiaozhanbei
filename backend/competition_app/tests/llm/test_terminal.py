import asyncio

import pytest

from competition_app.application.container import StreamingChatModel
from competition_app.runtime.event_stream import bind_event_sink, reset_event_sink
from competition_app.runtime.model_trace import ModelTraceRecorder


class DelayedModel:
    def __init__(self) -> None:
        self.started = 0
        self.both_started = asyncio.Event()

    async def complete_json(self, role, payload, on_delta=None, on_reasoning=None):
        self.started += 1
        if self.started == 2:
            self.both_started.set()
        on_delta(f"{role}:start")
        await asyncio.wait_for(self.both_started.wait(), timeout=0.2)
        on_delta(f"{role}:end")
        return {"role": role}


@pytest.mark.asyncio
async def test_streaming_wrapper_does_not_serialize_parallel_model_calls(capsys) -> None:
    inner = DelayedModel()
    model = StreamingChatModel(inner)

    await asyncio.gather(
        model.complete_json("memory_agent", {}),
        model.complete_json("knowledge_base_agent", {}),
    )

    assert inner.started == 2
    output = capsys.readouterr().out
    assert output.index(">>> memory_agent") < output.index("<<< memory_agent")
    assert output.index(">>> knowledge_base_agent") < output.index(
        "<<< knowledge_base_agent"
    )


class ProseModel:
    async def complete_text(self, role, payload, on_delta=None, on_reasoning=None):
        for delta in ("第一段", "第二段"):
            if on_delta:
                on_delta(delta)
        return "第一段第二段"


@pytest.mark.asyncio
async def test_prose_model_emits_genuine_business_text_deltas() -> None:
    events = []
    token = bind_event_sink(events.append)
    try:
        result = await StreamingChatModel(ProseModel()).complete_text(
            "expert_agent", {"workflow_step_id": "expert"}
        )
    finally:
        reset_event_sink(token)

    assert result == "第一段第二段"
    assert [event["event"] for event in events if event["event"].startswith("business_text_")] == [
        "business_text_started",
        "business_text_delta",
        "business_text_delta",
        "business_text_completed",
    ]
    assert "".join(
        event.get("delta", "")
        for event in events
        if event["event"] == "business_text_delta"
    ) == result


class StructuredModel:
    def __init__(self) -> None:
        self.received_callback = False

    async def complete_json(self, role, payload, on_delta=None, on_reasoning=None):
        self.received_callback = on_delta is not None
        return {"status": "ok"}


@pytest.mark.asyncio
async def test_sse_sink_does_not_force_structured_json_transport_streaming() -> None:
    inner = StructuredModel()
    token = bind_event_sink(lambda _event: None)
    try:
        assert await StreamingChatModel(inner, stream=False).complete_json(
            "planner_agent", {"workflow_step_id": "planner"}
        ) == {"status": "ok"}
    finally:
        reset_event_sink(token)

    assert inner.received_callback is False


class FailingStructuredModel:
    last_error_details = {
        "retry_count": 2,
        "transport_stage": "connect",
        "url": "https://provider.invalid/chat/completions",
        "authorization": "Bearer secret",
    }

    async def complete_json(self, role, payload, on_delta=None, on_reasoning=None):
        error = RuntimeError("Chat model request failed: ConnectError")
        error.reason = "transport_error"
        error.status_code = None
        raise error


@pytest.mark.asyncio
async def test_streaming_wrapper_records_safe_failure_diagnostics() -> None:
    events = []
    recorder = ModelTraceRecorder()
    token = bind_event_sink(events.append)
    try:
        with pytest.raises(RuntimeError):
            await StreamingChatModel(
                FailingStructuredModel(),
                model_trace_recorder=recorder,
                stream=False,
            ).complete_json("planner_agent", {"workflow_step_id": "planner"})
    finally:
        reset_event_sink(token)

    item = recorder.items[0]
    assert item.error_reason == "transport_error"
    assert item.error_retry_count == 2
    assert item.error_transport_stage == "connect"
    assert "secret" not in str(item.model_dump(mode="json"))
    failure_events = [event for event in events if event["event"] == "model_failed"]
    assert len(failure_events) == 1
    assert "provider.invalid" not in str(failure_events[0])
    assert "secret" not in str(failure_events[0])
