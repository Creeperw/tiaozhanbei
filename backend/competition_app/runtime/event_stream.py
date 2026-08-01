from __future__ import annotations

import threading
from contextvars import ContextVar, Token
from typing import Any, Callable

from competition_app.runtime.snapshot import _sanitize


EventSink = Callable[[dict[str, Any]], None]
_EVENT_SINK: ContextVar[EventSink | None] = ContextVar("competition_event_sink", default=None)


def bind_event_sink(sink: EventSink) -> Token:
    return _EVENT_SINK.set(sink)


def reset_event_sink(token: Token) -> None:
    _EVENT_SINK.reset(token)


def has_event_sink() -> bool:
    return _EVENT_SINK.get() is not None


def current_event_sink() -> EventSink | None:
    return _EVENT_SINK.get()


class RecordingEventSink:
    """Record low-volume UI events while forwarding the complete SSE stream."""

    def __init__(
        self,
        sink: EventSink | None,
        skip_types: frozenset[str] | None = None,
    ) -> None:
        self._sink = sink
        self._skip_types = skip_types or frozenset()
        self._lock = threading.Lock()
        self._events: list[dict[str, Any]] = []

    def __call__(self, event: dict[str, Any]) -> None:
        if event.get("event") not in self._skip_types:
            with self._lock:
                self._events.append(event)
        if self._sink is not None:
            self._sink(event)

    def drain(self) -> list[dict[str, Any]]:
        with self._lock:
            events, self._events = self._events, []
        return events


def bind_recording_sink(
    sink: EventSink | None,
    skip_types: frozenset[str] | None = None,
) -> Token:
    return _EVENT_SINK.set(RecordingEventSink(sink, skip_types))


def drain_recording_sink() -> list[dict[str, Any]]:
    sink = _EVENT_SINK.get()
    if isinstance(sink, RecordingEventSink):
        return sink.drain()
    return []


def emit_runtime_event(event_type: str, **payload: Any) -> None:
    sink = _EVENT_SINK.get()
    if sink is None:
        return
    sink({"event": event_type, **_sanitize(payload)})
