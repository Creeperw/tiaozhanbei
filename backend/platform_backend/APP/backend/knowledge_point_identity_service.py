from __future__ import annotations

import hashlib
import json
import unicodedata
from dataclasses import dataclass
from typing import Any, Iterable
from uuid import uuid4

from sqlalchemy.orm import Session

from APP.backend.database import (
    CandidateKnowledgePoint,
    KnowledgePoint,
    KnowledgePointCanonicalMap,
)
from APP.backend.time_utils import utc_now


ACTIVE_MAPPING_STATUS = "active"
EQUIVALENT_DECISION = "equivalent"
AGENT_PAPER_SOURCE = "agent_audited_paper"
AUTO_EXACT_EQUIVALENCE_BASIS = "strict_exact_label_and_provenance"
FORMAL_SOURCE_PREFIXES = (
    "formal-content:",
    "formal_question_bank",
    "formal-vector-question-bank:",
)


@dataclass(frozen=True)
class KnowledgePointResolution:
    source_kp_id: str
    canonical_kp_id: str | None
    status: str
    decision_basis: str
    confidence: float
    candidate_id: str | None = None

    @property
    def admitted(self) -> bool:
        return bool(self.canonical_kp_id) and self.status == "resolved"


def normalize_knowledge_point_label(value: Any) -> str:
    """Normalize a label for strict equality only, never fuzzy routing."""

    return "".join(unicodedata.normalize("NFKC", str(value or "")).split()).casefold()


def _json_list(value: str | None) -> list[str]:
    try:
        decoded = json.loads(value or "[]")
    except (TypeError, ValueError):
        return []
    if not isinstance(decoded, list):
        return []
    return [str(item).strip() for item in decoded if str(item).strip()]


def _labels(row: KnowledgePoint) -> set[str]:
    return {
        normalized
        for normalized in (
            normalize_knowledge_point_label(row.name),
            *(normalize_knowledge_point_label(item) for item in _json_list(row.aliases_json)),
        )
        if normalized
    }


def _is_formal_source(source: str | None) -> bool:
    normalized = str(source or "").strip()
    return any(normalized.startswith(prefix) for prefix in FORMAL_SOURCE_PREFIXES)


def _representative_key(row: KnowledgePoint) -> tuple[int, Any, int, str]:
    return (
        0 if _is_formal_source(row.source) else 1,
        row.created_at or utc_now(),
        int(row.id or 0),
        str(row.kp_id or ""),
    )


def canonical_map_for_ids(
    db: Session,
    kp_ids: Iterable[str],
) -> dict[str, str]:
    normalized_ids = list(dict.fromkeys(
        str(item).strip() for item in kp_ids if str(item).strip()
    ))
    if not normalized_ids:
        return {}
    rows = (
        db.query(KnowledgePointCanonicalMap)
        .filter(
            KnowledgePointCanonicalMap.source_kp_id.in_(normalized_ids),
            KnowledgePointCanonicalMap.status == ACTIVE_MAPPING_STATUS,
            KnowledgePointCanonicalMap.decision == EQUIVALENT_DECISION,
        )
        .all()
    )
    mapping = {str(row.source_kp_id): str(row.canonical_kp_id) for row in rows}
    return {kp_id: mapping.get(kp_id, kp_id) for kp_id in normalized_ids}


def canonicalize_knowledge_point_ids(
    db: Session,
    kp_ids: Iterable[str],
) -> tuple[str, ...]:
    source_ids = list(dict.fromkeys(
        str(item).strip() for item in kp_ids if str(item).strip()
    ))
    mapping = canonical_map_for_ids(db, source_ids)
    return tuple(dict.fromkeys(mapping[source_id] for source_id in source_ids))


def source_ids_by_canonical(
    db: Session,
    canonical_ids: Iterable[str],
) -> dict[str, tuple[str, ...]]:
    targets = list(dict.fromkeys(
        str(item).strip() for item in canonical_ids if str(item).strip()
    ))
    if not targets:
        return {}
    rows = (
        db.query(KnowledgePointCanonicalMap)
        .filter(
            KnowledgePointCanonicalMap.canonical_kp_id.in_(targets),
            KnowledgePointCanonicalMap.status == ACTIVE_MAPPING_STATUS,
            KnowledgePointCanonicalMap.decision == EQUIVALENT_DECISION,
        )
        .all()
    )
    grouped: dict[str, list[str]] = {target: [target] for target in targets}
    for row in rows:
        bucket = grouped.setdefault(str(row.canonical_kp_id), [str(row.canonical_kp_id)])
        source_id = str(row.source_kp_id)
        if source_id not in bucket:
            bucket.append(source_id)
    return {key: tuple(value) for key, value in grouped.items()}


