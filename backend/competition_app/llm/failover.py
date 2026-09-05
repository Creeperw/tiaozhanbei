from __future__ import annotations

import asyncio
from contextvars import ContextVar
from typing import Any, Callable, Sequence

from competition_app.llm.base import ChatModel
from competition_app.llm.openai_compatible import (
    ModelResponseError,
    _run_result_validator,
)


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

    async def complete_text(
        self,
        role: str,
        payload: dict[str, Any],
        on_delta: Callable[[str], None] | None = None,
        on_reasoning: Callable[[str], None] | None = None,
    ) -> str:
        """Fail over natural-language calls without changing them into JSON calls."""
        self._begin_call_transport()
        async with self._state_lock:
            start_index = self._active_index
        call_failovers: list[dict[str, Any]] = []
        last_error: ModelResponseError | None = None
        for index in range(start_index, len(self._candidates)):
            candidate = self._candidates[index]
            buffered: list[str] = []
            buffered_reasoning: list[str] = []
            try:
                result = await candidate.complete_text(
                    role,
                    payload,
                    on_delta=buffered.append if on_delta is not None else None,
                    on_reasoning=(
                        # 2026-08-23: reasoning 直通不缓冲，保证 Copilot
                        # 风格思考块实时流式到达前端。若候选失败切换，新
                        # 候选的推理继续按流发送即可（失败属少数场景）。
                        on_reasoning
                    ),
                )
            except ModelResponseError as exc:
                self._copy_transport(candidate, call_failovers, succeeded=False)
                last_error = exc
                if not exc.failover_eligible or index == len(self._candidates) - 1:
                    raise
                next_model = str(
                    getattr(self._candidates[index + 1], "model", "unknown")
                )
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
            self._copy_transport(candidate, call_failovers, succeeded=True)
            if on_delta is not None:
                for delta in buffered:
                    on_delta(delta)
            return str(result)
        if last_error is not None:
            raise last_error
        raise ModelResponseError("No chat model candidate was available", reason="unavailable")

    async def complete_json(
        self,
        role: str,
        payload: dict[str, Any],
        on_delta: Callable[[str], None] | None = None,
        on_reasoning: Callable[[str], None] | None = None,
    ) -> dict[str, Any]:
        self._begin_call_transport()
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
            buffered_reasoning: list[str] = []
            try:
                result = await candidate.complete_json(
                    role,
                    provider_payload,
                    on_delta=buffered_deltas.append if on_delta is not None else None,
                    on_reasoning=(
                        # 2026-08-23: reasoning 直通不缓冲，保持推理流
                        # 实时到达（JSON 契约 delta 仍需缓冲到解析成功）。
                        on_reasoning
                    ),
                )
                result = _run_result_validator(result, result_validator)
            except (TypeError, ValueError) as exc:
                exc = ModelResponseError(
                    "Chat model returned business-invalid structured output",
                    reason="business_schema_invalid",
                    failover_eligible=True,
                )
                self._copy_transport(candidate, call_failovers, succeeded=False)
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
                self._copy_transport(candidate, call_failovers, succeeded=False)
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
            self._copy_transport(candidate, call_failovers, succeeded=True)
            if on_delta is not None:
                for delta in buffered_deltas:
                    on_delta(delta)
            return result
        if last_error is not None:
            raise last_error
        raise ModelResponseError("No chat model candidate was available", reason="unavailable")

    def _begin_call_transport(self) -> None:
        self._transport.set(
            {
                "request_payload": None,
                "response_text": None,
                "reasoning_text": None,
                "error_details": None,
            }
        )

    def _copy_transport(
        self,
        candidate: ChatModel,
        call_failovers: list[dict[str, Any]],
        *,
        succeeded: bool,
    ) -> None:
        request_payload = getattr(candidate, "last_request_payload", None)
        if call_failovers:
            request_payload = {
                **(request_payload or {}),
                "failovers": list(call_failovers),
            }
        error_details = None if succeeded else getattr(
            candidate, "last_error_details", None
        )
        if error_details is not None:
            error_details = {
                **dict(error_details),
                "attempted_model": str(getattr(candidate, "model", "unknown")),
                "prior_failovers": list(call_failovers),
            }
        self._transport.set(
            {
                "request_payload": request_payload,
                "response_text": getattr(candidate, "last_response_text", None),
                "reasoning_text": getattr(candidate, "last_reasoning_text", None),
                "error_details": error_details,
            }
        )
