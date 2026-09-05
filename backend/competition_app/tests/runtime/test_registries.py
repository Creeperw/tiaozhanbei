import pytest

from competition_app.runtime.agent_registry import AgentRegistry, RegistryError


def test_agent_registry_rejects_duplicate_and_unknown_names() -> None:
    registry = AgentRegistry()
    registry.register("memory", object())
    with pytest.raises(RegistryError, match="already registered"):
        registry.register("memory", object())
    with pytest.raises(RegistryError, match="not registered"):
        registry.get("missing")
