from __future__ import annotations

import asyncio
from dataclasses import replace
import json
from pathlib import Path

import pytest

from competition_app.evaluation.d1_ab100_v2_runner import D1AB100V2Runner
from competition_app.evaluation.d1_v2_batch import D1V2BatchService
from competition_app.evaluation.d1_v2_registry import load_registered_d1_v2
from competition_app.evaluation.d1_v2_registry import (
    V2_FORMAL_EVIDENCE_SAMPLE_CASE_IDS,
)
from competition_app.runtime.model_trace import ModelTraceRecorder


def _valid_result(
    case_id: str,
    *,
    pair_order: str = "AB",
    expected_exposure: bool = True,
) -> dict:
    return {
        "case_id": case_id,
        "pair_order": pair_order,
        "formal_environment_write_allowed": False,
        "retrieval_pack_digest": "a" * 64,
        "validation": {"valid": True, "repair_contract_valid": True},
        "receipt": {
            "arms": [
                {
                    "arm": "A",
                    "input_digest": "b" * 64,
                    "rule_exposed": False,
                    "repair_count": 0,
                    "repair_attempt_count": 0,
                    "repair_exhausted": False,
                    "max_repair_attempts": 2,
                },
                {
                    "arm": "B",
                    "input_digest": "b" * 64,
                    "rule_exposed": expected_exposure,
                    "repair_count": 0,
                    "repair_attempt_count": 0,
                    "repair_exhausted": False,
                    "max_repair_attempts": 2,
                },
            ]
        },
    }


def _service(
    execute_pair,
    *,
    reviewed: bool,
    runtime_mode: str = "live",
    state_root: Path | None = None,
    model_trace_recorder: ModelTraceRecorder | None = None,
) -> D1V2BatchService:
    registry, cases = load_registered_d1_v2()
    return D1V2BatchService(
        registry=replace(
            registry,
            human_review_complete=reviewed,
            human_review_passed=reviewed,
            human_review_submitted_by=("reviewer-user" if reviewed else None),
            human_review_submitter_ids=(
                ("reviewer-user",) if reviewed else ()
            ),
        ),
        runner=D1AB100V2Runner(cases=cases),
        execute_pair=execute_pair,
        runtime_mode=runtime_mode,
        state_root=state_root,
        model_trace_recorder=model_trace_recorder,
    )


def _valid_result_for(service: D1V2BatchService, case_id: str) -> dict:
    case = next(item for item in service.runner.cases if item.case_id == case_id)
    return _valid_result(
        case_id,
        pair_order=case.pair_order,
        expected_exposure=bool(case.frozen_evidence.expected_rule_exposure),
    )


async def _complete_single(
    service: D1V2BatchService,
    *,
    requested_by: str = "admin",
    learner_id: str = "learner-1",
) -> str:
    started = service.start(
        requested_by=requested_by,
        learner_id=learner_id,
        case_ids=["EVO-D1-V2-001"],
        purpose="single_trace",
    )
    await service._tasks[started["run_id"]]
    assert service.status(started["run_id"])["status"] == "completed"
    return str(started["run_id"])


async def _complete_formal(
    service: D1V2BatchService,
    *,
    requested_by: str = "admin",
    learner_id: str = "learner-1",
) -> str:
    single_run_id = await _complete_single(
        service,
        requested_by=requested_by,
        learner_id=learner_id,
    )
    precheck = service.start(
        requested_by=requested_by,
        learner_id=learner_id,
        case_ids=list(service.registry.human_review_sample_case_ids),
        purpose="precheck",
        prerequisite_run_id=single_run_id,
    )
    await service._tasks[precheck["run_id"]]
    service.confirm_precheck(
        precheck["run_id"],
        requested_by=requested_by,
        decision="go",
        note="技术执行、固定输入与回执合同均已人工核对。",
    )
    formal = service.start(
        requested_by=requested_by,
        learner_id=learner_id,
        case_ids=[case.case_id for case in service.runner.cases],
        purpose="formal",
        prerequisite_run_id=precheck["run_id"],
    )
    await service._tasks[formal["run_id"]]
    assert service.status(formal["run_id"])["status"] == "completed"
    return str(formal["run_id"])


