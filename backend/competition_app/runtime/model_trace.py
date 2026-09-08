from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
import hashlib
import json
from threading import RLock
import time
from typing import Any

from pydantic import BaseModel

from competition_app.llm.validation_diagnostics import safe_validation_issues
from competition_app.llm.response_diagnostics import PLAN_HEADINGS, safe_response_diagnostics
from competition_app.runtime.snapshot import _sanitize


class ModelCallTrace(BaseModel):
    """A bounded diagnostic record of one model request and response.

    Production traces intentionally retain only sizes and digests.  Complete
    prompts and outputs are available only inside an explicit, context-local
    evaluation/debug capture scope.
    """

    sequence: int
    agent: str
    raw_input: dict[str, Any] | None = None
    transport_input: dict[str, Any] | None = None
    raw_output_text: str | None = None
    reasoning_text: str | None = None
    raw_output: dict[str, Any] | None = None
    input_chars: int = 0
    input_digest: str | None = None
    output_chars: int = 0
    output_digest: str | None = None
    transport_input_chars: int = 0
    transport_output_chars: int = 0
    full_capture: bool = False
    error_type: str | None = None
    error_reason: str | None = None
    error_cause_type: str | None = None
    error_status_code: int | None = None
    error_retry_count: int | None = None
    error_transport_stage: str | None = None
    validation_issues: list[dict[str, Any]] | None = None
    planning_validation_issues: list[dict[str, Any]] | None = None
    started_at_ms: int | None = None
    completed_at_ms: int | None = None
    total_duration_ms: int | None = None
    queue_wait_ms: int | None = None
    provider_duration_ms: int | None = None
    request_attempt_count: int | None = None
    last_reasoning_at_monotonic: float | None = None
    reasoning_delta_count: int | None = None
    response_chars: int | None = None
    response_diagnostics: dict[str, Any] | None = None


_SAFE_TRANSPORT_STAGES = frozenset(
    {
        "connect",
        "proxy",
        "read",
        "write",
        "pool",
        "protocol",
        "http_response",
        "timeout",
        "network",
    }
)


def _safe_diagnostic_token(value: Any, *, lowercase: bool = False) -> str | None:
    """Keep only short machine-generated diagnostic tokens."""

    text = str(value or "").strip()
    if lowercase:
        text = text.lower()
    if not text or len(text) > 80:
        return None
    if not all(character.isalnum() or character in "_- ." for character in text):
        return None
    return text.replace(" ", "_")


def _bounded_diagnostic_int(
    value: Any,
    *,
    minimum: int = 0,
    maximum: int = 1_000_000,
) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if minimum <= parsed <= maximum else None


