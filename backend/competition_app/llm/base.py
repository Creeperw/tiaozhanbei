from __future__ import annotations

from typing import Any, Callable, Protocol


class ChatModel(Protocol):
    async def complete_json(
        self,
        role: str,
        payload: dict[str, Any],
        on_delta: Callable[[str], None] | None = None,
    ) -> dict[str, Any]: ...