def _formal_reviews(service: D1V2BatchService) -> list[dict[str, str]]:
    by_id = {case.case_id: case for case in service.runner.cases}
    return [
        {
            "case_id": case_id,
            "reviewer_relation": str(
                by_id[case_id].frozen_evidence.gold_relation
            ),
            "reviewer_name": "independent-formal-reviewer",
            "review_notes": "已独立核对主张和两条冻结证据。",
        }
        for case_id in V2_FORMAL_EVIDENCE_SAMPLE_CASE_IDS
    ]


def test_v2_manifest_is_server_owned_and_review_pending():
    async def unused(_case_id, _learner_id):
        raise AssertionError("execution must remain blocked")

    service = _service(unused, reviewed=False)
    manifest = service.manifest_payload()

    assert manifest["file_sha256"] == (
        "bbc322a6f25c69666fac760ecaef5c46025342ded3d6e29ebac40981b131ceaf"
    )
    assert manifest["case_count"] == 100
    assert manifest["human_review_complete"] is False
    assert manifest["human_review_required_count"] == 10
    assert manifest["execution_allowed"] is False
    assert manifest["formal_rule_status_change_allowed"] is False
    with pytest.raises(ValueError, match="human review"):
        service.start(
            requested_by="admin",
            learner_id="learner",
            case_ids=["EVO-D1-V2-001"],
            purpose="single_trace",
        )


def test_v2_execution_requires_live_mode_and_fixed_precheck_sample():
    async def unused(_case_id, _learner_id):
        return _valid_result(_case_id)

    stub_service = _service(unused, reviewed=True, runtime_mode="stub")
    sample_ids = list(stub_service.registry.human_review_sample_case_ids)
    with pytest.raises(ValueError, match="COMPETITION_APP_MODE=live"):
        stub_service.start(
            requested_by="admin",
            learner_id="learner",
            case_ids=[sample_ids[0]],
            purpose="single_trace",
        )

    live_service = _service(unused, reviewed=True)
    selected = live_service._validate_selection(sample_ids, purpose="precheck")
    assert selected.case_ids == tuple(sample_ids)
    wrong_order = list(reversed(sample_ids))
    with pytest.raises(ValueError, match="fixed human-review sample order"):
        live_service._validate_selection(wrong_order, purpose="precheck")


def test_v2_compact_receipt_drops_raw_user_output():
    result = _valid_result("EVO-D1-V2-001")
    result["receipt"]["arms"][0]["user_output"] = "不得保存的模型原文"
    result["receipt"]["arms"][1]["user_output"] = "另一段不得保存的模型原文"

    compact = D1V2BatchService._compact_pair_result(
        "EVO-D1-V2-001",
        result,
        attempt=1,
    )

    assert all("user_output" not in arm for arm in compact["arms"])
    assert all(len(arm["user_output_sha256"]) == 64 for arm in compact["arms"])
    assert compact["arms"][0]["user_output_length"] == len("不得保存的模型原文")
    assert "不得保存" not in str(compact)


def test_v2_formal_selection_requires_exact_manifest_order():
    async def unused(_case_id, _learner_id):
        return _valid_result(_case_id)

    service = _service(unused, reviewed=True)
    all_ids = [case.case_id for case in service.runner.cases]

    with pytest.raises(ValueError, match="all 100 cases"):
        service._validate_selection(all_ids[:-1], purpose="formal")
    with pytest.raises(ValueError, match="manifest order"):
        service._validate_selection(list(reversed(all_ids)), purpose="formal")
    selected = service._validate_selection(all_ids, purpose="formal")
    assert selected.case_ids == tuple(all_ids)


