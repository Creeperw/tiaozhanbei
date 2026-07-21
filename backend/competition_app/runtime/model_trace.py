from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from competition_app.runtime.snapshot import _sanitize


class ModelCallTrace(BaseModel):
    """A secret-safe record of one raw model request and response."""

    sequence: int
    agent: str
    raw_input: dict[str, Any]
    transport_input: dict[str, Any] | None = None
    raw_output_text: str | None = None
    reasoning_text: str | None = None
    raw_output: dict[str, Any] | None = None
    error_type: str | None = None


class ModelTraceRecorder:
    def __init__(self) -> None:
        self._items: list[ModelCallTrace] = []

    def begin(self, agent: str, payload: dict[str, Any]) -> int:
        self._items.append(
            ModelCallTrace(
                sequence=len(self._items) + 1,
                agent=agent,
                raw_input=_sanitize(payload),
            )
        )
        return len(self._items) - 1

    def reset(self) -> None:
        self._items.clear()

    def succeed(self, index: int, payload: dict[str, Any]) -> None:
        self._items[index] = self._items[index].model_copy(
            update={"raw_output": _sanitize(payload)}
        )

    def record_transport(
        self,
        index: int,
        *,
        request_payload: dict[str, Any] | None,
        response_text: str | None,
        reasoning_text: str | None = None,
    ) -> None:
        self._items[index] = self._items[index].model_copy(
            update={
                "transport_input": _sanitize(request_payload),
                "raw_output_text": _sanitize(response_text),
                "reasoning_text": _sanitize(reasoning_text),
            }
        )

    def fail(self, index: int, error: BaseException) -> None:
        self._items[index] = self._items[index].model_copy(
            update={"error_type": type(error).__name__}
        )

    @property
    def items(self) -> list[ModelCallTrace]:
        return list(self._items)