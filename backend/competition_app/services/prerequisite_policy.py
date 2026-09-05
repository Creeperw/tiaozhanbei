from __future__ import annotations

import re
from typing import Any, Iterable

from competition_app.contracts.prerequisite import PrerequisiteEvidenceSnapshot


_CLAUSE_SPLIT = re.compile(r"[，,。；;！？!?\n]+")
_DOUBLE_NEGATION_MARKERS = (
    "不是没",
    "并非没",
    "并不是没",
    "不是没有",
    "并非没有",
    "没有忘",
    "没忘",
)
_META_OR_INSTRUCTION_MARKERS = (
    "忽略路线",
    "忽略规则",
    "设置",
    "设为",
    "标记为",
    "修改为",
    "改成",
    "伪造",
    "假设",
    "示例",
    "不要把",
    "这句话",
    "prerequisite_",
)
_NEGATIVE_MARKERS = (
    "没有学过",
    "没学过",
    "未学过",
    "没有学",
    "没学",
    "未学",
    "未完成",
    "没有完成",
    "没完成",
    "不会应用",
    "不会",
    "未通过",
    "没有通过",
    "忘记",
    "忘了",
    "忘得",
    "记不清",
)
_POSITIVE_MARKERS = (
    "已经完成",
    "已完成",
    "已经学完",
    "已学完",
    "学完了",
    "学完过",
    "已经通过",
    "已通过",
    "通过了",
    "能够通过",
    "能通过",
    "已经掌握",
    "已掌握",
    "掌握了",
)
_SHORT_POSITIVE_REPLIES = {
    "是",
    "是的",
    "对",
    "对的",
    "完成了",
    "学完了",
    "通过了",
    "能通过",
}
_SHORT_NEGATIVE_REPLIES = {
    "否",
    "不是",
    "没有",
    "没完成",
    "没学过",
    "忘了",
    "不会",
}


def _field(value: Any, name: str, default: Any = None) -> Any:
    if isinstance(value, dict):
        return value.get(name, default)
    return getattr(value, name, default)


def normalize_course_name(value: Any) -> str:
    """Normalize harmless presentation differences without fuzzy matching."""

    text = str(value or "").strip().strip("《》")
    return "".join(text.split()).casefold()


def unwrap_textbook_route(value: Any) -> Any:
    """Accept a route, a resolved route, or a planning-route wrapper."""

    current = value
    for _ in range(4):
        if current is None:
            return {}
        if _field(current, "stages") is not None and _field(
            current, "prerequisites"
        ) is not None:
            return current
        nested = _field(current, "textbook_route")
        if nested is not None:
            current = nested
            continue
        nested = _field(current, "route")
        if nested is not None:
            current = nested
            continue
        break
    return current or {}


def _stages(route: Any) -> list[Any]:
    stages = _field(route, "stages", [])
    if isinstance(stages, (list, tuple)) and stages:
        return list(stages)
    phases = _field(route, "phases", [])
    return list(phases) if isinstance(phases, (list, tuple)) else []


def _stage_id(stage: Any) -> str:
    return str(_field(stage, "stage_id") or _field(stage, "phase_id") or "").strip()


def all_prerequisite_courses(route_like: Any) -> list[str]:
    route = unwrap_textbook_route(route_like)
    courses: list[str] = []
    for rule in _field(route, "prerequisites", []) or []:
        course = str(_field(rule, "course") or "").strip().strip("《》")
        if course and normalize_course_name(course) not in {
            normalize_course_name(item) for item in courses
        }:
            courses.append(course)
    if courses:
        return courses

    # Compatibility for old plans/tests that embedded prerequisites in phases.
    for stage in _stages(route):
        for item in _field(stage, "prerequisites", []) or []:
            course = str(_field(item, "course") or item or "").strip().strip("《》")
            if course and normalize_course_name(course) not in {
                normalize_course_name(existing) for existing in courses
            }:
                courses.append(course)
    return courses


def required_courses_for_stage(route_like: Any, stage_id: str | None) -> list[str]:
    """Project route-level ``before_stage_id`` rules onto a target stage.

    Prerequisites are cumulative: a rule required before stage 2 remains
    required for stage 3 and later.  Old stage-local data is supported only as
    a fallback when no route-level rules exist.
    """

    route = unwrap_textbook_route(route_like)
    stages = _stages(route)
    order_by_id = {
        _stage_id(stage): int(_field(stage, "order") or index)
        for index, stage in enumerate(stages, start=1)
        if _stage_id(stage)
    }
    target_id = str(stage_id or "").strip()
    target_order = order_by_id.get(target_id)
    rules = list(_field(route, "prerequisites", []) or [])
    if rules:
        if target_order is None:
            return []
        courses: list[str] = []
        for rule in rules:
            before_id = str(_field(rule, "before_stage_id") or "").strip()
            before_order = order_by_id.get(before_id)
            course = str(_field(rule, "course") or "").strip().strip("《》")
            if not course or before_order is None or target_order < before_order:
                continue
            if normalize_course_name(course) not in {
                normalize_course_name(item) for item in courses
            }:
                courses.append(course)
        return courses

    selected = next((stage for stage in stages if _stage_id(stage) == target_id), None)
    if selected is None:
        return []
    result: list[str] = []
    for item in _field(selected, "prerequisites", []) or []:
        course = str(_field(item, "course") or item or "").strip().strip("《》")
        if course and normalize_course_name(course) not in {
            normalize_course_name(existing) for existing in result
        }:
            result.append(course)
    return result


