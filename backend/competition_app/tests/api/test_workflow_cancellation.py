from __future__ import annotations

import asyncio
import json
import threading
from pathlib import Path
from typing import Any, Callable

from fastapi import Request
from fastapi.responses import StreamingResponse
from fastapi.testclient import TestClient

from competition_app.api.app import create_app
from competition_app.application.container import ApplicationContainer
from competition_app.application.personalized_review_card import (
    ReviewCardRequest,
    ReviewCardResult,
)
from competition_app.config import Settings
from competition_app.runtime.orchestrator import ExecutionResult


_TERMINAL_EVENTS = {
    "run_completed",
    "run_failed",
    "run_interrupted",
    "run_waiting_human_review",
    "run_cancelled",
}


def _success_result(message: str = "这是一条已审核的回答。") -> ReviewCardResult:
    return ReviewCardResult(
        status="success",
        execution_id="EXE_API_CANCEL_TEST",
        task_type="casual_conversation",
        direct_response=message,
        agent_outputs=[],
        snapshot_path=Path("snapshot.json"),
        writeback_intents=[],
    )


def _route(app: Any, path: str, method: str):
    return next(
        route
        for route in app.routes
        if getattr(route, "path", None) == path
        and method in (getattr(route, "methods", None) or set())
    )


def _request(app: Any, method: str, path: str) -> Request:
    request = Request(
        {
            "type": "http",
            "method": method,
            "path": path,
            "headers": [],
            "app": app,
        }
    )
    request.state.current_user = None
    return request


def _parse_sse_lines(lines: list[str]) -> list[dict[str, Any]]:
    return [
        json.loads(line[6:])
        for line in lines
        if line.startswith("data: ")
    ]


def _collect_stream(
    app: Any,
    payload: dict[str, Any],
    result: dict[str, Any],
    on_event: Callable[[dict[str, Any]], None] | None = None,
) -> None:
    client = TestClient(app)
    try:
        with client.stream(
            "POST",
            "/api/v1/review-cards/stream",
            json=payload,
        ) as response:
            result["status_code"] = response.status_code
            events: list[dict[str, Any]] = []
            for line in response.iter_lines():
                if not line.startswith("data: "):
                    continue
                event = json.loads(line[6:])
                events.append(event)
                if on_event is not None:
                    on_event(event)
            result["events"] = events
    except BaseException as exc:  # pragma: no cover - reported by the caller
        result["error"] = exc
    finally:
        client.close()


def _stream_payload(
    thread_id: str,
    learner_id: str,
    user_request: str = "请回答一个问题",
) -> dict[str, Any]:
    return {
        "thread_id": thread_id,
        "learner_id": learner_id,
        "user_request": user_request,
    }


def _assert_one_terminal_event(events: list[dict[str, Any]], expected: str) -> None:
    terminals = [event for event in events if event.get("event") in _TERMINAL_EVENTS]
    assert [event["event"] for event in terminals] == [expected]
    assert events[-1]["event"] == expected


def test_cancel_before_worker_registration_is_persisted_and_emits_cancelled(
    tmp_path: Path,
) -> None:
    """A cancellation between route registration and event-source startup wins."""

    container = ApplicationContainer.build(Settings(mode="stub"), snapshot_root=tmp_path)
    app = create_app(container, auth_required=False)
    thread_id = "THREAD_CANCEL_BEFORE_WORKER_001"
    stream_route = _route(app, "/api/v1/review-cards/stream", "POST")
    cancel_route = _route(
        app,
        "/api/v1/review-cards/runs/{thread_id}/cancel",
        "POST",
    )
    request = ReviewCardRequest(
        thread_id=thread_id,
        learner_id="L_CANCEL_BEFORE_WORKER",
        user_request="请回答一个问题",
    )

    async def scenario() -> tuple[dict[str, Any], list[dict[str, Any]]]:
        response = await stream_route.endpoint(
            request,
            _request(app, "POST", "/api/v1/review-cards/stream"),
        )
        assert isinstance(response, StreamingResponse)
        cancellation = await cancel_route.endpoint(
            thread_id,
            _request(app, "POST", f"/api/v1/review-cards/runs/{thread_id}/cancel"),
        )
        chunks: list[str] = []
        async for chunk in response.body_iterator:
            chunks.append(chunk.decode() if isinstance(chunk, bytes) else chunk)
        return cancellation, _parse_sse_lines("".join(chunks).splitlines())

    cancellation, events = asyncio.run(scenario())

    assert cancellation == {
        "status": "cancellation_requested",
        "thread_id": thread_id,
    }
    _assert_one_terminal_event(events, "run_cancelled")
    assert not {event["event"] for event in events} & {
        "answer_started",
        "answer_delta",
        "answer_committed",
        "run_completed",
        "run_failed",
    }
    state = container.review_card_use_case.get_run_state(thread_id)
    assert state is not None
    assert state["status"] == "cancelled"


