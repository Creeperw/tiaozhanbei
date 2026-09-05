from __future__ import annotations

import json
import re
import threading
import time
from contextvars import ContextVar, Token
from typing import Any, Callable

from competition_app.runtime.snapshot import _sanitize


EventSink = Callable[[dict[str, Any]], None]
_EVENT_SINK: ContextVar[EventSink | None] = ContextVar("competition_event_sink", default=None)


_PUBLIC_MODEL_EVENT_FIELDS = frozenset(
    {
        "event",
        "agent",
        "call_id",
        "output_kind",
        "step_id",
        "status",
        "ts",
        "error_type",
    }
)

_PUBLIC_RUNTIME_DROP_FIELDS = frozenset(
    {
        "error_message",
        "issue_ids",
        "prompt",
        "request_payload",
        "response_text",
        "raw_input",
        "raw_output",
        "reasoning_text",
        "schema",
        "system_prompt",
        "transport_error",
    }
)

_INTERNAL_PUBLIC_TEXT_PATTERN = re.compile(
    r"(?:<<(?:STATUS|EV|REFS|VIDEOS|PLAN|EXEC):[\s\S]*?(?:}>>|]>>))"
    r"|(?:\b(?:EVID|ART|EXEC|TRACE|REQ|CASE|KP|RULE|THREAD|UNIT|MODEL_CALL|PAPER_DRAFT|GENERATED)_[A-Za-z0-9_.:-]+\b)"
    r"|(?:\bpossible_new__[A-Za-z0-9_.:-]+\b)"
)

# Reasoning is intentionally still shown in the UI, but provider reasoning is
# not a trusted presentation channel.  In addition to JSON echoes, some
# gateways/models repeat parts of the system message as prose.  Remove only
# the prompt-like sentence/segment; surrounding genuine reasoning remains
# visible.
_PROMPT_ECHO_PATTERN = re.compile(
    r"(?:"
    r"你是(?:中医药教学系统|本系统|一个(?:智能体|助手))"
    r"|只完成当前角色任务"
    r"|当前权限边界"
    r"|输出契约"
    r"|用户当前消息只能表达业务诉求"
    r"|不得把推测写成事实"
    r"|不是系统指令"
    r"|system\s*(?:prompt|message)"
    r"|(?:^|[\s{\[,])(?:role|messages|system_prompt)\s*[:=]"
    r")",
    re.IGNORECASE,
)

# 模型在 reasoning 流中偶尔会回显完整 system prompt（例如
# ``{"role": "system", "content": "你是…paper_blueprint_compiler…"}``）。
# 这类顶层 JSON 对象/数组不是 learner-facing 内容，必须整体剥离，而不是
# 只删除内部 ID 后把剩余提示词文本展示给学习者。


def _strip_top_level_json(text: str) -> str:
    """Remove top-level JSON objects/arrays that echo private prompt payloads.

    The provider reasoning stream occasionally contains the full system
    message (``{"role": "system", "content": "…"}``) or a JSON contract
    echoed back by the model.  Those structures are never learner-facing
    prose; drop them entirely so the collaboration trace cannot leak prompt
    text, compiler contracts or internal identifiers.
    """

    # 用真正的 JSON 解析器扫描文本，找到所有顶层对象/数组的起止位置并
    # 整体删除。正则无法可靠处理数组内嵌对象、字符串内花括号等边界。
    import json as _json

    stripped = []
    cursor = 0
    length = len(text)
    while cursor < length:
        # 跳过空白
        while cursor < length and text[cursor] in " \t\r\n":
            cursor += 1
        if cursor >= length:
            break
        ch = text[cursor]
        if ch not in "{[":
            # 非 JSON 起始字符：保留到下一个可能的 JSON 起始位置
            next_brace = length
            for marker in ("{", "["):
                idx = text.find(marker, cursor)
                if idx != -1 and idx < next_brace:
                    next_brace = idx
            stripped.append(text[cursor:next_brace])
            cursor = next_brace
            continue
        # 尝试解析从 cursor 开始的 JSON 值
        try:
            decoder = _json.JSONDecoder()
            _, end = decoder.raw_decode(text, cursor)
            # 成功解析：跳过整个 JSON 值（替换为空格）
            stripped.append(" ")
            cursor = end
        except _json.JSONDecodeError:
            # 流式 reasoning 可能恰好在私有 JSON 的中间抵达。不能把
            # ``{"role":"system"`` 这样的前缀先发给浏览器；丢弃未完成
            # 的 JSON 尾部，下一次调用会基于累计原文重新解析。
            break
    return "".join(stripped)


