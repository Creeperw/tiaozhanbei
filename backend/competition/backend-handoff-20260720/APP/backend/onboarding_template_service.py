from __future__ import annotations

from copy import deepcopy
from typing import Any


class OnboardingTemplateError(ValueError):
    pass


GROUP_TEMPLATES: list[dict[str, Any]] = [
    {
        "key": "cross_professional",
        "title": "跨专业进阶群体",
        "description": "非中医药专业出身、但需要系统掌握中医药技能的在职从业者。",
        "tags": ["在职碎片化", "技能认证/临床转化", "重案例实操"],
        "examples": ["基层全科医生", "乡村医生", "西学中人员", "健康管理师", "康复治疗师"],
        "default_profile": {
            "learning_goal": "系统补齐中医药基础，面向工作场景或职业技能认证形成可执行学习路径。",
            "daily_available_minutes": 30,
            "preferred_time_slot": "晚间或碎片化时间",
            "resource_preference": ["对比卡", "案例辨证", "考点速记"],
            "learning_mode": "案例优先",
            "preferred_difficulty": "D2",
            "current_difficulties": ["术语记不住", "证型容易混淆"],
        },
    },
    {
        "key": "academic",
        "title": "学历教育群体",
        "description": "中医药院校在读学生及规范化培训阶段的准医师。",
        "tags": ["课程体系", "章节训练", "规培/考试节点"],
        "examples": ["中医学专业学生", "中药学专业学生", "针灸推拿学学生", "中医规培学员", "师承弟子"],
        "default_profile": {
            "learning_goal": "对齐课程、规培或阶段考试，强化知识点掌握和案例辨证能力。",
            "daily_available_minutes": 45,
            "preferred_time_slot": "晚间固定学习时段",
            "resource_preference": ["章节讲义", "方剂/中诊/中药练习", "阶段测评"],
            "learning_mode": "章节训练",
            "preferred_difficulty": "D3",
            "current_difficulties": ["方剂组成混淆", "缺少练习反馈"],
        },
    },
]

# 已有用户可能保存过旧群体。它们只用于读取和规范化历史记录，不再通过
# 调查模板返回，也不能成为新注册页面的可选项。
LEGACY_GROUP_TEMPLATES: list[dict[str, Any]] = [
    {
        "key": "public_interest",
        "title": "大众兴趣群体",
        "description": "关注中医药文化与健康的普通公众、自学者和家庭健康管理人群。",
        "tags": ["健康文化", "生活化科普", "低风险食养"],
        "examples": ["家庭健康管理人群", "中老年人", "养生爱好者", "健身/瑜伽教练", "茶饮/食疗从业者"],
        "default_profile": {
            "learning_goal": "建立中医药文化和健康素养基础，理解低风险生活化知识，不替代诊疗。",
            "daily_available_minutes": 20,
            "preferred_time_slot": "碎片化不固定",
            "resource_preference": ["知识卡片", "药食同源科普", "生活场景问答"],
            "learning_mode": "科普入门",
            "preferred_difficulty": "D1",
            "current_difficulties": ["术语记不住", "资料太分散"],
        },
    }
]

ALL_GROUP_TEMPLATES = [*GROUP_TEMPLATES, *LEGACY_GROUP_TEMPLATES]

