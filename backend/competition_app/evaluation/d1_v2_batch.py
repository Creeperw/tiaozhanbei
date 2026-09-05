from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
from threading import RLock
from typing import Any, Awaitable, Callable
from uuid import uuid4

from competition_app.evaluation.d1_ab100_v2_runner import D1AB100V2Runner
from competition_app.evaluation.d1_v2_registry import (
    D1V2DatasetRegistry,
    D1V2DatasetRegistryService,
    V2_FORMAL_EVIDENCE_SAMPLE_CASE_IDS,
)
from competition_app.evaluation.d1_v2_scorer import rate, score_d1_v2
from competition_app.runtime.model_trace import ModelTraceRecorder


PairExecutor = Callable[[str, str], Awaitable[dict[str, Any]]]
DEFAULT_EXECUTION_PROTOCOL_VERSION = "production-prose-binding-semantic-judge-v3"

_COMPACT_RECEIPT_FIELDS = {
    "schema_version",
    "case_id",
    "pair_order",
    "attempt",
    "formal_environment_write_allowed",
    "retrieval_pack_digest",
    "validation",
    "arms",
}
_COMPACT_ARM_SOURCE_FIELDS = {
    "arm",
    "input_digest",
    "rule_exposed",
    "exposed_target_agent",
    "target_failure",
    "closure_allowed",
    "first_audit_decision",
    "final_audit_decision",
    "repair_count",
    "repair_attempt_count",
    "repair_exhausted",
    "max_repair_attempts",
    "release_allowed",
    "initial_target_failure",
    "final_target_failure",
    "actual_rerun_step_ids",
    "same_conflict_pair_resolved",
    "new_unsupported_claims",
    "audit_conflicting_evidence",
    "semantic_conflict_binding_created",
    "closure_rule_applied",
    "semantic_verdict",
}
_COMPACT_ARM_FIELDS = _COMPACT_ARM_SOURCE_FIELDS | {
    "user_output_sha256",
    "user_output_length",
}
_TECHNICAL_ERROR_FIELDS = {
    "schema_version",
    "run_id",
    "case_id",
    "attempt",
    "error_type",
    "error_digest",
    "recorded_at",
    "raw_prompt_saved",
    "raw_output_saved",
    "formal_environment_write_allowed",
}
_BLIND_REVIEW_FIELDS = {
    "case_id",
    "reviewer_relation",
    "reviewer_name",
    "review_notes",
    "reviewed_at",
    "submitted_by",
}
_PUBLIC_STATUS_FIELDS = {
    "schema_version",
    "run_id",
    "dataset_id",
    "dataset_version",
    "dataset_file_sha256",
    "canonical_case_digest",
    "purpose",
    "selection_kind",
    "selected_case_ids",
    "prerequisite_run_id",
    "status",
    "created_at",
    "started_at",
    "finished_at",
    "interrupted_at",
    "interruption_reason",
    "last_resumed_at",
    "resume_count",
    "active_case_id",
    "active_attempt",
    "precheck_decision",
    "precheck_decision_note",
    "precheck_decided_at",
    "formal_environment_write_allowed",
    "formal_rule_status_change_allowed",
    "total_case_count",
    "completed_case_count",
    "technical_failure_count",
    "recovered_case_count",
    "repeat_status",
    "repeat_completed_case_count",
    "coverage_valid",
    "coverage_reason_codes",
}
_PERSISTED_RUN_FIELDS = _PUBLIC_STATUS_FIELDS | {
    "requested_by",
    "learner_id",
    "precheck_decided_by",
    "receipts",
    "technical_errors",
    "blind_reviews",
    "repeat_receipts",
    "repeat_technical_errors",
    "score",
}


@dataclass(frozen=True)
class D1V2BatchSelection:
    case_ids: tuple[str, ...]
    selection_kind: str


