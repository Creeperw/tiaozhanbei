from __future__ import annotations

"""Opt-in, server-local diagnostic tracing.

The normal application protocol deliberately does not expose model prompts,
provider responses, or structured-output attempts.  This module is a separate
best-effort sink for an explicitly enabled server-side debug session.  It is
intentionally independent from the public event stream and from application
persistence.
"""

from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import threading
import time
from typing import Any, Iterator, Mapping
from uuid import uuid4

DEBUG_TRACE_SCHEMA_VERSION = "1.0"
DEFAULT_DEBUG_TRACE_MAX_BYTES = 16 * 1024 * 1024
DEFAULT_DEBUG_TRACE_ROTATE_BYTES = 4 * 1024 * 1024
DEFAULT_DEBUG_TRACE_MAX_EVENTS = 20_000
DEFAULT_DEBUG_TRACE_MAX_EVENT_BYTES = 512 * 1024

_RESERVED_RECORD_FIELDS = frozenset(
    {
        "schema_version",
        "ts",
        "seq",
        "record_type",
        "event",
        "run_id",
        "metadata",
    }
)


def _sanitize(value: Any) -> Any:
    """Load the shared sanitizer only when a trace value is serialized."""

    # Importing ``competition_app.runtime.debug_trace`` must not initialize
    # the runtime package's orchestrator exports. The orchestrator imports the
    # model client, while the model client imports this module. Delaying this
    # dependency until record serialization keeps the graph acyclic.
    from competition_app.runtime.snapshot import _sanitize as sanitize_value

    return sanitize_value(value)


@dataclass(frozen=True)
class DebugTraceConfig:
    """Runtime limits for one opt-in debug-trace manager."""

    enabled: bool = False
    root: Path = Path(".")
    max_bytes: int = DEFAULT_DEBUG_TRACE_MAX_BYTES
    rotate_bytes: int = DEFAULT_DEBUG_TRACE_ROTATE_BYTES
    flush: bool = True
    run_ids: tuple[str, ...] = ()
    allow_live: bool = False
    max_events: int = DEFAULT_DEBUG_TRACE_MAX_EVENTS
    max_event_bytes: int = DEFAULT_DEBUG_TRACE_MAX_EVENT_BYTES

    def __post_init__(self) -> None:
        if self.max_bytes < 1:
            raise ValueError("debug trace max_bytes must be positive")
        if self.rotate_bytes < 1:
            raise ValueError("debug trace rotate_bytes must be positive")
        if self.rotate_bytes > self.max_bytes:
            raise ValueError("debug trace rotate_bytes must not exceed max_bytes")
        if self.max_events < 1:
            raise ValueError("debug trace max_events must be positive")
        if self.max_event_bytes < 256:
            raise ValueError("debug trace max_event_bytes is too small")