QUESTIONS: list[dict[str, Any]] = [
    {
        "key": "learner_group",
        "label": "请选择你的学习群体",
        "type": "group_cards",
        "required": True,
        "options": [item["key"] for item in GROUP_TEMPLATES],
        "help_text": "群体用于建立最小 L0 画像，后续仍可调整。",
        "default_by_group": {},
    },
    {
        "key": "daily_available_minutes",
        "label": "每天大约可学习多久？",
        "type": "single_choice",
        "required": False,
        "options": [
            {"value": 15, "label": "10-15 分钟"},
            {"value": 20, "label": "20 分钟左右"},
            {"value": 30, "label": "20-30 分钟"},
            {"value": 45, "label": "45 分钟左右"},
            {"value": 60, "label": "45-60 分钟"},
            {"value": 90, "label": "90 分钟以上"},
        ],
        "default_by_group": {item["key"]: item["default_profile"]["daily_available_minutes"] for item in ALL_GROUP_TEMPLATES},
        "help_text": "用于估算今日任务数量和默认学习节奏。",
    },
    {
        "key": "preferred_time_slot",
        "label": "你通常适合什么时间学习？",
        "type": "single_choice",
        "required": False,
        "options": ["早晨", "午休", "晚间或碎片化时间", "晚间固定学习时段", "周末", "碎片化不固定"],
        "default_by_group": {item["key"]: item["default_profile"]["preferred_time_slot"] for item in ALL_GROUP_TEMPLATES},
        "help_text": "锁定后不会被行为日志自动覆盖。",
    },
    {
        "key": "resource_preference",
        "label": "你更喜欢哪些学习资源？",
        "type": "multi_choice",
        "required": False,
        "options": [
            "知识卡片",
            "章节讲义",
            "案例训练",
            "错题变式",
            "视频微课",
            "对比表",
            "考点速记",
            "对比卡",
            "案例辨证",
            "方剂/中诊/中药练习",
            "阶段测评",
            "药食同源科普",
            "生活场景问答",
        ],
        "default_by_group": {item["key"]: item["default_profile"]["resource_preference"] for item in ALL_GROUP_TEMPLATES},
        "help_text": "可多选，用于每日推荐和智能助教讲解风格。",
    },
    {
        "key": "current_difficulties",
        "label": "当前最困扰你的学习问题是什么？",
        "type": "multi_choice",
        "required": False,
        "options": ["术语记不住", "证型容易混淆", "方剂组成混淆", "缺少练习反馈", "时间不稳定", "资料太分散"],
        "default_by_group": {
            "cross_professional": ["术语记不住", "证型容易混淆"],
            "academic": ["方剂组成混淆", "缺少练习反馈"],
            "public_interest": ["术语记不住", "资料太分散"],
        },
        "help_text": "可跳过，系统会先按群体默认困难建立先验画像。",
    },
]


def _group_display_title(group: dict[str, Any]) -> str:
    return str(group["title"])


def _group_display_label(group: dict[str, Any]) -> str:
    return _group_display_title(group).replace("群体", "")


def _canonical_group_key(value: str) -> str:
    normalized = (value or "").strip()
    for group in ALL_GROUP_TEMPLATES:
        if normalized in {group["key"], _group_display_title(group), _group_display_label(group)}:
            return group["key"]
    raise OnboardingTemplateError("请选择有效的学习群体")


def _group_by_key(group_key: str) -> dict[str, Any]:
    canonical_key = _canonical_group_key(group_key)
    for group in ALL_GROUP_TEMPLATES:
        if group["key"] == canonical_key:
            return group
    raise OnboardingTemplateError("请选择有效的学习群体")


def get_group_templates() -> dict[str, Any]:
    return {
        "groups": deepcopy(GROUP_TEMPLATES),
        "questions": deepcopy(QUESTIONS),
        "required_fields": ["learner_group"],
    }


SURVEY_TO_PROFILE_LOCKED_FIELD_MAP = {
    "preferred_time_slot": "time_constraints",
    "daily_available_minutes": "time_constraints",
    "resource_preference": "resource_preferences",
    "current_difficulties": "current_difficulties",
    "learner_group": "learner_group",
}


def _is_unanswered(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, (list, tuple, set, dict)):
        return len(value) == 0
    return False


def _question_default(question_key: str, group_key: str) -> Any:
    for question in QUESTIONS:
        if question["key"] == question_key:
            return deepcopy(question.get("default_by_group", {}).get(group_key))
    return None


def _first_answered(*values: Any) -> Any:
    for value in values:
        if not _is_unanswered(value):
            return deepcopy(value)
    return None


def _field_source(*values: Any) -> str:
    for value in values:
        if not _is_unanswered(value):
            return "user_confirmed"
    return "defaulted"


def normalize_survey_locked_fields(locked_fields: Any) -> list[str]:
    if not isinstance(locked_fields, list):
        return []
    normalized_fields: list[str] = []
    for field in locked_fields:
        field_name = str(field or "").strip()
        if not field_name or field_name in normalized_fields:
            continue
        normalized_fields.append(field_name)
    return normalized_fields


def map_survey_locked_fields(locked_fields: Any) -> list[str]:
    normalized_fields = normalize_survey_locked_fields(locked_fields)
    mapped_fields: list[str] = []
    for field_name in normalized_fields:
        mapped_field = SURVEY_TO_PROFILE_LOCKED_FIELD_MAP.get(field_name, field_name)
        if mapped_field not in mapped_fields:
            mapped_fields.append(mapped_field)
    return mapped_fields