@pytest.mark.asyncio
async def test_v2_technical_retry_reruns_whole_pair_and_records_recovery():
    attempts: list[tuple[str, str]] = []

    async def execute_pair(case_id: str, learner_id: str):
        attempts.append((case_id, learner_id))
        if len(attempts) == 1:
            raise RuntimeError("sensitive provider text must not be persisted")
        return _valid_result(case_id)

    service = _service(execute_pair, reviewed=True)
    started = service.start(
        requested_by="admin",
        learner_id="learner-1",
        case_ids=["EVO-D1-V2-001"],
        purpose="single_trace",
    )
    task = service._tasks[started["run_id"]]
    await task

    status = service.status(started["run_id"])
    result = service.receipts(started["run_id"])
    assert attempts == [
        ("EVO-D1-V2-001", "learner-1"),
        ("EVO-D1-V2-001", "learner-1"),
    ]
    assert status["status"] == "completed"
    assert status["completed_case_count"] == 1
    assert status["recovered_case_count"] == 1
    assert status["technical_failure_count"] == 0
    assert status["coverage_valid"] is True
    assert len(result["receipts"]) == 1
    assert result["receipts"][0]["attempt"] == 2
    assert len(result["technical_errors"]) == 1
    assert "provider" not in str(result["technical_errors"]).lower()
    assert result["technical_errors"][0]["raw_prompt_saved"] is False
    assert result["technical_errors"][0]["raw_output_saved"] is False


@pytest.mark.asyncio
async def test_v2_only_single_trace_enables_context_local_full_model_capture(
    tmp_path,
):
    recorder = ModelTraceRecorder()
    capture_flags: list[tuple[str, bool]] = []
    holder: dict[str, D1V2BatchService] = {}

    async def execute_pair(case_id: str, _learner_id: str):
        index = recorder.begin("expert_agent", {"secret_prompt": case_id})
        capture_flags.append((case_id, recorder.items[index].full_capture))
        recorder.record_output_text(index, f"secret output for {case_id}")
        recorder.succeed(index, {"answer": "private raw answer"})
        return _valid_result_for(holder["service"], case_id)

    service = _service(
        execute_pair,
        reviewed=True,
        state_root=tmp_path,
        model_trace_recorder=recorder,
    )
    holder["service"] = service
    single_run_id = await _complete_single(service)

    assert capture_flags == [("EVO-D1-V2-001", True)]
    assert recorder.items[0].raw_input == {"secret_prompt": "EVO-D1-V2-001"}
    assert recorder.items[0].raw_output == {"answer": "private raw answer"}
    trace = service.single_trace(single_run_id)
    assert trace["model_call_count"] == 1
    assert trace["provider_reasoning_exported"] is False
    assert trace["model_calls"][0]["raw_input"] == {
        "secret_prompt": "EVO-D1-V2-001"
    }
    assert trace["model_calls"][0]["reasoning_text"] == (
        "[OMITTED_NOT_EXPORTED]"
    )
    assert "secret_prompt" not in (
        tmp_path / "batches" / f"{single_run_id}.json"
    ).read_text(encoding="utf-8")

    capture_flags.clear()
    precheck = service.start(
        requested_by="admin",
        learner_id="learner-1",
        case_ids=list(service.registry.human_review_sample_case_ids),
        purpose="precheck",
        prerequisite_run_id=single_run_id,
    )
    await service._tasks[precheck["run_id"]]

    assert capture_flags
    assert all(not full_capture for _, full_capture in capture_flags)
    assert all(item.raw_input is None for item in recorder.items)
    assert "secret_prompt" not in (
        tmp_path / "batches" / f"{precheck['run_id']}.json"
    ).read_text(encoding="utf-8")
    with pytest.raises(ValueError, match="only available for single_trace"):
        service.single_trace(precheck["run_id"])


