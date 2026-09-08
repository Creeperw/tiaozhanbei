"""Provider correlation metadata; never derived from user-authored prompt text."""
from contextvars import ContextVar
import hashlib


_SESSION: ContextVar[str | None] = ContextVar("provider_session", default=None)


def bind_provider_session(thread_id: str):
    value = "tcm-" + hashlib.sha256(thread_id.encode("utf-8")).hexdigest()
    return _SESSION.set(value)


def reset_provider_session(token):
    _SESSION.reset(token)


def current_provider_session() -> str | None:
    return _SESSION.get()