class DebugTraceWriter:
    """Thread-safe append-only JSONL writer for one workflow run.

    All write and serialization failures are swallowed.  Diagnostics must never
    change the result of a model call, cancellation path, or business workflow.
    """

    def __init__(
        self,
        root: Path,
        run_id: str,
        *,
        metadata: Mapping[str, Any] | None = None,
        max_bytes: int = DEFAULT_DEBUG_TRACE_MAX_BYTES,
        rotate_bytes: int = DEFAULT_DEBUG_TRACE_ROTATE_BYTES,
        flush: bool = True,
        max_events: int = DEFAULT_DEBUG_TRACE_MAX_EVENTS,
        max_event_bytes: int = DEFAULT_DEBUG_TRACE_MAX_EVENT_BYTES,
    ) -> None:
        self.root = Path(root)
        self.run_id = str(run_id or "run")
        self.max_bytes = max(1, int(max_bytes))
        self.rotate_bytes = max(1, min(int(rotate_bytes), self.max_bytes))
        self.flush = bool(flush)
        self.max_events = max(1, int(max_events))
        self.max_event_bytes = max(256, int(max_event_bytes))
        self._metadata: dict[str, Any] = dict(metadata or {})
        self._safe_run_id = _safe_path_component(self.run_id, fallback="run")
        self._session_id = f"{time.time_ns()}-{uuid4().hex[:12]}"
        self._directory = self.root / self._safe_run_id
        self._base_name = f"trace-{self._session_id}"
        self._file_index = 0
        self._created_paths: list[Path] = []
        self._file = None
        self._path: Path | None = None
        self._current_bytes = 0
        self._total_bytes = 0
        self._event_count = 0
        self._sequence = 0
        self._dropped_events = 0
        self._dropped_summary_written = False
        self._closed = False
        self._disabled = False
        self._lock = threading.RLock()

    @property
    def path(self) -> Path | None:
        """Return the currently active file, if it has been opened."""

        with self._lock:
            return self._path

    @property
    def paths(self) -> tuple[Path, ...]:
        """Return all files created by this writer so far."""

        with self._lock:
            return tuple(self._created_paths)

    @property
    def event_count(self) -> int:
        with self._lock:
            return self._event_count

    @property
    def dropped_events(self) -> int:
        with self._lock:
            return self._dropped_events

    @property
    def closed(self) -> bool:
        with self._lock:
            return self._closed

    def update_metadata(self, values: Mapping[str, Any] | None = None, **kwargs: Any) -> None:
        """Update stable run metadata without affecting business execution."""

        if values:
            with self._lock:
                self._metadata.update(dict(values))
        if kwargs:
            with self._lock:
                self._metadata.update(kwargs)

    def record(self, record_type: str, **fields: Any) -> bool:
        """Append one bounded, sanitized event and return whether it was stored."""

        with self._lock:
            if self._closed or self._disabled:
                return False
            try:
                return self._record_locked(record_type, fields)
            except Exception:
                # A malformed model object, path permission issue, disk-full
                # condition, or serializer failure is never a workflow failure.
                self._dropped_events += 1
                self._close_file_locked()
                return False

    def record_runtime_event(self, event: Mapping[str, Any]) -> bool:
        """Store one internal runtime event without invoking the public sink."""

        return self.record("runtime_event", runtime_event=dict(event))

    def close(self) -> None:
        """Flush and close the writer; safe to call more than once."""

        with self._lock:
            if self._closed:
                return
            try:
                if self._dropped_events and not self._dropped_summary_written:
                    self._dropped_summary_written = True
                    self._record_locked(
                        "debug_events_dropped",
                        {"dropped_events": self._dropped_events},
                    )
                if self._file is not None:
                    self._file.flush()
            except Exception:
                pass
            self._close_file_locked()
            self._closed = True

    def __enter__(self) -> "DebugTraceWriter":
        return self

    def __exit__(self, _exc_type, _exc, _tb) -> None:
        self.close()

    def _record_locked(self, record_type: str, fields: Mapping[str, Any]) -> bool:
        if self._event_count >= self.max_events:
            self._dropped_events += 1
            return False

        self._sequence += 1
        metadata = _bounded_value(_sanitize(self._metadata))
        if not isinstance(metadata, dict):
            metadata = {}
        record: dict[str, Any] = {
            "schema_version": DEBUG_TRACE_SCHEMA_VERSION,
            "ts": time.time_ns() // 1_000_000,
            "seq": self._sequence,
            "record_type": _bounded_token(record_type, fallback="event"),
            "event": _bounded_token(record_type, fallback="event"),
            "run_id": _bounded_text(self.run_id, 512),
            "metadata": metadata,
        }
        # Make the most useful correlation keys queryable without copying the
        # complete metadata object into every consumer-specific structure.
        for key in (
            "execution_id",
            "thread_id",
            "case_id",
            "learner_id",
            "trace_id",
            "task_id",
            "agent",
            "step_id",
            "call_id",
            "attempt_id",
        ):
            value = self._metadata.get(key)
            if value not in (None, ""):
                record[key] = _bounded_value(_sanitize(value), max_depth=2)

        call_context = current_debug_call_context()
        if call_context:
            for key, value in call_context.items():
                if key in _RESERVED_RECORD_FIELDS or value in (None, ""):
                    continue
                record[key] = _bounded_value(_sanitize(value), max_depth=3)

        for key, value in fields.items():
            safe_key = _bounded_text(str(key), 160)
            if safe_key in _RESERVED_RECORD_FIELDS:
                safe_key = f"field_{safe_key}"
            record[safe_key] = _bounded_value(_sanitize(value))

        encoded = _encode_record(record)
        if len(encoded) > self.max_event_bytes:
            # Keep the correlation envelope and replace only the potentially
            # large payload fields. This still leaves an auditable record that
            # the event was observed but bounded by the configured limit.
            record = _bounded_record_for_event(record, self.max_event_bytes)
            encoded = _encode_record(record)
        if len(encoded) > self.max_event_bytes:
            self._dropped_events += 1
            return False
        if self._total_bytes + len(encoded) > self.max_bytes:
            self._dropped_events += 1
            return False

        self._ensure_file_locked(len(encoded))
        if self._file is None:
            self._dropped_events += 1
            return False
        if self._current_bytes + len(encoded) > self.rotate_bytes and self._current_bytes:
            self._rotate_locked()
            self._ensure_file_locked(len(encoded))
        if self._file is None:
            self._dropped_events += 1
            return False
        try:
            self._file.write(encoded)
            if self.flush:
                self._file.flush()
            self._current_bytes += len(encoded)
            self._total_bytes += len(encoded)
            self._event_count += 1
            return True
        except Exception:
            self._disabled = True
            self._dropped_events += 1
            self._close_file_locked()
            return False

    def _ensure_file_locked(self, required_bytes: int) -> None:
        if self._file is not None:
            return
        try:
            self._directory.mkdir(parents=True, exist_ok=True, mode=0o700)
            _restrict_directory(self.root)
            _restrict_directory(self._directory)
            path = self._directory / f"{self._base_name}.{self._file_index:03d}.jsonl"
            flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND
            descriptor = os.open(path, flags, 0o600)
            # Records are encoded before the file boundary so byte limits are
            # measured accurately.  Open the descriptor in binary append mode
            # rather than passing bytes to a text-mode file object.
            self._file = os.fdopen(descriptor, "ab", buffering=0)
            try:
                os.chmod(path, 0o600)
            except OSError:
                pass
            self._path = path
            if path not in self._created_paths:
                self._created_paths.append(path)
            self._current_bytes = path.stat().st_size
            # A newly generated session should not collide, but if an external
            # process pre-created the path, account for it before writing.
            if self._current_bytes + required_bytes > self.rotate_bytes:
                self._rotate_locked()
        except Exception:
            self._disabled = True
            self._close_file_locked()

    def _rotate_locked(self) -> None:
        self._close_file_locked()
        self._file_index += 1
        self._current_bytes = 0
        self._path = None

    def _close_file_locked(self) -> None:
        file_object = self._file
        self._file = None
        if file_object is None:
            return
        try:
            file_object.flush()
        except Exception:
            pass
        try:
            file_object.close()
        except Exception:
            pass


