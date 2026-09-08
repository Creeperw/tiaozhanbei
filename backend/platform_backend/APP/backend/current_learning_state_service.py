"""Read-only projections. Never initialize users, dispatch work or rebuild state."""
from __future__ import annotations

import hashlib
import json
from typing import Any

from APP.backend.database import LearningActivityRecord


def read_completion_records(db, user_id: int, exam_track_id: str, book: str | None = None):
    """The frontend and agents must use the same completion predicate."""
    with db.no_autoflush:
        rows = db.query(LearningActivityRecord).filter(
            LearningActivityRecord.user_id == user_id,
            LearningActivityRecord.activity_type == "textbook_section_completed",
            LearningActivityRecord.resource_type == "textbook_section",
        ).order_by(LearningActivityRecord.created_at, LearningActivityRecord.id).all()
    records = []
    for row in rows:
        try:
            payload = json.loads(row.payload_json or "{}")
        except (TypeError, ValueError):
            continue
        if not isinstance(payload, dict):
            continue
        if str(payload.get("exam_track_id") or "") != exam_track_id:
            continue
        if book is not None and payload.get("book") != book:
            continue
        records.append((row, payload))
    return records


def completion_evidence(row, payload: dict) -> dict:
    source = str(payload.get("source") or "unspecified")
    # Provenance is structured metadata, never inferred from free-form notes.
    demo = payload.get("is_demo") is True or source == "authorized_demo_progress"
    return {
        "record_id": row.id,
        "section_id": row.resource_id,
        "chapter_id": payload.get("chapter_id", ""),
        "book": payload.get("book", ""),
        "section_name": payload.get("section_name", ""),
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "source": source,
        "basis": "authorized_demo" if demo else source if source in {
            "manual_confirmation", "system_verified"
        } else "unspecified",
        "is_demo": demo,
        "verified_assessment": False,
        "evidence_ref": f"learning_activity_records:{row.id}",
    }