def authoritative_rows_by_canonical(
    rows: Iterable[Any],
    mapping: dict[str, str],
    *,
    projection_name: str,
) -> dict[str, Any]:
    """Prefer the canonical row; reject ambiguous source-only projections."""
    grouped: dict[str, list[Any]] = {}
    for row in rows:
        grouped.setdefault(mapping[str(row.kp_id)], []).append(row)
    selected = {}
    for canonical_id, candidates in grouped.items():
        canonical = next(
            (row for row in candidates if str(row.kp_id) == canonical_id), None
        )
        if canonical is None:
            if len(candidates) != 1:
                raise ValueError(
                    f"{projection_name} canonical projection is missing for {canonical_id}"
                )
            canonical = candidates[0]
        selected[canonical_id] = canonical
    return selected


def _candidate_id(source_kp_id: str, normalized_name: str) -> str:
    digest = hashlib.sha256(
        f"agent-paper:{source_kp_id}:{normalized_name}".encode("utf-8")
    ).hexdigest()[:20].upper()
    return f"CAND_KP_{digest}"


def _ensure_candidate(
    db: Session,
    *,
    source_kp_id: str,
    name: str,
    user_id: int | None,
    evidence: dict[str, Any],
) -> CandidateKnowledgePoint:
    normalized_name = normalize_knowledge_point_label(name)
    candidate_id = _candidate_id(source_kp_id, normalized_name)
    candidate = (
        db.query(CandidateKnowledgePoint)
        .filter_by(candidate_id=candidate_id)
        .one_or_none()
    )
    if candidate is None:
        candidate = CandidateKnowledgePoint(
            candidate_id=candidate_id,
            name=str(name or source_kp_id)[:200],
            source_text=str(name or source_kp_id),
            status="pending",
            created_by_user_id=user_id,
            evidence_json=json.dumps(evidence, ensure_ascii=False),
        )
        db.add(candidate)
    else:
        candidate.evidence_json = json.dumps(evidence, ensure_ascii=False)
        candidate.updated_at = utc_now()
    return candidate


def _persist_mapping(
    db: Session,
    *,
    source_kp_id: str,
    canonical_kp_id: str,
    decision_basis: str,
    confidence: float,
    evidence: dict[str, Any],
    decided_by: str,
) -> KnowledgePointCanonicalMap:
    existing = (
        db.query(KnowledgePointCanonicalMap)
        .filter_by(source_kp_id=source_kp_id)
        .one_or_none()
    )
    if existing is None:
        existing = KnowledgePointCanonicalMap(
            mapping_id=f"KPMAP_{uuid4().hex}",
            source_kp_id=source_kp_id,
        )
        db.add(existing)
    existing.canonical_kp_id = canonical_kp_id
    existing.decision = EQUIVALENT_DECISION
    existing.decision_basis = decision_basis
    existing.confidence = confidence
    existing.status = ACTIVE_MAPPING_STATUS
    existing.evidence_json = json.dumps(evidence, ensure_ascii=False)
    existing.decided_by = decided_by
    existing.decided_at = utc_now()
    return existing


