from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import random
from typing import Any


BASE_DIR = Path(__file__).resolve().parents[1]
REFERENCE_SEED = BASE_DIR / "sample_data" / "shizhen_mvp_seed.json"
DEFAULT_OUTPUT = BASE_DIR / "sample_data" / "synthetic_user_usage_v1.json"
RANDOM_SEED = 20260905
ANCHOR = datetime(2026, 9, 4, 12, 0, tzinfo=timezone.utc)


PERSONAS = (
    {
        "group": "学历教育",
        "count": 4,
        "active_days": (18, 22),
        "daily_minutes": 50,
        "preferred_time_slot": "晚间20:00–21:30",
        "resource_preference": ["知识卡片", "对比表", "刷题"],
        "learning_mode": "混合",
        "difficulty_preference": "D2",
        "goal": "系统复习方剂学核心内容并完成阶段测验",
        "foundation": "basic",
        "completion_probability": 0.84,
        "initial_accuracy": 0.58,
        "final_accuracy": 0.82,
    },
    {
        "group": "跨专业进阶",
        "count": 4,
        "active_days": (13, 18),
        "daily_minutes": 35,
        "preferred_time_slot": "工作日晚间21:00后",
        "resource_preference": ["知识卡片", "病例题", "口诀"],
        "learning_mode": "讲解后练习",
        "difficulty_preference": "D1-D2",
        "goal": "建立证候、治法与方剂之间的基础联系",
        "foundation": "limited",
        "completion_probability": 0.72,
        "initial_accuracy": 0.46,
        "final_accuracy": 0.71,
    },
    {
        "group": "大众兴趣",
        "count": 4,
        "active_days": (7, 12),
        "daily_minutes": 20,
        "preferred_time_slot": "周末或晚间碎片时间",
        "resource_preference": ["通俗知识卡", "短视频", "入门测验"],
        "learning_mode": "讲解优先",
        "difficulty_preference": "D1",
        "goal": "理解常见中医药基础概念及学习边界",
        "foundation": "none",
        "completion_probability": 0.63,
        "initial_accuracy": 0.38,
        "final_accuracy": 0.61,
    },
)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _load_reference() -> dict[str, Any]:
    return json.loads(REFERENCE_SEED.read_text(encoding="utf-8"))


