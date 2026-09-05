from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any


DEFAULT_INPUT = (
    Path(__file__).resolve().parents[1]
    / "sample_data"
    / "synthetic_user_usage_v1.json"
)
SYNTHETIC_PASSWORD_HASH = "!synthetic-login-disabled-v1"
EXPECTED_SCHEMA = "synthetic_user_usage_v1"


class SyntheticUsageImportError(ValueError):
    pass


def _load(path: str | Path) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise SyntheticUsageImportError("dataset root must be an object")
    return payload


def _parse_time(value: Any, field: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise SyntheticUsageImportError(f"{field} must be an ISO-8601 timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise SyntheticUsageImportError(f"{field} is not a valid ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise SyntheticUsageImportError(f"{field} must include a timezone")
    return parsed.astimezone(timezone.utc).replace(tzinfo=None)


def _unique(rows: list[dict[str, Any]], key: str, section: str) -> set[str]:
    values = [str(row.get(key) or "") for row in rows]
    if any(not value for value in values):
        raise SyntheticUsageImportError(f"{section}.{key} must not be empty")
    duplicates = sorted(value for value, count in Counter(values).items() if count > 1)
    if duplicates:
        raise SyntheticUsageImportError(f"duplicate {section}.{key}: {duplicates[:3]}")
    return set(values)


def validate_dataset(dataset: dict[str, Any]) -> dict[str, int]:
    if dataset.get("schema_version") != EXPECTED_SCHEMA:
        raise SyntheticUsageImportError(f"schema_version must be {EXPECTED_SCHEMA}")
    if dataset.get("data_classification") != "synthetic":
        raise SyntheticUsageImportError("data_classification must be synthetic")
    if dataset.get("contains_real_personal_data") is not False:
        raise SyntheticUsageImportError("contains_real_personal_data must be false")

    required_sections = (
        "users",
        "sessions",
        "learning_activities",
        "question_attempts",
        "mistakes",
        "learning_plans",
        "interventions",
        "mastery_records",
        "agent_events",
        "learning_tasks",
        "focus_sessions",
        "daily_tasks",
        "memories",
    )
    for section in required_sections:
        if not isinstance(dataset.get(section), list):
            raise SyntheticUsageImportError(f"{section} must be a list")

    users = dataset["users"]
    if len(users) < 2:
        raise SyntheticUsageImportError("at least two differentiated learners are required")
    learner_ids = _unique(users, "learner_id", "users")
    _unique(users, "username", "users")
    _unique(users, "email", "users")
    groups = {str(row.get("learner_group") or "") for row in users}
    if len(groups - {""}) < 2:
        raise SyntheticUsageImportError("at least two learner groups are required")
    for row in users:
        if not str(row["username"]).startswith("sim_"):
            raise SyntheticUsageImportError("synthetic usernames must start with sim_")
        if not str(row["email"]).endswith("@example.test"):
            raise SyntheticUsageImportError("synthetic email addresses must use example.test")
        _parse_time(row.get("created_at"), "users.created_at")

    reference = dataset.get("reference_catalog") or {}
    question_ids = {
        str(row.get("question_id") or "") for row in reference.get("questions", [])
    }
    kp_ids = {
        str(row.get("kp_id") or "") for row in reference.get("knowledge_points", [])
    }
    if not question_ids or not kp_ids:
        raise SyntheticUsageImportError("reference_catalog must contain questions and knowledge points")

    timestamp_fields = {
        "sessions": "created_at",
        "learning_activities": "occurred_at",
        "question_attempts": "occurred_at",
        "mistakes": "created_at",
        "learning_plans": "created_at",
        "interventions": "created_at",
        "agent_events": "created_at",
        "learning_tasks": "created_at",
        "focus_sessions": "started_at",
        "daily_tasks": "created_at",
        "memories": "created_at",
    }
    for section in required_sections:
        for index, row in enumerate(dataset[section]):
            learner_id = str(row.get("learner_id") or "")
            if learner_id not in learner_ids:
                raise SyntheticUsageImportError(
                    f"{section}[{index}] references unknown learner_id {learner_id}"
                )
            field = timestamp_fields.get(section)
            if field:
                _parse_time(row.get(field), f"{section}[{index}].{field}")

    session_ids = _unique(dataset["sessions"], "session_id", "sessions")
    attempt_ids = _unique(dataset["question_attempts"], "attempt_id", "question_attempts")
    task_ids = _unique(dataset["learning_tasks"], "task_id", "learning_tasks")
    _unique(dataset["focus_sessions"], "focus_session_id", "focus_sessions")
    _unique(dataset["daily_tasks"], "host_task_id", "daily_tasks")

    for row in dataset["question_attempts"]:
        if row.get("question_id") not in question_ids:
            raise SyntheticUsageImportError(
                f"question attempt references unknown question {row.get('question_id')}"
            )
        if not set(row.get("kp_ids") or []).issubset(kp_ids):
            raise SyntheticUsageImportError("question attempt references unknown knowledge point")
        score = float(row.get("score"))
        if score < 0 or score > 100:
            raise SyntheticUsageImportError("question attempt score must be between 0 and 100")

    for row in dataset["mistakes"]:
        if row.get("question_id") not in question_ids:
            raise SyntheticUsageImportError("mistake references unknown question")
    for row in dataset["mastery_records"]:
        if row.get("kp_id") not in kp_ids:
            raise SyntheticUsageImportError("mastery record references unknown knowledge point")
        if not 0 <= float(row.get("mastery")) <= 1:
            raise SyntheticUsageImportError("mastery must be between 0 and 1")
    for row in dataset["agent_events"]:
        if row.get("session_id") not in session_ids:
            raise SyntheticUsageImportError("agent event references unknown session")
    for row in dataset["focus_sessions"]:
        if row.get("task_id") not in task_ids:
            raise SyntheticUsageImportError("focus session references unknown learning task")
    task_item_ids: list[str] = []
    for task in dataset["daily_tasks"]:
        items = task.get("items")
        if not isinstance(items, list) or not items:
            raise SyntheticUsageImportError("every daily task must contain items")
        for item in items:
            task_item_ids.append(str(item.get("task_item_id") or ""))
            if item.get("kp_id") not in kp_ids:
                raise SyntheticUsageImportError("daily task item references unknown knowledge point")
            if item.get("completed_at") is not None:
                _parse_time(item["completed_at"], "daily_tasks.items.completed_at")
    if any(not item_id for item_id in task_item_ids) or len(task_item_ids) != len(set(task_item_ids)):
        raise SyntheticUsageImportError("daily task item ids must be non-empty and unique")

    mistakes_by_question = Counter(
        (row["learner_id"], row["question_id"]) for row in dataset["mistakes"]
    )
    incorrect_by_question = Counter(
        (row["learner_id"], row["question_id"])
        for row in dataset["question_attempts"]
        if not row["is_correct"]
    )
    if mistakes_by_question != incorrect_by_question:
        raise SyntheticUsageImportError("every incorrect attempt must have exactly one mistake record")

    summary = {section: len(dataset[section]) for section in required_sections}
    summary["daily_task_items"] = len(task_item_ids)
    summary["learner_groups"] = len(groups - {""})
    summary["attempt_ids"] = len(attempt_ids)
    return summary


def _dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def import_dataset(dataset: dict[str, Any], session_factory=None) -> dict[str, int]:
    summary = validate_dataset(dataset)
    from APP.backend import database
    from APP.backend.system_data_service import rebuild_system_data

    factory = session_factory or database.SessionLocal
    db = factory()
    try:
        users_by_learner: dict[str, Any] = {}
        for learner in dataset["users"]:
            user = (
                db.query(database.UserModel)
                .filter(
                    (database.UserModel.username == learner["username"])
                    | (database.UserModel.email == learner["email"])
                )
                .one_or_none()
            )
            if user is None:
                user = database.UserModel(
                    username=learner["username"],
                    email=learner["email"],
                    hashed_password=SYNTHETIC_PASSWORD_HASH,
                    role="user",
                    created_at=_parse_time(learner["created_at"], "users.created_at"),
                )
                db.add(user)
                db.flush()
            elif (
                user.username != learner["username"]
                or user.email != learner["email"]
                or user.hashed_password != SYNTHETIC_PASSWORD_HASH
            ):
                raise SyntheticUsageImportError(
                    f"refusing to overwrite non-synthetic or mismatched account: {learner['username']}"
                )
            users_by_learner[learner["learner_id"]] = user

        user_ids = [user.id for user in users_by_learner.values()]
        session_ids = [row[0] for row in db.query(database.DbSession.id).filter(database.DbSession.user_id.in_(user_ids)).all()]
        if session_ids:
            db.query(database.DbMessage).filter(database.DbMessage.session_id.in_(session_ids)).delete(synchronize_session=False)
        delete_models = (
            database.SystemData,
            database.PersonalizationMemory,
            database.DailyTaskItemRecord,
            database.DailyTaskInstanceRecord,
            database.LearningFocusSession,
            database.LearningTask,
            database.LearnerKnowledgeMastery,
            database.QuestionAttempt,
            database.MistakeRecord,
            database.LearningPlanRecord,
            database.LearningInterventionRecord,
            database.LearningActivityRecord,
            database.AgentEvent,
            database.DbSession,
            database.UserProfile,
        )
        for model in delete_models:
            owner_column = model.user_id
            db.query(model).filter(owner_column.in_(user_ids)).delete(synchronize_session=False)
        db.flush()

        for learner in dataset["users"]:
            user = users_by_learner[learner["learner_id"]]
            profile = learner["profile"]
            db.add(
                database.UserProfile(
                    user_id=user.id,
                    display_name=learner["display_name"],
                    constitution=profile["constitution"],
                    health_goals=profile["health_goals"],
                    diet_restrictions=profile["diet_restrictions"],
                    exercise_preferences=profile["exercise_preferences"],
                    medical_history=profile["medical_history"],
                    custom_needs=profile["custom_needs"],
                    survey_json=_dump(learner["onboarding_answers"]),
                    locked_fields_json="[]",
                    lock_reason_json="{}",
                    created_at=_parse_time(learner["created_at"], "users.created_at"),
                )
            )

        for row in dataset["sessions"]:
            user = users_by_learner[row["learner_id"]]
            created_at = _parse_time(row["created_at"], "sessions.created_at")
            session = database.DbSession(
                id=row["session_id"],
                user_id=user.id,
                title=row["title"],
                title_auto_enabled=False,
                created_at=created_at,
            )
            db.add(session)
            db.flush()
            parent_id = None
            for message in row["messages"]:
                message_time = _parse_time(message["created_at"], "sessions.messages.created_at")
                record = database.DbMessage(
                    session_id=session.id,
                    parent_id=parent_id,
                    role=message["role"],
                    content=message["content"],
                    files="[]",
                    timestamp=message_time.strftime("%H:%M"),
                    created_at=message_time,
                )
                db.add(record)
                db.flush()
                parent_id = record.id
            session.active_leaf_message_id = parent_id

        for row in dataset["learning_activities"]:
            db.add(
                database.LearningActivityRecord(
                    user_id=users_by_learner[row["learner_id"]].id,
                    activity_type=row["activity_type"],
                    resource_id=row.get("resource_id", ""),
                    resource_type=row.get("resource_type", ""),
                    duration_minutes=int(row.get("duration_minutes", 0)),
                    completion_status=row.get("completion_status", "unknown"),
                    score=row.get("score"),
                    payload_json=_dump(row.get("payload", {})),
                    created_at=_parse_time(row["occurred_at"], "learning_activities.occurred_at"),
                )
            )
        for row in dataset["question_attempts"]:
            db.add(
                database.QuestionAttempt(
                    user_id=users_by_learner[row["learner_id"]].id,
                    question_id=row["question_id"],
                    answer=row["answer"],
                    is_correct=bool(row["is_correct"]),
                    score=float(row["score"]),
                    kp_ids_json=_dump(row["kp_ids"]),
                    feedback=row["feedback"],
                    created_at=_parse_time(row["occurred_at"], "question_attempts.occurred_at"),
                )
            )
        for row in dataset["mistakes"]:
            db.add(
                database.MistakeRecord(
                    user_id=users_by_learner[row["learner_id"]].id,
                    question_id=row["question_id"],
                    kp_ids_json=_dump(row["kp_ids"]),
                    error_type=row["error_type"],
                    summary=row["summary"],
                    status=row["status"],
                    created_at=_parse_time(row["created_at"], "mistakes.created_at"),
                    updated_at=_parse_time(row["created_at"], "mistakes.created_at"),
                )
            )
        for row in dataset["learning_plans"]:
            payload = {
                "plan_id": row["plan_id"],
                "target_kp_ids": row["target_kp_ids"],
                "daily_available_minutes": row["daily_available_minutes"],
                "source": EXPECTED_SCHEMA,
            }
            db.add(
                database.LearningPlanRecord(
                    user_id=users_by_learner[row["learner_id"]].id,
                    plan_type="diagnosis_driven",
                    title=row["title"],
                    summary=row["summary"],
                    status=row["status"],
                    payload_json=_dump(payload),
                    created_at=_parse_time(row["created_at"], "learning_plans.created_at"),
                    updated_at=_parse_time(row["created_at"], "learning_plans.created_at"),
                )
            )
        for row in dataset["interventions"]:
            created_at = _parse_time(row["created_at"], "interventions.created_at")
            db.add(
                database.LearningInterventionRecord(
                    user_id=users_by_learner[row["learner_id"]].id,
                    t_stage=row["t_stage"],
                    action=row["action"],
                    reason=row["reason"],
                    cooldown_hours=24,
                    feedback=row["feedback"],
                    effect_status=row["effect_status"],
                    created_at=created_at,
                    updated_at=created_at,
                )
            )
        for row in dataset["mastery_records"]:
            db.add(
                database.LearnerKnowledgeMastery(
                    user_id=users_by_learner[row["learner_id"]].id,
                    kp_id=row["kp_id"],
                    mastery=float(row["mastery"]),
                    confidence=float(row["confidence"]),
                    wrong_count=int(row["wrong_count"]),
                    review_count=int(row["review_count"]),
                    last_review_at=_parse_time(row["last_review_at"], "mastery_records.last_review_at"),
                    next_review_at=_parse_time(row["next_review_at"], "mastery_records.next_review_at"),
                    mastery_status=row["mastery_status"],
                )
            )
        for row in dataset["learning_tasks"]:
            db.add(
                database.LearningTask(
                    task_id=row["task_id"],
                    user_id=users_by_learner[row["learner_id"]].id,
                    task_type=row["task_type"],
                    kp_ids_json=_dump(row["kp_ids"]),
                    question_ids_json="[]",
                    resource_ids_json=_dump(row["resource_ids"]),
                    task_content=row["task_content"],
                    estimated_minutes=int(row["estimated_minutes"]),
                    expected_output="完成学习任务并留下可核验记录",
                    completion_criteria="任务状态为 completed",
                    status=row["status"],
                    created_at=_parse_time(row["created_at"], "learning_tasks.created_at"),
                    due_at=_parse_time(row["due_at"], "learning_tasks.due_at"),
                    completed_at=_parse_time(row["completed_at"], "learning_tasks.completed_at"),
                )
            )
        db.flush()
        for row in dataset["focus_sessions"]:
            started_at = _parse_time(row["started_at"], "focus_sessions.started_at")
            ended_at = _parse_time(row["ended_at"], "focus_sessions.ended_at")
            db.add(
                database.LearningFocusSession(
                    focus_session_id=row["focus_session_id"],
                    user_id=users_by_learner[row["learner_id"]].id,
                    task_id=row["task_id"],
                    resource_type=row["resource_type"],
                    resource_id=row["resource_id"],
                    status="completed",
                    is_visible=True,
                    last_interaction_at=ended_at,
                    active_seconds=int(row["active_seconds"]),
                    started_at=started_at,
                    ended_at=ended_at,
                    updated_at=ended_at,
                )
            )
        for row in dataset["daily_tasks"]:
            user_id = users_by_learner[row["learner_id"]].id
            created_at = _parse_time(row["created_at"], "daily_tasks.created_at")
            db.add(
                database.DailyTaskInstanceRecord(
                    host_task_id=row["host_task_id"],
                    host_task_version=1,
                    user_id=user_id,
                    status=row["status"],
                    created_at=created_at,
                    updated_at=created_at,
                )
            )
            for item in row["items"]:
                completed_at = (
                    _parse_time(item["completed_at"], "daily_tasks.items.completed_at")
                    if item.get("completed_at")
                    else None
                )
                db.add(
                    database.DailyTaskItemRecord(
                        task_item_id=item["task_item_id"],
                        host_task_id=row["host_task_id"],
                        host_task_version=1,
                        user_id=user_id,
                        kp_id=item["kp_id"],
                        item_kind=item["item_kind"],
                        ordinal=int(item["ordinal"]),
                        required_question_count=int(item["required_question_count"]),
                        resource_ref={"source": EXPECTED_SCHEMA},
                        completion_policy={"policy": "synthetic_record"},
                        status=item["status"],
                        completed_at=completed_at,
                        created_at=created_at,
                        updated_at=completed_at or created_at,
                    )
                )
        for row in dataset["memories"]:
            created_at = _parse_time(row["created_at"], "memories.created_at")
            db.add(
                database.PersonalizationMemory(
                    user_id=users_by_learner[row["learner_id"]].id,
                    category=row["category"],
                    importance="normal",
                    title=row["title"],
                    content=row["content"],
                    source=EXPECTED_SCHEMA,
                    is_active=True,
                    confidence=float(row["confidence"]),
                    created_at=created_at,
                    updated_at=created_at,
                )
            )
        for row in dataset["agent_events"]:
            db.add(
                database.AgentEvent(
                    user_id=users_by_learner[row["learner_id"]].id,
                    session_id=row["session_id"],
                    agent_name=row["agent_name"],
                    event_type=row["event_type"],
                    input_summary=row["input_summary"],
                    output_summary=row["output_summary"],
                    payload=_dump(row["payload"]),
                    created_at=_parse_time(row["created_at"], "agent_events.created_at"),
                )
            )
        db.flush()
        calculated_at = _parse_time(
            dataset["generation"]["anchor_time"], "generation.anchor_time"
        )
        for user in users_by_learner.values():
            rebuild_system_data(db, user_id=user.id, now=calculated_at)
        db.commit()
        return summary
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="校验或导入全合成用户使用记录；默认只校验，不写数据库"
    )
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="实际写入当前环境配置指向的数据库；不加此参数时只校验",
    )
    args = parser.parse_args()
    dataset = _load(args.input)
    summary = import_dataset(dataset) if args.apply else validate_dataset(dataset)
    mode = "applied" if args.apply else "validated_only"
    print(json.dumps({"mode": mode, "input": str(args.input), "counts": summary}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