def test_cancel_interrupts_registered_worker_and_does_not_report_success(
    tmp_path: Path,
) -> None:
    container = ApplicationContainer.build(Settings(mode="stub"), snapshot_root=tmp_path)
    use_case = container.review_card_use_case
    started = threading.Event()

    async def blocked_execute(_request: ReviewCardRequest) -> ReviewCardResult:
        started.set()
        never = asyncio.Event()
        try:
            await asyncio.wait_for(never.wait(), timeout=8)
        except asyncio.TimeoutError as exc:  # pragma: no cover - safety timeout
            raise AssertionError("worker was not cancelled") from exc
        raise AssertionError("unreachable")

    use_case.execute = blocked_execute
    app = create_app(container, auth_required=False)
    thread_id = "THREAD_CANCEL_WORKER_001"
    result: dict[str, Any] = {}
    stream_thread = threading.Thread(
        target=_collect_stream,
        args=(
            app,
            _stream_payload(thread_id, "L_CANCEL_WORKER"),
            result,
        ),
        daemon=True,
    )
    stream_thread.start()

    assert started.wait(5), "workflow worker did not start"
    cancel_client = TestClient(app)
    try:
        cancellation = cancel_client.post(
            f"/api/v1/review-cards/runs/{thread_id}/cancel"
        )
        assert cancellation.status_code == 200
        assert cancellation.json()["status"] == "cancellation_requested"
    finally:
        cancel_client.close()

    stream_thread.join(10)
    assert not stream_thread.is_alive()
    assert "error" not in result
    assert result["status_code"] == 200
    events = result["events"]
    _assert_one_terminal_event(events, "run_cancelled")
    assert not {event["event"] for event in events} & {
        "answer_started",
        "answer_delta",
        "answer_committed",
        "run_completed",
        "run_failed",
    }
    assert events[-1]["assistant_message"] == "已停止生成。"
    state = use_case.get_run_state(thread_id)
    assert state is not None
    assert state["status"] == "cancelled"


def test_cancel_after_worker_return_before_answer_start_is_authoritative(
    tmp_path: Path,
) -> None:
    """The post-worker state barrier prevents a formal answer after cancellation."""

    container = ApplicationContainer.build(Settings(mode="stub"), snapshot_root=tmp_path)
    use_case = container.review_card_use_case
    worker_returned = threading.Event()
    cancellation_injected = threading.Event()
    original_get_run_state = use_case.get_run_state

    async def fast_execute(_request: ReviewCardRequest) -> ReviewCardResult:
        worker_returned.set()
        return _success_result()

    def cancel_at_post_worker_read(thread_id: str):
        state = original_get_run_state(thread_id)
        if worker_returned.is_set() and not cancellation_injected.is_set():
            cancellation_injected.set()
            use_case.request_run_cancellation(thread_id)
        return state

    use_case.execute = fast_execute
    use_case.get_run_state = cancel_at_post_worker_read
    app = create_app(container, auth_required=False)
    result: dict[str, Any] = {}
    _collect_stream(
        app,
        _stream_payload(
            "THREAD_CANCEL_AFTER_RETURN_001",
            "L_CANCEL_AFTER_RETURN",
        ),
        result,
    )

    assert "error" not in result
    events = result["events"]
    assert cancellation_injected.is_set()
    _assert_one_terminal_event(events, "run_cancelled")
    assert "answer_started" not in {event["event"] for event in events}
    assert "assistant_message" not in events[-1] or events[-1]["assistant_message"] == "已停止生成。"
    state = use_case.get_run_state("THREAD_CANCEL_AFTER_RETURN_001")
    assert state is not None
    assert state["status"] == "cancelled"


