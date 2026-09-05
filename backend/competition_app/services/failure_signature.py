from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from uuid import uuid4

from competition_app.contracts.evolution import EvolutionFeedback, FailureSignature
from competition_app.repositories.evolution import EvolutionRepository


_INDEX_RE = re.compile(r"\[(?:\d+|'[^']*'|\"[^\"]*\")\]")
_SAFE_TOKEN_RE = re.compile(r"[^a-zA-Z0-9_.\-\[\]]+")

_STEP_AGENT_MAP = {
    "planner": "planner_agent",
    "diagnosis": "diagnosis_agent",
    "knowledge": "knowledge_base_agent",
    "expert": "expert_agent",
    "audit": "audit_agent",
    "paper_blueprint": "paper_blueprint_agent",
    "paper_assembly": "paper_assembly_agent",
}


class FailureSignatureService:
    """Deterministically aggregate reviewed failures without model clustering."""

    def __init__(
        self,
        repository: EvolutionRepository,
        *,
        min_cases: int = 3,
        min_executions: int = 2,
        min_high_trust: int = 2,
    ) -> None:
        self.repository = repository
        self.min_cases = min_cases
        self.min_executions = min_executions
        self.min_high_trust = min_high_trust

    @staticmethod
    def normalize_field_path(value: str | None) -> str:
        text = _INDEX_RE.sub("[]", str(value or "").strip())
        return _SAFE_TOKEN_RE.sub("_", text)[:512]

    @staticmethod
    def target_agent(owner_step_id: str | None, explicit: str | None = None) -> str:
        if explicit in set(_STEP_AGENT_MAP.values()):
            return str(explicit)
        owner = str(owner_step_id or "").strip().lower()
        for prefix, agent in _STEP_AGENT_MAP.items():
            if owner == prefix or owner.startswith(prefix + "_") or owner.startswith(prefix + ":"):
                return agent
        return "audit_agent"

    def ingest(self, feedback: EvolutionFeedback) -> FailureSignature | None:
        if feedback.status != "validated":
            return None
        owner = self._token(feedback.owner_step_id or "unknown", 128)
        agent = self.target_agent(owner, feedback.target_agent)
        dimensions = (
            self._token(feedback.task_type or "unknown", 64),
            owner,
            self._token(feedback.issue_type or "unresolved", 96),
            self.normalize_field_path(feedback.field_path),
            self._token(feedback.constraint_category or "general", 96),
        )
        key = hashlib.sha256("\x1f".join(dimensions).encode("utf-8")).hexdigest()
        current = self.repository.get_signature_by_key(key)
        case_ids = set(current.source_case_ids if current else [])
        execution_ids = set(current.source_execution_ids if current else [])
        observation_id = feedback.source_case_id or feedback.feedback_id
        if observation_id:
            case_ids.add(observation_id)
        if feedback.execution_id:
            execution_ids.add(feedback.execution_id)
        # Deduplication happens in the feedback repository. Counts therefore
        # reflect independent, traceable rows instead of repeated API retries.
        case_count = len(case_ids)
        high_count = (current.high_trust_count if current else 0) + (
            1 if feedback.trust_level == "high" and observation_id not in set(current.source_case_ids if current else []) else 0
        )
        ready = (
            case_count >= self.min_cases
            and len(execution_ids) >= self.min_executions
            and high_count >= self.min_high_trust
        )
        now = datetime.now(timezone.utc)
        signature = FailureSignature(
            signature_id=current.signature_id if current else f"SIG_{uuid4().hex}",
            signature_key=key,
            task_type=dimensions[0],
            owner_step_id=dimensions[1],
            target_agent=agent,
            issue_type=dimensions[2],
            field_path=dimensions[3],
            constraint_category=dimensions[4],
            case_count=case_count,
            execution_count=len(execution_ids),
            high_trust_count=high_count,
            source_case_ids=sorted(case_ids)[-50:],
            source_execution_ids=sorted(execution_ids)[-50:],
            candidate_ready=ready,
            first_seen_at=current.first_seen_at if current else now,
            last_seen_at=now,
        )
        return self.repository.save_signature(signature)

    @staticmethod
    def _token(value: object, max_chars: int) -> str:
        text = _SAFE_TOKEN_RE.sub("_", str(value or "").strip())
        return text[:max_chars] or "unknown"
