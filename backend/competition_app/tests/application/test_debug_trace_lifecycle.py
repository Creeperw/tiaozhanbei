import asyncio
import json
from unittest.mock import Mock

import pytest

from competition_app.application.personalized_review_card import (
    PersonalizedReviewCardUseCase, ReviewCardRequest, WorkflowResumeRequest,
)
from competition_app.runtime.debug_trace import (
    DebugTraceConfig, DebugTraceManager, current_debug_trace, record_debug_trace,
)


def use_case(tmp_path, *, enabled=True, run_ids=()):
    return PersonalizedReviewCardUseCase(
        orchestrator=Mock(), snapshot_exporter=Mock(),
        debug_trace_manager=DebugTraceManager(DebugTraceConfig(
            enabled=enabled, root=tmp_path, run_ids=run_ids,
        ), mode="stub"),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("resumed", [False, True])
@pytest.mark.parametrize("outcome", ["success", "failure", "cancelled"])
async def test_entrypoint_binds_and_closes_trace(tmp_path, monkeypatch, resumed, outcome):
    case = use_case(tmp_path)
    writers = []

    async def body(*args, **kwargs):
        writer = current_debug_trace()
        assert writer is not None
        writers.append(writer)
        assert record_debug_trace("structured_attempt", parsed_json={
            "focus_stage_id": "stage-1", "focus_books": ["中医学基础"],
        })
        if outcome == "failure":
            raise ValueError("identity mismatch")
        if outcome == "cancelled":
            raise asyncio.CancelledError()
        return "unchanged result"

    monkeypatch.setattr(case, "_resume_started_run" if resumed else "_execute_started_run", body)
    monkeypatch.setattr(case, "_record_run_failure", Mock())
    call = (case.resume("THREAD_debug_test", WorkflowResumeRequest(answer="继续"))
            if resumed else case.execute(ReviewCardRequest(
                thread_id="THREAD_debug_test", learner_id="L1", user_request="规划",
            )))
    if outcome == "success":
        assert await call == "unchanged result"
    else:
        with pytest.raises(ValueError if outcome == "failure" else asyncio.CancelledError):
            await call
    assert current_debug_trace() is None
    assert writers[0].closed
    records = [json.loads(line) for file in tmp_path.rglob("*.jsonl") for line in file.read_text().splitlines()]
    assert any(row.get("parsed_json", {}).get("focus_books") == ["中医学基础"] for row in records)
    assert records[-1]["record_type"] == "run_trace_closed"


@pytest.mark.asyncio
@pytest.mark.parametrize("enabled,allowed", [(False, ()), (True, ("OTHER_THREAD",))])
async def test_disabled_or_unselected_run_does_not_capture(tmp_path, monkeypatch, enabled, allowed):
    case = use_case(tmp_path, enabled=enabled, run_ids=allowed)

    async def body(**kwargs):
        assert current_debug_trace() is None
        assert not record_debug_trace("structured_attempt", parsed_json={"test": True})
        return "ok"

    monkeypatch.setattr(case, "_execute_started_run", body)
    assert await case.execute(ReviewCardRequest(thread_id="THREAD_test", learner_id="L", user_request="规划")) == "ok"
    assert not list(tmp_path.rglob("*.jsonl"))


@pytest.mark.asyncio
async def test_concurrent_selected_and_unselected_requests_are_isolated(tmp_path, monkeypatch):
    case = use_case(tmp_path, run_ids=("THREAD_selected",))
    both_started = asyncio.Event()
    count = 0

    async def body(**kwargs):
        nonlocal count
        count += 1
        if count == 2:
            both_started.set()
        await both_started.wait()
        selected = kwargs["thread_id"] == "THREAD_selected"
        assert (current_debug_trace() is not None) == selected
        assert record_debug_trace("selected_probe", learner_id=kwargs["request"].learner_id) == selected
        return kwargs["thread_id"]

    monkeypatch.setattr(case, "_execute_started_run", body)
    await asyncio.gather(*[
        case.execute(ReviewCardRequest(thread_id=thread, learner_id=thread, user_request="规划"))
        for thread in ("THREAD_selected", "THREAD_unselected")
    ])
    assert {file.parent.name for file in tmp_path.rglob("*.jsonl")} == {"THREAD_selected"}
    assert current_debug_trace() is None


@pytest.mark.asyncio
async def test_sink_open_failure_does_not_change_business_result(tmp_path, monkeypatch):
    case = use_case(tmp_path)
    monkeypatch.setattr(case.debug_trace_manager, "open", Mock(side_effect=OSError("disk failure")))

    async def body(**kwargs):
        assert current_debug_trace() is None
        return "ok"

    monkeypatch.setattr(case, "_execute_started_run", body)
    assert await case.execute(ReviewCardRequest(thread_id="THREAD_test", learner_id="L", user_request="规划")) == "ok"