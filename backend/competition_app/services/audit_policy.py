from __future__ import annotations

from typing import Any

from competition_app.contracts.audit_policy import AcceptancePolicy


RESOURCE_BLOCKING_ISSUE_TYPES = (
    "missing_evidence",
    "factual_error",
    "safety_violation",
)

RESOURCE_NON_BLOCKING_ISSUE_TYPES = (
    "content_quality",
    "conflicting_evidence",
    "learner_mismatch",
)


def _plain(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    return value


def _first_mapping(*values: Any) -> dict[str, Any]:
    for value in values:
        value = _plain(value)
        if isinstance(value, dict) and value:
            return value
    return {}


def _pick(source: dict[str, Any], *keys: str) -> dict[str, Any]:
    return {
        key: source[key]
        for key in keys
        if source.get(key) not in (None, "", [], {})
    }


def _compact_text(value: Any, *, limit: int = 500) -> str:
    return " ".join(str(value or "").split())[:limit]


def _compact_memories(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    result: list[str] = []
    for item in value[:5]:
        if isinstance(item, dict):
            text = item.get("content") or item.get("summary") or item.get("text")
        else:
            text = item
        text = _compact_text(text, limit=240)
        if text and text not in result:
            result.append(text)
    return result


def build_resource_acceptance_policy(
    context: dict[str, Any],
    *,
    formal_learning_task: Any = None,
) -> AcceptancePolicy:
    """Build the exact same resource policy for Expert and Audit.

    A missing formal task is represented explicitly.  Audit must not invent a
    task contract and then reject a producer that never received one.
    """

    dependencies = context.get("dependency_outputs") or {}
    plan_payload = getattr(dependencies.get("learning_plan"), "payload", None)
    schedule_payload = getattr(dependencies.get("schedule"), "payload", None)
    selected_review_task = getattr(schedule_payload, "selected_task", None)
    task = _first_mapping(
        formal_learning_task,
        getattr(plan_payload, "learning_task", None),
        selected_review_task,
    )
    profile = context.get("user_profile")
    profile = profile if isinstance(profile, dict) else {}
    preferences = _first_mapping(
        profile.get("preferences"),
        profile.get("user_preference"),
        profile.get("preference"),
        profile,
    )
    goals = _first_mapping(profile.get("goals"))
    learning_profile = context.get("learning_profile")
    learning_profile = learning_profile if isinstance(learning_profile, dict) else {}
    monitoring = context.get("learning_monitoring")
    monitoring = monitoring if isinstance(monitoring, dict) else {}
    current_status = learning_profile.get("current_status")
    current_status = current_status if isinstance(current_status, dict) else {}
    behavior_metrics = learning_profile.get("behavior_metrics")
    behavior_metrics = behavior_metrics if isinstance(behavior_metrics, dict) else {}
    short_term_plan = context.get("current_short_term_plan")
    short_term_plan = _plain(short_term_plan)
    short_term_plan = short_term_plan if isinstance(short_term_plan, dict) else {}
    learner_fit = {
        "goals": _pick(
            goals,
            "goal_type",
            "type",
            "goal_name",
            "name",
            "long_term_goal",
            "short_term_goal",
            "target_exam_or_course",
            "current_difficulties",
        ),
        "background": _pick(
            profile,
            "learner_group",
            "learning_background",
            "education",
            "user_major_or_profession",
            "completed_courses",
            "learning_habits",
            "time_constraints",
            "daily_available_minutes",
            "weekly_available_minutes",
        ),
        "preferences": _pick(
            preferences,
            "resource_preference",
            "learning_mode",
            "learning_periods",
            "difficulty_preference",
            "preferred_difficulty",
            "daily_available_minutes",
            "preferred_time_slot",
            "communication_style",
        ),
        "learning_profile": _pick(
            learning_profile,
            "current_level",
            "weak_kp_ids",
            "weaknesses",
            "recent_accuracy",
            "question_accuracy",
            "review_stability",
            "completion_rate",
        ),
        "current_status": _pick(
            current_status,
            "status_code",
            "status_name",
            "confidence",
            "evidence",
        ),
        "behavior_metrics": _pick(
            behavior_metrics,
            "task_completion_rate",
            "question_accuracy",
            "review_stability",
            "study_minutes",
            "active_days",
            "sample_counts",
        ),
        "monitoring": _pick(
            monitoring,
            "evidence_status",
            "freshness_status",
            "current_status",
            "behavior_summary",
            "metrics",
            "sample_counts",
            "calculated_at",
        ),
        "active_short_term_plan": _pick(
            short_term_plan,
            "status",
            "duration_days",
            "selected_books",
            "expected_output",
            "completion_criteria",
        ),
        "relevant_memories": _compact_memories(
            context.get("relevant_personalization_memories")
        ),
        "available_minutes": context.get("available_minutes"),
    }
    task_type = str(context.get("task_type") or "personalized_review_card")
    return AcceptancePolicy(
        policy_id=f"resource:{task_type}:v1",
        policy_version="1.1",
        task_type=task_type,
        subject_type="resource",
        hard_requirements=[
            (
                "核心教学结论必须由本次证据支持；不得把未获证据支持的补充内容"
                "冒充为教材原文或核心事实。非核心教学扩展的标签、范围和表达优化"
                "属于非阻断项。"
            ),
            "不得泄露正式题目的解答或解析，不得输出诊疗、处方或剂量建议。",
            "预计时长不得超过本轮可用时间。",
            *(
                ["资源动作、产出和完成标准必须与正式学习任务一致。"]
                if task
                else ["当前没有正式学习任务，不得把任务适配性作为阻断条件。"]
            ),
        ],
        non_blocking_preferences=[
            (
                "不影响核心事实、证据归属和安全边界的补充标签、教学扩展范围、"
                "开放式思考题、用字口径和表达方式属于可优化项。"
            ),
            "用户资源偏好用于排序和选择，不得在没有合适候选时强行追加资源。",
        ],
        blocking_issue_types=list(RESOURCE_BLOCKING_ISSUE_TYPES),
        non_blocking_issue_types=list(RESOURCE_NON_BLOCKING_ISSUE_TYPES),
        decision_policy={
            "pass": (
                "没有阻断类型问题时直接通过；即使仍有非阻断质量建议，也不得改判 revise。"
            ),
            "revise": (
                "存在可定位、可修复的 missing_evidence 或 factual_error 时返修。"
            ),
            "reject": (
                "仅用于整体核心依据不可用、伪造来源或无法确定安全返修范围的不可发布内容。"
            ),
            "needs_human_review": (
                "存在 safety_violation 或核心事实无法由可靠证据裁定时转人工。"
            ),
        },
        allowed_resource_origins=[
            "textbook_evidence",
            "formal_question_candidate",
            "selected_video_reference",
            "selected_external_reference",
            "clearly_labelled_teaching_supplement",
        ],
        formal_learning_task=task or None,
        formal_task_available=bool(task),
        learner_fit_facts=learner_fit,
        repair_ownership={
            "正文表达或教学结构": "expert",
            "资源选择": "expert",
            "候选池或证据不足": "knowledge",
            "结构合同": "compiler",
        },
    )