def apply_onboarding_defaults(payload: dict[str, Any]) -> dict[str, Any]:
    group_value = str(payload.get("learner_group") or "").strip()
    if not group_value:
        raise OnboardingTemplateError("请选择学习群体")

    group = _group_by_key(group_value)
    normalized = deepcopy(payload)
    default_profile = deepcopy(group["default_profile"])
    background = deepcopy(normalized.get("background") or {})
    goals = deepcopy(normalized.get("goals") or {})
    preferences = deepcopy(normalized.get("preferences") or {})
    special_requirements = deepcopy(normalized.get("special_requirements") or {})

    field_sources = {
        "learner_group": "user_confirmed",
        "goals.long_term_goal": _field_source(
            goals.get("long_term_goal"),
            normalized.get("long_term_goal"),
        ),
        "goals.short_term_goal": _field_source(
            goals.get("short_term_goal"),
            normalized.get("short_term_goal"),
        ),
        "goals.target_exam_or_course": _field_source(
            goals.get("target_exam_or_course"),
            normalized.get("target_exam_or_course"),
        ),
        "goals.current_difficulties": _field_source(
            goals.get("current_difficulties"),
            normalized.get("current_difficulties"),
            normalized.get("difficulty_notes"),
        ),
        "preferences.daily_available_minutes": _field_source(
            preferences.get("daily_available_minutes"),
            normalized.get("daily_available_minutes"),
        ),
        "preferences.preferred_time_slot": _field_source(
            preferences.get("preferred_time_slot"),
            normalized.get("preferred_time_slot"),
        ),
        "preferences.resource_preference": _field_source(
            preferences.get("resource_preference"),
            normalized.get("resource_preference"),
        ),
        "preferences.learning_mode": _field_source(
            preferences.get("learning_mode"),
            normalized.get("learning_mode"),
        ),
        "preferences.difficulty_preference": _field_source(
            preferences.get("difficulty_preference"),
            normalized.get("difficulty_preference"),
        ),
    }

    goals["target_exam_or_course"] = _first_answered(
        goals.get("target_exam_or_course"),
        normalized.get("target_exam_or_course"),
    )
    goals["long_term_goal"] = _first_answered(
        goals.get("target_exam_or_course"),
        goals.get("long_term_goal"),
        normalized.get("long_term_goal"),
        default_profile["learning_goal"],
    )
    short_term_goal = _first_answered(
        goals.get("short_term_goal"),
        normalized.get("short_term_goal"),
    )
    if short_term_goal is None:
        goals.pop("short_term_goal", None)
        field_sources.pop("goals.short_term_goal", None)
    else:
        goals["short_term_goal"] = short_term_goal
    if goals.get("target_exam_or_course"):
        field_sources["goals.long_term_goal"] = field_sources[
            "goals.target_exam_or_course"
        ]
    goals["current_difficulties"] = _first_answered(
        goals.get("current_difficulties"),
        normalized.get("current_difficulties"),
        normalized.get("difficulty_notes"),
        _question_default("current_difficulties", group["key"]),
    )
    preferences["daily_available_minutes"] = _first_answered(
        preferences.get("daily_available_minutes"),
        normalized.get("daily_available_minutes"),
        default_profile["daily_available_minutes"],
    )
    preferences["preferred_time_slot"] = _first_answered(
        preferences.get("preferred_time_slot"),
        normalized.get("preferred_time_slot"),
        _question_default("preferred_time_slot", group["key"]),
    )
    preferences["resource_preference"] = _first_answered(
        preferences.get("resource_preference"),
        normalized.get("resource_preference"),
        _question_default("resource_preference", group["key"]),
    )
    preferences["learning_mode"] = _first_answered(
        preferences.get("learning_mode"),
        normalized.get("learning_mode"),
        default_profile["learning_mode"],
    )
    preferences["difficulty_preference"] = _first_answered(
        preferences.get("difficulty_preference"),
        normalized.get("difficulty_preference"),
        default_profile["preferred_difficulty"],
    )

    survey_locked_fields = normalize_survey_locked_fields(normalized.get("locked_fields"))
    normalized["learner_group"] = group["key"]
    normalized["learner_group_title"] = _group_display_title(group)
    normalized["background"] = background
    normalized["goals"] = goals
    normalized["preferences"] = preferences
    normalized["special_requirements"] = special_requirements
    normalized["field_sources"] = field_sources
    if "locked_fields" in normalized and normalized["locked_fields"] is not None:
        normalized["locked_fields"] = survey_locked_fields
        normalized["profile_locked_fields"] = map_survey_locked_fields(survey_locked_fields)
    return normalized