class DebugTraceManager:
    """Create run-scoped writers according to server configuration."""

    def __init__(self, config: DebugTraceConfig, *, mode: str = "stub") -> None:
        self.config = config
        self.mode = str(mode or "stub").strip().lower()

    def enabled_for(self, *run_ids: str | None) -> bool:
        if not self.config.enabled:
            return False
        if self.mode == "live" and not self.config.allow_live:
            return False
        filters = tuple(item for item in self.config.run_ids if str(item).strip())
        if not filters:
            return True
        candidates = {str(item).strip() for item in run_ids if str(item or "").strip()}
        return any(
            pattern == "*" or candidate == pattern
            for pattern in filters
            for candidate in candidates
        )

    def open(
        self,
        run_id: str,
        *,
        metadata: Mapping[str, Any] | None = None,
    ) -> DebugTraceWriter | None:
        execution_id = (metadata or {}).get("execution_id")
        if not self.enabled_for(run_id, execution_id):
            return None
        try:
            return DebugTraceWriter(
                self.config.root,
                run_id,
                metadata=metadata,
                max_bytes=self.config.max_bytes,
                rotate_bytes=self.config.rotate_bytes,
                flush=self.config.flush,
                max_events=self.config.max_events,
                max_event_bytes=self.config.max_event_bytes,
            )
        except Exception:
            return None


_DEBUG_TRACE: ContextVar[DebugTraceWriter | None] = ContextVar(
    "competition_debug_trace", default=None
)
_DEBUG_CALL_CONTEXT: ContextVar[dict[str, Any] | None] = ContextVar(
    "competition_debug_trace_call_context", default=None
)


def bind_debug_trace(writer: DebugTraceWriter | None) -> Token:
    return _DEBUG_TRACE.set(writer)


def reset_debug_trace(token: Token) -> None:
    _DEBUG_TRACE.reset(token)


def current_debug_trace() -> DebugTraceWriter | None:
    return _DEBUG_TRACE.get()


def update_debug_trace_metadata(
    values: Mapping[str, Any] | None = None,
    **kwargs: Any,
) -> bool:
    writer = _DEBUG_TRACE.get()
    if writer is None:
        return False
    try:
        writer.update_metadata(values, **kwargs)
        return True
    except Exception:
        return False


def record_debug_trace(record_type: str, **fields: Any) -> bool:
    writer = _DEBUG_TRACE.get()
    if writer is None:
        return False
    return writer.record(record_type, **fields)


def current_debug_call_context() -> dict[str, Any]:
    value = _DEBUG_CALL_CONTEXT.get()
    return dict(value) if isinstance(value, dict) else {}


@contextmanager
def bind_debug_call_context(**values: Any) -> Iterator[None]:
    """Attach model-call correlation fields to nested provider events."""

    merged = current_debug_call_context()
    merged.update({key: value for key, value in values.items() if value not in (None, "")})
    token = _DEBUG_CALL_CONTEXT.set(merged)
    try:
        yield
    finally:
        _DEBUG_CALL_CONTEXT.reset(token)


