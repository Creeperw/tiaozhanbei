from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


DataAction = Literal["read", "write"]


class AgentDataCapability(BaseModel):
    agent: str
    domain: str
    actions: list[DataAction]
    writable_fields: list[str] = Field(default_factory=list)
    requires_user_confirmation: bool = False


class AgentDataPermissionGateway:
    """Central allowlist for agent-facing user data access and mutation."""

    _capabilities = (
        AgentDataCapability(agent="planner_agent", domain="learning_context", actions=["read"]),
        AgentDataCapability(agent="diagnosis_agent", domain="learning_context", actions=["read"]),
        AgentDataCapability(agent="diagnosis_agent", domain="learning_monitoring", actions=["read"]),
        AgentDataCapability(agent="learning_plan_service", domain="learning_plan", actions=["read", "write"]),
        AgentDataCapability(
            agent="memory_agent",
            domain="learner_profile",
            actions=["read", "write"],
            writable_fields=[
                "display_name", "learner_group", "learning_goal",
                "learning_background", "time_constraints",
            ],
            requires_user_confirmation=True,
        ),
        AgentDataCapability(agent="review_scheduler", domain="review_queue", actions=["read", "write"]),
        AgentDataCapability(agent="knowledge_base_agent", domain="knowledge_base", actions=["read"]),
        AgentDataCapability(agent="knowledge_base_agent", domain="knowledge_card", actions=["read"]),
        AgentDataCapability(
            agent="expert_agent",
            domain="knowledge_card",
            actions=["read", "write"],
            writable_fields=["kp_id", "title", "resource_bundle", "source_execution_id"],
        ),
        AgentDataCapability(agent="planner_agent", domain="training_workspace", actions=["read"]),
        AgentDataCapability(
            agent="paper_assembly_agent",
            domain="paper_workspace",
            actions=["read", "write"],
            writable_fields=["paper", "blueprint", "evidence_pack", "execution_id"],
        ),
        AgentDataCapability(agent="audit_agent", domain="paper_workspace", actions=["read"]),
        AgentDataCapability(agent="memory_agent", domain="memory", actions=["read", "write"]),
    )

    def authorize(
        self,
        *,
        agent: str,
        domain: str,
        action: DataAction,
        fields: set[str] | None = None,
        confirmed_fields: set[str] | None = None,
    ) -> None:
        capability = next(
            (
                item
                for item in self._capabilities
                if item.agent == agent and item.domain == domain and action in item.actions
            ),
            None,
        )
        if capability is None:
            raise PermissionError(f"{agent} is not allowed to {action} {domain}")
        requested = fields or set()
        if action == "write" and requested - set(capability.writable_fields):
            raise PermissionError("profile write contains fields outside the agent allowlist")
        if capability.requires_user_confirmation and requested - (confirmed_fields or set()):
            raise PermissionError("profile write requires an explicit user-confirmed field")

    def manifest(self) -> dict[str, Any]:
        return {
            "schema_version": "1.0",
            "policy": "least_privilege",
            "capabilities": [item.model_dump(mode="json") for item in self._capabilities],
        }
