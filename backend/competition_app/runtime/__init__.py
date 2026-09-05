"""Runtime package exports.

Keep these compatibility exports lazy.  Importing a concrete runtime module
such as ``runtime.debug_trace`` must not eagerly import the orchestrators: the
orchestrator imports the model client, while the model client also imports the
debug tracer.  Eager package exports would make that otherwise independent
debug module fail during API/test collection with a circular import.
"""

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from competition_app.runtime.agent_registry import AgentRegistry, RegistryError
    from competition_app.runtime.langgraph_orchestrator import LangGraphOrchestrator
    from competition_app.runtime.orchestrator import ExecutionResult, Orchestrator


__all__ = [
    "AgentRegistry",
    "ExecutionResult",
    "LangGraphOrchestrator",
    "Orchestrator",
    "RegistryError",
]


def __getattr__(name: str) -> Any:
    """Resolve legacy package-level names only when they are requested."""

    if name in {"AgentRegistry", "RegistryError"}:
        from competition_app.runtime.agent_registry import AgentRegistry, RegistryError

        return {
            "AgentRegistry": AgentRegistry,
            "RegistryError": RegistryError,
        }[name]
    if name == "LangGraphOrchestrator":
        from competition_app.runtime.langgraph_orchestrator import LangGraphOrchestrator

        return LangGraphOrchestrator
    if name in {"ExecutionResult", "Orchestrator"}:
        from competition_app.runtime.orchestrator import ExecutionResult, Orchestrator

        return {"ExecutionResult": ExecutionResult, "Orchestrator": Orchestrator}[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