class D1V2BatchService:
    """Single-worker, no-writeback batch execution for the frozen V2 cases."""

    def __init__(
        self,
        *,
        registry: D1V2DatasetRegistry,
        runner: D1AB100V2Runner,
        execute_pair: PairExecutor,
        registry_service: D1V2DatasetRegistryService | None = None,
        runtime_mode: str = "live",
        max_pair_attempts: int = 3,
        state_root: Path | None = None,
        model_trace_recorder: ModelTraceRecorder | None = None,
        model_name: str = "unknown",
        execution_protocol_version: str | None = None,
        startup_human_review_required: bool = True,
        formal_execution_allowed: bool = True,
        precheck_case_ids: tuple[str, ...] | None = None,
        precheck_case_count: int = 10,
    ) -> None:
        if max_pair_attempts < 1 or max_pair_attempts > 5:
            raise ValueError("max_pair_attempts must be between 1 and 5")
        self.registry = registry
        self.registry_service = registry_service
        self.runner = runner
        self.execute_pair = execute_pair
        self.runtime_mode = str(runtime_mode or "").strip().lower()
        self.max_pair_attempts = max_pair_attempts
        self.model_trace_recorder = model_trace_recorder
        self.model_name = str(model_name or "unknown")[:200]
        self.execution_protocol_version = str(execution_protocol_version or "").strip()
        self.startup_human_review_required = bool(startup_human_review_required)
        self.formal_execution_allowed = bool(formal_execution_allowed)
        self.precheck_case_ids = tuple(
            precheck_case_ids or registry.human_review_sample_case_ids
        )
        self.precheck_case_count = int(precheck_case_count)
        if self.precheck_case_count < 1:
            raise ValueError("precheck_case_count must be positive")
        self.state_root = Path(state_root) if state_root is not None else None
        self._protocol_state_root = (
            self.state_root / self.execution_protocol_version
            if self.state_root is not None and self.execution_protocol_version
            else self.state_root
        )
        self._batches_dir = (
            self._protocol_state_root / "batches"
            if self._protocol_state_root is not None
            else None
        )
        self._runs: dict[str, dict[str, Any]] = {}
        self._tasks: dict[str, asyncio.Task] = {}
        self._lock = RLock()
        self._load_persisted_runs()

    def manifest_payload(self) -> dict[str, Any]:
        registry = self._current_registry()
        execution_allowed = self.runtime_mode == "live" and (
            registry.human_review_passed or not self.startup_human_review_required
        )
        return {
            **registry.payload(),
            "execution_protocol_version": (
                self.execution_protocol_version or "legacy-unversioned"
            ),
            "runtime_mode": self.runtime_mode,
            "live_mode_required": True,
            "execution_allowed": execution_allowed,
            "execution_block_reason": (
                None
                if execution_allowed
                else "live_mode_required"
                if self.runtime_mode != "live"
                else "independent_human_review_retry_pending"
                if registry.human_review_retry_allowed
                else "independent_human_review_rejected"
                if registry.human_review_complete
                else "independent_human_review_evidence_invalid"
                if registry.human_review_attempt_count
                else "independent_human_review_pending"
            ),
            "startup_human_review_required": self.startup_human_review_required,
            "formal_execution_allowed": self.formal_execution_allowed,
            "formal_rule_status_change_allowed": False,
        }

    def start(
        self,
        *,
        requested_by: str,
        learner_id: str,
        case_ids: list[str],
        purpose: str,
        prerequisite_run_id: str | None = None,
    ) -> dict[str, Any]:
        registry = self._current_registry()
        if self.runtime_mode != "live":
            raise ValueError("D1 V2 execution requires COMPETITION_APP_MODE=live")
        if self.startup_human_review_required and not registry.human_review_passed:
            raise ValueError("independent human review has not passed")
        if not learner_id.strip():
            raise ValueError("D1 V2 batch requires an authenticated learner binding")
        if (
            self.startup_human_review_required
            and requested_by in registry.human_review_submitter_ids
        ):
            raise ValueError("evaluation runner must be independent from human reviewer")
        selection = self._validate_selection(case_ids, purpose=purpose)
        with self._lock:
            if self._worker_active_locked():
                raise ValueError("another D1 V2 batch is already running")
            if any(item.get("purpose") == purpose for item in self._runs.values()):
                raise ValueError(f"a D1 V2 {purpose} run already exists")
            if any(item["status"] == "interrupted" for item in self._runs.values()):
                raise ValueError("an interrupted D1 V2 batch must be resumed first")
            if any(
                item.get("repeat_status") == "interrupted"
                for item in self._runs.values()
            ):
                raise ValueError("an interrupted repeatability run must be resumed first")
            self._validate_stage_prerequisite_locked(
                purpose=purpose,
                prerequisite_run_id=prerequisite_run_id,
                requested_by=requested_by,
                learner_id=learner_id,
            )
            run_id = f"D1V2RUN_{uuid4().hex}"
            now = datetime.now(timezone.utc).isoformat()
            self._runs[run_id] = {
                "schema_version": "d1-v2-batch-status-1.1",
                "run_id": run_id,
                "dataset_id": registry.dataset_id,
                "dataset_version": registry.version,
                "dataset_file_sha256": registry.file_sha256,
                "canonical_case_digest": registry.canonical_case_digest,
                "purpose": purpose,
                "selection_kind": selection.selection_kind,
                "selected_case_ids": list(selection.case_ids),
                "prerequisite_run_id": prerequisite_run_id,
                "requested_by": requested_by,
                "learner_id": learner_id,
                "status": "queued",
                "created_at": now,
                "started_at": None,
                "finished_at": None,
                "interrupted_at": None,
                "interruption_reason": None,
                "last_resumed_at": None,
                "resume_count": 0,
                "active_case_id": None,
                "active_attempt": None,
                "precheck_decision": None,
                "precheck_decision_note": None,
                "precheck_decided_at": None,
                "precheck_decided_by": None,
                "formal_environment_write_allowed": False,
                "formal_rule_status_change_allowed": False,
                "total_case_count": len(selection.case_ids),
                "completed_case_count": 0,
                "technical_failure_count": 0,
                "recovered_case_count": 0,
                "receipts": [],
                "technical_errors": [],
                "blind_reviews": [],
                "repeat_receipts": [],
                "repeat_status": "not_started",
                "repeat_completed_case_count": 0,
                "repeat_technical_errors": [],
                "score": None,
                "coverage_valid": False,
                "coverage_reason_codes": ["batch_not_completed"],
            }
            self._persist_locked(self._runs[run_id])
            task = asyncio.create_task(
                self._run(
                    run_id,
                    execute_case_ids=selection.case_ids,
                    selected_case_ids=selection.case_ids,
                    learner_id=learner_id,
                )
            )
            self._tasks[run_id] = task
            return self._public_status(self._runs[run_id])

    def confirm_precheck(
        self,
        run_id: str,
        *,
        requested_by: str,
        decision: str,
        note: str,
    ) -> dict[str, Any]:
        """Record a human go/no-go; no effect threshold is invented here."""

        normalized_decision = str(decision or "").strip().lower()
        normalized_note = str(note or "").strip()
        if normalized_decision not in {"go", "no_go"}:
            raise ValueError("precheck decision must be go or no_go")
        if len(normalized_note) < 3:
            raise ValueError("precheck decision requires a review note")
        with self._lock:
            run = self._runs.get(run_id)
            if run is None:
                raise KeyError(run_id)
            if run["purpose"] != "precheck":
                raise ValueError("go/no-go can only be recorded for a precheck")
            if run["status"] != "completed" or not run["coverage_valid"]:
                raise ValueError("go/no-go requires a completed valid precheck")
            if requested_by != run["requested_by"]:
                raise ValueError("precheck decision requires the original evaluation user")
            if run.get("precheck_decision") is not None:
                raise ValueError("precheck decision has already been recorded")
            run.update(
                {
                    "precheck_decision": normalized_decision,
                    "precheck_decision_note": normalized_note[:2000],
                    "precheck_decided_at": _now(),
                    "precheck_decided_by": requested_by,
                }
            )
            self._persist_locked(run)
            return self._public_status(run)

    def resume(
        self,
        run_id: str,
        *,
        requested_by: str,
        learner_id: str,
    ) -> dict[str, Any]:
        registry = self._current_registry()
        if self.runtime_mode != "live":
            raise ValueError("D1 V2 execution requires COMPETITION_APP_MODE=live")
        if self.startup_human_review_required and not registry.human_review_passed:
            raise ValueError("independent human review has not passed")
        if not learner_id.strip():
            raise ValueError("D1 V2 batch requires an authenticated learner binding")
        if (
            self.startup_human_review_required
            and requested_by in registry.human_review_submitter_ids
        ):
            raise ValueError("evaluation runner must be independent from human reviewer")
        with self._lock:
            run = self._runs.get(run_id)
            if run is None:
                raise KeyError(run_id)
            if run["status"] in {"queued", "running"} or run_id in self._tasks:
                raise ValueError("D1 V2 batch is already running")
            if run["status"] != "interrupted":
                raise ValueError("only an interrupted D1 V2 batch can be resumed")
            if self._worker_active_locked(excluding_run_id=run_id):
                raise ValueError("another D1 V2 batch is already running")
            if (
                requested_by != run["requested_by"]
                or learner_id != run["learner_id"]
            ):
                raise ValueError("resume requires the original evaluation user")
            self._assert_dataset_identity(run, registry)
            selected = tuple(str(item) for item in run["selected_case_ids"])
            completed = int(run["completed_case_count"])
            remaining = selected[completed:]
            run.update(
                {
                    "status": "queued",
                    "finished_at": None,
                    "interrupted_at": None,
                    "interruption_reason": None,
                    "last_resumed_at": _now(),
                    "resume_count": int(run.get("resume_count") or 0) + 1,
                    "active_case_id": None,
                    "active_attempt": None,
                    "coverage_valid": False,
                    "coverage_reason_codes": ["batch_not_completed"],
                }
            )
            self._persist_locked(run)
            task = asyncio.create_task(
                self._run(
                    run_id,
                    execute_case_ids=remaining,
                    selected_case_ids=selected,
                    learner_id=learner_id,
                )
            )
            self._tasks[run_id] = task
            return self._public_status(run)

    def status(self, run_id: str) -> dict[str, Any]:
        with self._lock:
            run = self._runs.get(run_id)
            if run is None:
                raise KeyError(run_id)
            return self._public_status(run)

    def list_runs(self, *, requested_by: str | None = None) -> list[dict[str, Any]]:
        with self._lock:
            ordered = sorted(
                self._runs.values(),
                key=lambda item: str(item.get("created_at") or ""),
                reverse=True,
            )
            return [
                self._public_status(run)
                for run in ordered
                if requested_by is None or run.get("requested_by") == requested_by
            ]

    def is_owned_by(self, run_id: str, user_id: str) -> bool:
        with self._lock:
            run = self._runs.get(run_id)
            if run is None:
                raise KeyError(run_id)
            return run.get("requested_by") == user_id

    def has_runs(self) -> bool:
        with self._lock:
            return bool(self._runs)

    def overview(self) -> dict[str, Any]:
        with self._lock:
            ordered_runs = sorted(
                self._runs.values(),
                key=lambda item: str(item.get("created_at") or ""),
                reverse=True,
            )
            runs = []
            for run in ordered_runs[:20]:
                item = self._public_status(run)
                if run.get("score") is not None:
                    item["score"] = json.loads(
                        json.dumps(run["score"], ensure_ascii=False)
                    )
                runs.append(item)
        return {
            "schema_version": "d1-v2-admin-overview-1.0",
            "manifest": self.manifest_payload(),
            "runs": runs,
            "evaluation_does_not_approve_or_activate_rule": True,
        }

    def receipts(self, run_id: str) -> dict[str, Any]:
        with self._lock:
            run = self._runs.get(run_id)
            if run is None:
                raise KeyError(run_id)
            return json.loads(json.dumps({
                "schema_version": "d1-v2-batch-receipts-1.0",
                "run_id": run_id,
                "status": run["status"],
                "formal_environment_write_allowed": False,
                "receipts": run["receipts"],
                "technical_errors": run["technical_errors"],
                "measurement_summary": self._measurement_summary(run["receipts"]),
                "repeat_receipts": run["repeat_receipts"],
                "repeat_technical_errors": run["repeat_technical_errors"],
            }, ensure_ascii=False))

    def single_trace(self, run_id: str) -> dict[str, Any]:
        """Read the separate diagnostic artifact for a completed single run."""

        with self._lock:
            run = self._runs.get(run_id)
            if run is None:
                raise KeyError(run_id)
            if run.get("purpose") != "single_trace":
                raise ValueError("model trace is only available for single_trace runs")
        path = self._single_trace_path(run_id)
        if path is None or not path.is_file():
            raise ValueError("single_trace model artifact is not available")
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError("single_trace model artifact is invalid") from exc
        if (
            not isinstance(value, dict)
            or value.get("run_id") != run_id
            or value.get("provider_reasoning_exported") is not False
        ):
            raise ValueError("single_trace model artifact is invalid")
        return value

    def score(self, run_id: str) -> dict[str, Any]:
        with self._lock:
            run = self._runs.get(run_id)
            if run is None:
                raise KeyError(run_id)
            if run.get("score") is None:
                raise ValueError("V2 formal score is not available")
            return json.loads(json.dumps(run["score"], ensure_ascii=False))

    def attach_blind_reviews(
        self,
        run_id: str,
        *,
        blind_reviews: list[dict[str, Any]],
        requested_by: str,
    ) -> dict[str, Any]:
        with self._lock:
            run = self._runs.get(run_id)
            if run is None:
                raise KeyError(run_id)
            if run["status"] != "completed" or run["purpose"] != "formal":
                raise ValueError("review evidence requires a completed formal V2 batch")
            if requested_by == run["requested_by"]:
                raise ValueError("formal blind reviewer must be independent from runner")
            if run["blind_reviews"]:
                raise ValueError("formal blind review has already been recorded")
            expected_ids = list(V2_FORMAL_EVIDENCE_SAMPLE_CASE_IDS)
            observed_ids = [str(item.get("case_id") or "") for item in blind_reviews]
            if observed_ids != expected_ids:
                raise ValueError("formal blind review must use the fixed 20-case order")
            allowed_relations = {"contradiction", "compatible", "not_applicable"}
            safe_reviews: list[dict[str, Any]] = []
            reviewed_at = _now()
            for item in blind_reviews:
                relation = str(item.get("reviewer_relation") or "").strip().lower()
                reviewer_name = str(item.get("reviewer_name") or "").strip()
                review_notes = str(item.get("review_notes") or "").strip()
                if relation not in allowed_relations:
                    raise ValueError("formal blind review contains an invalid relation")
                if not reviewer_name or len(review_notes) < 3:
                    raise ValueError("formal blind review requires reviewer name and notes")
                safe_reviews.append(
                    {
                        "case_id": str(item["case_id"]),
                        "reviewer_relation": relation,
                        "reviewer_name": reviewer_name[:200],
                        "review_notes": review_notes[:2000],
                        "reviewed_at": reviewed_at,
                        "submitted_by": requested_by[:200],
                    }
                )
            run["blind_reviews"] = safe_reviews
            run["score"] = self._score_run(run)
            self._persist_locked(run)
            return json.loads(json.dumps(run["score"], ensure_ascii=False))

    def start_repeatability(
        self,
        run_id: str,
        *,
        requested_by: str,
        learner_id: str,
    ) -> dict[str, Any]:
        """Run the fixed 20-case repeat sample through the same server executor."""

        registry = self._current_registry()
        if self.runtime_mode != "live":
            raise ValueError("D1 V2 execution requires COMPETITION_APP_MODE=live")
        if not registry.human_review_passed:
            raise ValueError("independent human review has not passed")
        with self._lock:
            run = self._runs.get(run_id)
            if run is None:
                raise KeyError(run_id)
            if run["purpose"] != "formal" or run["status"] != "completed":
                raise ValueError("repeatability requires a completed formal V2 batch")
            if requested_by != run["requested_by"] or learner_id != run["learner_id"]:
                raise ValueError("repeatability requires the original evaluation user")
            if run["repeat_receipts"]:
                raise ValueError("repeatability evidence has already been recorded")
            if run.get("repeat_status") != "not_started":
                raise ValueError("repeatability execution has already been started")
            if run_id in self._tasks or self._worker_active_locked(
                excluding_run_id=run_id
            ):
                raise ValueError("another D1 V2 batch is already running")
            run["repeat_status"] = "queued"
            run["repeat_completed_case_count"] = 0
            run["repeat_technical_errors"] = []
            self._persist_locked(run)
            task = asyncio.create_task(self._run_repeats(run_id, learner_id))
            self._tasks[run_id] = task
            return self._public_status(run)

    def resume_repeatability(
        self,
        run_id: str,
        *,
        requested_by: str,
        learner_id: str,
    ) -> dict[str, Any]:
        registry = self._current_registry()
        if self.runtime_mode != "live":
            raise ValueError("D1 V2 execution requires COMPETITION_APP_MODE=live")
        if not registry.human_review_passed:
            raise ValueError("independent human review has not passed")
        with self._lock:
            run = self._runs.get(run_id)
            if run is None:
                raise KeyError(run_id)
            if run.get("repeat_status") in {"queued", "running"} or run_id in self._tasks:
                raise ValueError("D1 V2 repeatability execution is already running")
            if run.get("repeat_status") != "interrupted":
                raise ValueError("only interrupted repeatability execution can be resumed")
            if self._worker_active_locked(excluding_run_id=run_id):
                raise ValueError("another D1 V2 batch is already running")
            if requested_by != run["requested_by"] or learner_id != run["learner_id"]:
                raise ValueError("repeatability resume requires the original evaluation user")
            self._assert_dataset_identity(run, registry)
            run["repeat_status"] = "queued"
            self._persist_locked(run)
            task = asyncio.create_task(self._run_repeats(run_id, learner_id))
            self._tasks[run_id] = task
            return self._public_status(run)

    async def _run_repeats(self, run_id: str, learner_id: str) -> None:
        self._update(run_id, repeat_status="running")
        try:
            with self._lock:
                completed = len(self._runs[run_id]["repeat_receipts"])
            for case_id in V2_FORMAL_EVIDENCE_SAMPLE_CASE_IDS[completed:]:
                with self._lock:
                    prior_attempts = max(
                        (
                            int(item.get("attempt") or 0)
                            for item in self._runs[run_id]["repeat_technical_errors"]
                            if item.get("case_id") == case_id
                        ),
                        default=0,
                    )
                succeeded = False
                for attempt in range(prior_attempts + 1, self.max_pair_attempts + 1):
                    try:
                        result = await self.execute_pair(case_id, learner_id)
                        if not self._valid_pair_result(case_id, result):
                            raise ValueError("repeat execution returned an invalid receipt")
                        compact = self._compact_pair_result(
                            case_id,
                            result,
                            attempt=attempt,
                        )
                        with self._lock:
                            run = self._runs[run_id]
                            run["repeat_receipts"].append(compact)
                            run["repeat_completed_case_count"] += 1
                            self._persist_locked(run)
                        succeeded = True
                        break
                    except Exception as exc:
                        error = self._technical_error(
                            run_id=run_id,
                            case_id=case_id,
                            attempt=attempt,
                            exc=exc,
                        )
                        with self._lock:
                            run = self._runs[run_id]
                            run["repeat_technical_errors"].append(error)
                            if attempt >= self.max_pair_attempts:
                                run["repeat_status"] = "failed"
                            self._persist_locked(run)
                if not succeeded:
                    if prior_attempts >= self.max_pair_attempts:
                        self._update(run_id, repeat_status="failed")
                    return
            with self._lock:
                run = self._runs[run_id]
                run["repeat_status"] = "completed"
                run["score"] = self._score_run(run)
                self._persist_locked(run)
        except Exception as exc:
            with self._lock:
                run = self._runs[run_id]
                run["repeat_status"] = "failed"
                run["repeat_technical_errors"].append(
                    self._technical_error(
                        run_id=run_id,
                        case_id="__repeat_batch__",
                        attempt=1,
                        exc=exc,
                    )
                )
                self._persist_locked(run)
        finally:
            with self._lock:
                self._tasks.pop(run_id, None)

    async def _run(
        self,
        run_id: str,
        *,
        execute_case_ids: tuple[str, ...],
        selected_case_ids: tuple[str, ...],
        learner_id: str,
    ) -> None:
        with self._lock:
            run = self._runs[run_id]
            started_at = run.get("started_at") or _now()
        self._update(run_id, status="running", started_at=started_at)
        try:
            stop_after_final_failure = False
            for case_id in execute_case_ids:
                with self._lock:
                    prior_attempts = max(
                        (
                            int(item.get("attempt") or 0)
                            for item in self._runs[run_id]["technical_errors"]
                            if item.get("case_id") == case_id
                        ),
                        default=0,
                    )
                recovered = prior_attempts > 0
                for attempt in range(prior_attempts + 1, self.max_pair_attempts + 1):
                    self._update(
                        run_id,
                        active_case_id=case_id,
                        active_attempt=attempt,
                    )
                    try:
                        recorder = (
                            self.model_trace_recorder
                            if self._runs[run_id]["purpose"] == "single_trace"
                            else None
                        )
                        if recorder is None:
                            result = await self.execute_pair(case_id, learner_id)
                        else:
                            recorder.reset()
                            with recorder.capture_full():
                                result = await self.execute_pair(case_id, learner_id)
                        if not self._valid_pair_result(case_id, result):
                            raise ValueError("pair execution returned an invalid receipt")
                        if recorder is not None:
                            self._write_single_trace_artifact(
                                run_id=run_id,
                                case_id=case_id,
                                result=result,
                                attempt=attempt,
                                recorder=recorder,
                            )
                        compact = self._compact_pair_result(case_id, result, attempt=attempt)
                        with self._lock:
                            run = self._runs[run_id]
                            run["receipts"].append(compact)
                            run["completed_case_count"] += 1
                            if recovered:
                                run["recovered_case_count"] += 1
                            run["active_case_id"] = None
                            run["active_attempt"] = None
                            self._persist_locked(run)
                        break
                    except Exception as exc:
                        final_attempt = attempt >= self.max_pair_attempts
                        error_record = self._technical_error(
                            run_id=run_id,
                            case_id=case_id,
                            attempt=attempt,
                            exc=exc,
                        )
                        with self._lock:
                            run = self._runs[run_id]
                            run["technical_errors"].append(error_record)
                            run["active_case_id"] = None
                            run["active_attempt"] = None
                            if final_attempt:
                                run["technical_failure_count"] += 1
                            self._persist_locked(run)
                        recovered = True
                        if final_attempt:
                            stop_after_final_failure = True
                            break
                if stop_after_final_failure:
                    break
            coverage = self._validate_coverage(run_id, selected_case_ids)
            status = "completed" if coverage["valid"] else "failed"
            self._update(
                run_id,
                status=status,
                finished_at=_now(),
                coverage_valid=coverage["valid"],
                coverage_reason_codes=coverage["reason_codes"],
            )
            if status == "completed":
                with self._lock:
                    run = self._runs[run_id]
                    if run["purpose"] == "formal":
                        run["score"] = self._score_run(run)
                        self._persist_locked(run)
        except Exception as exc:
            self._update(
                run_id,
                status="failed",
                finished_at=_now(),
                coverage_valid=False,
                coverage_reason_codes=["batch_worker_failed"],
            )
            with self._lock:
                run = self._runs[run_id]
                run["technical_errors"].append(
                    self._technical_error(
                        run_id=run_id,
                        case_id="__batch__",
                        attempt=1,
                        exc=exc,
                    )
                )
                run["active_case_id"] = None
                run["active_attempt"] = None
                self._persist_locked(run)
        finally:
            with self._lock:
                self._tasks.pop(run_id, None)

    def _validate_selection(self, case_ids: list[str], *, purpose: str) -> D1V2BatchSelection:
        allowed = [case.case_id for case in self.runner.cases]
        if not case_ids:
            raise ValueError("at least one V2 case_id is required")
        if len(case_ids) != len(set(case_ids)):
            raise ValueError("V2 batch case_ids must be unique")
        unknown = sorted(set(case_ids) - set(allowed))
        if unknown:
            raise ValueError(f"unknown V2 case_ids: {','.join(unknown)}")
        if purpose == "precheck" and len(case_ids) != self.precheck_case_count:
            raise ValueError(
                f"V2 precheck requires exactly {self.precheck_case_count} cases"
            )
        if purpose == "precheck" and case_ids != list(self.precheck_case_ids):
            raise ValueError("V2 precheck must use the fixed human-review sample order")
        if purpose == "formal" and not self.formal_execution_allowed:
            raise ValueError("formal evaluation is disabled for this pilot")
        if purpose == "formal" and case_ids != allowed:
            raise ValueError("V2 formal evaluation requires all 100 cases in manifest order")
        if purpose not in {"precheck", "formal", "single_trace"}:
            raise ValueError("unsupported V2 batch purpose")
        if purpose == "single_trace" and len(case_ids) != 1:
            raise ValueError("single_trace requires exactly one case")
        return D1V2BatchSelection(tuple(case_ids), purpose)

    def _validate_stage_prerequisite_locked(
        self,
        *,
        purpose: str,
        prerequisite_run_id: str | None,
        requested_by: str,
        learner_id: str,
    ) -> None:
        if purpose == "single_trace":
            if prerequisite_run_id is not None:
                raise ValueError("single_trace must not have a prerequisite run")
            return
        expected_purpose = "single_trace" if purpose == "precheck" else "precheck"
        if not prerequisite_run_id:
            raise ValueError(f"{purpose} requires a completed {expected_purpose} run")
        prerequisite = self._runs.get(prerequisite_run_id)
        if prerequisite is None:
            raise ValueError("V2 prerequisite run does not exist")
        self._assert_dataset_identity(prerequisite, self._current_registry())
        if (
            prerequisite.get("purpose") != expected_purpose
            or prerequisite.get("status") != "completed"
            or prerequisite.get("coverage_valid") is not True
        ):
            raise ValueError(f"{purpose} requires a completed {expected_purpose} run")
        if (
            prerequisite.get("requested_by") != requested_by
            or prerequisite.get("learner_id") != learner_id
        ):
            raise ValueError("V2 stage prerequisite requires the original evaluation user")
        if purpose == "formal" and prerequisite.get("precheck_decision") != "go":
            raise ValueError("formal evaluation requires human precheck go confirmation")

    def _worker_active_locked(self, *, excluding_run_id: str | None = None) -> bool:
        return any(
            candidate_id != excluding_run_id
            and (
                item.get("status") in {"queued", "running"}
                or item.get("repeat_status") in {"queued", "running"}
            )
            for candidate_id, item in self._runs.items()
        )

    def _valid_pair_result(self, case_id: str, result: dict[str, Any]) -> bool:
        if result.get("case_id") != case_id:
            return False
        if result.get("formal_environment_write_allowed") is not False:
            return False
        validation = result.get("validation") or {}
        receipt = result.get("receipt") or {}
        arms = receipt.get("arms") or []
        case = next((item for item in self.runner.cases if item.case_id == case_id), None)
        if case is None or len(arms) != 2:
            return False
        by_arm = {
            str(item.get("arm") or ""): item
            for item in arms
            if isinstance(item, dict)
        }
        if set(by_arm) != {"A", "B"}:
            return False
        expected_exposure = bool(case.frozen_evidence.expected_rule_exposure)
        arm_contract_valid = all(
            int(arm.get("max_repair_attempts") or -1) == 2
            and int(arm.get("repair_attempt_count") or 0) in {0, 1, 2}
            and int(arm.get("repair_count") or 0) in {0, 1, 2, 3}
            and bool(arm.get("repair_exhausted"))
            == (int(arm.get("repair_count") or 0) == 3)
            for arm in by_arm.values()
        )
        return bool(
            validation.get("valid")
            and validation.get("repair_contract_valid")
            and result.get("pair_order") == case.pair_order
            and arm_contract_valid
            and by_arm["A"].get("input_digest") == by_arm["B"].get("input_digest")
            and not bool(by_arm["A"].get("rule_exposed"))
            and bool(by_arm["B"].get("rule_exposed")) == expected_exposure
        )

    @staticmethod
    def _compact_pair_result(case_id: str, result: dict[str, Any], *, attempt: int) -> dict[str, Any]:
        receipt = result["receipt"]
        safe_arms: list[dict[str, Any]] = []
        for raw_arm in receipt.get("arms") or []:
            safe_arm = {
                key: raw_arm.get(key)
                for key in _COMPACT_ARM_SOURCE_FIELDS
                if key in raw_arm
            }
            output = str(raw_arm.get("user_output") or "")
            if "semantic_verdict" in safe_arm:
                safe_arm["semantic_verdict"] = D1V2BatchService._compact_semantic_verdict(
                    safe_arm["semantic_verdict"]
                )
            safe_arm["user_output_sha256"] = hashlib.sha256(
                output.encode("utf-8")
            ).hexdigest()
            safe_arm["user_output_length"] = len(output)
            safe_arms.append(safe_arm)
        return {
            "schema_version": "d1-v2-case-receipt-1.0",
            "case_id": case_id,
            "pair_order": result.get("pair_order"),
            "attempt": attempt,
            "formal_environment_write_allowed": False,
            "retrieval_pack_digest": result.get("retrieval_pack_digest"),
            "validation": {
                "valid": bool((result.get("validation") or {}).get("valid")),
                "repair_contract_valid": bool(
                    (result.get("validation") or {}).get("repair_contract_valid")
                ),
            },
            "arms": safe_arms,
        }

    @staticmethod
    def _compact_semantic_verdict(value: Any) -> dict[str, Any]:
        raw = value if isinstance(value, dict) else {}
        status = str(raw.get("status") or "unavailable")
        common = {
            "schema_version": "d1-semantic-verdict-1.0",
            "judge_version": str(raw.get("judge_version") or "unknown")[:128],
            "status": status if status in {"judged", "unavailable"} else "unavailable",
            "acceptable": raw.get("acceptable") if isinstance(raw.get("acceptable"), bool) else None,
            "request_fulfilled": raw.get("request_fulfilled") if isinstance(raw.get("request_fulfilled"), bool) else None,
            "material_a_handled_correctly": raw.get("material_a_handled_correctly") if isinstance(raw.get("material_a_handled_correctly"), bool) else None,
            "material_b_handled_correctly": raw.get("material_b_handled_correctly") if isinstance(raw.get("material_b_handled_correctly"), bool) else None,
            "relationship_handled_correctly": raw.get("relationship_handled_correctly") if isinstance(raw.get("relationship_handled_correctly"), bool) else None,
            "no_unsupported_resolution": raw.get("no_unsupported_resolution") if isinstance(raw.get("no_unsupported_resolution"), bool) else None,
            "rationale_code": str(raw.get("rationale_code") or "judge_unavailable")[:128],
        }
        if common["status"] != "judged":
            common.update({
                key: None
                for key in (
                    "acceptable",
                    "request_fulfilled",
                    "material_a_handled_correctly",
                    "material_b_handled_correctly",
                    "relationship_handled_correctly",
                    "no_unsupported_resolution",
                )
            })
            common["rationale_code"] = "judge_unavailable"
            common["error_type"] = str(raw.get("error_type") or "UnknownJudgeError")[:128]
        return common

    def _measurement_summary(self, receipts: list[dict[str, Any]]) -> dict[str, Any]:
        by_id = {case.case_id: case for case in self.runner.cases}
        groups = {
            "target_fault": [],
            "non_regression_control": [],
            "targeting_negative_control": [],
        }
        for row in receipts:
            case = by_id.get(str(row.get("case_id") or ""))
            if case is not None and case.case_group in groups:
                groups[case.case_group].append(row)

        def arm(row: dict[str, Any], name: str) -> dict[str, Any]:
            return next(
                (item for item in row.get("arms") or [] if item.get("arm") == name),
                {},
            )

        def judged(row: dict[str, Any], name: str) -> bool:
            return (arm(row, name).get("semantic_verdict") or {}).get("status") == "judged"

        def acceptable(row: dict[str, Any], name: str) -> bool:
            verdict = arm(row, name).get("semantic_verdict") or {}
            return verdict.get("status") == "judged" and verdict.get("acceptable") is True

        targets = groups["target_fault"]
        normals = groups["non_regression_control"]
        boundaries = groups["targeting_negative_control"]
        all_arm_count = len(receipts) * 2
        judged_arm_count = sum(
            judged(row, name) for row in receipts for name in ("A", "B")
        )
        semantic_improvements = [
            row["case_id"]
            for row in targets
            if judged(row, "A") and judged(row, "B")
            and not acceptable(row, "A") and acceptable(row, "B")
        ]
        semantic_regressions = [
            row["case_id"]
            for row in targets
            if judged(row, "A") and judged(row, "B")
            and acceptable(row, "A") and not acceptable(row, "B")
        ]
        binding_improvements = [
            row["case_id"]
            for row in targets
            if bool(arm(row, "A").get("final_target_failure"))
            and not bool(arm(row, "B").get("final_target_failure"))
        ]
        normal_regressions = [
            row["case_id"]
            for row in normals
            if judged(row, "A") and judged(row, "B")
            and acceptable(row, "A") and not acceptable(row, "B")
        ]
        boundary_harms = [
            row["case_id"]
            for row in boundaries
            if judged(row, "A") and judged(row, "B")
            and acceptable(row, "A") and not acceptable(row, "B")
        ]
        baseline_failures = [
            row
            for row in targets
            if judged(row, "A") and not acceptable(row, "A")
        ]
        evaluable_baseline_failures = [
            row for row in baseline_failures if judged(row, "B")
        ]
        repaired_baseline_failures = [
            row["case_id"]
            for row in evaluable_baseline_failures
            if acceptable(row, "B")
        ]
        still_failed = [
            row["case_id"]
            for row in evaluable_baseline_failures
            if not acceptable(row, "B")
        ]
        both_acceptable = [
            row["case_id"]
            for row in targets
            if judged(row, "A") and judged(row, "B")
            and acceptable(row, "A") and acceptable(row, "B")
        ]
        normal_disagreements = [
            row["case_id"]
            for row in normals
            if judged(row, "A") and judged(row, "B")
            and acceptable(row, "A") != acceptable(row, "B")
        ]
        a_target_acceptable = sum(acceptable(row, "A") for row in targets)
        b_target_acceptable = sum(acceptable(row, "B") for row in targets)
        a_target_judged = sum(judged(row, "A") for row in targets)
        b_target_judged = sum(judged(row, "B") for row in targets)
        return {
            "schema_version": "d1-v2-measurement-summary-1.0",
            "automatic_semantic_judge_is_final_human_review": False,
            "effect_conclusion_status": "pending_human_prose_blind_review",
            "binding_proxy": {
                "metric_type": "system_evidence_binding_proxy",
                "target_improvement_count": len(binding_improvements),
                "target_denominator": len(targets),
                "improved_case_ids": binding_improvements,
            },
            "learner_visible_semantic": {
                "metric_type": "automatic_blinded_prose_judgement",
                "coverage": {
                    "judged_arm_count": judged_arm_count,
                    "total_arm_count": all_arm_count,
                    "rate": rate(judged_arm_count, all_arm_count),
                },
                "target_baseline_acceptable": {
                    "numerator": a_target_acceptable,
                    "denominator": a_target_judged,
                    "rate": rate(a_target_acceptable, a_target_judged),
                },
                "target_candidate_acceptable": {
                    "numerator": b_target_acceptable,
                    "denominator": b_target_judged,
                    "rate": rate(b_target_acceptable, b_target_judged),
                },
                "failure_conditioned_repair": {
                    "metric_name": "known_baseline_failure_repair_rate",
                    "repaired_count": len(repaired_baseline_failures),
                    "evaluable_baseline_failure_count": len(evaluable_baseline_failures),
                    "observed_baseline_failure_count": len(baseline_failures),
                    "rate": rate(
                        len(repaired_baseline_failures),
                        len(evaluable_baseline_failures),
                    ),
                    "repaired_case_ids": repaired_baseline_failures,
                    "still_failed_case_ids": still_failed,
                    "candidate_judge_unavailable_count": (
                        len(baseline_failures) - len(evaluable_baseline_failures)
                    ),
                    "interpretation_boundary": (
                        "Diagnostic rate conditioned on an observed final A failure; "
                        "not an unbiased population uplift estimate."
                    ),
                },
                "target_both_acceptable_count": len(both_acceptable),
                "target_both_acceptable_case_ids": both_acceptable,
                "target_improvement_count": len(semantic_improvements),
                "target_regression_count": len(semantic_regressions),
                "target_net_improvement_count": len(semantic_improvements) - len(semantic_regressions),
                "improved_case_ids": semantic_improvements,
                "regressed_case_ids": semantic_regressions,
                "normal_control_regression_count": len(normal_regressions),
                "normal_control_denominator": len(normals),
                "normal_control_regression_case_ids": normal_regressions,
                "normal_control_disagreement_count": len(normal_disagreements),
                "normal_control_disagreement_case_ids": normal_disagreements,
                "compatible_boundary_harm_count": len(boundary_harms),
                "compatible_boundary_denominator": len(boundaries),
                "compatible_boundary_harm_case_ids": boundary_harms,
            },
        }

    def _validate_coverage(self, run_id: str, selected: tuple[str, ...]) -> dict[str, Any]:
        with self._lock:
            run = self._runs[run_id]
            receipts = list(run["receipts"])
            failures = int(run["technical_failure_count"])
        ids = [item.get("case_id") for item in receipts]
        reasons: list[str] = []
        if failures:
            reasons.append("technical_failure_present")
        if ids != list(selected):
            reasons.append("receipt_coverage_or_order_mismatch")
        if len(ids) != len(set(ids)):
            reasons.append("duplicate_case_receipt")
        return {"valid": not reasons, "reason_codes": reasons}

    def _score_run(self, run: dict[str, Any]) -> dict[str, Any]:
        return score_d1_v2(
            cases=self.runner.cases,
            receipts=list(run["receipts"]),
            dataset_file_sha256=self.registry.file_sha256,
            expected_dataset_file_sha256=self.registry.file_sha256,
            technical_errors=list(run["technical_errors"]),
            blind_reviews=list(run["blind_reviews"]),
            repeat_receipts=list(run["repeat_receipts"]),
        )

    def _current_registry(self) -> D1V2DatasetRegistry:
        if self.registry_service is None:
            return self.registry
        registry, _cases = self.registry_service.load()
        self.registry = registry
        return registry

    @staticmethod
    def _technical_error(
        *,
        run_id: str,
        case_id: str,
        attempt: int,
        exc: Exception,
    ) -> dict[str, Any]:
        safe_type = type(exc).__name__[:128]
        digest = hashlib.sha256(
            f"{safe_type}:{str(exc)[:500]}".encode("utf-8")
        ).hexdigest()
        return {
            "schema_version": "d1-v2-technical-error-1.0",
            "run_id": run_id,
            "case_id": case_id,
            "attempt": attempt,
            "error_type": safe_type,
            "error_digest": digest,
            "recorded_at": _now(),
            "raw_prompt_saved": False,
            "raw_output_saved": False,
            "formal_environment_write_allowed": False,
        }

    def _update(self, run_id: str, **values: Any) -> None:
        with self._lock:
            run = self._runs[run_id]
            run.update(values)
            self._persist_locked(run)

    def _single_trace_path(self, run_id: str) -> Path | None:
        if self._protocol_state_root is None:
            return None
        return (
            self._protocol_state_root
            / "single-traces"
            / f"{run_id}.model_io.json"
        )

    def _write_single_trace_artifact(
        self,
        *,
        run_id: str,
        case_id: str,
        result: dict[str, Any],
        attempt: int,
        recorder: ModelTraceRecorder,
    ) -> None:
        path = self._single_trace_path(run_id)
        if path is None:
            return
        model_calls: list[dict[str, Any]] = []
        for item in recorder.items:
            value = item.model_dump(mode="json", exclude={"reasoning_text"})
            value["reasoning_text"] = "[OMITTED_NOT_EXPORTED]"
            model_calls.append(value)
        artifact = {
            "schema_version": "d1-v2-single-model-io-trace-1.0",
            "trace_purpose": "single_trace_developer_diagnostic",
            "run_id": run_id,
            "case_id": case_id,
            "attempt": attempt,
            "formal_environment_write_allowed": False,
            "provider_reasoning_exported": False,
            "model_name": self.model_name,
            "model_call_count": len(model_calls),
            "model_calls": model_calls,
            "pair_result": result,
            "recorded_at": _now(),
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        encoded = json.dumps(artifact, ensure_ascii=False, indent=2) + "\n"
        with temporary.open("w", encoding="utf-8") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(path)

    def _load_persisted_runs(self) -> None:
        if self._batches_dir is None:
            return
        self._batches_dir.mkdir(parents=True, exist_ok=True)
        for path in sorted(self._batches_dir.glob("D1V2RUN_*.json")):
            try:
                value = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise ValueError("invalid persisted D1 V2 batch state") from exc
            try:
                run = self._validate_persisted_run(value, expected_run_id=path.stem)
            except (KeyError, TypeError, ValueError) as exc:
                if str(exc) == "persisted D1 V2 dataset identity mismatch":
                    raise
                raise ValueError("invalid persisted D1 V2 batch state") from exc
            self._runs[run["run_id"]] = run
        try:
            self._validate_current_review_gate_for_loaded_runs()
            self._validate_loaded_stage_chains()
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("invalid persisted D1 V2 batch state") from exc
        for run in self._runs.values():
            changed = False
            if run["status"] in {"queued", "running"}:
                run.update(
                    {
                        "status": "interrupted",
                        "interrupted_at": _now(),
                        "interruption_reason": "service_restart",
                        "active_case_id": None,
                        "active_attempt": None,
                        "coverage_valid": False,
                        "coverage_reason_codes": ["worker_restart_interrupted"],
                    }
                )
                changed = True
            if run.get("repeat_status") in {"queued", "running"}:
                run["repeat_status"] = "interrupted"
                changed = True
            if changed:
                self._persist_locked(run)

    def _validate_persisted_run(
        self,
        value: Any,
        *,
        expected_run_id: str,
    ) -> dict[str, Any]:
        if not isinstance(value, dict):
            raise ValueError("persisted batch must be an object")
        run = json.loads(json.dumps(value, ensure_ascii=False))
        if set(run) != _PERSISTED_RUN_FIELDS:
            raise ValueError("persisted batch fields are invalid")
        run_id = str(run.get("run_id") or "")
        if (
            run_id != expected_run_id
            or re.fullmatch(r"D1V2RUN_[0-9a-f]{32}", run_id) is None
        ):
            raise ValueError("persisted run_id mismatch")
        if run.get("schema_version") != "d1-v2-batch-status-1.1":
            raise ValueError("unsupported persisted batch schema")
        self._assert_dataset_identity(run, self.registry)
        if run.get("status") not in {
            "queued",
            "running",
            "interrupted",
            "completed",
            "failed",
        }:
            raise ValueError("invalid persisted batch status")
        purpose = str(run.get("purpose") or "")
        selected_raw = run.get("selected_case_ids")
        if not isinstance(selected_raw, list):
            raise ValueError("invalid persisted batch selection")
        selection = self._validate_selection(
            [str(item) for item in selected_raw],
            purpose=purpose,
        )
        if run.get("selection_kind") != selection.selection_kind:
            raise ValueError("persisted selection kind mismatch")
        if not str(run.get("requested_by") or "").strip() or not str(
            run.get("learner_id") or ""
        ).strip():
            raise ValueError("persisted evaluation user is missing")
        if (
            run.get("formal_environment_write_allowed") is not False
            or run.get("formal_rule_status_change_allowed") is not False
        ):
            raise ValueError("persisted writeback boundary is invalid")
        receipts = run.get("receipts")
        technical_errors = run.get("technical_errors")
        if not isinstance(receipts, list) or not isinstance(technical_errors, list):
            raise ValueError("persisted receipt collections are invalid")
        receipt_ids = [str(item.get("case_id") or "") for item in receipts]
        if receipt_ids != list(selection.case_ids[: len(receipt_ids)]):
            raise ValueError("persisted receipts are not an ordered prefix")
        if not all(
            self._valid_compact_receipt(item, expected_case_id=case_id)
            for case_id, item in zip(receipt_ids, receipts)
        ):
            raise ValueError("persisted compact receipt is invalid")
        completed = int(run.get("completed_case_count") or 0)
        if completed != len(receipts):
            raise ValueError("persisted completed count mismatch")
        if int(run.get("total_case_count") or 0) != len(selection.case_ids):
            raise ValueError("persisted total count mismatch")
        self._validate_persisted_completion_state(run, receipts)
        self._validate_persisted_precheck_decision(run)
        if not all(
            self._valid_technical_error(
                item,
                run_id=run_id,
                allowed_case_ids=set(selection.case_ids) | {"__batch__"},
            )
            for item in technical_errors
        ):
            raise ValueError("persisted technical error is not redacted")
        blind_reviews = run.get("blind_reviews")
        repeat_receipts = run.get("repeat_receipts")
        repeat_technical_errors = run.get("repeat_technical_errors")
        if not all(
            isinstance(collection, list)
            for collection in (
                blind_reviews,
                repeat_receipts,
                repeat_technical_errors,
            )
        ):
            raise ValueError("persisted review evidence is invalid")
        self._validate_persisted_blind_reviews(run, blind_reviews)
        self._validate_persisted_repeatability(
            run,
            repeat_receipts,
            repeat_technical_errors,
        )
        run.setdefault("interrupted_at", None)
        run.setdefault("interruption_reason", None)
        run.setdefault("last_resumed_at", None)
        run.setdefault("resume_count", 0)
        run.setdefault("active_case_id", None)
        run.setdefault("active_attempt", None)
        run.setdefault("prerequisite_run_id", None)
        run.setdefault("precheck_decision", None)
        run.setdefault("precheck_decision_note", None)
        run.setdefault("precheck_decided_at", None)
        run.setdefault("precheck_decided_by", None)
        if purpose == "formal" and run.get("status") == "completed":
            run["score"] = self._score_run(run)
        else:
            run["score"] = None
        return run

    def _validate_persisted_completion_state(
        self,
        run: dict[str, Any],
        receipts: list[dict[str, Any]],
    ) -> None:
        try:
            technical_failure_count = int(run.get("technical_failure_count"))
            recovered_case_count = int(run.get("recovered_case_count"))
            resume_count = int(run.get("resume_count"))
        except (TypeError, ValueError) as exc:
            raise ValueError("persisted batch counters are invalid") from exc
        if min(technical_failure_count, recovered_case_count, resume_count) < 0:
            raise ValueError("persisted batch counters are invalid")
        expected_recovered_count = sum(
            int(item.get("attempt") or 1) > 1 for item in receipts
        )
        if recovered_case_count != expected_recovered_count:
            raise ValueError("persisted recovered count mismatch")
        coverage_valid = run.get("coverage_valid")
        reason_codes = run.get("coverage_reason_codes")
        if not isinstance(coverage_valid, bool) or not isinstance(reason_codes, list):
            raise ValueError("persisted coverage state is invalid")
        if not all(isinstance(item, str) and item for item in reason_codes):
            raise ValueError("persisted coverage reasons are invalid")
        if run.get("status") == "completed":
            if (
                len(receipts) != int(run["total_case_count"])
                or technical_failure_count != 0
                or coverage_valid is not True
                or reason_codes
            ):
                raise ValueError("persisted completed batch coverage is invalid")
        elif coverage_valid is not False:
            raise ValueError("non-completed persisted batch cannot have valid coverage")

    @staticmethod
    def _validate_persisted_precheck_decision(run: dict[str, Any]) -> None:
        decision = run.get("precheck_decision")
        note = run.get("precheck_decision_note")
        decided_at = run.get("precheck_decided_at")
        decided_by = run.get("precheck_decided_by")
        decision_values = (decision, note, decided_at, decided_by)
        if decision is None:
            if any(item is not None for item in decision_values):
                raise ValueError("persisted precheck decision is incomplete")
            return
        if (
            run.get("purpose") != "precheck"
            or run.get("status") != "completed"
            or run.get("coverage_valid") is not True
            or decision not in {"go", "no_go"}
            or len(str(note or "").strip()) < 3
            or not str(decided_at or "").strip()
            or decided_by != run.get("requested_by")
        ):
            raise ValueError("persisted precheck decision is invalid")

    def _validate_loaded_stage_chains(self) -> None:
        for run in self._runs.values():
            purpose = run["purpose"]
            prerequisite_run_id = run.get("prerequisite_run_id")
            if purpose == "single_trace":
                if prerequisite_run_id is not None:
                    raise ValueError("persisted single trace has a prerequisite")
                continue
            expected_purpose = "single_trace" if purpose == "precheck" else "precheck"
            prerequisite = self._runs.get(str(prerequisite_run_id or ""))
            if (
                prerequisite is None
                or prerequisite.get("purpose") != expected_purpose
                or prerequisite.get("status") != "completed"
                or prerequisite.get("coverage_valid") is not True
                or prerequisite.get("requested_by") != run.get("requested_by")
                or prerequisite.get("learner_id") != run.get("learner_id")
            ):
                raise ValueError("persisted stage prerequisite is invalid")
            self._assert_dataset_identity(prerequisite, self.registry)
            if purpose == "formal" and prerequisite.get("precheck_decision") != "go":
                raise ValueError("persisted formal run lacks precheck go confirmation")

    def _validate_current_review_gate_for_loaded_runs(self) -> None:
        if not self._runs:
            return
        if not self.startup_human_review_required:
            return
        reviewer_ids = {
            str(item).strip()
            for item in self.registry.human_review_submitter_ids
            if str(item).strip()
        }
        if not self.registry.human_review_passed or not reviewer_ids:
            raise ValueError("persisted runs require a current passed human review")
        if any(
            str(run.get("requested_by") or "") in reviewer_ids
            for run in self._runs.values()
        ):
            raise ValueError("persisted runner is not independent from reviewer")

    def _valid_compact_receipt(
        self,
        item: Any,
        *,
        expected_case_id: str,
    ) -> bool:
        if not isinstance(item, dict) or set(item) != _COMPACT_RECEIPT_FIELDS:
            return False
        if item.get("schema_version") != "d1-v2-case-receipt-1.0":
            return False
        validation = item.get("validation")
        if (
            not isinstance(validation, dict)
            or set(validation) != {"valid", "repair_contract_valid"}
            or not all(isinstance(value, bool) for value in validation.values())
        ):
            return False
        try:
            attempt = int(item.get("attempt"))
        except (TypeError, ValueError):
            return False
        if attempt < 1 or attempt > self.max_pair_attempts:
            return False
        digest = str(item.get("retrieval_pack_digest") or "")
        if re.fullmatch(r"[0-9a-f]{64}", digest) is None:
            return False
        arms = item.get("arms")
        if not isinstance(arms, list) or len(arms) != 2:
            return False
        for arm in arms:
            if not isinstance(arm, dict) or not set(arm).issubset(_COMPACT_ARM_FIELDS):
                return False
            output_digest = str(arm.get("user_output_sha256") or "")
            output_length = arm.get("user_output_length")
            input_digest = str(arm.get("input_digest") or "")
            if (
                re.fullmatch(r"[0-9a-f]{64}", output_digest) is None
                or re.fullmatch(r"[0-9a-f]{64}", input_digest) is None
                or not isinstance(output_length, int)
                or isinstance(output_length, bool)
                or output_length < 0
            ):
                return False
            if "semantic_verdict" in arm and not self._valid_semantic_verdict(
                arm.get("semantic_verdict")
            ):
                return False
        return self._valid_pair_result(
            expected_case_id,
            {
                "case_id": item.get("case_id"),
                "pair_order": item.get("pair_order"),
                "formal_environment_write_allowed": item.get(
                    "formal_environment_write_allowed"
                ),
                "validation": item.get("validation"),
                "receipt": {"arms": arms},
            },
        )

    @staticmethod
    def _valid_semantic_verdict(value: Any) -> bool:
        if not isinstance(value, dict):
            return False
        common_fields = {
            "schema_version",
            "judge_version",
            "status",
            "acceptable",
            "request_fulfilled",
            "material_a_handled_correctly",
            "material_b_handled_correctly",
            "relationship_handled_correctly",
            "no_unsupported_resolution",
            "rationale_code",
        }
        status = value.get("status")
        expected_fields = common_fields | ({"error_type"} if status == "unavailable" else set())
        if (
            set(value) != expected_fields
            or value.get("schema_version") != "d1-semantic-verdict-1.0"
            or not str(value.get("judge_version") or "")
            or status not in {"judged", "unavailable"}
        ):
            return False
        booleans = [
            value.get(key)
            for key in (
                "acceptable",
                "request_fulfilled",
                "material_a_handled_correctly",
                "material_b_handled_correctly",
                "relationship_handled_correctly",
                "no_unsupported_resolution",
            )
        ]
        if status == "unavailable":
            return bool(
                all(item is None for item in booleans)
                and value.get("rationale_code") == "judge_unavailable"
                and str(value.get("error_type") or "")
            )
        allowed_codes = {
            "acceptable",
            "request_not_fulfilled",
            "material_a_mishandled",
            "material_b_mishandled",
            "relationship_mishandled",
            "unsupported_resolution",
        }
        return bool(
            all(isinstance(item, bool) for item in booleans)
            and value.get("rationale_code") in allowed_codes
            and value.get("acceptable") is all(booleans[1:])
        )

    def _valid_technical_error(
        self,
        item: Any,
        *,
        run_id: str,
        allowed_case_ids: set[str],
    ) -> bool:
        if not isinstance(item, dict) or set(item) != _TECHNICAL_ERROR_FIELDS:
            return False
        try:
            attempt = int(item.get("attempt"))
        except (TypeError, ValueError):
            return False
        return bool(
            item.get("schema_version") == "d1-v2-technical-error-1.0"
            and item.get("run_id") == run_id
            and str(item.get("case_id") or "") in allowed_case_ids
            and 1 <= attempt <= self.max_pair_attempts
            and 0 < len(str(item.get("error_type") or "")) <= 128
            and re.fullmatch(r"[0-9a-f]{64}", str(item.get("error_digest") or ""))
            is not None
            and bool(str(item.get("recorded_at") or ""))
            and item.get("raw_prompt_saved") is False
            and item.get("raw_output_saved") is False
            and item.get("formal_environment_write_allowed") is False
        )

    def _validate_persisted_blind_reviews(
        self,
        run: dict[str, Any],
        reviews: list[Any],
    ) -> None:
        if not reviews:
            return
        if run.get("purpose") != "formal" or run.get("status") != "completed":
            raise ValueError("persisted blind review is attached to an invalid run")
        expected_ids = list(V2_FORMAL_EVIDENCE_SAMPLE_CASE_IDS)
        if [str(item.get("case_id") or "") for item in reviews] != expected_ids:
            raise ValueError("persisted blind review order is invalid")
        allowed_relations = {"contradiction", "compatible", "not_applicable"}
        submitters: set[str] = set()
        for item in reviews:
            if not isinstance(item, dict) or set(item) != _BLIND_REVIEW_FIELDS:
                raise ValueError("persisted blind review contains unsafe fields")
            submitter = str(item.get("submitted_by") or "").strip()
            submitters.add(submitter)
            if (
                str(item.get("reviewer_relation") or "").strip().lower()
                not in allowed_relations
                or not str(item.get("reviewer_name") or "").strip()
                or len(str(item.get("review_notes") or "").strip()) < 3
                or not str(item.get("reviewed_at") or "").strip()
                or not submitter
            ):
                raise ValueError("persisted blind review is invalid")
        if len(submitters) != 1 or run.get("requested_by") in submitters:
            raise ValueError("persisted blind reviewer independence is invalid")

    def _validate_persisted_repeatability(
        self,
        run: dict[str, Any],
        receipts: list[Any],
        technical_errors: list[Any],
    ) -> None:
        repeat_status = run.get("repeat_status")
        if repeat_status not in {
            "not_started",
            "queued",
            "running",
            "interrupted",
            "completed",
            "failed",
        }:
            raise ValueError("persisted repeatability status is invalid")
        expected_ids = list(V2_FORMAL_EVIDENCE_SAMPLE_CASE_IDS)
        observed_ids = [str(item.get("case_id") or "") for item in receipts]
        if observed_ids != expected_ids[: len(observed_ids)]:
            raise ValueError("persisted repeat receipts are not an ordered prefix")
        if not all(
            self._valid_compact_receipt(item, expected_case_id=case_id)
            for case_id, item in zip(observed_ids, receipts)
        ):
            raise ValueError("persisted repeat receipt is invalid")
        try:
            completed = int(run.get("repeat_completed_case_count"))
        except (TypeError, ValueError) as exc:
            raise ValueError("persisted repeat completed count is invalid") from exc
        if completed != len(receipts):
            raise ValueError("persisted repeat completed count mismatch")
        if not all(
            self._valid_technical_error(
                item,
                run_id=str(run["run_id"]),
                allowed_case_ids=set(expected_ids) | {"__repeat_batch__"},
            )
            for item in technical_errors
        ):
            raise ValueError("persisted repeat technical error is not redacted")
        if run.get("purpose") != "formal" and (
            receipts or technical_errors or repeat_status != "not_started"
        ):
            raise ValueError("persisted repeatability evidence requires a formal run")
        if repeat_status == "not_started" and (receipts or technical_errors):
            raise ValueError("unstarted repeatability contains evidence")
        if repeat_status == "completed" and len(receipts) != len(expected_ids):
            raise ValueError("completed repeatability evidence is incomplete")

    @staticmethod
    def _assert_dataset_identity(
        run: dict[str, Any],
        registry: D1V2DatasetRegistry,
    ) -> None:
        expected = (
            registry.dataset_id,
            registry.version,
            registry.file_sha256,
            registry.canonical_case_digest,
        )
        observed = (
            run.get("dataset_id"),
            run.get("dataset_version"),
            run.get("dataset_file_sha256"),
            run.get("canonical_case_digest"),
        )
        if observed != expected:
            raise ValueError("persisted D1 V2 dataset identity mismatch")

    def _persist_locked(self, run: dict[str, Any]) -> None:
        if self._batches_dir is None:
            return
        self._batches_dir.mkdir(parents=True, exist_ok=True)
        path = self._batches_dir / f"{run['run_id']}.json"
        temporary = path.with_suffix(path.suffix + ".tmp")
        payload = json.dumps(
            run,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        ) + "\n"
        with temporary.open("w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(path)

    @staticmethod
    def _public_status(run: dict[str, Any]) -> dict[str, Any]:
        payload = {
            key: value
            for key, value in run.items()
            if key in _PUBLIC_STATUS_FIELDS
        }
        payload["technical_error_record_count"] = len(run["technical_errors"])
        payload["repeat_technical_error_record_count"] = len(
            run.get("repeat_technical_errors") or []
        )
        payload["score_available"] = run.get("score") is not None
        payload["resumable"] = run.get("status") == "interrupted"
        payload["repeat_resumable"] = run.get("repeat_status") == "interrupted"
        return json.loads(json.dumps(payload, ensure_ascii=False))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
