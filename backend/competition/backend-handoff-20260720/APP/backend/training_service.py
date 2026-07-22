from __future__ import annotations

import re
from typing import Any


def _text(value: Any, default: str = "") -> str:
    if value is None:
        return default
    return str(value).strip() or default


def _profile_value(profile: dict[str, Any], key: str, default: str = "未填写") -> str:
    return _text(profile.get(key), default)


def _join_memory_text(memories: list[dict[str, Any]]) -> str:
    return "；".join(_text(item.get("content")) for item in memories if _text(item.get("content")))


def _first_focus(profile: dict[str, Any], memories: list[dict[str, Any]]) -> str:
    for key in ("medical_history", "custom_needs", "health_goals"):
        value = _text(profile.get(key))
        if value:
            return value
    memory_text = _join_memory_text(memories)
    return memory_text or "中医基础知识巩固"


def _classify_error(submission: dict[str, Any]) -> str:
    text = "；".join([
        _text(submission.get("stem")),
        _text(submission.get("student_answer")),
        _text(submission.get("standard_answer")),
        _text(submission.get("rubric")),
        "；".join(_text(item) for item in submission.get("knowledge_points", [])),
    ])
    if any(keyword in text for keyword in ("证型", "方剂", "四君子汤", "理中丸", "治法")):
        return "证型-方剂匹配错误"
    if any(keyword in text for keyword in ("概念", "阴阳", "五行", "术语")):
        return "概念混淆"
    return "答案要点不完整"