@pytest.mark.asyncio
async def test_v2_final_technical_failure_stops_before_later_cases(tmp_path):
    calls: list[str] = []
    fail = False
    holder: dict[str, D1V2BatchService] = {}

    async def always_fails(case_id: str, _learner_id: str):
        calls.append(case_id)
        if not fail:
            return _valid_result_for(holder["service"], case_id)
        raise RuntimeError("provider detail must be redacted")

    service = _service(always_fails, reviewed=True, state_root=tmp_path)
    holder["service"] = service
    prerequisite_run_id = await _complete_single(service)
    calls.clear()
    fail = True
    selected = list(service.registry.human_review_sample_case_ids)
    started = service.start(
        requested_by="admin",
        learner_id="learner-1",
        case_ids=selected,
        purpose="precheck",
        prerequisite_run_id=prerequisite_run_id,
    )
    await service._tasks[started["run_id"]]

    assert calls == [selected[0]] * 3
    status = service.status(started["run_id"])
    assert status["status"] == "failed"
    assert status["technical_failure_count"] == 1
    assert status["completed_case_count"] == 0
    assert service.receipts(started["run_id"])["receipts"] == []


@pytest.mark.asyncio
async def test_v2_completed_batch_is_atomically_persisted_and_reloaded(tmp_path):
    holder: dict[str, D1V2BatchService] = {}

    async def execute_pair(case_id: str, _learner_id: str):
        result = _valid_result_for(holder["service"], case_id)
        result["receipt"]["arms"][0]["user_output"] = "不得写入状态文件的原文"
        return result

    service = _service(
        execute_pair,
        reviewed=True,
        state_root=tmp_path,
    )
    holder["service"] = service
    started = service.start(
        requested_by="admin",
        learner_id="learner-1",
        case_ids=["EVO-D1-V2-001"],
        purpose="single_trace",
    )
    await service._tasks[started["run_id"]]

    state_path = tmp_path / "batches" / f"{started['run_id']}.json"
    assert state_path.is_file()
    assert not state_path.with_suffix(".json.tmp").exists()
    serialized = state_path.read_text(encoding="utf-8")
    assert "不得写入状态文件的原文" not in serialized
    restored = _service(
        execute_pair,
        reviewed=True,
        state_root=tmp_path,
    )
    assert restored.status(started["run_id"])["status"] == "completed"
    assert restored.receipts(started["run_id"])["receipts"] == service.receipts(
        started["run_id"]
    )["receipts"]


@pytest.mark.asyncio
async def test_v2_restart_marks_active_batch_interrupted_and_resume_skips_prefix(
    tmp_path,
):
    entered_second = asyncio.Event()
    release_second = asyncio.Event()
    first_process_calls: list[str] = []
    holder: dict[str, D1V2BatchService] = {}

    async def first_executor(case_id: str, _learner_id: str):
        first_process_calls.append(case_id)
        if len(first_process_calls) == 2:
            entered_second.set()
            await release_second.wait()
        return _valid_result_for(holder["service"], case_id)

    service = _service(first_executor, reviewed=True, state_root=tmp_path)
    holder["service"] = service
    prerequisite_run_id = await _complete_single(service)
    first_process_calls.clear()
    sample_ids = list(service.registry.human_review_sample_case_ids)
    started = service.start(
        requested_by="admin",
        learner_id="learner-1",
        case_ids=sample_ids,
        purpose="precheck",
        prerequisite_run_id=prerequisite_run_id,
    )
    await entered_second.wait()
    assert service.status(started["run_id"])["completed_case_count"] == 1

    resumed_calls: list[str] = []
    restored_holder: dict[str, D1V2BatchService] = {}

    async def resumed_executor(case_id: str, _learner_id: str):
        resumed_calls.append(case_id)
        return _valid_result_for(restored_holder["service"], case_id)

    restored = _service(
        resumed_executor,
        reviewed=True,
        state_root=tmp_path,
    )
    restored_holder["service"] = restored
    interrupted = restored.status(started["run_id"])
    assert interrupted["status"] == "interrupted"
    assert interrupted["resumable"] is True
    assert interrupted["completed_case_count"] == 1

    resumed = restored.resume(
        started["run_id"],
        requested_by="admin",
        learner_id="learner-1",
    )
    assert resumed["resume_count"] == 1
    with pytest.raises(ValueError, match="already running"):
        restored.resume(
            started["run_id"],
            requested_by="admin",
            learner_id="learner-1",
        )
    await restored._tasks[started["run_id"]]

    assert resumed_calls == sample_ids[1:]
    receipts = restored.receipts(started["run_id"])["receipts"]
    assert [item["case_id"] for item in receipts] == sample_ids
    assert len({item["case_id"] for item in receipts}) == 10
    assert restored.status(started["run_id"])["status"] == "completed"

    service._tasks[started["run_id"]].cancel()
    with pytest.raises(asyncio.CancelledError):
        await service._tasks[started["run_id"]]


