from __future__ import annotations

import json
from typing import Any


PRIMARY_MODULES = [
    {
        "key": "assistant",
        "title": "智能助教问答",
        "description": "围绕学习问题、资料理解与个性化讲解展开对话。",
        "accent": "emerald",
    },
    {
        "key": "practice",
        "title": "练习与批改",
        "description": "进入练习、批改、错因分析与错题沉淀闭环。",
        "accent": "orange",
    },
    {
        "key": "knowledge",
        "title": "资料/知识库",
        "description": "管理公共资料、个人资料与后续检索来源。",
        "accent": "indigo",
    },
    {
        "key": "planning",
        "title": "学习规划",
        "description": "查看长期目标、近期任务与学习路径建议。",
        "accent": "teal",
    },
]


def _pick_display_name(user: dict[str, Any], profile: dict[str, Any]) -> str:
    return (
        (profile or {}).get("display_name")
        or (user or {}).get("username")
        or "学习者"
    )


def _is_serialized_onboarding_memory(memory: dict[str, Any]) -> bool:
    if str(memory.get("source") or "").strip().lower() == "onboarding_survey":
        return True
    content = memory.get("content")
    if not isinstance(content, str) or not content.lstrip().startswith("{"):
        return False
    try:
        payload = json.loads(content)
    except (TypeError, ValueError):
        return False
    return (
        isinstance(payload, dict)
        and payload.get("status") == "onboarding_completed"
        and isinstance(payload.get("survey_answers"), dict)
    )


def _context_focus(profile: dict[str, Any], memories: list[dict[str, Any]], events: list[dict[str, Any]]) -> str:
    for memory in memories:
        if _is_serialized_onboarding_memory(memory):
            continue
        focus = memory.get("content") or memory.get("title")
        if isinstance(focus, str) and focus.strip():
            return focus.strip()
    if events:
        return events[0].get("output_summary") or "最近一次学习主题"
    return profile.get("medical_history") or profile.get("health_goals") or "先完善学习目标"


def _build_recommendations(
    profile: dict[str, Any],
    memories: list[dict[str, Any]],
    events: list[dict[str, Any]],
    sessions: list[dict[str, Any]],
) -> list[dict[str, str]]:
    goal = profile.get("health_goals") or "先完善学习目标"
    preference = profile.get("exercise_preferences") or "题目、案例、知识卡和短视频"
    difficulty = profile.get("medical_history") or "当前薄弱点待补充"
    focus = _context_focus(profile, memories, events)
    reason = "基于你的长期目标与近期关注内容生成" if memories else "基于当前画像与平台默认学习路径生成"
    source_signal = f"{len(memories)} 条活跃记忆、{len(events)} 条近期 Agent 动态、{len(sessions)} 个近期会话"

    return [
        {
            "key": "daily-question",
            "title": "每日推荐题目",
            "reason": reason,
            "summary": f"围绕“{focus}”安排 5-10 道短练，并优先覆盖“{difficulty}”。",
            "resource_type": "question",
            "source_signal": source_signal,
            "action_label": "进入练习",
            "target_page": "practice",
        },
        {
            "key": "case-training",
            "title": "个性化案例训练",
            "reason": "案例训练能把知识点迁移到辨证推理和应用判断中",
            "summary": f"结合目标“{goal}”，推荐 1 个标准化案例，训练症状整理、证型判断和方药思路。",
            "resource_type": "case",
            "source_signal": source_signal,
            "action_label": "查看案例",
            "target_page": "practice",
        },
        {
            "key": "video-preview",
            "title": "视频/微课预览",
            "reason": "适合碎片化时间先建立直观理解，后续可接入联网搜索扩展来源",
            "summary": f"按你的资源偏好“{preference}”，预留 3-8 分钟微课或视频讲解位。",
            "resource_type": "video",
            "source_signal": source_signal,
            "action_label": "问助教要资源",
            "target_page": "assistant",
        },
        {
            "key": "resource-card",
            "title": "个性化资源卡",
            "reason": "资料库、画像和近期学习记录会共同影响资源推荐",
            "summary": "推荐讲义、知识卡、对比表或复习卡，后续会根据错题和完成情况动态调整。",
            "resource_type": "resource",
            "source_signal": source_signal,
            "action_label": "管理资料",
            "target_page": "knowledge",
        },
    ]