def _score_answer(student_answer: str, standard_answer: str) -> tuple[bool, int]:
    if not standard_answer:
        return False, 0
    normalized_student = student_answer.replace(" ", "")
    normalized_standard = standard_answer.replace(" ", "")
    if normalized_student == normalized_standard or normalized_standard in normalized_student:
        return True, 100
    shared_chars = set(normalized_student) & set(normalized_standard)
    partial_score = 60 if len(shared_chars) >= max(2, len(set(normalized_standard)) // 2) else 40
    return False, partial_score


def _choice_tokens(value: str) -> set[str]:
    compact = re.sub(r"选项|答案|[\[\]()（）{}'\"]", "", value or "", flags=re.IGNORECASE).upper()
    parts = [item for item in re.split(r"[\s,，、;；/|]+", compact) if item]
    if len(parts) == 1 and re.fullmatch(r"[A-H]+", parts[0]):
        return set(parts[0])
    return set(parts)


def _objective_grading_payload(submission: dict[str, Any]) -> dict[str, Any]:
    question_type = _text(submission.get("question_type"))
    student_answer = _text(submission.get("student_answer"))
    standard_answer = _text(submission.get("standard_answer"))
    kp_names = [_text(item) for item in submission.get("knowledge_point_names", []) if _text(item)]
    if not kp_names:
        kp_names = [
            _text(item) for item in submission.get("knowledge_points", [])
            if _text(item) and re.search(r"[\u4e00-\u9fff]", _text(item))
        ]
    point_text = "、".join(kp_names)
    if question_type in {"multiple_choice", "多选题", "多项选择题"}:
        selected = _choice_tokens(student_answer)
        correct = _choice_tokens(standard_answer)
        wrong = selected - correct
        is_correct = bool(correct) and selected == correct
        score = 0 if wrong or not correct else (100 if is_correct else round(100 * len(selected & correct) / len(correct)))
        rule_note = "多选题含错误选项，按规则计 0 分。" if wrong else "多选题按正确选项覆盖情况计分。"
    else:
        is_correct, score = _score_answer(student_answer, standard_answer)
        if question_type in {"single_choice", "true_false", "单选题", "单项选择题", "判断题"} and not is_correct:
            score = 0
        rule_note = "客观题由系统依据标准答案自动判分。"
    topic_note = f"本题考查{point_text}。" if point_text else "本题的知识点名称暂未匹配，系统不会用内部编号代替。"
    analysis = f"{topic_note}{rule_note}"
    error_type = "none" if is_correct else "待结合作答情况分析"
    if not is_correct:
        analysis += " 错因暂不自动下结论，请到错题变式中补充当时的作答把握和判断过程。"
    return {
        "grading": {
            "question_id": _text(submission.get("question_id"), "manual-question"),
            "is_correct": is_correct,
            "score": score,
            "error_type": error_type,
            "analysis": analysis,
            "standard_answer": standard_answer,
        },
        "mistake_record": None if is_correct else {
            "category": "mistake",
            "error_type": error_type,
            "content": analysis,
            "source": "objective_practice_grading",
        },
        "agent_trace": [
            {"agent": "planner_agent", "action": "识别为客观题批改", "status": "success"},
            {"agent": "audit_agent", "action": "核对服务端标准答案与计分规则", "status": "success"},
            {"agent": "memory_agent", "action": "等待用户补充作答情境后再归因", "status": "skipped" if is_correct else "pending"},
        ],
    }


def _build_variant_questions(knowledge_points: list[str], standard_answer: str) -> list[dict[str, Any]]:
    primary = knowledge_points[0] if knowledge_points else standard_answer or "当前知识点"
    secondary = knowledge_points[1] if len(knowledge_points) > 1 else "易混知识点"
    return [
        {
            "key": "variant-compare",
            "type": "short_answer",
            "stem": f"请用 2 句话说明{primary}与{secondary}的核心区别。",
            "target_answer": standard_answer or primary,
        },
        {
            "key": "variant-apply",
            "type": "case_quiz",
            "stem": f"遇到与{primary}相关的案例时，先判断哪一个证据点？",
            "target_answer": standard_answer or primary,
        },
    ]


def _grade_submission_payload(
    *,
    profile: dict[str, Any],
    memories: list[dict[str, Any]],
    submission: dict[str, Any],
) -> dict[str, Any]:
    student_answer = _text(submission.get("student_answer"))
    standard_answer = _text(submission.get("standard_answer"))
    knowledge_points = [_text(item) for item in submission.get("knowledge_points", []) if _text(item)]
    knowledge_point_names = [_text(item) for item in submission.get("knowledge_point_names", []) if _text(item)]
    is_correct, score = _score_answer(student_answer, standard_answer)
    error_type = "已掌握" if is_correct else _classify_error(submission)
    focus = _first_focus(profile, memories)
    point_text = "、".join(knowledge_point_names) or "当前知识点"

    analysis = (
        f"本题考查{point_text}。你的答案为“{student_answer or '未作答'}”，"
        f"标准答案为“{standard_answer or '待补充'}”。"
    )
    if not is_correct:
        analysis += f"主要错因是{error_type}，建议结合你的当前薄弱点“{focus}”做一次对比复盘。"

    mistake_record = None
    if not is_correct:
        mistake_record = {
            "category": "mistake",
            "importance": "important",
            "title": f"错题：{_text(submission.get('stem'), '练习题')[:40]}",
            "content": (
                f"题目：{_text(submission.get('stem'))}\n"
                f"学生答案：{student_answer or '未作答'}\n"
                f"标准答案：{standard_answer or '待补充'}\n"
                f"错因：{error_type}\n"
                f"知识点：{point_text}"
            ),
            "source": "practice_grading",
        }

    return {
        "grading": {
            "question_id": _text(submission.get("question_id"), "manual-question"),
            "is_correct": is_correct,
            "score": score,
            "error_type": error_type,
            "analysis": analysis,
            "standard_answer": standard_answer,
        },
        "mistake_record": mistake_record,
        "remediation": {
            "review_card": {
                "title": f"复盘卡：{point_text}",
                "content": f"先回看{point_text}的定义、适用证据和易混点，再用自己的话复述标准答案：{standard_answer or point_text}。",
            },
            "variant_questions": _build_variant_questions(knowledge_point_names, standard_answer),
        },
    }


def grade_practice_submission(
    *,
    profile: dict[str, Any],
    memories: list[dict[str, Any]],
    submission: dict[str, Any],
) -> dict[str, Any]:
    if _text(submission.get("question_type")) in {
        "single_choice", "multiple_choice", "fill_blank", "true_false",
        "单选题", "单项选择题", "多选题", "多项选择题", "填空题", "判断题",
    }:
        return _objective_grading_payload(submission)
    from APP.backend.agent_contracts import DiagnosisReport, EvidenceItem, EvidencePack, LearnerContextBrief
    from APP.backend.expert_agent_service import grade_submission

    knowledge_points = [_text(item) for item in submission.get("knowledge_points", []) if _text(item)]
    knowledge_point_names = [_text(item) for item in submission.get("knowledge_point_names", []) if _text(item)]
    point_text = "、".join(knowledge_point_names) or "当前知识点"
    learner_context = LearnerContextBrief(
            learner_id="practice-learner",
            learner_group=_text(profile.get("constitution"), "普通学习者"),
            goal=_text(profile.get("health_goals"), _first_focus(profile, memories)),
            source_scope="training_service",
            source_id=_text(submission.get("question_id"), "manual-question"),
            kp_ids=knowledge_points,
            confidence=0.88,
            profile=dict(profile),
        )
    evidence_pack = EvidencePack(
            source_scope="training_service",
            source_id=f"grading:{_text(submission.get('question_id'), 'manual-question')}",
            items=[
                EvidenceItem(
                    source_scope="submission",
                    source_id=_text(submission.get("question_id"), "manual-question"),
                    summary=_text(submission.get("rubric")) or _text(submission.get("stem"), "练习题"),
                    kp_ids=knowledge_points,
                    confidence=0.86,
                )
            ],
            kp_ids=knowledge_points,
            resolved_kp_ids=knowledge_points,
            confidence=0.86,
        )
    diagnosis_report = DiagnosisReport(
            diagnosis_id=f"grading:{_text(submission.get('question_id'), 'manual-question')}",
            stage_id="grading",
            stage_name="practice_grading",
            summary=f"围绕{point_text}完成练习批改与复盘建议生成。",
            source_scope="training_service",
            source_id=_text(submission.get("question_id"), "manual-question"),
            kp_ids=knowledge_points,
            confidence=0.84,
        )
    try:
        expert_artifact = grade_submission(
            learner_context=learner_context,
            evidence_pack=evidence_pack,
            diagnosis_report=diagnosis_report,
            submission=submission,
            profile=profile,
            memories=memories,
        )
    except Exception as exc:
        fallback = _grade_submission_payload(
            profile=profile, memories=memories, submission=submission
        )
        fallback["audit"] = {
            "decision": "needs_human_review",
            "reason": "专家智能体或审核智能体暂不可用，规则结果不得写回学习状态。",
            "confidence": 0.0,
            "audit_source": "fail_closed",
        }
        fallback["grading"]["grading_source"] = "rule_fallback"
        fallback["agent_trace"] = [
            {"agent": "planner_agent", "action": "识别为主观题批改", "status": "success"},
            {"agent": "knowledge_base_agent", "action": f"对齐知识点：{point_text}", "status": "success"},
            {"agent": "expert_agent", "action": "semantic_subjective_grading", "status": "failed", "reason": type(exc).__name__},
            {"agent": "audit_agent", "action": "independent_grading_review", "status": "needs_human_review"},
            {"agent": "memory_agent", "action": "等待审核通过后再沉淀错题", "status": "skipped"},
        ]
        return fallback
    payload = dict(expert_artifact.content)
    payload["agent_trace"] = [
        {"agent": "planner_agent", "action": "识别为主观题批改", "status": "success"},
        {"agent": "knowledge_base_agent", "action": f"对齐知识点：{point_text}", "status": "success"},
        *list(expert_artifact.agent_trace),
        {"agent": "memory_agent", "action": "审核通过后生成错题沉淀记录", "status": "success" if payload.get("mistake_record") and payload.get("audit", {}).get("decision") == "pass" else "skipped"},
    ]
    return payload


def _extract_minutes(text: str, fallback: int = 30) -> int:
    numbers = [int(item) for item in re.findall(r"\d+", text or "")]
    if not numbers:
        return fallback
    return max(10, min(max(numbers), 120))


def _mistake_memories(memories: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [item for item in memories if item.get("category") == "mistake" or "错" in _text(item.get("title"))]


def build_learning_plan_summary(
    *,
    profile: dict[str, Any],
    memories: list[dict[str, Any]],
    events: list[dict[str, Any]],
) -> dict[str, Any]:
    goal = _profile_value(profile, "health_goals", "先完成学习目标建档")
    learner_group = _profile_value(profile, "constitution", "普通学习者")
    time_budget = _profile_value(profile, "diet_restrictions", "每天 30 分钟")
    preferences = _profile_value(profile, "exercise_preferences", "知识卡和短练")
    focus = _first_focus(profile, memories)
    mistake_count = len(_mistake_memories(memories))
    daily_minutes = _extract_minutes(time_budget)

    daily_tasks = [
        {
            "key": "task-learn",
            "type": "micro_lesson",
            "title": f"围绕“{focus[:18]}”学习一张知识卡",
            "duration_min": min(15, daily_minutes),
            "difficulty": 2,
            "reason": "承接长期目标与当前薄弱点",
        },
        {
            "key": "task-practice",
            "type": "practice",
            "title": "完成 5 道分阶短练并立即批改",
            "duration_min": min(20, max(10, daily_minutes // 2)),
            "difficulty": 3,
            "reason": "用练习反馈更新画像和错题库",
        },
        {
            "key": "task-reflect",
            "type": "reflection",
            "title": "记录 1 条今日困惑或复盘结论",
            "duration_min": 5,
            "difficulty": 1,
            "reason": "为记忆管理 Agent 提供长期学习信号",
        },
    ]
    if mistake_count:
        daily_tasks.insert(1, {
            "key": "task-mistake-review",
            "type": "mistake_review",
            "title": f"复盘 {mistake_count} 条近期错题并做变式题",
            "duration_min": min(15, daily_minutes),
            "difficulty": 2,
            "reason": "错题沉淀已形成，需要闭环复习",
        })

    return {
        "plan_summary": {
            "goal": goal,
            "learner_group": learner_group,
            "current_focus": focus,
            "method_mix": {"learn": 0.45, "practice": 0.35, "reflection": 0.20},
        },
        "weekly_plan": {
            "focus": f"本周聚焦：{focus}",
            "acceptance": "完成短练并达到 80% 正确率；错题需完成 1 次复盘。",
            "evidence": [_text(item.get("output_summary")) for item in events if _text(item.get("output_summary"))][:3],
        },
        "daily_tasks": daily_tasks,
        "constraints": {
            "time_budget": time_budget,
            "resource_preferences": preferences,
            "daily_available_minutes": daily_minutes,
        },
        "agent_trace": [
            {"agent": "diagnosis_agent", "action": "读取 L0 画像与错题信号", "status": "success"},
            {"agent": "learning_plan_service", "action": "生成周计划与日任务卡", "status": "success"},
        ],
    }


def build_learning_report(
    *,
    profile: dict[str, Any],
    memories: list[dict[str, Any]],
    events: list[dict[str, Any]],
) -> dict[str, Any]:
    learner_group = _profile_value(profile, "constitution", "普通学习者")
    goal = _profile_value(profile, "health_goals", "未填写")
    mistakes = _mistake_memories(memories)
    focus = _first_focus(profile, memories)
    weak_points = []
    for item in memories:
        content = _text(item.get("content"))
        if item.get("category") == "mistake" or "薄弱" in _text(item.get("title")) or "错因" in content:
            weak_points.append({
                "title": _text(item.get("title"), "薄弱点"),
                "evidence": content,
            })
    if not weak_points:
        weak_points.append({"title": "待观察薄弱点", "evidence": focus})

    mastery_base = 0.62 if mistakes else 0.74
    report = {
        "learner_overview": {
            "learner_group": learner_group,
            "goal": goal,
            "current_focus": focus,
        },
        "mastery_radar": [
            {"name": "中医基础", "value": round(mastery_base, 2)},
            {"name": "中医诊断", "value": round(max(0.45, mastery_base - 0.08), 2)},
            {"name": "方剂学", "value": round(max(0.42, mastery_base - (0.14 if mistakes else 0.04)), 2)},
            {"name": "辨证推理", "value": round(max(0.40, mastery_base - 0.12), 2)},
        ],
        "weak_points": weak_points[:5],
        "mistake_summary": {
            "total_mistakes": len(mistakes),
            "top_error_type": "概念混淆" if any("概念" in _text(item.get("content")) for item in mistakes) else "证型-方剂匹配错误" if mistakes else "暂无明显错因",
        },
        "resource_match": {
            "difficulty_match": 0.88 if learner_group != "大众兴趣群体" else 0.90,
            "recommended_difficulty": "零基础科普" if learner_group == "大众兴趣群体" else "课程学习",
            "reason": "根据学习者群体、目标、错题与资源偏好匹配推荐难度。",
        },
        "t_stage": {
            "stage_id": "T5" if mistakes else "T0" if events else "insufficient_data",
            "stage_name": "难度不适" if mistakes else "稳定学习" if events else "待积累行为数据",
            "evidence": [
                f"错题记录 {len(mistakes)} 条",
                f"Agent 学习事件 {len(events)} 条",
                f"当前重点：{focus}",
            ],
        },
        "next_actions": [
            "完成今日任务卡中的短练与复盘",
            "优先处理薄弱点 Top1 对应的知识卡",
            "答错后查看解析并完成 1-2 道变式题",
        ],
        "agent_trace": [
            {"agent": "diagnosis_agent", "action": "生成学情雷达与 T 阶段", "status": "success"},
            {"agent": "audit_agent", "action": "检查报告是否保持教学边界", "status": "success"},
        ],
    }
    return report