@pytest.mark.asyncio
async def test_v2_resume_requires_original_user_and_current_dataset(tmp_path):
    entered = asyncio.Event()

    async def blocked(_case_id: str, _learner_id: str):
        entered.set()
        await asyncio.Event().wait()
        return _valid_result(_case_id)

    service = _service(blocked, reviewed=True, state_root=tmp_path)
    started = service.start(
        requested_by="admin",
        learner_id="learner-1",
        case_ids=["EVO-D1-V2-001"],
        purpose="single_trace",
    )
    await entered.wait()
    restored = _service(blocked, reviewed=True, state_root=tmp_path)

    with pytest.raises(ValueError, match="original evaluation user"):
        restored.resume(
            started["run_id"],
            requested_by="other-user",
            learner_id="other-user",
        )

    service._tasks[started["run_id"]].cancel()
    with pytest.raises(asyncio.CancelledError):
        await service._tasks[started["run_id"]]

    state_path = tmp_path / "batches" / f"{started['run_id']}.json"
    payload = json.loads(state_path.read_text(encoding="utf-8"))
    payload["dataset_file_sha256"] = "0" * 64
    state_path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="dataset identity mismatch"):
        _service(blocked, reviewed=True, state_root=tmp_path)


def test_v2_corrupt_persisted_state_fails_closed(tmp_path):
    batches = tmp_path / "batches"
    batches.mkdir(parents=True)
    (batches / ("D1V2RUN_" + "a" * 32 + ".json")).write_text(
        "{not-json",
        encoding="utf-8",
    )

    async def unused(_case_id: str, _learner_id: str):
        return _valid_result(_case_id)

    with pytest.raises(ValueError, match="invalid persisted D1 V2 batch state"):
        _service(unused, reviewed=True, state_root=tmp_path)


@pytest.mark.asyncio
async def test_v2_stage_order_and_human_precheck_confirmation_are_enforced(tmp_path):
    holder: dict[str, D1V2BatchService] = {}

    async def execute_pair(case_id: str, _learner_id: str):
        return _valid_result_for(holder["service"], case_id)

    service = _service(execute_pair, reviewed=True, state_root=tmp_path)
    holder["service"] = service
    sample_ids = list(service.registry.human_review_sample_case_ids)
    formal_ids = [case.case_id for case in service.runner.cases]
    with pytest.raises(ValueError, match="completed single_trace"):
        service.start(
            requested_by="admin",
            learner_id="learner-1",
            case_ids=sample_ids,
            purpose="precheck",
        )

    single_run_id = await _complete_single(service)
    precheck = service.start(
        requested_by="admin",
        learner_id="learner-1",
        case_ids=sample_ids,
        purpose="precheck",
        prerequisite_run_id=single_run_id,
    )
    await service._tasks[precheck["run_id"]]
    with pytest.raises(ValueError, match="human precheck go"):
        service.start(
            requested_by="admin",
            learner_id="learner-1",
            case_ids=formal_ids,
            purpose="formal",
            prerequisite_run_id=precheck["run_id"],
        )

    decision = service.confirm_precheck(
        precheck["run_id"],
        requested_by="admin",
        decision="go",
        note="技术执行、固定输入与回执合同均已人工核对。",
    )
    assert decision["precheck_decision"] == "go"
    formal = service.start(
        requested_by="admin",
        learner_id="learner-1",
        case_ids=formal_ids,
        purpose="formal",
        prerequisite_run_id=precheck["run_id"],
    )
    await service._tasks[formal["run_id"]]
    assert service.status(formal["run_id"])["status"] == "completed"


