from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from typing import Any

from competition_app.contracts.agent_communication import (
    AgentHandoffBundle,
    CognitiveGapResult,
    ConfirmedFact,
    DownstreamNeed,
    EvidenceReference,
    UncertaintyItem,
)
from competition_app.contracts.base import AgentEnvelope, ArtifactReference
from competition_app.contracts.execution import ExecutionStep


AGENT_NEED_CATALOG: dict[str, tuple[DownstreamNeed, ...]] = {
    "knowledge_base_agent": (
        DownstreamNeed(field="user_request", reason="解析知识对象"),
        DownstreamNeed(field="source_policy", reason="限制可信来源"),
    ),
    "diagnosis_agent": (
        DownstreamNeed(field="learning_goal", reason="确定规划目标"),
        DownstreamNeed(field="time_budget", reason="约束任务量"),
        DownstreamNeed(field="multi_scale_learning_state", reason="依据真实学情"),
    ),
    "learning_plan_service": (
        DownstreamNeed(field="diagnosis_proposal", reason="物化正式计划"),
    ),
    "review_scheduler": (
        DownstreamNeed(field="graded_knowledge_state", reason="只调度已完成练习的知识点"),
    ),
    "expert_agent": (
        DownstreamNeed(field="evidence", reason="生成有证据资源"),
        DownstreamNeed(field="formal_task", reason="对齐正式任务"),
    ),
    "audit_agent": (
        DownstreamNeed(field="artifact", reason="审核目标"),
        DownstreamNeed(field="evidence", reason="核验事实"),
    ),
}


class CognitiveGapAnalysis:
    """The handoff payload and its deterministic readiness assessment."""

    def __init__(self, bundle: AgentHandoffBundle, gap: CognitiveGapResult) -> None:
        self.bundle = bundle
        self.gap = gap