def resolve_agent_knowledge_point(
    db: Session,
    *,
    source_kp_id: str,
    name: str,
    user_id: int | None,
) -> KnowledgePointResolution:
    """Resolve an agent bridge using IDs or strict, provenance-scoped equality.

    An unseen free-form label never creates an active knowledge point. It is
    persisted as a pending candidate and therefore cannot enter mastery state.
    """

    normalized_id = str(source_kp_id or "").strip()
    normalized_name = normalize_knowledge_point_label(name)
    if not normalized_id:
        raise ValueError("knowledge point id is required")

    explicit = (
        db.query(KnowledgePointCanonicalMap)
        .filter_by(source_kp_id=normalized_id, status=ACTIVE_MAPPING_STATUS)
        .one_or_none()
    )
    if explicit is not None and explicit.decision == EQUIVALENT_DECISION:
        return KnowledgePointResolution(
            normalized_id,
            str(explicit.canonical_kp_id),
            "resolved",
            str(explicit.decision_basis),
            float(explicit.confidence),
        )

    direct = db.query(KnowledgePoint).filter_by(kp_id=normalized_id).one_or_none()
    if (
        direct is not None
        and direct.status == "active"
        and direct.source != AGENT_PAPER_SOURCE
    ):
        return KnowledgePointResolution(
            normalized_id,
            normalized_id,
            "resolved",
            "existing_active_id",
            1.0,
        )

    agent_rows = (
        db.query(KnowledgePoint)
        .filter(
            KnowledgePoint.status == "active",
            KnowledgePoint.source == AGENT_PAPER_SOURCE,
        )
        .all()
    )
    exact_agent_matches = [
        row for row in agent_rows
        if normalized_name and normalized_name in _labels(row)
    ]
    formal_rows = (
        db.query(KnowledgePoint)
        .filter(KnowledgePoint.status == "active")
        .all()
    )
    exact_formal_matches = [
        row for row in formal_rows
        if _is_formal_source(row.source)
        and normalized_name
        and normalized_name in _labels(row)
    ]

    candidates = exact_formal_matches or exact_agent_matches
    if candidates:
        canonical = sorted(candidates, key=_representative_key)[0]
        evidence = {
            "source_kp_id": normalized_id,
            "normalized_label": normalized_name,
            "matched_kp_ids": sorted(str(row.kp_id) for row in candidates),
            "matched_source_scope": "formal" if exact_formal_matches else AGENT_PAPER_SOURCE,
        }
        _persist_mapping(
            db,
            source_kp_id=normalized_id,
            canonical_kp_id=str(canonical.kp_id),
            decision_basis=AUTO_EXACT_EQUIVALENCE_BASIS,
            confidence=1.0,
            evidence=evidence,
            decided_by="system:agent-paper-resolver-v1",
        )
        return KnowledgePointResolution(
            normalized_id,
            str(canonical.kp_id),
            "resolved",
            AUTO_EXACT_EQUIVALENCE_BASIS,
            1.0,
        )

    if direct is not None and direct.status == "active":
        return KnowledgePointResolution(
            normalized_id,
            normalized_id,
            "resolved",
            "existing_active_id",
            1.0,
        )

    evidence = {
        "source_kp_id": normalized_id,
        "name": str(name or ""),
        "normalized_label": normalized_name,
        "source": AGENT_PAPER_SOURCE,
        "reason": "no_strict_authoritative_or_same_provenance_match",
    }
    candidate = _ensure_candidate(
        db,
        source_kp_id=normalized_id,
        name=name,
        user_id=user_id,
        evidence=evidence,
    )
    return KnowledgePointResolution(
        normalized_id,
        None,
        "pending",
        "candidate_review_required",
        0.0,
        str(candidate.candidate_id),
    )


def register_reviewed_equivalence(
    db: Session,
    *,
    source_kp_id: str,
    canonical_kp_id: str,
    decision_basis: str,
    evidence: dict[str, Any],
    decided_by: str,
    confidence: float = 1.0,
) -> KnowledgePointCanonicalMap:
    source = db.query(KnowledgePoint).filter_by(kp_id=source_kp_id).one_or_none()
    target = db.query(KnowledgePoint).filter_by(kp_id=canonical_kp_id).one_or_none()
    if target is None or target.status != "active":
        raise ValueError("canonical mapping endpoints are invalid")
    if not 0.0 <= float(confidence) <= 1.0:
        raise ValueError("canonical mapping confidence is invalid")
    reviewed_evidence = dict(evidence)
    reviewed_evidence.setdefault(
        "source_kp_record_status",
        "present" if source is not None else "external_source_id",
    )
    return _persist_mapping(
        db,
        source_kp_id=source_kp_id,
        canonical_kp_id=canonical_kp_id,
        decision_basis=decision_basis,
        confidence=float(confidence),
        evidence=reviewed_evidence,
        decided_by=decided_by,
    )
