from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from competition_app.contracts.audit_compilation import (
    AuditLocation,
    CompiledAuditFinding,
)
from competition_app.contracts.local_repair import RepairIssue


@dataclass(frozen=True)
class ResponsibilityContext:
    """System-owned facts used for responsibility attribution.

    Model prose is deliberately excluded.  A malformed or empty EvidencePack
    is attributable to Knowledge; a healthy pack followed by an unsupported
    or contradictory claim is attributable to Expert.
    """

    subject_type: str | None = None
    knowledge_pack_missing: bool = False
    knowledge_pack_invalid: bool = False

    @property
    def knowledge_pack_unhealthy(self) -> bool:
        return self.knowledge_pack_missing or self.knowledge_pack_invalid


class AuditIssueResolver:
    """Resolve model-compiled business issues to system-owned step responsibility."""

    _OWNER_BY_TYPE = {
        "missing_evidence": "expert",
        "conflicting_evidence": "expert",
        "factual_error": "expert",
        "learner_mismatch": "expert",
        "route_or_prerequisite_error": "expert",
        "content_quality": "expert",
        "paper_blueprint_mismatch": "paper_assembly",
        "plan_quality": "diagnosis",
        "plan_contract_invalid": "diagnosis",
        "plan_parent_constraint": "diagnosis",
        "question_pool_insufficient": "paper_assembly",
        "paper_item_invalid": "paper_assembly",
        "answer_or_explanation_invalid": "paper_assembly",
        "safety_violation": None,
        "unresolved": None,
    }

    def resolve(
        self,
        findings: Sequence[CompiledAuditFinding],
        *,
        location_catalog: Sequence[AuditLocation],
        issue_id_prefix: str,
        responsibility_context: ResponsibilityContext | None = None,
    ) -> list[RepairIssue]:
        locations = {item.location_key: item for item in location_catalog}
        resolved: list[RepairIssue] = []
        for index, finding in enumerate(findings, start=1):
            finding_locations = [
                locations[key] for key in finding.location_keys if key in locations
            ]
            issue_type = self._normalize_issue_type(
                finding.issue_type, finding_locations
            )
            origin, owner, affected_steps = self.resolve_responsibility(
                issue_type,
                finding.location_keys,
                responsibility_context=responsibility_context,
            )
            resolved.append(
                RepairIssue(
                    issue_id=f"{issue_id_prefix}_{index}",
                    issue_type=issue_type,
                    message=finding.message,
                    origin_step_id=origin,
                    owner_step_id=owner,
                    affected_step_ids=affected_steps,
                    severity=(
                        "high"
                        if issue_type
                        in {
                            "missing_evidence",
                            "conflicting_evidence",
                            "factual_error",
                            "safety_violation",
                        }
                        else "medium"
                    ),
                    origin="audit_model",
                    blocking=finding.blocking,
                    locations=[
                        *finding_locations
                    ],
                    source_anchors=finding.source_anchors,
                    policy_id=f"audit:{issue_type}",
                )
            )
        return resolved

    @classmethod
    def resolve_responsibility(
        cls,
        issue_type: str,
        location_keys: Sequence[str] = (),
        *,
        responsibility_context: ResponsibilityContext | None = None,
    ) -> tuple[str | None, str | None, list[str]]:
        context = responsibility_context or ResponsibilityContext()
        owner = cls._owner(issue_type, location_keys)
        if (
            context.subject_type in {"long_term_plan", "short_term_plan"}
            and issue_type not in {"safety_violation", "unresolved"}
        ):
            # A learning plan is authored and revised by Diagnosis regardless
            # of whether the prose Compiler labels a route mismatch as
            # factual_error, missing_evidence, plan_quality, etc. Routing a
            # plan-local issue to Expert creates a nonexistent repair branch
            # and incorrectly escalates an actionable revise to human review.
            # Subject type is supplied by the system audit branch, never by
            # user/model wording.
            owner = "diagnosis"
        if (
            context.subject_type == "resource"
            and context.knowledge_pack_unhealthy
            and issue_type in {"missing_evidence", "conflicting_evidence"}
        ):
            owner = "knowledge"
        origin = owner
        if owner == "knowledge" and context.subject_type == "resource":
            return origin, owner, ["knowledge", "expert"]
        return origin, owner, [owner] if owner else []

    @classmethod
    def _owner(cls, issue_type: str, location_keys: Sequence[str]) -> str | None:
        owner = cls._OWNER_BY_TYPE.get(issue_type)
        # Content-quality findings on plans are owned by Diagnosis; on papers
        # they are owned by PaperAssembly. Location is system-catalogued, so it
        # is safe to use for this deterministic specialization.
        if issue_type == "content_quality" and location_keys:
            if any(key.startswith("plan:") for key in location_keys):
                return "diagnosis"
            if any(key.startswith("paper:") for key in location_keys):
                return "paper_assembly"
        return owner

    @staticmethod
    def _normalize_issue_type(
        issue_type: str,
        locations: Sequence[AuditLocation],
    ) -> str:
        subject_types = {item.subject_type for item in locations}
        if subject_types == {"resource"} and issue_type in {
            "paper_blueprint_mismatch",
            "question_pool_insufficient",
            "paper_item_invalid",
            "answer_or_explanation_invalid",
            "plan_quality",
            "plan_contract_invalid",
            "plan_parent_constraint",
        }:
            return "content_quality"
        if subject_types.issubset({"long_term_plan", "short_term_plan"}) and issue_type in {
            "paper_blueprint_mismatch",
            "question_pool_insufficient",
            "paper_item_invalid",
            "answer_or_explanation_invalid",
        }:
            return "plan_quality"
        return issue_type
