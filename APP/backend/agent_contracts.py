from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator


def _has_keys(value: dict[str, Any] | None, *keys: str) -> bool:
    if not isinstance(value, dict):
        return False
    for key in keys:
        item = value.get(key)
        if item is None or (isinstance(item, str) and not item.strip()):
            return False
    return True


class ContractBase(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_scope: str
    source_id: str
    kp_ids: list[str] = Field(default_factory=list)
    risk_notes: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)
    agent_trace: list[dict[str, Any]] = Field(default_factory=list)


class LearnerContextBrief(ContractBase):
    learner_id: str
    learner_group: str
    goal: str
    profile: dict[str, Any] = Field(default_factory=dict)
    short_term_memory: dict[str, Any] = Field(default_factory=dict)
    long_term_memory: dict[str, Any] = Field(default_factory=dict)
    planning_memory: dict[str, Any] = Field(default_factory=dict)
    learning_state: dict[str, Any] = Field(default_factory=dict)


class AgentExecutionPlan(ContractBase):
    source_scope: str | None = None
    source_id: str | None = None
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    task_type: str | None = None
    need_cross_validation: bool = False
    risk_level: str | None = None
    plan_summary: dict[str, Any] | None = None
    weekly_plan: dict[str, Any] | None = None
    daily_tasks: list[dict[str, Any]] = Field(default_factory=list)
    constraints: dict[str, Any] | None = None
    plan_id: str | None = None
    objective: str | None = None
    assigned_agents: list[str] = Field(default_factory=list)
    steps: list[dict[str, Any] | str] = Field(default_factory=list)

    @model_validator(mode="after")
    def require_plan_shape(self) -> "AgentExecutionPlan":
        has_learning_plan = (
            _has_keys(self.plan_summary, "goal")
            and _has_keys(self.weekly_plan, "acceptance")
            and _has_keys(self.constraints, "daily_available_minutes")
            and any(_has_keys(task, "type", "title") for task in self.daily_tasks)
        )
        has_agent_plan = (
            self.objective is not None
            and bool(self.steps)
            and all(isinstance(step, (dict, str)) for step in self.steps)
            and (bool(self.assigned_agents) or any(isinstance(step, dict) and step.get("agent") for step in self.steps))
        )
        if not has_learning_plan and not has_agent_plan:
            raise ValueError("AgentExecutionPlan requires a learning-plan payload or an agent execution plan")
        return self


class EvidenceItem(ContractBase):
    summary: str


class EvidencePack(ContractBase):
    items: list[EvidenceItem]
    resolved_kp_ids: list[str] = Field(default_factory=list)
    candidate_kp_ids: list[str] = Field(default_factory=list)
    personal_evidence: list[dict[str, Any]] = Field(default_factory=list)
    public_evidence: list[dict[str, Any]] = Field(default_factory=list)
    question_evidence: list[dict[str, Any]] = Field(default_factory=list)
    resource_evidence: list[dict[str, Any]] = Field(default_factory=list)
    conflict_evidence: list[dict[str, Any]] = Field(default_factory=list)


class ExpertArtifact(ContractBase):
    artifact_type: str
    title: str
    content: dict[str, Any]


class ReviewDecision(ContractBase):
    source_scope: str | None = None
    source_id: str | None = None
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    decision: str
    reviewer: str | None = None
    reason: str | None = None
    fact_consistency: float | None = Field(default=None, ge=0.0, le=1.0)
    evidence_coverage: float | None = Field(default=None, ge=0.0, le=1.0)
    difficulty_match: float | None = Field(default=None, ge=0.0, le=1.0)
    knowledge_coverage: float | None = Field(default=None, ge=0.0, le=1.0)
    safety_risk: str | None = None
    conflicts: list[str] = Field(default_factory=list)


class DiagnosisReport(ContractBase):
    source_scope: str | None = None
    source_id: str | None = None
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    diagnosis_id: str | None = None
    stage_id: str | None = None
    stage_name: str | None = None
    summary: str | None = None
    evidence_pack: EvidencePack | None = None
    interventions: list[str] = Field(default_factory=list)
    t_stage: dict[str, Any] | None = None
    l0_baseline: dict[str, Any] | None = None
    l3_window: dict[str, Any] | None = None
    attribution: dict[str, Any] | None = None

    @model_validator(mode="after")
    def require_diagnosis_shape(self) -> "DiagnosisReport":
        has_current_diagnosis = (
            _has_keys(self.t_stage, "stage_id", "stage_name")
            and _has_keys(self.l0_baseline, "daily_available_minutes")
            and _has_keys(self.l3_window, "task_completion_rate")
            and _has_keys(self.attribution, "primary")
        )
        has_report = self.diagnosis_id is not None and self.stage_id is not None and self.stage_name is not None and self.summary is not None
        if not has_current_diagnosis and not has_report:
            raise ValueError("DiagnosisReport requires a current diagnosis payload or a diagnosis report")
        return self
