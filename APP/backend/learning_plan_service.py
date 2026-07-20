from __future__ import annotations

import json
from typing import Any

from sqlalchemy.orm import Session

from APP.backend.agent_contracts import DiagnosisReport
from APP.backend.database import LearningPlanRecord


def _text(value: Any, default: str = "") -> str:
    if value is None:
        return default
    return str(value).strip() or default


def _listify(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [_text(item) for item in value if _text(item)]
    text = _text(value)
    return [text] if text else []


def _normalize_answers(onboarding_answers: dict[str, Any]) -> dict[str, Any]:
    background = onboarding_answers.get("background") if isinstance(onboarding_answers.get("background"), dict) else {}
    goals = onboarding_answers.get("goals") if isinstance(onboarding_answers.get("goals"), dict) else {}
    preferences = onboarding_answers.get("preferences") if isinstance(onboarding_answers.get("preferences"), dict) else {}
    daily_available_minutes = preferences.get("daily_available_minutes")
    if daily_available_minutes is None:
        daily_available_minutes = onboarding_answers.get("daily_available_minutes")
    return {
        "education": _text(background.get("education") or onboarding_answers.get("education")),
        "major_or_role": _text(background.get("major_or_role") or onboarding_answers.get("major_or_role")),
        "long_term_goal": _text(goals.get("long_term_goal") or onboarding_answers.get("long_term_goal")),
        "short_term_goal": _text(goals.get("short_term_goal") or onboarding_answers.get("short_term_goal")),
        "target_exam_or_course": _text(goals.get("target_exam_or_course") or onboarding_answers.get("target_exam_or_course")),
        "daily_available_minutes": int(daily_available_minutes or 30),
        "preferred_time_slot": _text(
            preferences.get("preferred_time_slot") or onboarding_answers.get("preferred_time_slot"),
            "未填写",
        ),
        "resource_preference": _listify(
            preferences.get("resource_preference") or onboarding_answers.get("resource_preference")
        ),
        "learning_mode": _text(preferences.get("learning_mode") or onboarding_answers.get("learning_mode")),
    }


def _current_focus(normalized: dict[str, Any], learning_profile: dict[str, Any]) -> str:
    if normalized["short_term_goal"]:
        return normalized["short_term_goal"]
    weak_kp_ids = learning_profile.get("weak_kp_ids", [])
    if weak_kp_ids:
        return f"优先补强 {weak_kp_ids[0]}"
    return normalized["long_term_goal"] or normalized["target_exam_or_course"] or "建立稳定学习节奏"


def _method_mix(learner_group: str, diagnosis_report: DiagnosisReport) -> dict[str, float]:
    if diagnosis_report.stage_id in {"T1", "T5"}:
        return {"learn": 0.35, "practice": 0.25, "review": 0.3, "reflection": 0.1}
    if learner_group == "大众兴趣":
        return {"learn": 0.5, "practice": 0.2, "review": 0.2, "reflection": 0.1}
    return {"learn": 0.4, "practice": 0.3, "review": 0.2, "reflection": 0.1}


def _build_phase_plan(
    normalized: dict[str, Any],
    learner_group: str,
    diagnosis_report: DiagnosisReport,
    learning_profile: dict[str, Any],
) -> list[dict[str, Any]]:
    focus = _current_focus(normalized, learning_profile)
    weak_kps = learning_profile.get("weak_kp_ids", [])
    target = normalized["target_exam_or_course"] or normalized["long_term_goal"] or focus
    phase_two_title = "核心知识包补强" if weak_kps else "核心能力推进"
    phase_three_title = "案例迁移与阶段复盘" if learner_group != "大众兴趣" else "生活场景应用与复盘"
    return [
        {
            "title": "长期规划：起点建档",
            "duration": "1-2 周",
            "focus_kp_ids": weak_kps[:2],
            "objective": f"围绕“{focus}”建立稳定节奏与先修地图",
            "acceptance": "完成画像建档，连续 3 天完成当日任务卡",
        },
        {
            "title": f"长期规划：{phase_two_title}",
            "duration": "2-4 周",
            "focus_kp_ids": weak_kps[:4],
            "objective": f"围绕目标“{target}”补齐核心薄弱知识点",
            "acceptance": "周练正确率达到 80%，薄弱知识点完成 1 次错题闭环",
        },
        {
            "title": f"长期规划：{phase_three_title}",
            "duration": "1-2 周",
            "focus_kp_ids": weak_kps[:3],
            "objective": "将知识点迁移到应用场景，并完成阶段复盘",
            "acceptance": "完成阶段测评或应用任务，输出 1 条复盘结论",
        },
    ]


def _build_weekly_plan(
    normalized: dict[str, Any],
    diagnosis_report: DiagnosisReport,
    learning_profile: dict[str, Any],
) -> dict[str, Any]:
    weak_kps = learning_profile.get("weak_kp_ids", [])
    focus = _current_focus(normalized, learning_profile)
    review_rule = "周末汇总错因并重排下周任务"
    if diagnosis_report.stage_id in {"T1", "T5"}:
        review_rule = "周中先做降载复盘，周末再恢复主路径验收"
    return {
        "week_goal": focus,
        "focus_kp_ids": weak_kps[:4],
        "acceptance": "完成本周知识卡、短练和复盘任务，并保持正确率不低于 80%",
        "review_rule": review_rule,
        "risk_note": diagnosis_report.stage_name,
    }


def _duration(minutes: int, fallback: int) -> int:
    return max(5, min(minutes, fallback))


def _build_daily_task_cards(
    normalized: dict[str, Any],
    diagnosis_report: DiagnosisReport,
    learning_profile: dict[str, Any],
) -> list[dict[str, Any]]:
    daily_minutes = max(10, min(normalized["daily_available_minutes"], 180))
    focus = _current_focus(normalized, learning_profile)
    preferred_difficulty = learning_profile.get("preferred_difficulty", "D2")
    cards = [
        {
            "type": "micro_lesson",
            "title": f"学习卡：{focus}",
            "duration_min": _duration(daily_minutes, 15),
            "difficulty": preferred_difficulty,
            "acceptance": "完成 1 张知识卡并复述关键概念",
        },
        {
            "type": "practice",
            "title": "短练：完成 5 道分阶练习",
            "duration_min": _duration(daily_minutes // 2, 20),
            "difficulty": preferred_difficulty,
            "acceptance": "完成练习并查看解析",
        },
        {
            "type": "reflection",
            "title": "复盘：记录 1 条今日困惑或收获",
            "duration_min": 5,
            "difficulty": "D1",
            "acceptance": "留下 1 条可复用复盘结论",
        },
    ]
    if learning_profile.get("weak_kp_ids"):
        cards.insert(
            1,
            {
                "type": "mistake_review",
                "title": "错题复盘：回看薄弱知识点并做变式题",
                "duration_min": _duration(daily_minutes // 3, 15),
                "difficulty": "D1" if diagnosis_report.stage_id in {"T1", "T5"} else preferred_difficulty,
                "acceptance": "完成 1 轮错题复盘并做 1 道变式题",
            },
        )
    return cards


def _legacy_daily_tasks(cards: list[dict[str, Any]]) -> list[dict[str, Any]]:
    tasks = []
    for index, card in enumerate(cards, start=1):
        tasks.append({
            "key": f"task-{index}",
            "type": card.get("type", "task"),
            "title": card.get("title", "未命名任务"),
            "duration_min": card.get("duration_min", 10),
            "difficulty": card.get("difficulty", "D2"),
            "reason": card.get("acceptance", "按计划完成本任务。"),
            "acceptance": card.get("acceptance", "按计划完成本任务。"),
        })
    return tasks


def generate_learning_plan(
    *,
    learner_id: str,
    learner_group: str,
    onboarding_answers: dict[str, Any],
    diagnosis_report: DiagnosisReport,
    learning_profile: dict[str, Any],
) -> dict[str, Any]:
    normalized = _normalize_answers(onboarding_answers)
    current_focus = _current_focus(normalized, learning_profile)
    phase_plan = _build_phase_plan(normalized, learner_group, diagnosis_report, learning_profile)
    weekly_plan = _build_weekly_plan(normalized, diagnosis_report, learning_profile)
    daily_task_cards = _build_daily_task_cards(normalized, diagnosis_report, learning_profile)
    daily_tasks = _legacy_daily_tasks(daily_task_cards)
    constraints = {
        "daily_available_minutes": normalized["daily_available_minutes"],
        "preferred_time_slot": normalized["preferred_time_slot"],
        "resource_preference": normalized["resource_preference"],
        "learning_mode": normalized["learning_mode"],
    }
    weekly_plan_with_aliases = {
        **weekly_plan,
        "focus": weekly_plan["week_goal"],
    }
    return {
        "learner_id": learner_id,
        "plan_summary": {
            "goal": normalized["long_term_goal"] or normalized["target_exam_or_course"] or current_focus,
            "learner_group": learner_group,
            "current_focus": current_focus,
            "method_mix": _method_mix(learner_group, diagnosis_report),
        },
        "phase_plan": phase_plan,
        "weekly_plan": weekly_plan_with_aliases,
        "daily_task_cards": daily_task_cards,
        "daily_tasks": daily_tasks,
        "constraints": constraints,
        "diagnosis_stage": {
            "stage_id": diagnosis_report.stage_id,
            "stage_name": diagnosis_report.stage_name,
        },
        "learning_profile": learning_profile,
    }


def create_or_update_learning_plan_record(
    db: Session,
    *,
    user_id: int,
    learner_group: str,
    onboarding_answers: dict[str, Any],
    diagnosis_report: DiagnosisReport,
    learning_profile: dict[str, Any],
) -> dict[str, Any]:
    payload = generate_learning_plan(
        learner_id=str(user_id),
        learner_group=learner_group,
        onboarding_answers=onboarding_answers,
        diagnosis_report=diagnosis_report,
        learning_profile=learning_profile,
    )
    summary = (
        f"长期规划：{payload['phase_plan'][0]['objective']}；"
        f"本周聚焦：{payload['weekly_plan']['week_goal']}"
    )
    record = (
        db.query(LearningPlanRecord)
        .filter(LearningPlanRecord.user_id == user_id, LearningPlanRecord.plan_type == "diagnosis_driven")
        .first()
    )
    if record is None:
        record = LearningPlanRecord(
            user_id=user_id,
            plan_type="diagnosis_driven",
            title="诊断驱动学习计划",
            summary=summary,
            status="active",
            payload_json=json.dumps(payload, ensure_ascii=False),
        )
        db.add(record)
        db.flush()
    else:
        record.title = "诊断驱动学习计划"
        record.summary = summary
        record.status = "active"
        record.payload_json = json.dumps(payload, ensure_ascii=False)
    db.commit()
    payload["record_id"] = record.id
    return payload
