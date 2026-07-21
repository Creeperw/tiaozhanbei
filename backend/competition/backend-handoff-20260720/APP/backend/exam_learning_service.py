from __future__ import annotations

import json
import threading

from sqlalchemy.orm import Session

from APP.backend.config import OFFICIAL_EXAM_DATA_DIR
from APP.backend.database import (
    KnowledgeMasteryState,
    LearnerKPReviewState,
    MistakeRecord,
)
from APP.backend.official_exam_repository import OfficialExamRepository
from APP.backend.time_utils import utc_now


_repository = None
_repository_lock = threading.Lock()


def get_official_exam_repository() -> OfficialExamRepository:
    global _repository
    if _repository is None:
        with _repository_lock:
            if _repository is None:
                _repository = OfficialExamRepository(OFFICIAL_EXAM_DATA_DIR)
    return _repository


def list_tracks() -> dict:
    repository = get_official_exam_repository()
    tracks = repository.list_tracks()
    version = next((str(row.get("schema_version") or "") for row in tracks), "")
    return {"items": tracks, "total": len(tracks), "version": version}


def _compact_membership(item: dict) -> dict:
    keys = (
        "membership_id",
        "parent_membership_id",
        "track_id",
        "node_id",
        "title",
        "child_count",
        "depth",
        "display_order",
        "sort_index",
        "order_path",
        "is_requirement",
        "requirement_kind",
        "schema_version",
    )
    return {key: item.get(key) for key in keys if key in item}


def list_nodes(track_id: str, parent_membership_id: str | None = None) -> dict:
    repository = get_official_exam_repository()
    items = [
        _compact_membership(item)
        for item in repository.get_membership_children(track_id, parent_membership_id)
    ]
    return {
        "track": repository.get_track(track_id),
        "parent_membership_id": parent_membership_id,
        "items": items,
        "total": len(items),
    }


def get_node(track_id: str, membership_id: str) -> dict:
    repository = get_official_exam_repository()
    detail = repository.get_membership(track_id, membership_id)
    detail["track"] = repository.get_track(track_id)
    return detail


def get_node_knowledge_points(
    track_id: str,
    membership_id: str,
    *,
    offset: int = 0,
    limit: int = 50,
) -> dict:
    repository = get_official_exam_repository()
    repository.get_membership(track_id, membership_id)
    payload = repository.get_catalog_subtree_knowledge_points(
        membership_id,
        accepted_only=True,
    )
    public_items = [
        {
            key: item[key]
            for key in ("kp_id", "name", "path", "accepted_count")
            if key in item
        }
        for item in payload["knowledge_points"]
    ]
    total = len(public_items)
    return {
        "track_id": track_id,
        "membership_id": membership_id,
        "items": public_items[offset:offset + limit],
        "total": total,
        "offset": offset,
        "limit": limit,
        "has_more": offset + limit < total,
        "requirement_count": payload["requirement_count"],
        "mapping_count": payload["mapping_count"],
    }


def get_learner_knowledge_point_state(
    db: Session,
    *,
    user_id: int,
    kp_id: str,
) -> dict:
    mastery = db.query(KnowledgeMasteryState).filter_by(
        learner_id=user_id,
        kp_id=kp_id,
    ).one_or_none()
    review = db.query(LearnerKPReviewState).filter_by(
        learner_id=user_id,
        kp_id=kp_id,
    ).one_or_none()
    active_mistakes = db.query(MistakeRecord).filter(
        MistakeRecord.user_id == user_id,
        MistakeRecord.status == "active",
    ).all()
    mistake_count = 0
    for mistake in active_mistakes:
        try:
            mistake_kp_ids = json.loads(mistake.kp_ids_json or "[]")
        except (TypeError, ValueError):
            mistake_kp_ids = []
        if isinstance(mistake_kp_ids, list) and kp_id in mistake_kp_ids:
            mistake_count += 1

    score = float(mastery.mastery_score) if mastery is not None else None
    mastery_status = (
        "unassessed"
        if score is None
        else "mastered"
        if score >= 80
        else "learning"
        if score >= 40
        else "needs_review"
    )
    next_review_at = review.next_review_at if review is not None else None
    return {
        "kp_id": kp_id,
        "mastery_score": score,
        "mastery_confidence": (
            float(mastery.mastery_confidence) if mastery is not None else None
        ),
        "mastery_status": mastery_status,
        "attempt_count": int(mastery.attempt_count or 0) if mastery is not None else 0,
        "last_assessed_at": (
            mastery.last_assessed_at.isoformat()
            if mastery is not None and mastery.last_assessed_at
            else None
        ),
        "review_stage": review.review_stage if review is not None else "new",
        "review_due": bool(next_review_at and next_review_at <= utc_now()),
        "next_review_at": next_review_at.isoformat() if next_review_at else None,
        "requires_remediation": bool(
            review.requires_remediation if review is not None else False
        ),
        "active_mistake_count": mistake_count,
    }