@pytest.mark.asyncio
async def test_v2_repeatability_is_server_executed_in_fixed_order(tmp_path):
    calls: list[str] = []
    holder: dict[str, D1V2BatchService] = {}

    async def execute_pair(case_id: str, _learner_id: str):
        calls.append(case_id)
        return _valid_result_for(holder["service"], case_id)

    service = _service(execute_pair, reviewed=True, state_root=tmp_path)
    holder["service"] = service
    formal_run_id = await _complete_formal(service)
    calls.clear()

    service.start_repeatability(
        formal_run_id,
        requested_by="admin",
        learner_id="learner-1",
    )
    await service._tasks[formal_run_id]

    status = service.status(formal_run_id)
    evidence = service.receipts(formal_run_id)
    assert calls == list(V2_FORMAL_EVIDENCE_SAMPLE_CASE_IDS)
    assert status["repeat_status"] == "completed"
    assert status["repeat_completed_case_count"] == 20
    assert [item["case_id"] for item in evidence["repeat_receipts"]] == list(
        V2_FORMAL_EVIDENCE_SAMPLE_CASE_IDS
    )
    assert service.score(formal_run_id)["repeatability"]["minimum_met"] is True


@pytest.mark.asyncio
async def test_v2_repeatability_restart_resumes_after_valid_prefix(tmp_path):
    holder: dict[str, D1V2BatchService] = {}

    async def initial_executor(case_id: str, _learner_id: str):
        return _valid_result_for(holder["service"], case_id)

    service = _service(initial_executor, reviewed=True, state_root=tmp_path)
    holder["service"] = service
    formal_run_id = await _complete_formal(service)

    entered_second = asyncio.Event()
    release_second = asyncio.Event()
    repeat_calls: list[str] = []

    async def blocking_executor(case_id: str, _learner_id: str):
        repeat_calls.append(case_id)
        if len(repeat_calls) == 2:
            entered_second.set()
            await release_second.wait()
        return _valid_result_for(service, case_id)

    service.execute_pair = blocking_executor
    service.start_repeatability(
        formal_run_id,
        requested_by="admin",
        learner_id="learner-1",
    )
    await entered_second.wait()
    assert service.status(formal_run_id)["repeat_completed_case_count"] == 1

    resumed_calls: list[str] = []
    restored_holder: dict[str, D1V2BatchService] = {}

    async def resumed_executor(case_id: str, _learner_id: str):
        resumed_calls.append(case_id)
        return _valid_result_for(restored_holder["service"], case_id)

    restored = _service(
        resumed_executor,
        reviewed=True,
        state_root=tmp_path,
    )
    restored_holder["service"] = restored
    assert restored.status(formal_run_id)["repeat_status"] == "interrupted"
    restored.resume_repeatability(
        formal_run_id,
        requested_by="admin",
        learner_id="learner-1",
    )
    await restored._tasks[formal_run_id]

    assert resumed_calls == list(V2_FORMAL_EVIDENCE_SAMPLE_CASE_IDS[1:])
    assert restored.status(formal_run_id)["repeat_status"] == "completed"
    service._tasks[formal_run_id].cancel()
    with pytest.raises(asyncio.CancelledError):
        await service._tasks[formal_run_id]


