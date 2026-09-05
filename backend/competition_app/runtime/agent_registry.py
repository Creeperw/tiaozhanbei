from __future__ import annotations

from typing import Any


class RegistryError(LookupError):
    """Raised for invalid runtime registry operations."""


class AgentRegistry:
    def __init__(self) -> None:
        self._agents: dict[str, Any] = {}

    def register(self, name: str, agent: Any) -> None:
        if name in self._agents:
            raise RegistryError(f"agent {name!r} is already registered")
        self._agents[name] = agent

    def get(self, name: str) -> Any:
        try:
            return self._agents[name]
        except KeyError as exc:
            raise RegistryError(f"agent {name!r} is not registered") from exc

    def items(self) -> tuple[tuple[str, Any], ...]:
        """Return a stable snapshot for isolated child registries.

        Evaluation runtimes can reuse production Agent instances while
        replacing selected boundaries with no-persistence wrappers.  Returning
        a tuple prevents callers from mutating the authoritative registry.
        """

        return tuple(self._agents.items())