def get_node_learner_summary(
    db: Session,
    *,
    user_id: int,
    track_id: str,
    membership_id: str,
) -> dict:
    repository = get_official_exam_repository()
    repository.get_membership(track_id, membership_id)
    payload = repository.get_catalog_subtree_knowledge_points(
        membership_id,
        accepted_only=True,
    )
    kp_ids = sorted({
        str(item.get("kp_id"))
        for item in payload.get("knowledge_points", [])
        if item.get("kp_id")
    })
    if not kp_ids:
        return {
            "track_id": track_id,
            "membership_id": membership_id,
            "total_count": 0,
            "completed_count": 0,
            "incomplete_count": 0,
            "average_mastery": None,
            "review_due_count": 0,
            "status": "unassessed",
        }

    mastery_rows = db.query(KnowledgeMasteryState).filter(
        KnowledgeMasteryState.learner_id == user_id,
        KnowledgeMasteryState.kp_id.in_(kp_ids),
    ).all()
    review_rows = db.query(LearnerKPReviewState).filter(
        LearnerKPReviewState.learner_id == user_id,
        LearnerKPReviewState.kp_id.in_(kp_ids),
    ).all()
    scores = [
        float(row.mastery_score)
        for row in mastery_rows
        if row.mastery_score is not None
    ]
    completed_count = sum(score >= 80 for score in scores)
    now = utc_now()
    review_due_count = sum(
        bool(row.next_review_at and row.next_review_at <= now)
        for row in review_rows
    )
    average_mastery = round(sum(scores) / len(scores), 1) if scores else None
    status = (
        "unassessed"
        if not scores
        else "completed"
        if completed_count == len(kp_ids)
        else "in_progress"
    )
    return {
        "track_id": track_id,
        "membership_id": membership_id,
        "total_count": len(kp_ids),
        "completed_count": completed_count,
        "incomplete_count": len(kp_ids) - completed_count,
        "average_mastery": average_mastery,
        "review_due_count": review_due_count,
        "status": status,
    }


def get_visible_node_learner_states(
    db: Session,
    *,
    user_id: int,
    track_id: str,
    membership_ids: list[str],
) -> dict:
    repository = get_official_exam_repository()
    ordered_ids = list(dict.fromkeys(str(item) for item in membership_ids if item))
    if not ordered_ids or len(ordered_ids) > 120:
        raise ValueError("membership_ids must contain between 1 and 120 items")

    memberships = {}
    kp_ids_by_membership = {}
    all_kp_ids = set()
    for membership_id in ordered_ids:
        detail = repository.get_membership(track_id, membership_id)
        memberships[membership_id] = detail.get("membership", {})
        try:
            subtree = repository.get_catalog_subtree_knowledge_points(
                membership_id,
                accepted_only=True,
            )
        except KeyError:
            subtree = {"knowledge_points": []}
        kp_ids = {
            str(item.get("kp_id"))
            for item in subtree.get("knowledge_points", [])
            if item.get("kp_id")
        }
        kp_ids_by_membership[membership_id] = kp_ids
        all_kp_ids.update(kp_ids)

    mastery_by_kp = {}
    review_by_kp = {}
    if all_kp_ids:
        mastery_rows = db.query(KnowledgeMasteryState).filter(
            KnowledgeMasteryState.learner_id == user_id,
            KnowledgeMasteryState.kp_id.in_(all_kp_ids),
        ).all()
        review_rows = db.query(LearnerKPReviewState).filter(
            LearnerKPReviewState.learner_id == user_id,
            LearnerKPReviewState.kp_id.in_(all_kp_ids),
        ).all()
        mastery_by_kp = {row.kp_id: row for row in mastery_rows}
        review_by_kp = {row.kp_id: row for row in review_rows}

    now = utc_now()
    items = []
    for membership_id in ordered_ids:
        membership = memberships[membership_id]
        kp_ids = kp_ids_by_membership[membership_id]
        mastery_rows = [mastery_by_kp[kp_id] for kp_id in kp_ids if kp_id in mastery_by_kp]
        review_rows = [review_by_kp[kp_id] for kp_id in kp_ids if kp_id in review_by_kp]
        scores = [
            float(row.mastery_score)
            for row in mastery_rows
            if row.mastery_score is not None
        ]
        last_assessed_values = [
            row.last_assessed_at for row in mastery_rows if row.last_assessed_at
        ]
        next_review_values = [
            row.next_review_at for row in review_rows if row.next_review_at
        ]
        completed = bool(kp_ids) and len(scores) == len(kp_ids) and all(score >= 80 for score in scores)
        status = "unassessed" if not scores else "completed" if completed else "in_progress"
        items.append({
            "membership_id": membership_id,
            "status": status,
            "mastery_score": round(sum(scores) / len(scores), 1) if scores else None,
            "last_assessed_at": max(last_assessed_values).isoformat() if last_assessed_values else None,
            "review_due": any(value <= now for value in next_review_values),
            "next_review_at": min(next_review_values).isoformat() if next_review_values else None,
            "display_order": membership.get("display_order"),
            "sort_index": membership.get("sort_index"),
            "order_path": membership.get("order_path"),
        })
    return {"track_id": track_id, "items": items, "total": len(items)}


def get_requirement_knowledge_points(node_id: str) -> dict:
    payload = get_official_exam_repository().get_requirement_matches(
        node_id,
        include_candidates=False,
    )
    items = [
        {
            key: item[key]
            for key in ("kp_id", "name", "path", "accepted_count")
            if key in item
        }
        for item in payload["matches"]
    ]
    return {
        "requirement": payload["requirement"],
        "mapping_status": payload["mapping_status"],
        "items": items,
        "total": len(items),
    }