@pytest.mark.asyncio
async def test_v2_repeatability_resume_respects_single_worker_boundary(tmp_path):
    holder: dict[str, D1V2BatchService] = {}

    async def execute_pair(case_id: str, _learner_id: str):
        return _valid_result_for(holder["service"], case_id)

    service = _service(execute_pair, reviewed=True, state_root=tmp_path)
    holder["service"] = service
    formal_run_id = await _complete_formal(service)
    service._runs[formal_run_id]["repeat_status"] = "interrupted"
    other_run_id = next(
        run_id for run_id in service._runs if run_id != formal_run_id
    )
    service._runs[other_run_id]["status"] = "running"

    with pytest.raises(ValueError, match="another D1 V2 batch"):
        service.resume_repeatability(
            formal_run_id,
            requested_by="admin",
            learner_id="learner-1",
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "corruption",
    [
        "main_receipt_extra",
        "validation_extra",
        "repeat_order",
        "repeat_error_extra",
        "blind_review_extra",
    ],
)
async def test_v2_persisted_formal_evidence_fails_closed_on_corruption(
    tmp_path,
    corruption,
):
    holder: dict[str, D1V2BatchService] = {}

    async def execute_pair(case_id: str, _learner_id: str):
        return _valid_result_for(holder["service"], case_id)

    service = _service(execute_pair, reviewed=True, state_root=tmp_path)
    holder["service"] = service
    formal_run_id = await _complete_formal(service)
    service.attach_blind_reviews(
        formal_run_id,
        blind_reviews=_formal_reviews(service),
        requested_by="reviewer-user",
    )
    service.start_repeatability(
        formal_run_id,
        requested_by="admin",
        learner_id="learner-1",
    )
    await service._tasks[formal_run_id]

    state_path = tmp_path / "batches" / f"{formal_run_id}.json"
    payload = json.loads(state_path.read_text(encoding="utf-8"))
    if corruption == "main_receipt_extra":
        payload["receipts"][0]["raw_output"] = "must never survive recovery"
    elif corruption == "validation_extra":
        payload["receipts"][0]["validation"]["provider_message"] = (
            "must never survive recovery"
        )
    elif corruption == "repeat_order":
        payload["repeat_receipts"][0], payload["repeat_receipts"][1] = (
            payload["repeat_receipts"][1],
            payload["repeat_receipts"][0],
        )
    elif corruption == "repeat_error_extra":
        payload["repeat_technical_errors"].append(
            {
                "schema_version": "d1-v2-technical-error-1.0",
                "run_id": formal_run_id,
                "case_id": V2_FORMAL_EVIDENCE_SAMPLE_CASE_IDS[0],
                "attempt": 1,
                "error_type": "RuntimeError",
                "error_digest": "a" * 64,
                "recorded_at": "2026-09-03T00:00:00+00:00",
                "raw_prompt_saved": False,
                "raw_output_saved": False,
                "formal_environment_write_allowed": False,
                "provider_message": "must never survive recovery",
            }
        )
    else:
        payload["blind_reviews"][0]["prompt"] = "must never survive recovery"
    state_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="invalid persisted D1 V2 batch state"):
        _service(execute_pair, reviewed=True, state_root=tmp_path)


@pytest.mark.asyncio
async def test_v2_persisted_score_is_recomputed_from_validated_evidence(tmp_path):
    holder: dict[str, D1V2BatchService] = {}

    async def execute_pair(case_id: str, _learner_id: str):
        return _valid_result_for(holder["service"], case_id)

    service = _service(execute_pair, reviewed=True, state_root=tmp_path)
    holder["service"] = service
    formal_run_id = await _complete_formal(service)
    expected = service.score(formal_run_id)
    state_path = tmp_path / "batches" / f"{formal_run_id}.json"
    payload = json.loads(state_path.read_text(encoding="utf-8"))
    payload["score"] = {"fabricated_metric": 1, "secret": "must not be served"}
    state_path.write_text(json.dumps(payload), encoding="utf-8")

    restored = _service(execute_pair, reviewed=True, state_root=tmp_path)

    assert restored.score(formal_run_id) == expected
    assert "fabricated_metric" not in str(restored.score(formal_run_id))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("coverage_valid", False),
        ("coverage_reason_codes", ["fabricated_reason"]),
        ("technical_failure_count", 1),
        ("completed_case_count", 99),
    ],
)
async def test_v2_completed_formal_state_must_match_full_coverage(
    tmp_path,
    field,
    value,
):
    holder: dict[str, D1V2BatchService] = {}

    async def execute_pair(case_id: str, _learner_id: str):
        return _valid_result_for(holder["service"], case_id)

    service = _service(execute_pair, reviewed=True, state_root=tmp_path)
    holder["service"] = service
    formal_run_id = await _complete_formal(service)
    state_path = tmp_path / "batches" / f"{formal_run_id}.json"
    payload = json.loads(state_path.read_text(encoding="utf-8"))
    payload[field] = value
    state_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="invalid persisted D1 V2 batch state"):
        _service(execute_pair, reviewed=True, state_root=tmp_path)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("precheck_decision", "invalid"),
        ("precheck_decision_note", ""),
        ("precheck_decided_by", "other-user"),
        ("precheck_decided_at", None),
    ],
)
async def test_v2_persisted_precheck_decision_must_be_authentic(
    tmp_path,
    field,
    value,
):
    holder: dict[str, D1V2BatchService] = {}

    async def execute_pair(case_id: str, _learner_id: str):
        return _valid_result_for(holder["service"], case_id)

    service = _service(execute_pair, reviewed=True, state_root=tmp_path)
    holder["service"] = service
    await _complete_formal(service)
    precheck_run_id = next(
        run_id
        for run_id, run in service._runs.items()
        if run["purpose"] == "precheck"
    )
    state_path = tmp_path / "batches" / f"{precheck_run_id}.json"
    payload = json.loads(state_path.read_text(encoding="utf-8"))
    payload[field] = value
    state_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="invalid persisted D1 V2 batch state"):
        _service(execute_pair, reviewed=True, state_root=tmp_path)


