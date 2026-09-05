from __future__ import annotations

import inspect
import time
from dataclasses import dataclass
from typing import Any, Callable

from competition_app.runtime.trace import ToolTrace, TraceRecorder


class ToolPermissionError(PermissionError):
    """Raised when an agent is not authorized to invoke a tool."""


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    handler: Callable[..., Any]
    allowed_agents: frozenset[str]


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, ToolDefinition] = {}

    def register(
        self,
        name: str,
        handler: Callable[..., Any],
        *,
        allowed_agents: set[str],
    ) -> None:
        if name in self._tools:
            raise ValueError(f"tool already registered: {name}")
        self._tools[name] = ToolDefinition(name, handler, frozenset(allowed_agents))

    async def invoke(
        self,
        name: str,
        agent: str,
        *,
        trace_recorder: TraceRecorder | None = None,
        safe_input_summary: dict[str, object] | None = None,
        safe_output_summary_factory: Callable[[Any], dict[str, object]] | None = None,
        **kwargs: Any,
    ) -> Any:
        try:
            definition = self._tools[name]
        except KeyError as exc:
            raise KeyError(f"unknown tool: {name}") from exc
        if agent not in definition.allowed_agents:
            raise ToolPermissionError(f"agent {agent} is not allowed to invoke {name}")
        inspect.signature(definition.handler).bind(**kwargs)
        started_at = time.perf_counter()
        try:
            result = definition.handler(**kwargs)
            if inspect.isawaitable(result):
                result = await result
        except Exception as exc:
            self._record_trace(
                trace_recorder,
                name,
                agent,
                "failed",
                started_at,
                safe_input_summary or {},
                {},
                type(exc).__name__,
            )
            raise
        self._record_trace(
            trace_recorder,
            name,
            agent,
            "success",
            started_at,
            safe_input_summary or {},
            safe_output_summary_factory(result) if safe_output_summary_factory else {},
            None,
        )
        return result

    @staticmethod
    def _record_trace(
        recorder: TraceRecorder | None,
        name: str,
        agent: str,
        status: str,
        started_at: float,
        safe_input_summary: dict[str, object],
        safe_output_summary: dict[str, object],
        error_type: str | None,
    ) -> None:
        if recorder is None:
            return
        recorder.record_tool(
            ToolTrace(
                tool_name=name,
                agent=agent,
                status=status,
                duration_ms=int((time.perf_counter() - started_at) * 1000),
                safe_input_summary=safe_input_summary,
                safe_output_summary=safe_output_summary,
                error_type=error_type,
            )
        )