def project_textbook(book: str, atlas, records: list, mastery: list[dict], *,
                     cursor: int | None = None, limit: int = 8,
                     section_id: str | None = None) -> dict[str, Any]:
    """Use the same canonical chapter/section index as the textbook reader."""
    if not 1 <= limit <= 20 or (cursor is not None and cursor < 0):
        raise ValueError("invalid section pagination")
    atlas.ensure_hierarchy()
    chapters = atlas.chapters_by_book.get(book)
    if not chapters:
        return {"book": book, "availability": "unavailable", "reason": "catalog_missing"}
    sections = [section for chapter in chapters for section in chapter["sections"]]
    catalog = {section["id"]: section for section in sections
               if not str(section["id"]).startswith("UNRESOLVED_")
               and section.get("review_status") != "needs_review"}
    evidence_by_id: dict[str, list[dict]] = {}
    unmapped = []
    for row, payload in records:
        if payload.get("book") != book:
            continue
        evidence = completion_evidence(row, payload)
        target = catalog.get(row.resource_id)
        if (target is None or payload.get("section_id") not in (None, "", row.resource_id)
                or payload.get("chapter_id") not in (None, "", target["chapter_id"])):
            unmapped.append(evidence)
            continue
        evidence_by_id.setdefault(row.resource_id, []).append(evidence)
    ordered = list(catalog.values())
    completed = [section for section in ordered if section["id"] in evidence_by_id]
    last_index = max((i for i, section in enumerate(ordered)
                      if section["id"] in evidence_by_id), default=-1)
    gaps = [s for s in ordered[:last_index + 1] if s["id"] not in evidence_by_id]
    following = [s for s in ordered[last_index + 1:] if s["id"] not in evidence_by_id]
    if section_id is not None and section_id not in catalog:
        raise ValueError("section_id is not in the selected textbook")
    if section_id:
        chosen = [catalog[section_id]]
        offset = next(i for i, s in enumerate(ordered) if s["id"] == section_id)
    else:
        offset = cursor if cursor is not None else last_index + 1
        chosen = ordered[offset:offset + limit]
    mastery_by_id = {str(row["kp_id"]): row for row in mastery}

    def brief(section):
        return {"section_id": section["id"], "name": section["name"],
                "chapter_id": section["chapter_id"], "chapter": section["chapter_name"],
            "book": book, "order": section.get("order_index"),
            "mapping_status": "canonical"}

    def detail(section):
        item = brief(section)
        evidence = evidence_by_id.get(section["id"], [])
        item.update(status="recorded_completed" if evidence else "no_completion_record",
                completion_evidence=evidence[-limit:],
                completion_evidence_count=len(evidence),
                    completion_does_not_imply_mastery=True)
        # Resource failure must remain unknown, not become an empty/zero fact.
        try:
            resources = atlas.section_detail(section["id"], recommendation_limit=1)
            atlas.ensure_questions()
            kps = resources.get("knowledge_points") or []
            item["knowledge_points"] = [{
                "kp_id": kp["kp_id"], "name": kp["name"],
                "question_count": len(atlas.questions_by_kp.get(kp["kp_id"], [])),
                "mastery": mastery_by_id.get(kp["kp_id"]),
            } for kp in kps]
            question_ids = {str(question["question_id"])
                            for kp in kps for question in atlas.questions_by_kp.get(kp["kp_id"], [])
                            if question.get("question_id")}
            item["resources"] = {
                "availability": "available", "video_state": resources.get("resource_state"),
                "section_videos": resources.get("section_videos", []),
                "recommended_videos": resources.get("recommended_videos", []),
                "content_status": section.get("content_status", "unknown"),
                "question_counts_are_catalog_only": True,
                "question_count": len(question_ids),
                "meets_three_question_minimum": len(question_ids) >= 3,
            }
            if hasattr(atlas, "_video_signature") and atlas._video_signature is None:
                item["resources"]["video_state"] = "unknown"
        except (OSError, ValueError, KeyError, RuntimeError):
            item["resources"] = {"availability": "unavailable"}
            item["knowledge_points"] = []
        return item

    latest = max((ev for values in evidence_by_id.values() for ev in values),
                 key=lambda ev: (ev["created_at"] or "", ev["record_id"]), default=None)
    catalog_version = hashlib.sha256(json.dumps(
        [brief(s) for s in ordered], ensure_ascii=False, sort_keys=True
    ).encode()).hexdigest()[:24]
    return {
        "book": book, "book_id": book, "availability": "available",
        "catalog_version": catalog_version,
        "total_sections": len(ordered), "completed_count": len(completed),
        "completion_percent": round(100 * len(completed) / len(ordered), 2) if ordered else None,
        "completed_sections": [{**brief(s), "evidence": evidence_by_id[s["id"]][-limit:]}
                       for s in completed[-limit:]],
        "completed_sections_truncated": len(completed) > limit,
        "latest_recorded_completion": latest,
        "latest_demo_completion": max(
            (ev for values in evidence_by_id.values() for ev in values if ev["is_demo"]),
            key=lambda ev: (ev["created_at"] or "", ev["record_id"]), default=None,
        ),
        "last_visited_section": None,
        "visit_availability": "unknown",
        "furthest_completed_section": brief(ordered[last_index]) if last_index >= 0 else None,
        "earlier_gaps": [brief(s) for s in gaps[:limit]],
        "earlier_gap_count": len(gaps),
        "earlier_gaps_truncated": len(gaps) > limit,
        "next_candidates": [brief(s) for s in following[:limit]],
        "sections": [detail(s) for s in chosen],
        "cursor": offset,
        "next_cursor": offset + len(chosen) if offset + len(chosen) < len(ordered) else None,
        "unmapped_records": unmapped[-limit:],
        "unmapped_record_count": len(unmapped),
        "unmapped_records_truncated": len(unmapped) > limit,
        "unresolved_catalog_count": len(sections) - len(ordered),
        "interpretation": "无完成记录不等于从未学过；后续候选不是强制起点，前序缺口应单独核实。",
    }