def _build_today_tasks(profile: dict[str, Any], memories: list[dict[str, Any]]) -> list[dict[str, str]]:
    focus = _context_focus(profile, memories, [])
    duration = profile.get("diet_restrictions") or "15-20 分钟"

    return [
        {
            "key": "micro-review",
            "title": "完成一次短练",
            "duration": duration,
            "reason": f"围绕“{focus}”快速检测掌握情况",
        },
        {
            "key": "resource-card",
            "title": "阅读一张知识卡",
            "duration": "5-8 分钟",
            "reason": "先用低负担资源建立今天的学习起点",
        },
    ]


def _build_monitoring_summary(
    profile: dict[str, Any],
    memories: list[dict[str, Any]],
    events: list[dict[str, Any]],
    sessions: list[dict[str, Any]],
) -> dict[str, Any]:
    has_behavior_signal = bool(events or sessions)

    return {
        "l0_baseline": {
            "learner_group": profile.get("constitution") or "未填写",
            "goal": profile.get("health_goals") or "未填写",
            "time_constraints": profile.get("diet_restrictions") or "未填写",
            "resource_preferences": profile.get("exercise_preferences") or "未填写",
        },
        "l3_monitoring": {
            "agent_events": len(events),
            "active_memories": len(memories),
            "recent_sessions": len(sessions),
            "latest_signal": events[0].get("output_summary") if events else "暂无近期 Agent 动态",
        },
        "t_stage": {
            "stage_id": "observing" if has_behavior_signal else "insufficient_data",
            "stage_name": "持续观察" if has_behavior_signal else "待积累行为数据",
            "evidence": [
                f"近期 Agent 动态 {len(events)} 条",
                f"可继续学习会话 {len(sessions)} 个",
                f"活跃学习记忆 {len(memories)} 条",
            ],
        },
    }


def build_dashboard_payload(
    *,
    user: dict[str, Any],
    profile: dict[str, Any],
    active_memories: list[dict[str, Any]],
    recent_events: list[dict[str, Any]],
    recent_sessions: list[dict[str, Any]],
    learning_target: dict[str, Any] | None = None,
    announcements: list[dict[str, Any]] | None = None,
    checkin_status: dict[str, Any] | None = None,
    difficulty_notice: dict[str, Any] | None = None,
) -> dict[str, Any]:
    profile = profile or {}
    learning_target = learning_target or None
    active_memories = active_memories or []
    recent_events = recent_events or []
    recent_sessions = recent_sessions or []

    display_name = _pick_display_name(user, profile)
    target_goal = str((learning_target or {}).get("exam_name") or "").strip()
    effective_profile = (
        {**profile, "health_goals": target_goal}
        if target_goal
        else profile
    )
    goal = effective_profile.get("health_goals") or "先完善学习目标，系统会据此生成更精准的推荐"
    focus = effective_profile.get("custom_needs") or "从首页推荐中选择一个业务入口开始今日学习"
    today_tasks = _build_today_tasks(effective_profile, active_memories)
    monitoring_summary = _build_monitoring_summary(
        effective_profile,
        active_memories,
        recent_events,
        recent_sessions,
    )

    status_cards = [
        {
            "key": "goal",
            "label": "L0 学习基线",
            "value": goal,
        },
        {
            "key": "memory",
            "label": "活跃学习记忆",
            "value": str(len(active_memories)) if active_memories else "待积累",
        },
        {
            "key": "activity",
            "label": "L3 监控摘要",
            "value": recent_events[0].get("output_summary") if recent_events else "暂无学习动态，建议先开始一次训练",
        },
        {
            "key": "t-stage",
            "label": "T 阶段观测",
            "value": monitoring_summary["t_stage"]["stage_name"],
        },
    ]

    continue_learning = [
        {
            "session_id": session.get("id"),
            "title": session.get("title") or "未命名学习会话",
            "updated_at": session.get("updated_at") or session.get("created_at"),
        }
        for session in recent_sessions[:3]
    ]

    return {
        "hero": {
            "greeting": f"{display_name}，你的培训助手已准备就绪",
            "goal": goal,
            "focus": focus,
        },
        "business_modules": PRIMARY_MODULES,
        "recommendations": _build_recommendations(
            effective_profile,
            active_memories,
            recent_events,
            recent_sessions,
        ),
        "status_cards": status_cards,
        "today_tasks": today_tasks,
        "monitoring_summary": monitoring_summary,
        "continue_learning": continue_learning,
        "announcements": announcements or [],
        "checkin_status": checkin_status or {"checked_in_today": False, "streak": 0},
        "difficulty_notice": difficulty_notice or None,
        "learning_target": learning_target,
    }
