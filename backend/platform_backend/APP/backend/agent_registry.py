from __future__ import annotations

from dataclasses import dataclass

from APP.backend.agent_prompts import (
    AUDIT_PROMPT,
    DIAGNOSIS_PROMPT,
    EXPERT_PROMPT,
    EXPERT_TYPE_PROMPTS,
    KNOWLEDGE_PROMPT,
    MEMORY_PROMPT,
    PLANNER_PROMPT,
)


@dataclass(frozen=True)
class AgentDefinition:
    name: str
    role: str
    system_prompt: str
    allowed_tools: frozenset[str]


AGENT_REGISTRY: dict[str, AgentDefinition] = {
    "memory_agent": AgentDefinition("memory_agent", "profile_memory_context", MEMORY_PROMPT, frozenset()),
    "planner_agent": AgentDefinition("planner_agent", "global_execution_planning", PLANNER_PROMPT, frozenset()),
    "knowledge_base_agent": AgentDefinition("knowledge_base_agent", "evidence_retrieval", KNOWLEDGE_PROMPT, frozenset({"search_rag", "search_health_web", "search_health_video"})),
    "diagnosis_agent": AgentDefinition("diagnosis_agent", "learning_state_diagnosis", DIAGNOSIS_PROMPT, frozenset()),
    "expert_handout": AgentDefinition("expert_handout", "handout_generation", EXPERT_TYPE_PROMPTS["expert_handout"], frozenset()),
    "expert_knowledge_card": AgentDefinition("expert_knowledge_card", "knowledge_card_generation", EXPERT_TYPE_PROMPTS["expert_knowledge_card"], frozenset()),
    "expert_paper": AgentDefinition("expert_paper", "paper_generation", EXPERT_TYPE_PROMPTS["expert_paper"], frozenset()),
    "expert_grading": AgentDefinition("expert_grading", "grading_and_remediation", EXPERT_TYPE_PROMPTS["expert_grading"], frozenset()),
    "expert_case_training": AgentDefinition("expert_case_training", "case_training_generation", EXPERT_TYPE_PROMPTS["expert_case_training"], frozenset()),
    "expert_question_variation": AgentDefinition("expert_question_variation", "mistake_variation_generation", EXPERT_PROMPT, frozenset()),
    "audit_agent": AgentDefinition("audit_agent", "quality_and_compliance_review", AUDIT_PROMPT, frozenset()),
}


def get_agent_definition(name: str) -> AgentDefinition | None:
    return AGENT_REGISTRY.get(name)


def allowed_agent_names() -> set[str]:
    return set(AGENT_REGISTRY)
