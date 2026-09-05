from __future__ import annotations

import json
import re
from typing import Any


RESOURCE_PREFERENCE_TYPES = {
    "knowledge_card": "knowledge_card", "知识卡": "knowledge_card", "知识卡片": "knowledge_card",
    "question": "question", "题目": "question", "分阶测试题": "question", "练习题": "question",
    "video": "video", "视频": "video", "短视频": "video", "视频微课": "video",
    "lecture": "lecture", "讲义讲解": "lecture", "章节讲义": "lecture",
    "case": "case", "案例训练": "case", "案例辨证": "case",
    "错题变式": "question", "方剂/中诊/中药练习": "question", "阶段测评": "question",
    "对比卡": "knowledge_card", "对比表": "knowledge_card", "考点速记": "knowledge_card",
}


def resource_preference_values(value: Any) -> list[str]:
    if isinstance(value, list):
        return list(dict.fromkeys(str(item).strip() for item in value if str(item).strip()))
    text = str(value or "").strip()
    if not text:
        return []
    if text in RESOURCE_PREFERENCE_TYPES:
        return [text]
    parts = [part for part in re.split(r"[,，、;；/\s]+", text) if part]
    # This is compatibility for an entire list of fixed UI enums, not keyword
    # extraction: any unrecognized text keeps the whole original value intact.
    return list(dict.fromkeys(parts)) if parts and all(part in RESOURCE_PREFERENCE_TYPES for part in parts) else [text]


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
    survey = parse_json_field(_read_value(profile, "survey_json"), {})
    survey = survey if isinstance(survey, dict) else {}
    background = survey.get("background") if isinstance(survey.get("background"), dict) else {}
    preferences = survey.get("preferences") if isinstance(survey.get("preferences"), dict) else {}
    resource_values, resource_source = read_resource_preferences(profile)
    payload["resource_preference"] = resource_values
    payload["resource_preference_source"] = resource_source
    if resource_source != "user_profiles.exercise_preferences":
        payload["resource_preferences"] = "、".join(resource_values)
    payload["education_major"] = (
        survey.get("education_major")
        or survey.get("major_or_role")
        or background.get("education_major")
        or background.get("major_or_role")
        or ""
    )
    payload["learning_background"] = (
        survey.get("learning_background")
        or background.get("learning_background")
        or ""
    )
    payload["learning_habits"] = (
        survey.get("learning_habits")
        or preferences.get("learning_habits")
        or preferences.get("learning_mode")
        or ""
    )
    return payload


def read_resource_preferences(profile: dict[str, Any] | Any) -> tuple[list[str], str]:
    """One read contract for UI, survey restoration and recommendation ranking.

    An explicit empty list clears the preference. Legacy prose is preserved as
    a whole value, never mined for keywords to infer resource categories.
    """
    survey = parse_json_field(_read_value(profile, "survey_json"), {})
    survey = survey if isinstance(survey, dict) else {}
    nested = survey.get("preferences")
    nested = nested if isinstance(nested, dict) else {}
    for owner, source in (
        (survey, "user_profiles.survey_json.resource_preference"),
        (nested, "user_profiles.survey_json.preferences.resource_preference"),
    ):
        value = owner.get("resource_preference")
        if value is not None and value != "":
            return resource_preference_values(value), source
    legacy_values = resource_preference_values(_read_value(profile, "exercise_preferences"))
    legacy_source = "user_profiles.exercise_preferences"
    return (legacy_values, legacy_source)


def set_resource_preferences(profile: Any, values: list[str], *, source: str) -> None:
    survey = parse_json_field(_read_value(profile, "survey_json"), {})
    survey = survey if isinstance(survey, dict) else {}
    preferences = survey.get("preferences")
    preferences = dict(preferences) if isinstance(preferences, dict) else {}
    values = list(dict.fromkeys(value.strip() for value in values if value.strip()))
    # Synchronize the two supported survey shapes, including explicit clears.
    survey["resource_preference"] = values
    preferences["resource_preference"] = values
    survey["preferences"] = preferences
    field_sources = survey.get("field_sources")
    field_sources = dict(field_sources) if isinstance(field_sources, dict) else {}
    field_sources["preferences.resource_preference"] = "user_confirmed" if source == "manual" else source
    survey["field_sources"] = field_sources
    profile.survey_json = serialize_json_field(survey)
    profile.exercise_preferences = "、".join(values)


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
    if "resource_preferences" not in locked_fields:
        if "resource_preference" in update:
            set_resource_preferences(profile, update["resource_preference"] or [], source=source)
            changed["exercise_preferences"] = profile.exercise_preferences
        elif "resource_preferences" in update and source == "manual":
            # Older clients still send prose. Preserve it without keyword routing.
            value = str(update["resource_preferences"] or "").strip()
            set_resource_preferences(profile, resource_preference_values(value), source=source)
    survey_updates = {
        key: update[key]
        for key in ("learning_background", "learning_habits")
        if key in update and (source not in AUTO_UPDATE_SOURCES or key not in locked_fields)
    }
    if survey_updates:
        survey = parse_json_field(_read_value(profile, "survey_json"), {})
        survey = survey if isinstance(survey, dict) else {}
        survey.update(survey_updates)
        setattr(profile, "survey_json", serialize_json_field(survey))
        changed.update(survey_updates)
    return changed
