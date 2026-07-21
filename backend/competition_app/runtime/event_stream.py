from __future__ import annotations

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


def emit_runtime_event(event_type: str, **payload: Any) -> None:
    sink = _EVENT_SINK.get()
    if sink is None:
        return
    sink({"event": event_type, **_sanitize(payload)})