class CognitiveGapAnalyzer:
    _ROOT_FIELDS = frozenset(
        {
            "user_request",
            "learning_goal",
            "available_minutes",
            "time_budget",
            "multi_scale_learning_state",
            "source_policy",
            "diagnosis_proposal",
            "graded_knowledge_state",
            "formal_task",
            "artifact",
            "evidence",
            "task_constraints",
        }
    )
    _ROOT_ALIASES = {"available_minutes": "time_budget"}
    _SECRET_MARKERS = ("api_key", "token", "password")
    _FOREIGN_USER_MARKERS = ("user_id", "user_ids", "learner_id", "learner_ids")

    def analyze(
        self,
        step: ExecutionStep,
        root_context: Mapping[str, Any],
        dependency_outputs: Mapping[str, AgentEnvelope[Any]],
    ) -> CognitiveGapAnalysis:
        learner_id = str(root_context["learner_id"])
        direct_steps = tuple(step.depends_on)
        omitted = self._omitted_root_categories(root_context)
        known_values: dict[str, tuple[Any, str]] = {}
        evidence: list[EvidenceReference] = []

        for field in self._ROOT_FIELDS:
            if field in root_context and self._is_safe_field(field):
                value = self._sanitize_value(root_context[field])
                if self._is_known(value):
                    known_values.setdefault(field, (value, "root_context"))
                    alias = self._ROOT_ALIASES.get(field)
                    if alias:
                        known_values.setdefault(alias, (value, "root_context"))

        for step_id in direct_steps:
            output = dependency_outputs.get(step_id)
            if output is None:
                continue
            if output.learner_id != learner_id:
                omitted.append(f"{step_id}:cross_user_output")
                continue
            self._collect_dependency_values(output, step_id, known_values, evidence, omitted)

        catalog_needs = AGENT_NEED_CATALOG.get(step.agent)
        compatibility_mode = catalog_needs is None
        needs = () if compatibility_mode else catalog_needs
        satisfied_fields = [
            need.field
            for need in needs
            if self._need_is_satisfied(need, known_values, evidence, root_context)
        ]
        missing_fields = [need.field for need in needs if need.field not in satisfied_fields]
        blocking_fields = [
            need.field for need in needs if need.required and need.field not in satisfied_fields
        ]
        allowed_fields = (
            {field for field, (_, source) in known_values.items() if source != "root_context"}
            if compatibility_mode
            else {need.field for need in needs}
        )
        facts = [
            ConfirmedFact(
                fact_id=f"{step.step_id}:{field}",
                category=field,
                content=self._fact_content(value),
                learner_id=learner_id,
                source_step_id=source_step_id,
            )
            for field, (value, source_step_id) in known_values.items()
            if field in allowed_fields
        ]
        uncertainties = [
            UncertaintyItem(
                uncertainty_id=f"{step.step_id}:missing:{field}",
                category=field,
                description=f"缺少下游所需字段：{field}",
                blocking=field in blocking_fields,
            )
            for field in missing_fields
        ]
        generated_at = root_context.get("now")
        if not isinstance(generated_at, datetime):
            generated_at = datetime.now(timezone.utc)
        gap = CognitiveGapResult(
            target_agent=step.agent,
            satisfied_fields=satisfied_fields,
            missing_fields=missing_fields,
            blocking_fields=blocking_fields,
            omitted_categories=self._unique(omitted),
        )
        bundle = AgentHandoffBundle(
            handoff_id=f"HANDOFF_{root_context['execution_id']}_{step.step_id}",
            trace_id=str(root_context["trace_id"]),
            execution_id=str(root_context["execution_id"]),
            learner_id=learner_id,
            source_steps=list(direct_steps),
            target_agent=step.agent,
            purpose=f"handoff for {step.agent}",
            confirmed_facts=facts,
            evidence=evidence,
            uncertainties=uncertainties,
            task_constraints={} if compatibility_mode else self._safe_constraints(root_context),
            downstream_needs=list(needs),
            omitted_categories=self._unique(omitted),
            generated_at=generated_at,
        )
        return CognitiveGapAnalysis(bundle=bundle, gap=gap)

    def _collect_dependency_values(
        self,
        output: AgentEnvelope[Any],
        step_id: str,
        known_values: dict[str, tuple[Any, str]],
        evidence: list[EvidenceReference],
        omitted: list[str],
    ) -> None:
        payload = output.payload
        if isinstance(payload, Mapping):
            for field, value in payload.items():
                field_name = str(field)
                if not self._is_safe_field(field_name):
                    continue
                sanitized = self._sanitize_value(value)
                if self._is_known(sanitized):
                    known_values.setdefault(field_name, (sanitized, step_id))
                    alias = self._ROOT_ALIASES.get(field_name)
                    if alias:
                        known_values.setdefault(alias, (sanitized, step_id))
        for reference in output.evidence_refs:
            evidence.append(self._evidence_reference(reference))
        if output.evidence_refs:
            known_values.setdefault("evidence", (output.evidence_refs, step_id))

    def _omitted_root_categories(self, root_context: Mapping[str, Any]) -> list[str]:
        protected = {"trace_id", "execution_id", "learner_id", "now"}
        return [
            str(field)
            for field in root_context
            if field not in self._ROOT_FIELDS | protected and self._is_safe_field(str(field))
        ]

    def _safe_constraints(self, root_context: Mapping[str, Any]) -> dict[str, Any]:
        constraints = root_context.get("task_constraints", {})
        if not isinstance(constraints, Mapping):
            return {}
        sanitized = self._sanitize_value(constraints)
        return sanitized if isinstance(sanitized, dict) else {}

    @staticmethod
    def _need_is_satisfied(
        need: DownstreamNeed,
        known_values: Mapping[str, tuple[Any, str]],
        evidence: Sequence[EvidenceReference],
        root_context: Mapping[str, Any],
    ) -> bool:
        if need.field != "evidence":
            return need.field in known_values
        if not evidence:
            return False
        policy = root_context.get("source_policy")
        trusted_source_types: set[str] = set()
        if isinstance(policy, Mapping):
            configured_types = policy.get("trusted_source_types", ())
            if isinstance(configured_types, Sequence) and not isinstance(configured_types, str):
                trusted_source_types = {
                    source_type
                    for source_type in configured_types
                    if isinstance(source_type, str) and source_type
                }
        if not trusted_source_types:
            return False
        accepted_source_types = set(need.accepted_source_types)
        return any(
            (not accepted_source_types or item.source_type in accepted_source_types)
            and (not trusted_source_types or item.source_type in trusted_source_types)
            for item in evidence
        )

    def _is_safe_field(self, field: str) -> bool:
        normalized = field.lower()
        return not (
            any(marker in normalized for marker in self._SECRET_MARKERS)
            or normalized in self._FOREIGN_USER_MARKERS
            or (normalized != "user_request" and ("user" in normalized or "learner" in normalized))
        )

    def _sanitize_value(self, value: Any) -> Any:
        if isinstance(value, Mapping):
            return {
                str(field): self._sanitize_value(item)
                for field, item in value.items()
                if self._is_safe_field(str(field))
            }
        if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
            return [self._sanitize_value(item) for item in value]
        return value

    @staticmethod
    def _is_known(value: Any) -> bool:
        return value is not None

    @staticmethod
    def _evidence_reference(reference: ArtifactReference) -> EvidenceReference:
        return EvidenceReference(
            evidence_id=reference.ref_id,
            source_type=reference.ref_type,
            source_id=reference.ref_id,
            claim=reference.purpose or reference.ref_id,
        )

    @staticmethod
    def _fact_content(value: Any) -> str:
        if isinstance(value, str):
            return value
        return str(value)

    @staticmethod
    def _unique(items: list[str]) -> list[str]:
        return list(dict.fromkeys(items))