def parse_debug_trace_run_ids(raw: str | None) -> tuple[str, ...]:
    if not raw:
        return ()
    return tuple(
        dict.fromkeys(
            item.strip()
            for item in re.split(r"[,;\s]+", str(raw))
            if item.strip()
        )
    )


def _safe_path_component(value: Any, *, fallback: str) -> str:
    original = str(value or "").strip()
    if not original or original in {".", ".."}:
        return fallback
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", original).strip("._")
    if not safe:
        safe = fallback
    if len(safe) > 100:
        safe = safe[:88]
    if safe != original:
        digest = hashlib.sha256(original.encode("utf-8", "replace")).hexdigest()[:10]
        safe = f"{safe[:88]}-{digest}"
    return safe[:120]


def _bounded_token(value: Any, *, fallback: str) -> str:
    text = str(value or "").strip()
    text = re.sub(r"[^A-Za-z0-9._:-]+", "_", text)
    return text[:120] or fallback


def _bounded_text(value: Any, limit: int = 4_000) -> str:
    text = str(value or "")
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 32)] + f"…[truncated:{len(text) - limit}]"


def _bounded_value(
    value: Any,
    *,
    depth: int = 0,
    max_depth: int = 8,
    max_items: int = 400,
    max_text: int = 100_000,
) -> Any:
    if depth > max_depth:
        return "[TRUNCATED_DEPTH]"
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return _bounded_text(_sanitize(value), max_text)
    if isinstance(value, bytes):
        return _bounded_text(value.decode("utf-8", "replace"), max_text)
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        items = list(value.items())
        for index, (key, item) in enumerate(items):
            if index >= max_items:
                result["__omitted_items__"] = len(items) - max_items
                break
            result[_bounded_text(key, 200)] = _bounded_value(
                item,
                depth=depth + 1,
                max_depth=max_depth,
                max_items=max_items,
                max_text=max_text,
            )
        return result
    if isinstance(value, (list, tuple, set)):
        items = list(value)
        result = [
            _bounded_value(
                item,
                depth=depth + 1,
                max_depth=max_depth,
                max_items=max_items,
                max_text=max_text,
            )
            for item in items[:max_items]
        ]
        if len(items) > max_items:
            result.append(f"[OMITTED_ITEMS:{len(items) - max_items}]")
        return result
    if hasattr(value, "model_dump"):
        try:
            return _bounded_value(
                value.model_dump(mode="json"),
                depth=depth + 1,
                max_depth=max_depth,
                max_items=max_items,
                max_text=max_text,
            )
        except Exception:
            return f"[{type(value).__name__}]"
    return _bounded_text(_sanitize(str(value)), max_text)


def _encode_record(record: Mapping[str, Any]) -> bytes:
    return (
        json.dumps(record, ensure_ascii=False, separators=(",", ":"), default=str).encode(
            "utf-8", "replace"
        )
        + b"\n"
    )


def _bounded_record_for_event(record: dict[str, Any], limit: int) -> dict[str, Any]:
    compact = dict(record)
    # Preserve correlation metadata and replace large content-bearing values in
    # descending order of likely size. The record remains valid JSONL even when
    # a provider sends an unexpectedly large prompt or response.
    for key in (
        "request_payload",
        "response_text",
        "reasoning_text",
        "content_deltas",
        "reasoning_deltas",
        "parsed_json",
        "runtime_event",
        "fields",
    ):
        if key in compact:
            compact[key] = "[TRUNCATED_EVENT_PAYLOAD]"
            if len(_encode_record(compact)) <= limit:
                return compact
    # Last resort: retain only a small correlation envelope.
    keep = {
        key: value
        for key, value in compact.items()
        if key
        in {
            "schema_version",
            "ts",
            "seq",
            "record_type",
            "event",
            "run_id",
            "execution_id",
            "thread_id",
            "case_id",
            "agent",
            "step_id",
            "call_id",
            "attempt_id",
        }
    }
    keep["payload_truncated"] = True
    return keep


def _restrict_directory(path: Path) -> None:
    try:
        if path.exists():
            os.chmod(path, 0o700)
    except OSError:
        pass


__all__ = [
    "DEBUG_TRACE_SCHEMA_VERSION",
    "DEFAULT_DEBUG_TRACE_MAX_BYTES",
    "DEFAULT_DEBUG_TRACE_ROTATE_BYTES",
    "DebugTraceConfig",
    "DebugTraceManager",
    "DebugTraceWriter",
    "bind_debug_call_context",
    "bind_debug_trace",
    "current_debug_call_context",
    "current_debug_trace",
    "parse_debug_trace_run_ids",
    "record_debug_trace",
    "reset_debug_trace",
    "update_debug_trace_metadata",
]