def test_cancel_during_answer_deltas_stops_before_terminal_success(
    tmp_path: Path,
) -> None:
    container = ApplicationContainer.build(Settings(mode="stub"), snapshot_root=tmp_path)
    use_case = container.review_card_use_case

    async def fast_execute(_request: ReviewCardRequest) -> ReviewCardResult:
        return _success_result("已审核回答。" * 3000)

    use_case.execute = fast_execute
    app = create_app(container, auth_required=False)
    stream_route = _route(app, "/api/v1/review-cards/stream", "POST")
    cancel_route = _route(
        app,
        "/api/v1/review-cards/runs/{thread_id}/cancel",
        "POST",
    )

    async def scenario() -> tuple[dict[str, Any], list[dict[str, Any]]]:
        thread_id = "THREAD_CANCEL_ANSWER_001"
        response = await stream_route.endpoint(
            ReviewCardRequest.model_validate(_stream_payload(thread_id, "L_CANCEL_ANSWER")),
            _request(app, "POST", "/api/v1/review-cards/stream"),
        )
        assert isinstance(response, StreamingResponse)
        events: list[dict[str, Any]] = []
        cancellation: dict[str, Any] | None = None
        async for chunk in response.body_iterator:
            text = chunk.decode() if isinstance(chunk, bytes) else chunk
            current = _parse_sse_lines(text.splitlines())
            events.extend(current)
            if cancellation is None and any(
                event.get("event") == "answer_delta" for event in current
            ):
                cancellation = await cancel_route.endpoint(
                    thread_id,
                    _request(
                        app,
                        "POST",
                        f"/api/v1/review-cards/runs/{thread_id}/cancel",
                    ),
                )
        assert cancellation is not None
        return cancellation, events

    cancellation, events = asyncio.run(scenario())
    assert cancellation["status"] == "cancellation_requested"
    _assert_one_terminal_event(events, "run_cancelled")
    assert "run_completed" not in {event["event"] for event in events}
    assert "run_failed" not in {event["event"] for event in events}
    assert "answer_committed" not in {event["event"] for event in events}
    assert events[-1].get("assistant_message") == "已停止生成。"
    state = use_case.get_run_state("THREAD_CANCEL_ANSWER_001")
    assert state is not None
    assert state["status"] == "cancelled"


def test_cancel_after_completed_run_keeps_completed_terminal_state(
    tmp_path: Path,
) -> None:
    container = ApplicationContainer.build(Settings(mode="stub"), snapshot_root=tmp_path)
    use_case = container.review_card_use_case

    async def fast_execute(_request: ReviewCardRequest) -> ReviewCardResult:
        return _success_result()

    use_case.execute = fast_execute
    app = create_app(container, auth_required=False)
    client = TestClient(app)
    with client.stream(
        "POST",
        "/api/v1/review-cards/stream",
        json=_stream_payload(
            "THREAD_COMPLETED_BEFORE_CANCEL_001",
            "L_COMPLETED_BEFORE_CANCEL",
        ),
    ) as response:
        events = _parse_sse_lines(
            list(response.iter_lines())
        )

    _assert_one_terminal_event(events, "run_completed")
    use_case._remember_run(
        "THREAD_COMPLETED_BEFORE_CANCEL_001",
        {"status": "completed", "result": _success_result()},
    )
    cancellation = client.post(
        "/api/v1/review-cards/runs/THREAD_COMPLETED_BEFORE_CANCEL_001/cancel"
    )
    assert cancellation.status_code == 200
    assert cancellation.json()["status"] == "completed"
    assert use_case.get_run_state("THREAD_COMPLETED_BEFORE_CANCEL_001")["status"] == "completed"


def test_execution_result_failed_is_emitted_as_run_failed_not_completed(
    tmp_path: Path,
) -> None:
    container = ApplicationContainer.build(Settings(mode="stub"), snapshot_root=tmp_path)

    async def failed_execute(_request: ReviewCardRequest) -> ExecutionResult:
        return ExecutionResult(
            status="failed",
            error_type="TestFailure",
            error_message="simulated workflow failure",
        )

    container.review_card_use_case.execute = failed_execute
    app = create_app(container, auth_required=False)
    result: dict[str, Any] = {}
    _collect_stream(
        app,
        _stream_payload("THREAD_RESULT_FAILED_001", "L_RESULT_FAILED"),
        result,
    )

    assert "error" not in result
    events = result["events"]
    _assert_one_terminal_event(events, "run_failed")
    assert "run_completed" not in {event["event"] for event in events}
    assert container.review_card_use_case.get_run_state(
        "THREAD_RESULT_FAILED_001"
    )["status"] == "failed"