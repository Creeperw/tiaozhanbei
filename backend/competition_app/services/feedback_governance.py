from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from uuid import uuid4

from competition_app.contracts.evolution import (
    EvolutionFeedback,
    FeedbackReviewRequest,
    UserFeedbackRequest,
)
from competition_app.repositories.evolution import EvolutionRepository


_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_SECRET_RE = re.compile(
    r"(?i)(api[_ -]?key|access[_ -]?token|authorization|password|secret)\s*[:=]\s*[^\s,;]+"
)

# Administrator-selected, code-owned classifications. Open feedback prose is
# never keyword-routed into an Agent, step, or field path. These dimensions
# correspond to the closed templates accepted by the candidate gate.
FEEDBACK_RULE_CLASSIFICATIONS: dict[str, dict[str, str]] = {
    "expert_evidence_reference": {
        "label": "专家输出引用了当前证据包外的编号",
        "issue_type": "missing_evidence",
        "target_agent": "expert_agent",
        "owner_step_id": "expert",
        "field_path": "claims[].evidence_ids[]",
        "constraint_category": "evidence_boundary",
    },
    "knowledge_summary_reference": {
        "label": "知识摘要引用了不存在的资源编号",
        "issue_type": "missing_evidence",
        "target_agent": "knowledge_base_agent",
        "owner_step_id": "knowledge",
        "field_path": "summary_items[].evidence_id",
        "constraint_category": "evidence_boundary",
    },
    "audit_owner_assignment": {
        "label": "审核责任节点不在当前执行图中",
        "issue_type": "invalid_repair_owner",
        "target_agent": "audit_agent",
        "owner_step_id": "audit",
        "field_path": "structured_findings[].owner_step_id",
        "constraint_category": "repair_ownership",
    },
}


def list_feedback_rule_classifications() -> list[dict[str, str]]:
    return [
        {"classification_id": classification_id, **dimensions}
        for classification_id, dimensions in FEEDBACK_RULE_CLASSIFICATIONS.items()
    ]


def sanitize_feedback_text(value: object, *, max_chars: int = 2000) -> str:
    """Treat feedback as untrusted data, never as an instruction block."""

    text = _CONTROL_RE.sub("", str(value or "")).strip()
    text = _SECRET_RE.sub(r"\1=[已脱敏]", text)
    return text[:max_chars]


