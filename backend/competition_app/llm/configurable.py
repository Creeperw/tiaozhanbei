from __future__ import annotations

from typing import Any, Callable

from competition_app.llm.openai_compatible import OpenAICompatibleChatModel
from competition_app.runtime.model_credentials import current_model_credentials


class UserConfiguredChatModel:
    """Use the request owner's DeepSeek key without mutating global process state."""

    def __init__(self, fallback) -> None:
        self.fallback = fallback
        self._clients: dict[str, OpenAICompatibleChatModel] = {}

    @property
    def active_model(self):
        credentials = current_model_credentials()
        key = credentials.deepseek_api_key if credentials else ""
        if not key:
            return self.fallback
        client = self._clients.get(key)
        if client is None:
            client = OpenAICompatibleChatModel(
                "https://api.deepseek.com",
                key,
                "deepseek-v4-flash",
            )
            self._clients[key] = client
        return client

    @property
    def last_request_payload(self):
        return getattr(self.active_model, "last_request_payload", None)

    @property
    def last_response_text(self):
        return getattr(self.active_model, "last_response_text", None)

    @property
    def last_reasoning_text(self):
        return getattr(self.active_model, "last_reasoning_text", None)

    async def complete_json(
        self,
        role: str,
        payload: dict[str, Any],
        on_delta: Callable[[str], None] | None = None,
    ) -> dict[str, Any]:
        return await self.active_model.complete_json(role, payload, on_delta=on_delta)
