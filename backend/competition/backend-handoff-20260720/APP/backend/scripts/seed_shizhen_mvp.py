from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from APP.backend.database import (
    AgentEvent,
    EvidencePackItem,
    EvidencePackRecord,
    KnowledgePoint,
    LearnerKnowledgeMastery,
    LearningActivityRecord,
    LearningInterventionRecord,
    LearningPlanRecord,
    MistakeRecord,
    QuestionAttempt,
    QuestionBankItem,
    SessionLocal,
    TeachingResource,
    UserModel,
    UserProfile,
)

SAMPLE_FILE = Path(__file__).resolve().parents[1] / "sample_data" / "shizhen_mvp_seed.json"
SEED_SOURCE = "shizhen_mvp_seed"
DEMO_PASSWORD_HASH = "!demo-login-disabled"


def _load_seed(seed_path: str | Path | None = None) -> dict[str, Any]:
    path = Path(seed_path) if seed_path is not None else SAMPLE_FILE
    return json.loads(path.read_text(encoding="utf-8"))


def _dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def _learner_map(seed: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {item["learner_id"]: item for item in seed.get("learner_profiles", [])}


def _upsert_user(db, learner: dict[str, Any]) -> UserModel:
    user = db.query(UserModel).filter(UserModel.username == learner["username"]).first()
    if user is None:
        user = UserModel(
            username=learner["username"],
            email=learner["email"],
            hashed_password=DEMO_PASSWORD_HASH,
            role="user",
        )
        db.add(user)
        db.flush()
        return user
    if user.hashed_password != DEMO_PASSWORD_HASH:
        raise ValueError(f"Refusing to overwrite existing non-demo user: {learner['username']}")
    return user


def _upsert_profile(db, user_id: int, learner: dict[str, Any]) -> None:
    profile = db.query(UserProfile).filter(UserProfile.user_id == user_id).first()
    if profile is None:
        profile = UserProfile(user_id=user_id)
        db.add(profile)
    profile_data = learner.get("profile", {})
    profile.display_name = learner.get("display_name", "")
    profile.constitution = profile_data.get("constitution", learner.get("group", ""))
    profile.health_goals = profile_data.get("health_goals", "")
    profile.diet_restrictions = profile_data.get("diet_restrictions", "")
    profile.exercise_preferences = profile_data.get("exercise_preferences", "")
    profile.medical_history = profile_data.get("medical_history", "")
    profile.custom_needs = profile_data.get("custom_needs", "")


def _replace_user_demo_rows(db, user_id: int) -> None:
    db.query(LearnerKnowledgeMastery).filter(LearnerKnowledgeMastery.user_id == user_id).delete()
    db.query(QuestionAttempt).filter(QuestionAttempt.user_id == user_id).delete()
    db.query(MistakeRecord).filter(MistakeRecord.user_id == user_id).delete()
    db.query(LearningPlanRecord).filter(LearningPlanRecord.user_id == user_id).delete()
    db.query(LearningInterventionRecord).filter(LearningInterventionRecord.user_id == user_id).delete()
    db.query(LearningActivityRecord).filter(LearningActivityRecord.user_id == user_id).delete()
    db.query(AgentEvent).filter(AgentEvent.user_id == user_id).delete()
    db.query(EvidencePackRecord).filter(EvidencePackRecord.user_id == user_id).delete()


def _replace_global_demo_rows(db, seed: dict[str, Any]) -> None:
    db.query(EvidencePackItem).filter(EvidencePackItem.source_id.like(f"{SEED_SOURCE}:%")).delete()
    db.query(TeachingResource).filter(TeachingResource.source == SEED_SOURCE).delete()
    db.query(QuestionBankItem).filter(QuestionBankItem.source == SEED_SOURCE).delete()
    db.query(KnowledgePoint).filter(KnowledgePoint.source == SEED_SOURCE).delete()


def _create_knowledge_points(db, seed: dict[str, Any]) -> int:
    count = 0
    for item in seed.get("knowledge_points", []):
        row = db.query(KnowledgePoint).filter(KnowledgePoint.kp_id == item["kp_id"]).first()
        if row is None:
            row = KnowledgePoint(kp_id=item["kp_id"])
            db.add(row)
        elif row.source != SEED_SOURCE:
            continue
        row.name = item["name"]
        row.aliases_json = _dump(item.get("aliases", []))
        row.description = item.get("description", "")
        row.source = SEED_SOURCE
        row.status = "active"
        count += 1
    return count


def _create_question_bank(db, seed: dict[str, Any]) -> int:
    count = 0
    for item in seed.get("question_bank", []):
        row = db.query(QuestionBankItem).filter(QuestionBankItem.question_id == item["question_id"]).first()
        if row is None:
            row = QuestionBankItem(question_id=item["question_id"])
            db.add(row)
        elif row.source != SEED_SOURCE:
            continue
        row.stem = item["stem"]
        row.answer = item.get("answer", "")
        row.analysis = item.get("analysis", "")
        row.kp_ids_json = _dump(item.get("kp_ids", []))
        row.question_type = item.get("question_type", "single_choice")
        row.difficulty = float(item.get("difficulty", 2.0))
        row.quality_score = float(item.get("quality_score", 0.8))
        row.source = SEED_SOURCE
        row.status = "active"
        count += 1
    return count


def _create_teaching_resources(db, seed: dict[str, Any]) -> int:
    resources = [
        {
            "resource_id": "RES_SJZ_CARD_001",
            "title": "脾胃气虚证与四君子汤知识卡",
            "resource_type": "knowledge_card",
            "summary": "从症状、病机、治法到方剂建立方证对应主线。",
            "kp_ids": ["KP_ZD_021", "KP_FJ_001", "KP_CASE_001"],
        },
        {
            "resource_id": "RES_SJZ_LECTURE_001",
            "title": "四君子汤方义讲义",
            "resource_type": "lecture",
            "summary": "覆盖组成、功用、君臣佐使和考试化表达。",
            "kp_ids": ["KP_FJ_002", "KP_FJ_003", "KP_FJ_004"],
        },
        {
            "resource_id": "RES_SJZ_SAFE_001",
            "title": "教学与医疗边界提示卡",
            "resource_type": "safety_card",
            "summary": "说明学习内容不能替代专业诊疗或自行用药。",
            "kp_ids": ["KP_SAFE_001", "KP_TRACE_001"],
        },
    ]
    count = 0
    for item in resources:
        row = db.query(TeachingResource).filter(TeachingResource.resource_id == item["resource_id"]).first()
        if row is None:
            row = TeachingResource(resource_id=item["resource_id"])
            db.add(row)
        elif row.source != SEED_SOURCE:
            continue
        row.title = item["title"]
        row.resource_type = item["resource_type"]
        row.summary = item["summary"]
        row.kp_ids_json = _dump(item["kp_ids"])
        row.source = SEED_SOURCE
        row.quality_score = 0.9
        row.status = "active"
        count += 1
    return count


def _create_mistakes(db, seed: dict[str, Any], users_by_learner_id: dict[str, UserModel], base_time: datetime) -> int:
    for index, item in enumerate(seed.get("mistakes", [])):
        user = users_by_learner_id[item["learner_id"]]
        db.add(MistakeRecord(
            user_id=user.id,
            question_id=item["question_id"],
            kp_ids_json=_dump(item.get("kp_ids", [])),
            error_type=item.get("error_type", ""),
            summary=item.get("summary", ""),
            status="active",
            created_at=base_time - timedelta(days=index + 1),
            updated_at=base_time - timedelta(hours=index + 1),
        ))
    return len(seed.get("mistakes", []))


def _create_question_attempts(db, seed: dict[str, Any], users_by_learner_id: dict[str, UserModel], base_time: datetime) -> int:
    attempts = seed.get("mistakes", [])
    for index, item in enumerate(attempts):
        user = users_by_learner_id[item["learner_id"]]
        payload = {
            "question_id": item["question_id"],
            "kp_ids": item.get("kp_ids", []),
            "is_correct": False,
            "score": 45,
            "feedback": item.get("summary", ""),
            "error_type": item.get("error_type", ""),
            "source": SEED_SOURCE,
        }
        created_at = base_time - timedelta(days=index + 1, minutes=index)
        db.add(QuestionAttempt(
            user_id=user.id,
            question_id=item["question_id"],
            answer=item.get("learner_answer", ""),
            is_correct=False,
            score=45.0,
            kp_ids_json=_dump(item.get("kp_ids", [])),
            feedback=item.get("summary", ""),
            created_at=created_at,
        ))
        db.add(LearningActivityRecord(
            user_id=user.id,
            activity_type="question_attempt",
            resource_id=item["question_id"],
            resource_type="question",
            duration_minutes=12,
            completion_status="needs_review",
            score=45.0,
            payload_json=_dump(payload),
            created_at=created_at,
        ))
    return len(attempts)


def _create_plans(db, seed: dict[str, Any], users_by_learner_id: dict[str, UserModel], base_time: datetime) -> int:
    for index, item in enumerate(seed.get("plans", [])):
        user = users_by_learner_id[item["learner_id"]]
        db.add(LearningPlanRecord(
            user_id=user.id,
            plan_type="diagnosis_driven",
            title=item.get("title", ""),
            summary=item.get("summary", ""),
            status=item.get("status", "active"),
            payload_json=_dump({
                "plan_id": item["plan_id"],
                "demo_plan_type": item.get("plan_type", ""),
                "target_kp_ids": item.get("target_kp_ids", []),
                "daily_tasks": item.get("daily_tasks", []),
                "safety_label_ids": item.get("safety_label_ids", []),
                "source": SEED_SOURCE,
                "plan_summary": {
                    "plan_id": item["plan_id"],
                    "title": item.get("title", ""),
                    "summary": item.get("summary", ""),
                    "goal": item.get("goal", item.get("title", "")),
                    "status": item.get("status", "active"),
                    "target_kp_ids": item.get("target_kp_ids", []),
                },
                "weekly_plan": [
                    {
                        "week": 1,
                        "focus": item.get("summary", item.get("title", "")),
                        "tasks": item.get("daily_tasks", []),
                        "target_kp_ids": item.get("target_kp_ids", []),
                    }
                ],
                "constraints": {
                    "daily_available_minutes": item.get("daily_available_minutes", 30),
                    "safety_label_ids": item.get("safety_label_ids", []),
                },
            }),
            created_at=base_time - timedelta(days=index),
            updated_at=base_time - timedelta(hours=index),
        ))
    return len(seed.get("plans", []))


def _create_diagnoses(db, seed: dict[str, Any], users_by_learner_id: dict[str, UserModel], base_time: datetime) -> int:
    for index, item in enumerate(seed.get("diagnoses", [])):
        user = users_by_learner_id[item["learner_id"]]
        payload = {
            "diagnosis_id": item["diagnosis_id"],
            "t_stage": item.get("t_stage", ""),
            "weak_kp_ids": item.get("weak_kp_ids", []),
            "attribution": item.get("attribution", ""),
            "intervention": item.get("intervention", ""),
            "safety_label_ids": item.get("safety_label_ids", []),
            "source": SEED_SOURCE,
        }
        db.add(LearningActivityRecord(
            user_id=user.id,
            activity_type="diagnosis",
            resource_id=item["diagnosis_id"],
            resource_type="learning_diagnosis",
            duration_minutes=0,
            completion_status="completed",
            score=None,
            payload_json=_dump(payload),
            created_at=base_time - timedelta(hours=index + 1),
        ))
        db.add(LearningInterventionRecord(
            user_id=user.id,
            t_stage=item.get("t_stage", ""),
            action=item.get("intervention", ""),
            reason=item.get("summary", ""),
            cooldown_hours=24,
            feedback=item.get("attribution", ""),
            effect_status="pending",
            created_at=base_time - timedelta(hours=index + 1),
            updated_at=base_time - timedelta(hours=index + 1),
        ))
    return len(seed.get("diagnoses", []))


def _create_evaluation_tasks(db, seed: dict[str, Any], users_by_learner_id: dict[str, UserModel], base_time: datetime) -> int:
    for index, item in enumerate(seed.get("evaluation_tasks", [])):
        user = users_by_learner_id[item["learner_id"]]
        pack_id = f"PACK_{item['task_id']}"
        db.add(EvidencePackRecord(
            pack_id=pack_id,
            user_id=user.id,
            query=item.get("text", ""),
            resolved_kp_ids_json=_dump(item.get("required_kp_ids", [])),
            candidate_kp_ids_json="[]",
            payload_json=_dump({
                "task_id": item["task_id"],
                "task_type": item.get("task_type", ""),
                "question_ids": item.get("question_ids", []),
                "expected_output": item.get("expected_output", ""),
                "safety_label_ids": item.get("safety_label_ids", []),
                "source": SEED_SOURCE,
            }),
            created_at=base_time - timedelta(minutes=index * 5),
        ))
        for source_index, kp_id in enumerate(item.get("required_kp_ids", [])):
            db.add(EvidencePackItem(
                pack_id=pack_id,
                source_scope="demo_seed",
                source_id=f"{SEED_SOURCE}:{item['task_id']}:{kp_id}",
                summary=item.get("expected_output", ""),
                kp_ids_json=_dump([kp_id]),
                confidence=0.9,
                payload_json=_dump({"safety_label_ids": item.get("safety_label_ids", [])}),
                created_at=base_time - timedelta(minutes=index * 5 + source_index),
            ))
        db.add(AgentEvent(
            user_id=user.id,
            session_id=None,
            agent_name="evaluation_agent",
            event_type="evaluation_task",
            input_summary=item.get("text", ""),
            output_summary=item.get("expected_output", ""),
            payload=_dump({
                "task_id": item["task_id"],
                "task_type": item.get("task_type", ""),
                "required_kp_ids": item.get("required_kp_ids", []),
                "question_ids": item.get("question_ids", []),
                "safety_label_ids": item.get("safety_label_ids", []),
                "source": SEED_SOURCE,
            }),
            created_at=base_time - timedelta(minutes=index * 5),
        ))
    return len(seed.get("evaluation_tasks", []))


def _create_mastery(db, seed: dict[str, Any], users_by_learner_id: dict[str, UserModel], base_time: datetime) -> int:
    count = 0
    learner_weak_kps: dict[str, set[str]] = {}
    for mistake in seed.get("mistakes", []):
        learner_weak_kps.setdefault(mistake["learner_id"], set()).update(mistake.get("kp_ids", []))
    for plan in seed.get("plans", []):
        learner_weak_kps.setdefault(plan["learner_id"], set()).update(plan.get("target_kp_ids", [])[:2])

    for learner_id, kp_ids in learner_weak_kps.items():
        user = users_by_learner_id[learner_id]
        for index, kp_id in enumerate(sorted(kp_ids)):
            db.add(LearnerKnowledgeMastery(
                user_id=user.id,
                kp_id=kp_id,
                mastery=0.35 if index % 2 == 0 else 0.58,
                confidence=0.82,
                wrong_count=1 if index % 2 == 0 else 2,
                review_count=1,
                last_review_at=base_time - timedelta(days=1),
                next_review_at=base_time + timedelta(days=2),
                mastery_status="reviewing",
                created_at=base_time - timedelta(days=3),
                updated_at=base_time - timedelta(hours=index + 1),
            ))
            count += 1
    return count


def seed_shizhen_mvp_data(seed_path: str | Path | None = None, session_factory=SessionLocal) -> dict[str, int]:
    seed = _load_seed(seed_path)
    db = session_factory()
    try:
        base_time = datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0)
        users_by_learner_id: dict[str, UserModel] = {}

        learners = _learner_map(seed)
        for learner_id, learner in learners.items():
            user = _upsert_user(db, learner)
            users_by_learner_id[learner_id] = user
            db.flush()
            _upsert_profile(db, user.id, learner)
            _replace_user_demo_rows(db, user.id)

        _replace_global_demo_rows(db, seed)
        knowledge_points = _create_knowledge_points(db, seed)
        questions = _create_question_bank(db, seed)
        resources = _create_teaching_resources(db, seed)
        mistakes = _create_mistakes(db, seed, users_by_learner_id, base_time)
        attempts = _create_question_attempts(db, seed, users_by_learner_id, base_time)
        plans = _create_plans(db, seed, users_by_learner_id, base_time)
        diagnoses = _create_diagnoses(db, seed, users_by_learner_id, base_time)
        evaluation_tasks = _create_evaluation_tasks(db, seed, users_by_learner_id, base_time)
        mastery_records = _create_mastery(db, seed, users_by_learner_id, base_time)

        db.commit()
        return {
            "users": len(learners),
            "knowledge_points": knowledge_points,
            "questions": questions,
            "mistakes": mistakes,
            "question_attempts": attempts,
            "plans": plans,
            "diagnoses": diagnoses,
            "evaluation_tasks": evaluation_tasks,
            "teaching_resources": resources,
            "mastery_records": mastery_records,
        }
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    result = seed_shizhen_mvp_data()
    print("Seeded Shizhen MVP demonstration data:")
    for key, value in result.items():
        print(f"- {key}: {value}")