@pytest.mark.asyncio
async def test_v2_persisted_stage_chain_must_reference_valid_prerequisites(tmp_path):
    holder: dict[str, D1V2BatchService] = {}

    async def execute_pair(case_id: str, _learner_id: str):
        return _valid_result_for(holder["service"], case_id)

    service = _service(execute_pair, reviewed=True, state_root=tmp_path)
    holder["service"] = service
    formal_run_id = await _complete_formal(service)
    state_path = tmp_path / "batches" / f"{formal_run_id}.json"
    payload = json.loads(state_path.read_text(encoding="utf-8"))
    payload["prerequisite_run_id"] = "D1V2RUN_" + "f" * 32
    state_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="invalid persisted D1 V2 batch state"):
        _service(execute_pair, reviewed=True, state_root=tmp_path)


@pytest.mark.asyncio
async def test_v2_each_evaluation_stage_is_single_shot(tmp_path):
    holder: dict[str, D1V2BatchService] = {}

    async def execute_pair(case_id: str, _learner_id: str):
        return _valid_result_for(holder["service"], case_id)

    service = _service(execute_pair, reviewed=True, state_root=tmp_path)
    holder["service"] = service
    await _complete_formal(service)
    purpose_inputs = {
        "single_trace": (["EVO-D1-V2-002"], None),
        "precheck": (
            list(service.registry.human_review_sample_case_ids),
            next(
                run_id
                for run_id, run in service._runs.items()
                if run["purpose"] == "single_trace"
            ),
        ),
        "formal": (
            [case.case_id for case in service.runner.cases],
            next(
                run_id
                for run_id, run in service._runs.items()
                if run["purpose"] == "precheck"
            ),
        ),
    }
    for purpose, (case_ids, prerequisite_run_id) in purpose_inputs.items():
        with pytest.raises(ValueError, match="already exists"):
            service.start(
                requested_by="admin",
                learner_id="learner-1",
                case_ids=case_ids,
                purpose=purpose,
                prerequisite_run_id=prerequisite_run_id,
            )


@pytest.mark.asyncio
async def test_v2_persisted_runs_require_current_independent_review_gate(tmp_path):
    holder: dict[str, D1V2BatchService] = {}

    async def execute_pair(case_id: str, _learner_id: str):
        return _valid_result_for(holder["service"], case_id)

    service = _service(execute_pair, reviewed=True, state_root=tmp_path)
    holder["service"] = service
    await _complete_single(service)

    with pytest.raises(ValueError, match="invalid persisted D1 V2 batch state"):
        _service(execute_pair, reviewed=False, state_root=tmp_path)

    registry = replace(
        service.registry,
        human_review_submitted_by="admin",
        human_review_submitter_ids=("admin",),
    )
    with pytest.raises(ValueError, match="invalid persisted D1 V2 batch state"):
        D1V2BatchService(
            registry=registry,
            runner=service.runner,
            execute_pair=execute_pair,
            runtime_mode="live",
            state_root=tmp_path,
        )
