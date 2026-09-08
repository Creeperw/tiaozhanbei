"""Source checks and execution gates; free-text meaning belongs to Diagnosis/Audit."""

import hashlib
import json
from copy import deepcopy
from typing import Any

from competition_app.contracts.prerequisite import PrerequisiteJudgment
from competition_app.services.prerequisite_policy import (
    all_prerequisite_courses, normalize_course_name, required_courses_for_stage,
    unwrap_textbook_route,
)


def judgment_sources(context: dict[str, Any]) -> dict[str, str]:
    sources = {}
    for key in ("original_user_request", "user_request", "latest_resume_answer"):
        if context.get(key):
            sources[key] = str(context[key])[:8000]
    messages = list(context.get("messages") or [])
    for index, message in list(enumerate(messages))[-12:]:
        if isinstance(message, dict) and message.get("role") == "user" and message.get("content"):
            sources[f"message:{index}"] = str(message["content"])[:2000]
    profile = context.get("user_profile") or {}
    for key in ("learning_background", "completed_courses"):
        if profile.get(key):
            sources[f"profile:{key}"] = json.dumps(profile[key], ensure_ascii=False)[:8000]
    snapshot = context.get("learning_path_progress") or context.get("current_learning_state") or {}
    if isinstance(snapshot, dict) and snapshot.get("tool") == "get_current_learning_state":
        for index, book in enumerate(list(snapshot.get("books") or [])[:30]):
            if isinstance(book, dict):
                sources[f"learning_state:book:{index}"] = json.dumps(
                    {key: book.get(key) for key in ("book", "availability", "total_sections", "completed_count")},
                    ensure_ascii=False,
                )
    return sources


def interpret_judgments(raw: Any, route: Any, sources: dict[str, str]) -> dict[str, Any]:
    required = all_prerequisite_courses(route)
    canonical = {normalize_course_name(name): name for name in required}
    if raw is not None and (not isinstance(raw, list) or len(raw) > 20):
        raise ValueError("prerequisite judgments must be a bounded list")
    judgments = [PrerequisiteJudgment.model_validate(item) for item in (raw or [])]
    seen = set()
    for item in judgments:
        key = normalize_course_name(item.course)
        if key not in canonical or key in seen:
            raise ValueError("prerequisite judgment must name a unique route-owned course")
        seen.add(key)
        if item.source_ref not in sources or item.source_quote not in sources[item.source_ref]:
            raise ValueError("prerequisite judgment has no exact authorized source quote")
        item.course = canonical[key]
    by_course = {item.course: item.status for item in judgments}
    textbook = unwrap_textbook_route(route)
    route_id = textbook.get("route_id", "") if isinstance(textbook, dict) else getattr(textbook, "route_id", "")
    route_data = textbook.model_dump(mode="json") if hasattr(textbook, "model_dump") else textbook
    route_data = route_data if isinstance(route_data, dict) else {}
    return {
        "route_id": route_id,
        "route_version": route_data.get("route_version"),
        "route_requirements_digest": hashlib.sha256(json.dumps(
            {key: route_data.get(key) for key in ("route_id", "route_version", "stages", "prerequisites")},
            sort_keys=True, ensure_ascii=False,
        ).encode()).hexdigest(),
        "required_courses": required,
        "satisfied_courses": [name for name in required if by_course.get(name) == "satisfied"],
        "unmet_courses": [name for name in required if by_course.get(name) == "unmet"],
        "unknown_courses": [name for name in required if by_course.get(name, "unknown") == "unknown"],
        "judgments": [item.model_dump(mode="json") for item in judgments],
        "sources": sources,
        "source_digest": hashlib.sha256(json.dumps(sources, sort_keys=True, ensure_ascii=False).encode()).hexdigest(),
    }


def missing_execution_prerequisites(route: Any, stage_id: str, books: list[str], confirmed: set[str]) -> list[str]:
    """A prerequisite book exempts only itself, never a dependent companion book."""
    satisfied = {normalize_course_name(item) for item in confirmed}
    missing = []
    for book in books:
        for course in required_courses_for_stage(route, stage_id, selected_books=[book]):
            if normalize_course_name(course) not in satisfied and normalize_course_name(book) != normalize_course_name(course):
                if course not in missing:
                    missing.append(course)
    return missing


def route_conditions(route: Any) -> list[dict[str, Any]]:
    """Trusted options before semantic assessment; not persisted-plan candidates."""
    textbook = unwrap_textbook_route(route)
    if hasattr(textbook, "model_dump"):
        textbook = textbook.model_dump(mode="json")
    if not isinstance(textbook, dict):
        return []
    return [
        {
            "route_id": textbook.get("route_id"),
            "stage_id": stage.get("stage_id"),
            "books": list(stage.get("books") or []),
            "required_courses": required_courses_for_stage(route, str(stage.get("stage_id") or "")),
        }
        for stage in textbook.get("stages", [])
        if isinstance(stage, dict)
    ]


def refresh_candidate_prerequisites(candidates: Any, assessment: dict[str, Any], route: Any) -> dict[str, Any]:
    """Replace only prerequisite results; never waive parent/time/data gates."""
    result = deepcopy(candidates) if isinstance(candidates, dict) else {}
    old_route_id = (result.get("prerequisite_evidence") or {}).get("route_id")
    result["prerequisite_evidence"] = deepcopy(assessment)
    result["route_conditions"] = route_conditions(route)
    if old_route_id != assessment["route_id"]:
        result["eligible"], result["blocked"] = [], []
        result["candidate_context_status"] = "route_rebound_conditions_only"
        return result
    eligible, blocked = [], []
    for item in [*(result.get("eligible") or []), *(result.get("blocked") or [])]:
        if not isinstance(item, dict):
            continue
        stage = item.get("stage") or {}
        books = [str(book.get("name") or "") if isinstance(book, dict) else str(book) for book in item.get("books") or []]
        missing = missing_execution_prerequisites(
            route, str(stage.get("stage_id") or stage.get("phase_id") or ""), books,
            set(assessment["satisfied_courses"]) - set(assessment["unmet_courses"]),
        )
        constraints = item.get("hard_constraint_results") or []
        has_prerequisite_gate = False
        for constraint in constraints:
            if constraint.get("key") == "prerequisite_satisfied":
                has_prerequisite_gate = True
                constraint.update(passed=not missing, reason="prerequisite_unconfirmed" if missing else "prerequisite_satisfied")
        item["eligible"] = has_prerequisite_gate and all(c.get("passed") is True for c in constraints)
        item["blocked_reasons"] = [c.get("reason") or c.get("key") for c in constraints if c.get("passed") is not True]
        (eligible if item["eligible"] else blocked).append(item)
    result["eligible"], result["blocked"] = eligible, blocked
    return result