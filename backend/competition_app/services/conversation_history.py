from __future__ import annotations

import re
from typing import Any, Iterable


_ROLLBACK_RE = re.compile(r"<<ROLLBACK:[\s\S]*?>>")
_THINK_BLOCK_RE = re.compile(r"<think>[\s\S]*?</think>", re.IGNORECASE)
_OPEN_THINK_RE = re.compile(r"<think>[\s\S]*$", re.IGNORECASE)
_PROTOCOL_TAG_RE = re.compile(
    r"<<(?:STATUS|EV|REFS|VIDEOS|PLAN|EXEC):[\s\S]*?>>"
)
# UI navigation is presentation metadata, not conversation content.  Keep it
# available to the conversation API so a reopened session can still render
# buttons, while deliberately excluding all model/trace/evidence fields.
# Presentation metadata is kept out of formal dialogue text but can be
# restored by the UI when a conversation is reopened.  ``trace_events`` is a
# compact, redacted collaboration receipt; it is deliberately not included
# in model context or the plain-text history.
_SAFE_UI_METADATA_KEYS = frozenset({"actions", "ui_actions", "trace_events", "traceEvents"})


def sanitize_conversation_content(content: Any) -> str:
    """Keep only formal user/assistant prose for durable conversation history."""

    text = str(content or "")
    rollbacks = list(_ROLLBACK_RE.finditer(text))
    if rollbacks:
        text = text[rollbacks[-1].end() :]
    text = _THINK_BLOCK_RE.sub("", text)
    text = _OPEN_THINK_RE.sub("", text)
    text = _PROTOCOL_TAG_RE.sub("", text)
    text = _ROLLBACK_RE.sub("", text)
    return text.strip()


def sanitize_conversation_messages(
    messages: Iterable[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    """Normalize messages for persistence and later model context.

    Trace events, raw model transport, retrieved evidence and UI metadata have
    separate stores. They are deliberately not copied into formal history.
    """

    clean: list[dict[str, Any]] = []
    for item in messages or []:
        if not isinstance(item, dict):
            continue
        role = str(item.get("role") or "").strip().lower()
        if role not in {"user", "assistant"}:
            continue
        content = sanitize_conversation_content(item.get("content"))
        if not content:
            continue
        message: dict[str, Any] = {"role": role, "content": content}
        for key in ("message_id", "created_at"):
            value = item.get(key)
            if value not in (None, ""):
                message[key] = value
        for key in _SAFE_UI_METADATA_KEYS:
            value = item.get(key)
            if value not in (None, ""):
                message[key] = value
        clean.append(message)
    return clean


def format_dialogue_history(messages: Iterable[dict[str, Any]] | None) -> str:
    return "\n".join(
        f"{item['role']}：{item['content']}"
        for item in sanitize_conversation_messages(messages)
    )


def sanitize_compressed_dialogue_summary(summary: Any) -> str:
    """Keep Memory Agent compression in formal dialogue-only format."""
    text = sanitize_conversation_content(summary)
    if not text:
        return ""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    dialogue_lines = [
        line for line in lines
        if line.startswith("user：") or line.startswith("assistant：")
    ]
    if dialogue_lines:
        return "\n".join(dialogue_lines)[:2_000]
    # Compatibility for older summaries: retain prose as assistant content,
    # never as evidence or tool output.
    return f"assistant：{text}"[:2_000]