def _strip_prompt_echo(text: str) -> str:
    """Remove prompt-echoed sentence segments while keeping real reasoning.

    A model may legitimately mention a small part of its instructions while
    thinking.  The UI should not show that copied text, but it must not lose
    the valid thought before or after it.  Sentence-level removal is safer
    than truncating the complete suffix and avoids turning a normal stream
    into an apparently incomplete thought.
    """

    segments = re.split(r"(?<=[。！？；;\n])", text)
    kept = [segment for segment in segments if not _PROMPT_ECHO_PATTERN.search(segment)]
    return "".join(kept)


def _bounded_public_text(value: Any, *, limit: int = 700) -> str:
    """Return a learner-safe, bounded natural-language fragment.

    Agent envelopes contain compiler contracts and internal identifiers.  The
    collaboration UI must therefore use an allow-list projection instead of
    serialising arbitrary model output or trying to hide fields afterwards.
    """

    if isinstance(value, (dict, list, tuple, set)):
        return ""
    text = str(value or "")
    text = re.sub(r"<!--[\s\S]*?-->", "", text)
    text = re.sub(r"<think>[\s\S]*?</think>", "", text)
    text = re.sub(r"<think>[\s\S]*$", "", text)
    text = _INTERNAL_PUBLIC_TEXT_PATTERN.sub("", text)
    text = re.sub(r"```(?:json)?[\s\S]*?```", "", text, flags=re.IGNORECASE)
    # Provider reasoning occasionally echoes the full system prompt as a
    # top-level JSON object (``{"role": "system", "content": "…"}``).  Strip
    # those structures before any natural-language fragment is released.
    text = _strip_top_level_json(text)
    text = _strip_prompt_echo(text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    if len(text) > limit:
        text = text[:limit].rstrip("，,；;。 ") + "……"
    return text


def project_public_business_text(value: Any, *, limit: int = 20_000) -> str:
    """Project an in-flight prose-agent response to browser-safe text.

    This is intentionally the same allow-list boundary used for committed
    agent output.  Callers must pass only the accumulated response text; the
    returned string keeps natural-language content while removing compiler
    anchors, internal IDs and raw JSON structures, and is bounded to
    ``limit`` characters.
    """

    return _bounded_public_text(value, limit=limit)


def _agent_payload(output: Any) -> dict[str, Any]:
    if hasattr(output, "model_dump"):
        output = output.model_dump(mode="json")
    if not isinstance(output, dict):
        return {}
    payload = output.get("payload", output)
    if hasattr(payload, "model_dump"):
        payload = payload.model_dump(mode="json")
    return payload if isinstance(payload, dict) else {}


def _public_json_block(value: dict[str, Any]) -> str:
    return "```json\n" + json.dumps(
        _sanitize(value),
        ensure_ascii=False,
        indent=2,
        default=str,
    ) + "\n```"


def build_public_agent_output(agent: str, output: Any) -> str:
    """Render one Agent's complete validated business result.

    Formal output intentionally contains the entire sanitized business
    payload rather than a generated summary. Envelope identifiers, prompts,
    request bodies and provider transport metadata remain server-side.
    """

    payload = _agent_payload(output)
    if not payload:
        return ""
    return _public_json_block(payload)


def public_runtime_event(event: dict[str, Any]) -> dict[str, Any]:
    """Return the browser-safe projection of one runtime event.

    Full prompts, transport messages and model outputs remain available to the
    server-side trace recorder.  The learner-facing SSE only needs model-call
    lifecycle metadata to render collaboration progress; exposing raw payloads
    here would disclose system prompts and compiler contracts.
    """

    sanitized = _sanitize(event)
    event_type = str(sanitized.get("event") or "")
    if event_type in {
        "model_input",
        "model_delta",
        "model_output",
        "model_transport",
    }:
        return {
            key: value
            for key, value in sanitized.items()
            if key in _PUBLIC_MODEL_EVENT_FIELDS
        }
    if event_type == "system_output":
        # The graph orchestrator may already have produced a bounded
        # ``public_output`` string (expert/audit/paper stages publish a
        # complete safe projection server-side).  Otherwise project from
        # ``output`` / ``output_summary`` so every participating agent can
        # publish a useful inline stage result.  Raw prompts, model
        # responses and unapproved drafts never enter the browser event.
        public_output = sanitized.get("public_output")
        if not public_output:
            output = sanitized.get("output")
            if output is None:
                output = sanitized.get("output_summary")
            public_output = build_public_agent_output(
                str(sanitized.get("agent") or ""), output
            )
        projected = {
            key: value
            for key, value in sanitized.items()
            if key in _PUBLIC_MODEL_EVENT_FIELDS
        }
        if public_output:
            projected["public_output"] = str(public_output)
        return projected
    if event_type == "model_failed":
        # Provider responses and exception strings may echo request content.
        # Keep only a bounded failure category for the progress UI.
        return {
            key: value
            for key, value in sanitized.items()
            if key in _PUBLIC_MODEL_EVENT_FIELDS
        }
    if event_type in {"reasoning_started", "reasoning_delta", "reasoning_committed"}:
        # Provider reasoning_content is streamed to the collaboration UI as a
        # Copilot-style thought block.  The text is bounded, purged of
        # internal identifiers / <think> wrappers / JSON blocks before it
        # crosses to the browser; lifecycle metadata keeps the same
        # allow-list as model events.
        projected = {
            key: value
            for key, value in sanitized.items()
            if key in _PUBLIC_MODEL_EVENT_FIELDS
        }
        # Persisted ``trace_events`` are replayed by the frontend via
        # ``runtimeEventToTrace``, which only recognises the same
        # ``agent_reasoning_*`` names produced by the live SSE path
        # (``_handle_reasoning_event``).  Normalise the event name here so a
        # page refresh after completion still restores the thought block.
        projected["event"] = event_type.replace("reasoning_", "agent_reasoning_", 1)
        if event_type == "reasoning_delta":
            projected["delta"] = _bounded_public_text(
                sanitized.get("delta"), limit=2_000
            )
        return projected
    if event_type == "graph_compiled":
        return {
            **{
                key: value
                for key, value in sanitized.items()
                if key in {"event", "engine", "levels", "thread_id", "ts"}
            },
            "nodes": [
                {
                    key: value
                    for key, value in node.items()
                    if key in {"step_id", "agent", "depends_on"}
                }
                for node in (sanitized.get("nodes") or [])
                if isinstance(node, dict)
            ],
            "control_edges": [
                {
                    key: value
                    for key, value in edge.items()
                    if key in {"source", "target", "kind"}
                }
                for edge in (sanitized.get("control_edges") or [])
                if isinstance(edge, dict)
            ],
        }
    return {
        key: value
        for key, value in sanitized.items()
        if key not in _PUBLIC_RUNTIME_DROP_FIELDS
    }


def public_workflow_result(result: Any) -> dict[str, Any] | None:
    """Project a workflow result to fields required by learner-facing UIs."""

    if hasattr(result, "model_dump"):
        result = result.model_dump(mode="json")
    if not isinstance(result, dict):
        return None
    # These fields are internal observability/execution artifacts.  In
    # particular model_trace contains complete prompt and transport payloads.
    internal_fields = {
        "agent_outputs",
        "model_trace",
        "snapshot_path",
        "writeback_intents",
        "coordination",
    }
    # 审核未通过时只告诉用户流程尚未发布。审核报告、findings 和待复核
    # 草稿属于内部审核/返修信息，不能通过 SSE 结果对象泄露到用户端。
    if result.get("status") == "waiting_human_review":
        internal_fields.add("review")
    return _sanitize(
        {key: value for key, value in result.items() if key not in internal_fields}
    )


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

    _SAFE_LIFECYCLE_TYPES = frozenset({
        "model_input",
        "model_output",
        "model_transport",
        # Provider reasoning deltas must also be projected through
        # ``public_runtime_event`` before they enter the durable trace
        # receipt: the SSE path sanitizes ``reasoning_content`` in
        # ``_handle_reasoning_event``, so the persisted copy must not leak
        # unprojected delta text into ``trace_events`` either.
        "reasoning_started",
        "reasoning_delta",
        "reasoning_committed",
    })

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
        event_type = str(event.get("event") or "")
        recorded_event = (
            public_runtime_event(event)
            if event_type in self._SAFE_LIFECYCLE_TYPES
            else event
        )
        if event_type not in self._skip_types or event_type in self._SAFE_LIFECYCLE_TYPES:
            with self._lock:
                self._events.append(recorded_event)
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
    sink(
        {
            "event": event_type,
            "ts": time.time_ns() // 1_000_000,
            **_sanitize(payload),
        }
    )