def _course_mentions(text: str, courses: Iterable[str]) -> set[str]:
    compact = normalize_course_name(text)
    return {
        course
        for course in courses
        if normalize_course_name(course) and normalize_course_name(course) in compact
    }


def _explicit_clause_state(clause: str) -> str | None:
    compact = "".join(str(clause or "").split())
    if (
        not compact
        or any(marker in compact for marker in _DOUBLE_NEGATION_MARKERS)
        or any(marker in compact.casefold() for marker in _META_OR_INSTRUCTION_MARKERS)
    ):
        return None
    if any(marker in compact for marker in _NEGATIVE_MARKERS):
        return "unmet"
    if any(marker in compact for marker in _POSITIVE_MARKERS):
        return "satisfied"
    return None


def _elliptical_clause_state(clause: str) -> str | None:
    """Resolve a narrow follow-up such as “但现在基本忘了”.

    A bare “忘了带书/忘了做题” must not change course readiness, so ellipsis
    is accepted only for short capability-oriented continuations.
    """

    compact = "".join(str(clause or "").split())
    if not compact or any(marker in compact for marker in _DOUBLE_NEGATION_MARKERS):
        return None
    if not compact.startswith(("但", "不过", "只是", "现在", "目前", "基本", "已经")):
        return None
    if re.search(r"(?:基本|几乎|全都|都|差不多)?忘(?:了|记)?$", compact):
        return "unmet"
    if compact.endswith(("不会应用", "不会了", "记不清了", "未通过")):
        return "unmet"
    return None


def _message_ref(message: dict[str, Any], index: int) -> str:
    message_id = str(message.get("message_id") or "").strip()
    return f"conversation_message:{message_id}" if message_id else f"recent_message:{index}"


def resolve_prerequisite_evidence(
    route_like: Any,
    *,
    persisted_completed_courses: Iterable[Any] = (),
    recent_messages: Iterable[dict[str, Any]] = (),
    current_user_request: str = "",
    persisted_source_refs: Iterable[str] = (),
) -> PrerequisiteEvidenceSnapshot:
    """Build one conservative prerequisite evidence snapshot.

    Only courses declared by the approved route can be affected.  User text is
    treated as evidence, never as an executable structure: JSON booleans,
    candidate IDs and invented prerequisite names cannot alter the route.
    """

    route = unwrap_textbook_route(route_like)
    required = all_prerequisite_courses(route)
    canonical_by_key = {normalize_course_name(course): course for course in required}
    state_by_key: dict[str, str] = {key: "unknown" for key in canonical_by_key}
    refs: list[str] = [
        str(item) for item in persisted_source_refs if str(item).strip()
    ]
    for item in persisted_completed_courses or ():
        key = normalize_course_name(item)
        if key in state_by_key:
            state_by_key[key] = "satisfied"

    pending_courses: set[str] = set()

    def apply_user_text(text: str, source_ref: str, implied: set[str] | None = None) -> None:
        nonlocal refs
        raw = str(text or "").strip()
        if not raw:
            return
        changed = False
        clauses = [item.strip() for item in _CLAUSE_SPLIT.split(raw) if item.strip()]
        previous_mentions: set[str] = set()
        for clause in clauses:
            mentioned = _course_mentions(clause, required)
            state = _explicit_clause_state(clause)
            if mentioned and state:
                for course in mentioned:
                    state_by_key[normalize_course_name(course)] = state
                changed = True
            elif not mentioned and previous_mentions:
                elliptical_state = _elliptical_clause_state(clause)
                if elliptical_state:
                    for course in previous_mentions:
                        state_by_key[normalize_course_name(course)] = elliptical_state
                    changed = True
            if mentioned:
                previous_mentions = mentioned
        compact_reply = "".join(raw.split()).strip("。.!！?")
        if implied and not _course_mentions(raw, required):
            state = (
                "satisfied"
                if compact_reply in _SHORT_POSITIVE_REPLIES
                else "unmet"
                if compact_reply in _SHORT_NEGATIVE_REPLIES
                else None
            )
            if state:
                for course in implied:
                    state_by_key[normalize_course_name(course)] = state
                changed = True
        if changed and source_ref and source_ref not in refs:
            refs.append(source_ref)

    messages = [item for item in recent_messages or () if isinstance(item, dict)][-6:]
    for index, message in enumerate(messages, start=1):
        role = str(message.get("role") or "")
        text = str(message.get("content") or "")[:1000]
        if role == "assistant":
            pending_courses = _course_mentions(text, required)
        elif role == "user":
            apply_user_text(text, _message_ref(message, index), pending_courses)
            pending_courses = set()

    request = str(current_user_request or "").strip()[:2000]
    if request:
        apply_user_text(request, "current_user_request", pending_courses)

    satisfied = [
        course
        for key, course in canonical_by_key.items()
        if state_by_key[key] == "satisfied"
    ]
    unmet = [
        course for key, course in canonical_by_key.items() if state_by_key[key] == "unmet"
    ]
    unknown = [
        course
        for key, course in canonical_by_key.items()
        if state_by_key[key] == "unknown"
    ]
    route_id = str(_field(route, "route_id") or "").strip()
    route_ref = f"textbook_route:{route_id}" if route_id else ""
    if route_ref and route_ref not in refs:
        refs.insert(0, route_ref)
    return PrerequisiteEvidenceSnapshot(
        route_id=route_id,
        required_courses=required,
        satisfied_courses=satisfied,
        unmet_courses=unmet,
        unknown_courses=unknown,
        source_refs=refs,
    )