class FeedbackGovernanceService:
    def __init__(self, repository: EvolutionRepository, *, enabled: bool = False) -> None:
        self.repository = repository
        self.enabled = enabled

    def submit_user_feedback(
        self,
        *,
        learner_id: str,
        request: UserFeedbackRequest,
    ) -> EvolutionFeedback:
        comment = sanitize_feedback_text(request.comment, max_chars=1200)
        issue_type = request.issue_type or (
            "positive_feedback" if request.feedback_type == "like" else "unspecified_dislike"
        )
        trust = "medium" if request.feedback_type == "dislike" and comment else "low"
        status = "pending"
        dedup = self._digest(
            "user",
            learner_id,
            request.message_id,
            request.execution_id,
            request.feedback_type,
            issue_type,
            comment,
        )
        return self.repository.save_feedback(EvolutionFeedback(
            feedback_id=f"EFB_{uuid4().hex}",
            source_type="user",
            trust_level=trust,
            status=status,
            execution_id=request.execution_id,
            conversation_id=request.conversation_id,
            message_id=request.message_id,
            learner_id=learner_id,
            task_type=sanitize_feedback_text(request.task_type, max_chars=64) or "general_learning_support",
            issue_type=issue_type,
            severity="info" if request.feedback_type == "like" else "warning",
            summary=comment or ("用户点赞" if request.feedback_type == "like" else "用户点踩，未补充原因"),
            dedup_key=dedup,
        ))

    def record_automatic_feedback(
        self,
        *,
        source_type: str,
        execution_id: str | None,
        conversation_id: str | None = None,
        learner_id: str | None,
        task_type: str,
        issue_type: str,
        summary: str,
        source_case_id: str | None = None,
        target_agent: str | None = None,
        owner_step_id: str | None = None,
        field_path: str = "",
        constraint_category: str = "general",
        severity: str = "blocking",
        trust_level: str = "medium",
        status: str = "pending",
    ) -> EvolutionFeedback | None:
        """Persist a machine observation without silently granting human trust.

        Semantic Audit output is still model output, so the safe default is a
        medium-trust pending record.  A caller may explicitly mark a
        system-owned deterministic gate as high-trust/validated.  This keeps
        failure collection automatic while ensuring model prose cannot promote
        itself into a rule candidate without administrator review.
        """

        if not self.enabled:
            return None
        normalized_trust = (
            trust_level if trust_level in {"low", "medium", "high"} else "medium"
        )
        normalized_status = (
            status if status in {"pending", "validated", "rejected"} else "pending"
        )
        safe_summary = sanitize_feedback_text(summary)
        dedup = self._digest(
            source_type,
            execution_id,
            source_case_id,
            issue_type,
            owner_step_id,
            field_path,
            safe_summary,
        )
        return self.repository.save_feedback(EvolutionFeedback(
            feedback_id=f"EFB_{uuid4().hex}",
            source_type=source_type,
            trust_level=normalized_trust,
            status=normalized_status,
            execution_id=execution_id,
            conversation_id=conversation_id,
            learner_id=learner_id,
            task_type=sanitize_feedback_text(task_type, max_chars=64) or "unknown",
            target_agent=sanitize_feedback_text(target_agent, max_chars=64) or None,
            owner_step_id=sanitize_feedback_text(owner_step_id, max_chars=128) or None,
            issue_type=sanitize_feedback_text(issue_type, max_chars=96) or "unresolved",
            severity=severity,
            field_path=sanitize_feedback_text(field_path, max_chars=512),
            constraint_category=sanitize_feedback_text(constraint_category, max_chars=96) or "general",
            summary=safe_summary or "系统检测到可追溯失败",
            source_case_id=source_case_id,
            dedup_key=dedup,
            reviewed_at=(
                datetime.now(timezone.utc)
                if normalized_status in {"validated", "rejected"}
                else None
            ),
            reviewer_id=(
                "system"
                if normalized_status == "validated" and normalized_trust == "high"
                else None
            ),
        ))

    def review(
        self,
        feedback_id: str,
        request: FeedbackReviewRequest,
        *,
        reviewer_id: str,
    ) -> EvolutionFeedback:
        current = self.repository.get_feedback(feedback_id)
        if current is None:
            raise KeyError(feedback_id)
        classification = (
            FEEDBACK_RULE_CLASSIFICATIONS.get(request.classification_id)
            if request.classification_id
            else None
        )
        if (
            request.status == "validated"
            and current.source_type in {"user", "human_review"}
            and not classification
            and not current.field_path
        ):
            raise ValueError(
                "validated user feedback requires an administrator-selected "
                "rule classification"
            )
        updated = current.model_copy(update={
            "status": request.status,
            # Administrator validation is the explicit human trust boundary:
            # it confirms the normalized issue dimensions, so the reviewed
            # observation may count as high-trust.  Pending model/user input
            # never receives this promotion by itself.
            "trust_level": (
                "high" if request.status == "validated" else current.trust_level
            ),
            "issue_type": sanitize_feedback_text(
                classification.get("issue_type") if classification else request.issue_type,
                max_chars=96,
            ) or current.issue_type,
            "target_agent": sanitize_feedback_text(
                classification.get("target_agent") if classification else request.target_agent,
                max_chars=64,
            ) or current.target_agent,
            "owner_step_id": sanitize_feedback_text(
                classification.get("owner_step_id") if classification else request.owner_step_id,
                max_chars=128,
            ) or current.owner_step_id,
            "field_path": sanitize_feedback_text(
                classification.get("field_path") if classification else None,
                max_chars=512,
            ) or current.field_path,
            "constraint_category": sanitize_feedback_text(
                (
                    classification.get("constraint_category")
                    if classification
                    else None
                ),
                max_chars=96,
            ) or current.constraint_category,
            "reviewed_at": datetime.now(timezone.utc),
            "reviewer_id": reviewer_id,
        })
        return self.repository.save_feedback(updated)

    @staticmethod
    def _digest(*parts: object) -> str:
        canonical = "\x1f".join(str(part or "").strip() for part in parts)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
