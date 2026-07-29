from __future__ import annotations

import asyncio
from contextvars import ContextVar
from typing import Any, Callable, Sequence

from competition_app.llm.base import ChatModel
from competition_app.llm.openai_compatible import ModelResponseError


class FailoverChatModel(ChatModel):
    """Keep using the first healthy model and retire failed candidates in order."""

    def __init__(self, candidates: Sequence[ChatModel]) -> None:
        if not candidates:
            raise ValueError("at least one chat model candidate is required")
        self._candidates = list(candidates)
        self._active_index = 0
        self._state_lock = asyncio.Lock()
        self._transport: ContextVar[dict[str, Any] | None] = ContextVar(
            f"failover_transport_{id(self)}", default=None
        )
        self.failover_history: list[dict[str, Any]] = []

    @property
    def last_request_payload(self) -> dict[str, Any] | None:
        transport = self._transport.get() or {}
        return transport.get("request_payload")

    @property
    def last_response_text(self) -> str | None:
        transport = self._transport.get() or {}
        return transport.get("response_text")

    @property
    def last_reasoning_text(self) -> str | None:
        transport = self._transport.get() or {}
        return transport.get("reasoning_text")

    @property
    def last_error_details(self) -> dict[str, Any] | None:
        transport = self._transport.get() or {}
        return transport.get("error_details")

    @property
    def model(self) -> str:
        return str(getattr(self._candidates[self._active_index], "model", "unknown"))

    @property
    def candidate_models(self) -> tuple[str, ...]:
        return tuple(str(getattr(item, "model", "unknown")) for item in self._candidates)

    async def complete_json(
        self,
        role: str,
        payload: dict[str, Any],
        on_delta: Callable[[str], None] | None = None,
    ) -> dict[str, Any]:
        async with self._state_lock:
            start_index = self._active_index
        call_failovers: list[dict[str, Any]] = []
        last_error: ModelResponseError | None = None
        result_validator = payload.get("_result_validator")
        provider_payload = {
            key: value for key, value in payload.items() if key != "_result_validator"
        }
        for index in range(start_index, len(self._candidates)):
            candidate = self._candidates[index]
            buffered_deltas: list[str] = []
            try:
                result = await candidate.complete_json(
                    role,
                    provider_payload,
                    on_delta=buffered_deltas.append if on_delta is not None else None,
                )
                if callable(result_validator):
                    result = result_validator(result)
            except (TypeError, ValueError) as exc:
                exc = ModelResponseError(
                    "Chat model returned business-invalid structured output",
                    reason="business_schema_invalid",
                    failover_eligible=True,
                )
                self._copy_transport(candidate, call_failovers)
                last_error = exc
                if index == len(self._candidates) - 1:
                    raise exc
                next_model = str(getattr(self._candidates[index + 1], "model", "unknown"))
                failover = {
                    "from_model": str(getattr(candidate, "model", "unknown")),
                    "to_model": next_model,
                    "reason": exc.reason,
                    "status_code": None,
                }
                call_failovers.append(failover)
                async with self._state_lock:
                    self.failover_history.append(failover)
                    self.failover_history[:] = self.failover_history[-100:]
                    self._active_index = max(self._active_index, index + 1)
                continue
            except ModelResponseError as exc:
                self._copy_transport(candidate, call_failovers)
                last_error = exc
                if not exc.failover_eligible or index == len(self._candidates) - 1:
                    raise
                next_model = str(getattr(self._candidates[index + 1], "model", "unknown"))
                failover = {
                    "from_model": str(getattr(candidate, "model", "unknown")),
                    "to_model": next_model,
                    "reason": exc.reason,
                    "status_code": exc.status_code,
                }
                call_failovers.append(failover)
                async with self._state_lock:
                    self.failover_history.append(failover)
                    self.failover_history[:] = self.failover_history[-100:]
                    self._active_index = max(self._active_index, index + 1)
                continue
            async with self._state_lock:
                self._active_index = max(self._active_index, index)
            self._copy_transport(candidate, call_failovers)
            if on_delta is not None:
                for delta in buffered_deltas:
                    on_delta(delta)
            return result
        if last_error is not None:
            raise last_error
        raise ModelResponseError("No chat model candidate was available", reason="unavailable")

    def _copy_transport(
        self,
        candidate: ChatModel,
        call_failovers: list[dict[str, Any]],
    ) -> None:
        request_payload = getattr(candidate, "last_request_payload", None)
        if call_failovers:
            request_payload = {
                **(request_payload or {}),
                "failovers": list(call_failovers),
            }
        self._transport.set(
            {
                "request_payload": request_payload,
                "response_text": getattr(candidate, "last_response_text", None),
                "reasoning_text": getattr(candidate, "last_reasoning_text", None),
                "error_details": getattr(candidate, "last_error_details", None),
            }
        )
