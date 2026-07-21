from __future__ import annotations

import json
from typing import Any


FIELD_MAP = {
    "display_name": "display_name",
    "learner_group": "constitution",
    "learning_goal": "health_goals",
    "time_constraints": "diet_restrictions",
    "resource_preferences": "exercise_preferences",
    "current_difficulties": "medical_history",
    "learning_needs": "custom_needs",
}


REVERSE_FIELD_MAP = {value: key for key, value in FIELD_MAP.items()}


DEFAULT_PROFILE_HINTS = {
    "learner_group": "未选择用户群体",
    "learning_goal": "未填写学习目标",
    "time_constraints": "未填写可投入时间",
    "resource_preferences": "未填写资源偏好",
    "current_difficulties": "未填写当前困难",
    "learning_needs": "未填写个性化学习需求",
}

AUTO_UPDATE_SOURCES = {"learning_analytics_service", "diagnosis_agent", "memory_agent", "intervention_service"}


def parse_json_field(value: str | None, fallback: Any) -> Any:
    if not value:
        return fallback
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return fallback


def serialize_json_field(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def get_locked_profile_fields(profile: dict[str, Any] | Any) -> set[str]:
    raw = _read_value(profile, "locked_fields_json")
    values = parse_json_field(raw, [])
    if not isinstance(values, list):
        return set()
    return {str(item) for item in values if item}


def _read_value(profile: dict[str, Any] | Any, key: str) -> Any:
    if isinstance(profile, dict):
        return profile.get(key)
    return getattr(profile, key, None)


def build_learner_profile_payload(profile: dict[str, Any] | Any) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    for learner_key, storage_key in FIELD_MAP.items():
        value = _read_value(profile, storage_key)
        if value is None and learner_key in DEFAULT_PROFILE_HINTS:
            value = DEFAULT_PROFILE_HINTS[learner_key]
        payload[learner_key] = value or ""
    return payload


def map_learner_profile_update(update: dict[str, Any]) -> dict[str, Any]:
    mapped: dict[str, Any] = {}
    for learner_key, storage_key in FIELD_MAP.items():
        if learner_key in update:
            mapped[storage_key] = update[learner_key]
    return mapped


def apply_learner_profile_update(profile: Any, update: dict[str, Any], *, source: str = "manual") -> dict[str, Any]:
    locked_fields = get_locked_profile_fields(profile) if source in AUTO_UPDATE_SOURCES else set()
    changed: dict[str, Any] = {}
    for learner_key, storage_key in FIELD_MAP.items():
        if learner_key not in update:
            continue
        if learner_key in locked_fields:
            continue
        value = update[learner_key]
        setattr(profile, storage_key, value)
        changed[storage_key] = value
    return changed