class ModelTraceRecorder:
    def __init__(self, *, full_capture: bool = False) -> None:
        self._items: ContextVar[list[ModelCallTrace] | None] = ContextVar(
            f"model_trace_items_{id(self)}", default=None
        )
        self._full_capture: ContextVar[bool] = ContextVar(
            f"model_trace_full_capture_{id(self)}", default=bool(full_capture)
        )
        # ContextVar keeps concurrent requests isolated, but an HTTP test or
        # diagnostics caller reads the recorder after the request task has
        # exited and therefore has no request-local value to inspect. Keep a
        # bounded reference to the latest context list for that read-only
        # fallback. Request code with an active context never consults it, so
        # concurrent learner traces cannot be merged into one another.
        self._latest_items: list[ModelCallTrace] = []
        self._latest_lock = RLock()

    @staticmethod
    def _json_text(value: Any) -> str:
        try:
            return json.dumps(
                value,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                default=str,
            )
        except (TypeError, ValueError):
            return str(value)

    @classmethod
    def _summary(cls, value: Any) -> tuple[int, str]:
        text = cls._json_text(value)
        return len(text), hashlib.sha256(text.encode("utf-8")).hexdigest()

    @staticmethod
    def _safe_output_summary(agent: str, payload: dict[str, Any]) -> dict[str, Any] | None:
        """Retain bounded planning evidence, never prompts or reasoning."""

        if agent == "diagnosis_agent" and isinstance(payload.get("plan_document"), str):
            document = payload["plan_document"]
            return {
                "plan_document_excerpt": _sanitize(document[:12000]),
                "plan_document_chars": len(document),
                "plan_document_truncated": len(document) > 12000,
                "plan_document_headings_present": {h: f"【{h}】" in document for h in PLAN_HEADINGS},
            }
        if agent != "plan_contract_compiler":
            return None
        issues = payload.get("issues")
        safe_issues = []
        if isinstance(issues, list):
            for issue in issues[:20]:
                if not isinstance(issue, dict):
                    continue
                safe_issues.append(
                    {
                        "code": str(issue.get("code") or "unknown")[:80],
                        "field_path": str(issue.get("field_path") or "/")[:200],
                    }
                )
        return {
            "status": str(payload.get("status") or "unknown")[:40],
            "issues": safe_issues,
        }

    @contextmanager
    def capture_full(self):
        """Enable raw trace capture for this request/task only.

        ContextVar scoping prevents an internal evaluation request from
        enabling prompt capture in concurrent learner conversations.
        """

        token = self._full_capture.set(True)
        try:
            yield
        finally:
            self._full_capture.reset(token)

    def _current(self) -> list[ModelCallTrace]:
        items = self._items.get()
        if items is None:
            items = []
            self._items.set(items)
            with self._latest_lock:
                self._latest_items = items
        return items

    def begin(self, agent: str, payload: dict[str, Any]) -> int:
        items = self._current()
        capture_full = self._full_capture.get()
        input_chars, input_digest = self._summary(payload)
        items.append(
            ModelCallTrace(
                sequence=len(items) + 1,
                agent=agent,
                raw_input=_sanitize(payload) if capture_full else None,
                input_chars=input_chars,
                input_digest=input_digest,
                full_capture=capture_full,
                started_at_ms=time.time_ns() // 1_000_000,
            )
        )
        return len(items) - 1

    def reset(self) -> None:
        items: list[ModelCallTrace] = []
        self._items.set(items)
        with self._latest_lock:
            self._latest_items = items

    def succeed(self, index: int, payload: dict[str, Any]) -> None:
        items = self._current()
        output_chars, output_digest = self._summary(payload)
        capture_full = items[index].full_capture
        stored_output = (
            _sanitize(payload)
            if capture_full
            else self._safe_output_summary(items[index].agent, payload)
        )
        completed_at_ms = time.time_ns() // 1_000_000
        items[index] = items[index].model_copy(
            update={
                "raw_output": stored_output,
                "output_chars": output_chars,
                "output_digest": output_digest,
                "completed_at_ms": completed_at_ms,
                "total_duration_ms": (
                    completed_at_ms - items[index].started_at_ms
                    if items[index].started_at_ms is not None
                    else None
                ),
            }
        )

    def record_output_text(self, index: int, text: str) -> None:
        items = self._current()
        output_chars, output_digest = self._summary(text)
        items[index] = items[index].model_copy(
            update={
                "raw_output_text": _sanitize(text) if items[index].full_capture else None,
                "output_chars": output_chars,
                "output_digest": output_digest,
            }
        )

    def record_planning_validation(self, diagnostics: list[dict[str, str]], *, attempt: int) -> None:
        """Attach fixed business-rule codes to the current request's draft."""
        allowed = {
            ("duration_sum_mismatch", "/total_duration_days"),
            ("route_stage_missing", "/stages"),
            ("route_books_mismatch", "/stages/*/books"),
            ("route_goal_mismatch", "/stages/*/goal"),
            ("completed_book_new_learning", "/selection_mode"),
            ("prerequisite_unconfirmed", "/selected_books"),
            ("prerequisite_training_missing", "/long_term_plan_content"),
            ("planning_rule_rejected", "/"),
        }
        if type(attempt) is not int or attempt not in {1, 2}:
            return
        safe = [
            {"code": item["code"], "field_path": item["field_path"], "attempt": attempt}
            for item in diagnostics[:20]
            if isinstance(item, dict) and (item.get("code"), item.get("field_path")) in allowed
        ]
        items = self._current()
        for index in range(len(items) - 1, -1, -1):
            if items[index].agent == "diagnosis_agent":
                items[index] = items[index].model_copy(update={"planning_validation_issues": safe})
                break

    def record_transport(
        self,
        index: int,
        *,
        request_payload: dict[str, Any] | None,
        response_text: str | None,
        reasoning_text: str | None = None,
        timing_details: dict[str, Any] | None = None,
    ) -> None:
        items = self._current()
        request_chars = len(self._json_text(request_payload)) if request_payload else 0
        response_chars = len(response_text or "")
        capture_full = items[index].full_capture
        items[index] = items[index].model_copy(
            update={
                "transport_input": _sanitize(request_payload) if capture_full else None,
                "response_diagnostics": safe_response_diagnostics((timing_details or {}).get("response_diagnostics")) or None,
                "raw_output_text": _sanitize(response_text) if capture_full else None,
                # Provider reasoning is never needed by the learner-facing
                # product.  Keep it only in an explicit internal capture.
                "reasoning_text": _sanitize(reasoning_text) if capture_full else None,
                "transport_input_chars": request_chars,
                "transport_output_chars": response_chars,
                "queue_wait_ms": int((timing_details or {}).get("queue_wait_ms", 0)),
                "provider_duration_ms": int(
                    (timing_details or {}).get("provider_duration_ms", 0)
                ),
                "request_attempt_count": int(
                    (timing_details or {}).get("request_attempt_count", 0)
                ),
                "last_reasoning_at_monotonic": (
                    float((timing_details or {})["last_reasoning_at_monotonic"])
                    if (timing_details or {}).get("last_reasoning_at_monotonic")
                    is not None
                    else None
                ),
                "reasoning_delta_count": int(
                    (timing_details or {}).get("reasoning_delta_count", 0)
                ),
                "response_chars": int(
                    (timing_details or {}).get("response_chars", response_chars)
                ),
            }
        )

    def fail(self, index: int, error: BaseException) -> None:
        items = self._current()
        completed_at_ms = time.time_ns() // 1_000_000
        timing_details = getattr(error, "last_timing_details", None)
        if not isinstance(timing_details, dict):
            timing_details = {}
        reason = _safe_diagnostic_token(
            getattr(error, "reason", None), lowercase=True
        )
        cause = error.__cause__ or error.__context__
        cause_type = _safe_diagnostic_token(
            type(cause).__name__ if cause is not None else None
        )
        status_code = _bounded_diagnostic_int(
            getattr(error, "status_code", None), minimum=100, maximum=599
        )
        retry_count = _bounded_diagnostic_int(
            (getattr(error, "last_error_details", None) or {}).get("retry_count")
            if isinstance(getattr(error, "last_error_details", None), dict)
            else None,
            maximum=100,
        )
        transport_stage = _safe_diagnostic_token(
            (getattr(error, "last_error_details", None) or {}).get(
                "transport_stage"
            )
            if isinstance(getattr(error, "last_error_details", None), dict)
            else None,
            lowercase=True,
        )
        if transport_stage not in _SAFE_TRANSPORT_STAGES:
            transport_stage = None
        items[index] = items[index].model_copy(
            update={
                "error_type": type(error).__name__,
                "response_diagnostics": safe_response_diagnostics(timing_details.get("response_diagnostics")) or None,
                "error_reason": reason,
                "error_cause_type": cause_type,
                "error_status_code": status_code,
                "error_retry_count": retry_count,
                "error_transport_stage": transport_stage,
                "validation_issues": safe_validation_issues(
                    (getattr(error, "last_error_details", None) or {}).get("validation_issues")
                    if isinstance(getattr(error, "last_error_details", None), dict) else None
                ) or None,
                "queue_wait_ms": _bounded_diagnostic_int(
                    timing_details.get("queue_wait_ms"), maximum=86_400_000
                ),
                "provider_duration_ms": _bounded_diagnostic_int(
                    timing_details.get("provider_duration_ms"), maximum=86_400_000
                ),
                "request_attempt_count": _bounded_diagnostic_int(
                    timing_details.get("request_attempt_count"), maximum=100
                ),
                "last_reasoning_at_monotonic": (
                    float(timing_details["last_reasoning_at_monotonic"])
                    if isinstance(
                        timing_details.get("last_reasoning_at_monotonic"),
                        (int, float),
                    )
                    and 0 <= float(timing_details["last_reasoning_at_monotonic"])
                    <= 10_000_000_000
                    else None
                ),
                "reasoning_delta_count": _bounded_diagnostic_int(
                    timing_details.get("reasoning_delta_count"), maximum=10_000_000
                ),
                "response_chars": _bounded_diagnostic_int(
                    timing_details.get("response_chars"), maximum=100_000_000
                ),
                "completed_at_ms": completed_at_ms,
                "total_duration_ms": (
                    completed_at_ms - items[index].started_at_ms
                    if items[index].started_at_ms is not None
                    else None
                ),
            }
        )

    @property
    def items(self) -> list[ModelCallTrace]:
        current = self._items.get()
        if current is not None:
            return list(current)
        with self._latest_lock:
            return list(self._latest_items)