def build_dataset() -> dict[str, Any]:
    rng = random.Random(RANDOM_SEED)
    reference = _load_reference()
    questions = reference["question_bank"]
    kp_names = {item["kp_id"]: item["name"] for item in reference["knowledge_points"]}

    users: list[dict[str, Any]] = []
    sessions: list[dict[str, Any]] = []
    activities: list[dict[str, Any]] = []
    attempts: list[dict[str, Any]] = []
    mistakes: list[dict[str, Any]] = []
    plans: list[dict[str, Any]] = []
    interventions: list[dict[str, Any]] = []
    mastery_records: list[dict[str, Any]] = []
    agent_events: list[dict[str, Any]] = []
    learning_tasks: list[dict[str, Any]] = []
    focus_sessions: list[dict[str, Any]] = []
    daily_tasks: list[dict[str, Any]] = []
    memories: list[dict[str, Any]] = []

    global_user_index = 0
    for persona in PERSONAS:
        for group_index in range(1, persona["count"] + 1):
            global_user_index += 1
            learner_id = f"SIM_U{global_user_index:03d}"
            username = f"sim_2026_user_{global_user_index:03d}"
            created_at = ANCHOR - timedelta(days=31, hours=global_user_index)
            users.append(
                {
                    "learner_id": learner_id,
                    "username": username,
                    "email": f"{username}@example.test",
                    "display_name": f"{persona['group']}学习者{group_index:02d}",
                    "learner_group": persona["group"],
                    "created_at": _iso(created_at),
                    "profile": {
                        "constitution": persona["group"],
                        "health_goals": persona["goal"],
                        "diet_restrictions": f"每日可学习约{persona['daily_minutes']}分钟",
                        "exercise_preferences": "、".join(persona["resource_preference"]),
                        "medical_history": "无真实病史；本字段仅存放合成学习背景",
                        "custom_needs": "学习内容仅用于教学，不作为诊疗或用药依据",
                    },
                    "onboarding_answers": {
                        "background": {
                            "education": "本科" if persona["group"] != "大众兴趣" else "未采集",
                            "major_or_role": "中医药相关专业学生" if persona["group"] == "学历教育" else "非中医药专业学习者",
                            "tcm_foundation": persona["foundation"],
                            "learned_courses": ["中医基础理论"] if persona["group"] == "学历教育" else [],
                        },
                        "goals": {
                            "long_term_goal": persona["goal"],
                            "short_term_goal": "完成四君子汤主题学习与复习",
                            "target_exam_or_course": "方剂学阶段复习" if persona["group"] == "学历教育" else "",
                        },
                        "preferences": {
                            "daily_available_minutes": persona["daily_minutes"],
                            "preferred_time_slot": persona["preferred_time_slot"],
                            "resource_preference": persona["resource_preference"],
                            "learning_mode": persona["learning_mode"],
                            "difficulty_preference": persona["difficulty_preference"],
                        },
                        "special_requirements": {
                            "has_requirement": persona["group"] == "大众兴趣",
                            "requirement_type": "plain_language" if persona["group"] == "大众兴趣" else "none",
                            "description": "术语需要通俗解释" if persona["group"] == "大众兴趣" else "none",
                            "examples": [],
                        },
                    },
                }
            )

            active_day_count = rng.randint(*persona["active_days"])
            day_offsets = sorted(rng.sample(range(27), active_day_count), reverse=True)
            user_attempts: list[dict[str, Any]] = []
            selected_kps = sorted({kp for q in questions for kp in q["kp_ids"]})[:7]
            plan_id = f"SIM_PLAN_{global_user_index:03d}"
            plans.append(
                {
                    "learner_id": learner_id,
                    "plan_id": plan_id,
                    "title": f"{persona['group']}四君子汤学习计划",
                    "summary": persona["goal"],
                    "status": "active",
                    "target_kp_ids": selected_kps,
                    "daily_available_minutes": persona["daily_minutes"],
                    "created_at": _iso(created_at + timedelta(days=1)),
                }
            )
            memories.append(
                {
                    "learner_id": learner_id,
                    "category": "preference",
                    "title": "学习资源偏好",
                    "content": "偏好" + "、".join(persona["resource_preference"]),
                    "confidence": 1.0,
                    "created_at": _iso(created_at + timedelta(days=1, minutes=5)),
                }
            )

            for sequence, day_offset in enumerate(day_offsets, start=1):
                local_hour = 12 + (global_user_index % 3)
                start = ANCHOR - timedelta(days=day_offset) + timedelta(hours=local_hour - 12)
                session_minutes = max(8, int(rng.gauss(persona["daily_minutes"], 7)))
                task_id = f"SIM_TASK_{global_user_index:03d}_{sequence:02d}"
                focus_id = f"SIM_FOCUS_{global_user_index:03d}_{sequence:02d}"
                activities.append(
                    {
                        "learner_id": learner_id,
                        "activity_type": "login",
                        "resource_id": "",
                        "resource_type": "session",
                        "duration_minutes": 0,
                        "completion_status": "completed",
                        "payload": {"source": "synthetic_usage_v1"},
                        "occurred_at": _iso(start),
                    }
                )
                if rng.random() < 0.76:
                    activities.append(
                        {
                            "learner_id": learner_id,
                            "activity_type": "daily_checkin",
                            "resource_id": f"checkin:{learner_id}:{sequence}",
                            "resource_type": "checkin",
                            "duration_minutes": 0,
                            "completion_status": "completed",
                            "payload": {"source": "synthetic_usage_v1"},
                            "occurred_at": _iso(start + timedelta(minutes=1)),
                        }
                    )

                learning_tasks.append(
                    {
                        "learner_id": learner_id,
                        "task_id": task_id,
                        "task_type": "daily_learning",
                        "kp_ids": selected_kps[:3],
                        "resource_ids": ["RES_SJZ_CARD_001"],
                        "task_content": "完成知识卡学习与配套练习",
                        "estimated_minutes": session_minutes,
                        "status": "completed",
                        "created_at": _iso(start),
                        "due_at": _iso(start + timedelta(hours=3)),
                        "completed_at": _iso(start + timedelta(minutes=session_minutes)),
                    }
                )
                focus_sessions.append(
                    {
                        "learner_id": learner_id,
                        "focus_session_id": focus_id,
                        "task_id": task_id,
                        "resource_type": "daily_learning",
                        "resource_id": "RES_SJZ_CARD_001",
                        "active_seconds": session_minutes * 60,
                        "started_at": _iso(start + timedelta(minutes=2)),
                        "ended_at": _iso(start + timedelta(minutes=2 + session_minutes)),
                    }
                )

                item1_done = rng.random() < persona["completion_probability"]
                item2_done = rng.random() < max(0.35, persona["completion_probability"] - 0.1)
                host_task_id = f"SIM_DAILY_{global_user_index:03d}_{sequence:02d}"
                daily_tasks.append(
                    {
                        "learner_id": learner_id,
                        "host_task_id": host_task_id,
                        "status": "completed" if item1_done and item2_done else "in_progress",
                        "created_at": _iso(start),
                        "items": [
                            {
                                "task_item_id": f"{host_task_id}_I1",
                                "kp_id": selected_kps[(sequence - 1) % len(selected_kps)],
                                "item_kind": "knowledge_practice",
                                "ordinal": 1,
                                "required_question_count": 3,
                                "status": "completed" if item1_done else "pending",
                                "completed_at": _iso(start + timedelta(minutes=15)) if item1_done else None,
                            },
                            {
                                "task_item_id": f"{host_task_id}_I2",
                                "kp_id": selected_kps[sequence % len(selected_kps)],
                                "item_kind": "review",
                                "ordinal": 2,
                                "required_question_count": 2,
                                "status": "completed" if item2_done else "pending",
                                "completed_at": _iso(start + timedelta(minutes=session_minutes)) if item2_done else None,
                            },
                        ],
                    }
                )

                progress = (sequence - 1) / max(1, active_day_count - 1)
                accuracy = persona["initial_accuracy"] + (
                    persona["final_accuracy"] - persona["initial_accuracy"]
                ) * progress
                attempt_count = rng.randint(2, 5)
                for attempt_index in range(attempt_count):
                    question = rng.choice(questions)
                    correct = rng.random() < accuracy
                    occurred_at = start + timedelta(minutes=5 + attempt_index * 4)
                    attempt_id = f"SIM_ATT_{global_user_index:03d}_{sequence:02d}_{attempt_index + 1:02d}"
                    answer = question["answer"] if correct else "模拟错误作答"
                    score = 100.0 if correct else rng.choice([35.0, 45.0, 60.0])
                    attempt = {
                        "attempt_id": attempt_id,
                        "learner_id": learner_id,
                        "question_id": question["question_id"],
                        "answer": answer,
                        "is_correct": correct,
                        "score": score,
                        "kp_ids": question["kp_ids"],
                        "feedback": "回答正确" if correct else "需回看对应知识卡后再次练习",
                        "occurred_at": _iso(occurred_at),
                    }
                    attempts.append(attempt)
                    user_attempts.append(attempt)
                    activities.append(
                        {
                            "learner_id": learner_id,
                            "activity_type": "question_attempt",
                            "resource_id": question["question_id"],
                            "resource_type": "question",
                            "duration_minutes": rng.randint(2, 5),
                            "completion_status": "completed" if correct else "needs_review",
                            "score": score,
                            "payload": {
                                "attempt_id": attempt_id,
                                "kp_ids": question["kp_ids"],
                                "is_correct": correct,
                                "source": "synthetic_usage_v1",
                            },
                            "occurred_at": _iso(occurred_at),
                        }
                    )
                    if not correct:
                        mistakes.append(
                            {
                                "mistake_id": f"SIM_M_{attempt_id}",
                                "learner_id": learner_id,
                                "question_id": question["question_id"],
                                "kp_ids": question["kp_ids"],
                                "error_type": "知识点掌握不稳",
                                "summary": "本次为合成错误记录，建议复习对应知识点",
                                "status": "active",
                                "created_at": _iso(occurred_at),
                            }
                        )

                view_id = f"recommendation-view:{learner_id}:{sequence}"
                activities.append(
                    {
                        "learner_id": learner_id,
                        "activity_type": "dashboard_recommendations_view",
                        "resource_id": view_id,
                        "resource_type": "dashboard_recommendations",
                        "duration_minutes": 0,
                        "completion_status": "viewed",
                        "payload": {
                            "recommendation_keys": ["RES_SJZ_CARD_001", "RES_SJZ_LECTURE_001"],
                            "source": "synthetic_usage_v1",
                        },
                        "occurred_at": _iso(start + timedelta(minutes=3)),
                    }
                )
                if rng.random() < 0.68:
                    activities.append(
                        {
                            "learner_id": learner_id,
                            "activity_type": "resource_click",
                            "resource_id": rng.choice(["RES_SJZ_CARD_001", "RES_SJZ_LECTURE_001"]),
                            "resource_type": "dashboard_recommendation",
                            "duration_minutes": 0,
                            "completion_status": "clicked",
                            "payload": {"recommendation_view_id": view_id, "source": "synthetic_usage_v1"},
                            "occurred_at": _iso(start + timedelta(minutes=4)),
                        }
                    )
                if sequence % 3 == 0:
                    activities.append(
                        {
                            "learner_id": learner_id,
                            "activity_type": "textbook_section_completed",
                            "resource_id": f"section-{(sequence % 6) + 1}",
                            "resource_type": "textbook_section",
                            "duration_minutes": max(5, session_minutes // 2),
                            "completion_status": "completed",
                            "payload": {
                                "book": "方剂学",
                                "route": "textbook_14_5",
                                "chapter_id": "chapter-fangji-jichu",
                                "chapter_name": "补益剂基础",
                                "section_name": "四君子汤主题学习",
                                "source": "synthetic_usage_v1",
                            },
                            "occurred_at": _iso(start + timedelta(minutes=session_minutes)),
                        }
                    )

            session_days = day_offsets[: min(3, len(day_offsets))]
            for session_index, day_offset in enumerate(session_days, start=1):
                session_id = f"SIM-CHAT-{global_user_index:03d}-{session_index:02d}"
                session_time = ANCHOR - timedelta(days=day_offset, hours=1)
                request = (
                    "请结合我的学习记录安排今天的四君子汤复习任务。"
                    if session_index == 1
                    else "我刚才的练习哪些知识点还需要复习？"
                )
                response = (
                    "已依据当前画像、练习结果和可用时间生成学习建议；这是教学用途的合成示例。"
                )
                sessions.append(
                    {
                        "learner_id": learner_id,
                        "session_id": session_id,
                        "title": "个性化学习建议",
                        "created_at": _iso(session_time),
                        "messages": [
                            {"role": "user", "content": request, "created_at": _iso(session_time)},
                            {"role": "assistant", "content": response, "created_at": _iso(session_time + timedelta(minutes=1))},
                        ],
                    }
                )
                for event_index, (agent_name, output) in enumerate(
                    (
                        ("planner_agent", "确定画像读取、诊断、知识检索和审核步骤"),
                        ("diagnosis_agent", "根据近期正确率和任务完成记录定位薄弱知识点"),
                        ("knowledge_base_agent", "从已配置的教材与知识库检索相关证据"),
                        ("audit_agent", "检查来源、安全边界和输出完整性"),
                    )
                ):
                    agent_events.append(
                        {
                            "learner_id": learner_id,
                            "session_id": session_id,
                            "agent_name": agent_name,
                            "event_type": "completed",
                            "input_summary": request,
                            "output_summary": output,
                            "payload": {
                                "synthetic": True,
                                "trace_order": event_index + 1,
                                "source": "synthetic_usage_v1",
                            },
                            "created_at": _iso(session_time + timedelta(seconds=event_index * 10)),
                        }
                    )

            stats: dict[str, dict[str, int]] = defaultdict(lambda: {"attempts": 0, "correct": 0})
            for attempt in user_attempts:
                for kp_id in attempt["kp_ids"]:
                    stats[kp_id]["attempts"] += 1
                    stats[kp_id]["correct"] += int(attempt["is_correct"])
            for kp_id, values in sorted(stats.items()):
                attempt_total = values["attempts"]
                correct_total = values["correct"]
                mastery = round((correct_total + 1) / (attempt_total + 2), 4)
                mastery_records.append(
                    {
                        "learner_id": learner_id,
                        "kp_id": kp_id,
                        "kp_name": kp_names.get(kp_id, ""),
                        "mastery": mastery,
                        "confidence": round(min(0.95, 0.5 + attempt_total * 0.04), 4),
                        "wrong_count": attempt_total - correct_total,
                        "review_count": max(1, attempt_total // 3),
                        "mastery_status": "mastered" if mastery >= 0.75 else "reviewing",
                        "last_review_at": _iso(ANCHOR - timedelta(days=1)),
                        "next_review_at": _iso(ANCHOR + timedelta(days=2 if mastery < 0.75 else 7)),
                    }
                )

            user_accuracy = sum(int(item["is_correct"]) for item in user_attempts) / max(1, len(user_attempts))
            interventions.append(
                {
                    "learner_id": learner_id,
                    "t_stage": "T1" if user_accuracy >= 0.7 else "T2",
                    "action": "保持当前难度并增加综合题" if user_accuracy >= 0.7 else "降低单次任务量并优先复习错题",
                    "reason": f"近27天合成练习正确率为{user_accuracy:.1%}",
                    "feedback": "等待后续学习窗口评估",
                    "effect_status": "pending",
                    "created_at": _iso(ANCHOR - timedelta(hours=2)),
                }
            )

    dataset = {
        "schema_version": "synthetic_user_usage_v1",
        "dataset_id": "SYNTHETIC_USAGE_20260905_V1",
        "data_classification": "synthetic",
        "contains_real_personal_data": False,
        "purpose": "竞赛材料展示与系统离线功能验证",
        "generation": {
            "generator": "generate_synthetic_user_usage.py",
            "random_seed": RANDOM_SEED,
            "anchor_time": _iso(ANCHOR),
            "observation_window_days": 27,
            "rules": "差异化活跃天数、任务完成概率和随学习推进提高的作答正确率；不拟合或复制任何真实个人轨迹",
        },
        "reference_catalog": {
            "source": "shizhen_mvp_seed.json",
            "knowledge_points": reference["knowledge_points"],
            "questions": reference["question_bank"],
        },
        "users": users,
        "sessions": sessions,
        "learning_activities": activities,
        "question_attempts": attempts,
        "mistakes": mistakes,
        "learning_plans": plans,
        "interventions": interventions,
        "mastery_records": mastery_records,
        "agent_events": agent_events,
        "learning_tasks": learning_tasks,
        "focus_sessions": focus_sessions,
        "daily_tasks": daily_tasks,
        "memories": memories,
    }
    return dataset


def main() -> None:
    parser = argparse.ArgumentParser(description="生成可复现的全合成用户使用记录")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    dataset = build_dataset()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(dataset, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"generated={args.output}")
    for key in (
        "users",
        "sessions",
        "learning_activities",
        "question_attempts",
        "mistakes",
        "mastery_records",
        "agent_events",
        "daily_tasks",
    ):
        print(f"{key}={len(dataset[key])}")


if __name__ == "__main__":
    main()
