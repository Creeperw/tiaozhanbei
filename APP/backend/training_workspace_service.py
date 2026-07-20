from __future__ import annotations
from APP.backend.time_utils import utc_now

import hashlib
import json
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from threading import Event, Thread
from typing import Any, Callable
from uuid import uuid4

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from APP.backend.agent_orchestrator_service import run_agent_orchestration
from APP.backend.database import (
    AgentEvent,
    AuditResultRecord,
    GradingResultRecord,
    LearningActivityRecord,
    LearningTask,
    LearningAttemptItemRecord,
    LearningAttemptRecord,
    MistakeRecord,
    QuestionAttempt,
    QuestionKPLinkRecord,
    QuestionVersionRecord,
    TrainingTaskRecord,
    VariationSetRecord,
)
from APP.backend.mistake_variation_service import (
    MistakeVariationNotFound,
    apply_mistake_variations,
)
from APP.backend.paper_generation_service import generate_and_publish_paper
from APP.backend.grading_application_service import (
    apply_practice_grading,
    from_workspace_request,
)
from APP.backend.training_orchestration_adapter import (
    OrchestrationRunner,
    TrainingOrchestrationInput,
    execute_training_orchestration,
)
from APP.backend.training_service import grade_practice_submission
from APP.backend.system_data_service import rebuild_system_data
from APP.backend.variation_repository import VariationRepository


TRAINING_TASK_QUESTION_ID_MAX_LENGTH = 120
TRAINING_TASK_MAX_JSON_BYTES = 64 * 1024
TRAINING_TASK_MAX_DEPTH = 6
TRAINING_TASK_MAX_OBJECT_KEYS = 50
TRAINING_TASK_MAX_LIST_ITEMS = 50
TRAINING_TASK_MAX_TEXT_LENGTH = 8000
TRAINING_TASK_MAX_KNOWLEDGE_POINTS = 20
VARIATION_CLAIM_LEASE = timedelta(minutes=5)
VARIATION_HEARTBEAT_INTERVAL_SECONDS = 60.0
TRAINING_TASK_OPTIONS = {
    "difficulty",
    "duration_minutes",
    "expected_duration_min",
    "save_activity",
    "need_audit",
    "question_count",
    "types",
    "distribution",
    "variation_count",
}


TRAINING_MODULES = [
    {
        "key": "practice_grading",
        "label": "练习批改",
        "description": "提交练习并获得批改与复盘建议。",
        "enabled": True,
        "badge": "MVP",
        "recommended": True,
    },
    {
        "key": "handout_generation",
        "label": "讲义生成",
        "description": "根据学习目标生成培训讲义。",
        "enabled": True,
        "badge": "MVP",
        "recommended": False,
    },
    {
        "key": "knowledge_card_generation",
        "label": "知识卡生成",
        "description": "根据知识点生成便于复习的知识卡。",
        "enabled": True,
        "badge": "MVP",
        "recommended": False,
    },
    {
        "key": "paper_generation",
        "label": "试卷生成",
        "description": "按训练目标生成综合试卷。",
        "enabled": True,
        "badge": "增强功能",
        "recommended": False,
    },
    {
        "key": "case_training",
        "label": "案例训练",
        "description": "围绕案例开展情境化训练。",
        "enabled": True,
        "badge": "增强功能",
        "recommended": False,
        "capability_url": "/training/case-sessions",
    },
    {
        "key": "mistake_variation",
        "label": "错题变式",
        "description": "根据错题生成变式练习。",
        "enabled": True,
        "badge": "增强功能",
        "recommended": False,
    },
]


class TrainingTaskExecutionError(Exception):
    pass


class InvalidTrainingTaskRequest(TrainingTaskExecutionError):
    pass


def _validate_json_shape(value: Any, *, path: str, depth: int) -> None:
    if depth > TRAINING_TASK_MAX_DEPTH:
        raise InvalidTrainingTaskRequest("maximum nesting depth exceeded")
    if isinstance(value, dict):
        if len(value) > TRAINING_TASK_MAX_OBJECT_KEYS:
            raise InvalidTrainingTaskRequest(f"{path} object has too many keys")
        for key, item in value.items():
            if not isinstance(key, str):
                raise InvalidTrainingTaskRequest(f"{path} object keys must be strings")
            _validate_json_shape(item, path=f"{path}.{key}", depth=depth + 1)
        return
    if isinstance(value, list):
        if len(value) > TRAINING_TASK_MAX_LIST_ITEMS:
            raise InvalidTrainingTaskRequest(f"{path} list has too many items")
        for item in value:
            _validate_json_shape(item, path=path, depth=depth + 1)
        return
    if isinstance(value, str) and len(value) > TRAINING_TASK_MAX_TEXT_LENGTH:
        raise InvalidTrainingTaskRequest(f"{path} is too long")
    if value is not None and not isinstance(value, (str, int, float, bool)):
        raise InvalidTrainingTaskRequest(f"{path} contains a non-JSON value")


def _validate_string(
    value: Any,
    *,
    path: str,
    max_length: int,
    type_path: str | None = None,
) -> None:
    if not isinstance(value, str):
        raise InvalidTrainingTaskRequest(f"{type_path or path} has invalid type")
    if len(value) > max_length:
        raise InvalidTrainingTaskRequest(f"{path} is too long")


def _validate_string_list(value: Any, *, path: str) -> None:
    if not isinstance(value, list) or not all(isinstance(item, str) and item.strip() for item in value):
        raise InvalidTrainingTaskRequest(f"{path} has invalid type")
    if len(value) > TRAINING_TASK_MAX_KNOWLEDGE_POINTS:
        raise InvalidTrainingTaskRequest(f"{path} has too many items")
    if any(len(item) > 120 for item in value):
        raise InvalidTrainingTaskRequest(f"{path} item is too long")


def _validate_integer_range(
    value: Any,
    *,
    path: str,
    minimum: int,
    maximum: int,
    type_path: str | None = None,
) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        raise InvalidTrainingTaskRequest(f"{type_path or path} has invalid type")
    if not minimum <= value <= maximum:
        raise InvalidTrainingTaskRequest(f"{path} must be between {minimum} and {maximum}")


def _validate_training_task_request(request: Any) -> dict[str, Any]:
    if not isinstance(request, dict):
        raise InvalidTrainingTaskRequest("request must be an object")
    try:
        encoded = json.dumps(request, ensure_ascii=False, allow_nan=False).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise InvalidTrainingTaskRequest("request must be JSON serializable") from exc
    if len(encoded) > TRAINING_TASK_MAX_JSON_BYTES:
        raise InvalidTrainingTaskRequest("payload is too large")
    _validate_json_shape(request, path="request", depth=1)

    for field, maximum in (("task_type", 80), ("title", 200), ("query", 8000)):
        if field in request:
            _validate_string(request[field], path=field, max_length=maximum)
    inputs = request.get("inputs", {})
    options = request.get("options", {})
    if not isinstance(inputs, dict):
        raise InvalidTrainingTaskRequest("inputs must be an object")
    if not isinstance(options, dict):
        raise InvalidTrainingTaskRequest("options must be an object")

    for field in ("query", "stem", "student_answer", "standard_answer", "rubric", "topic"):
        if field in inputs:
            _validate_string(
                inputs[field],
                path=f"inputs.{field}",
                type_path=field,
                max_length=TRAINING_TASK_MAX_TEXT_LENGTH,
            )
    for field in ("knowledge_points", "kp_ids"):
        if field in inputs:
            _validate_string_list(inputs[field], path=field)
    for field in ("difficulty",):
        if field in inputs:
            _validate_integer_range(
                inputs[field],
                path=f"inputs.{field}",
                type_path=field,
                minimum=1,
                maximum=5,
            )
    for field in ("duration_minutes", "expected_duration_min"):
        if field in inputs:
            _validate_integer_range(inputs[field], path=f"inputs.{field}", minimum=1, maximum=180)

    unknown_options = set(options) - TRAINING_TASK_OPTIONS
    if unknown_options:
        raise InvalidTrainingTaskRequest(f"unknown option: {sorted(unknown_options)[0]}")
    if "difficulty" in options:
        _validate_integer_range(options["difficulty"], path="options.difficulty", minimum=1, maximum=5)
    for field in ("duration_minutes", "expected_duration_min"):
        if field in options:
            _validate_integer_range(options[field], path=f"options.{field}", minimum=1, maximum=180)
    if "variation_count" in options:
        _validate_integer_range(options["variation_count"], path="options.variation_count", minimum=1, maximum=5)
    for field in ("save_activity", "need_audit"):
        if field in options and not isinstance(options[field], bool):
            raise InvalidTrainingTaskRequest(f"options.{field} has invalid type")
    return request


def _text(value: Any, default: str = "") -> str:
    if value is None:
        return default
    text = str(value).strip()
    return text or default


def _task_id() -> str:
    date_key = datetime.now(timezone.utc).strftime("%Y%m%d")
    return f"TT_{date_key}_{uuid4().hex[:8]}"


def _validate_practice_inputs(raw_inputs: Any) -> dict[str, Any]:
    if not isinstance(raw_inputs, dict):
        raise InvalidTrainingTaskRequest("inputs must be an object")

    inputs = dict(raw_inputs)
    string_fields = (
        "stem",
        "question_id",
        "question_type",
        "student_answer",
        "standard_answer",
        "rubric",
    )
    for field in string_fields:
        if field in inputs and not isinstance(inputs[field], str):
            raise InvalidTrainingTaskRequest(f"{field} has invalid type")

    if "stem" not in inputs or not inputs["stem"].strip():
        raise InvalidTrainingTaskRequest("stem is required")
    if len(inputs.get("question_id", "")) > TRAINING_TASK_QUESTION_ID_MAX_LENGTH:
        raise InvalidTrainingTaskRequest("question_id is too long")

    if "knowledge_points" in inputs:
        knowledge_points = inputs["knowledge_points"]
        if not isinstance(knowledge_points, list) or not all(
            isinstance(point, str) for point in knowledge_points
        ):
            raise InvalidTrainingTaskRequest("knowledge_points has invalid type")

    if "difficulty" in inputs and (
        isinstance(inputs["difficulty"], bool)
        or not isinstance(inputs["difficulty"], int)
    ):
        raise InvalidTrainingTaskRequest("difficulty has invalid type")
    return inputs


def _validate_generation_request(request: Any) -> tuple[str, str, str, dict[str, Any], dict[str, Any]]:
    if not isinstance(request, dict):
        raise InvalidTrainingTaskRequest("request must be an object")

    task_type = request.get("task_type")
    if task_type not in {"handout_generation", "knowledge_card_generation", "paper_generation", "mistake_variation"}:
        raise InvalidTrainingTaskRequest("unsupported task type")
    for field in ("title", "query"):
        if field in request and not isinstance(request[field], str):
            raise InvalidTrainingTaskRequest(f"{field} has invalid type")

    raw_inputs = request.get("inputs", {})
    raw_options = request.get("options", {})
    if not isinstance(raw_inputs, dict):
        raise InvalidTrainingTaskRequest("inputs must be an object")
    if not isinstance(raw_options, dict):
        raise InvalidTrainingTaskRequest("options must be an object")
    inputs = dict(raw_inputs)
    options = dict(raw_options)

    if "kp_ids" in inputs and (
        not isinstance(inputs["kp_ids"], list)
        or not all(isinstance(kp_id, str) and kp_id.strip() for kp_id in inputs["kp_ids"])
    ):
        raise InvalidTrainingTaskRequest("kp_ids has invalid type")
    if "knowledge_points" in inputs and (
        not isinstance(inputs["knowledge_points"], list)
        or not all(isinstance(point, str) and point.strip() for point in inputs["knowledge_points"])
    ):
        raise InvalidTrainingTaskRequest("knowledge_points has invalid type")
    for field in ("topic", "query"):
        if field in inputs and not isinstance(inputs[field], str):
            raise InvalidTrainingTaskRequest(f"inputs.{field} has invalid type")
    for field in ("difficulty", "duration_minutes"):
        if field in inputs and (isinstance(inputs[field], bool) or not isinstance(inputs[field], int)):
            raise InvalidTrainingTaskRequest(f"inputs.{field} has invalid type")
    for field in ("difficulty", "expected_duration_min"):
        if field in options and (isinstance(options[field], bool) or not isinstance(options[field], int)):
            raise InvalidTrainingTaskRequest(f"options.{field} has invalid type")

    if task_type == "mistake_variation":
        if "mistake_id" not in inputs:
            raise InvalidTrainingTaskRequest("mistake variation source is required")
        if isinstance(inputs["mistake_id"], bool) or not isinstance(inputs["mistake_id"], int) or inputs["mistake_id"] <= 0:
            raise InvalidTrainingTaskRequest("mistake_id has invalid type")
    if task_type == "paper_generation":
        question_count = options.get("question_count", inputs.get("question_count", 3))
        types = options.get("types", inputs.get("types", ["single_choice", "short_answer", "case_quiz"]))
        distribution = options.get("distribution", inputs.get("distribution"))
        if isinstance(question_count, bool) or not isinstance(question_count, int) or not 1 <= question_count <= 50:
            raise InvalidTrainingTaskRequest("question_count must be between 1 and 50")
        if not isinstance(types, list) or not types or not all(isinstance(value, str) and value.strip() for value in types):
            raise InvalidTrainingTaskRequest("types has invalid type")
        if len(types) != len(set(types)):
            raise InvalidTrainingTaskRequest("types must not contain duplicates")
        if (
            not isinstance(distribution, dict)
            or set(distribution) != set(types)
            or any(isinstance(count, bool) or not isinstance(count, int) or count < 0 for count in distribution.values())
            or sum(distribution.values()) != question_count
        ):
            raise InvalidTrainingTaskRequest("distribution must match types and question_count")
        if options.get("need_audit", True) is not True:
            raise InvalidTrainingTaskRequest("options.need_audit must be true for paper generation")
    title = _text(request.get("title"), "培训资料")[:200]
    query = _text(request.get("query")) or _text(inputs.get("query")) or _text(inputs.get("topic"))
    if not query:
        raise InvalidTrainingTaskRequest("query is required")
    return task_type, title, query, inputs, options


def _is_valid_evidence_snapshot(snapshot: Any) -> bool:
    if not isinstance(snapshot, dict):
        return False
    if not any(
        isinstance(snapshot.get(field), str) and snapshot[field].strip()
        for field in ("pack_id", "source_id")
    ):
        return False
    if any(
        field in snapshot and not isinstance(snapshot[field], list)
        for field in ("items", "kp_ids", "resolved_kp_ids")
    ):
        return False
    if "confidence" in snapshot and (
        isinstance(snapshot["confidence"], bool)
        or not isinstance(snapshot["confidence"], (int, float))
    ):
        return False
    return "source_scope" not in snapshot or isinstance(snapshot["source_scope"], str)


def _evidence_pack_placeholder(task: TrainingTaskRecord) -> dict[str, Any]:
    return {
        "pack_id": task.evidence_pack_id,
        "source_scope": "training_workspace_task",
        "source_id": task.task_id,
        "items": [],
        "kp_ids": [],
        "resolved_kp_ids": [],
        "confidence": 0.0,
    }


def _authorized_variation_inputs(db: Session, user_id: int, inputs: dict[str, Any]) -> dict[str, Any]:
    mistake_id = inputs["mistake_id"]
    row = db.query(MistakeRecord, QuestionVersionRecord, LearningAttemptItemRecord).join(
        QuestionVersionRecord,
        QuestionVersionRecord.question_version_id == MistakeRecord.question_version_id,
    ).join(
        LearningAttemptItemRecord,
        LearningAttemptItemRecord.attempt_item_id == MistakeRecord.attempt_item_id,
    ).join(
        LearningAttemptRecord,
        LearningAttemptRecord.attempt_id == LearningAttemptItemRecord.attempt_id,
    ).filter(
        MistakeRecord.id == mistake_id,
        MistakeRecord.user_id == user_id,
        MistakeRecord.status == "active",
        QuestionVersionRecord.status == "active",
        LearningAttemptRecord.learner_id == user_id,
        LearningAttemptItemRecord.question_version_id == MistakeRecord.question_version_id,
    ).one_or_none()
    if row is None:
        raise InvalidTrainingTaskRequest("mistake source is not owned or active")
    mistake, source, attempt_item = row
    audit = db.query(AuditResultRecord).join(
        GradingResultRecord,
        (GradingResultRecord.artifact_id == AuditResultRecord.source_artifact_id)
        & (GradingResultRecord.version == AuditResultRecord.source_artifact_version),
    ).join(
        LearningAttemptItemRecord,
        LearningAttemptItemRecord.attempt_item_id == GradingResultRecord.attempt_item_id,
    ).join(
        LearningAttemptRecord,
        LearningAttemptRecord.attempt_id == LearningAttemptItemRecord.attempt_id,
    ).filter(
        LearningAttemptRecord.learner_id == user_id,
        LearningAttemptItemRecord.attempt_item_id == attempt_item.attempt_item_id,
        LearningAttemptItemRecord.question_version_id == source.question_version_id,
        AuditResultRecord.decision == "pass",
        AuditResultRecord.status == "completed",
    ).order_by(AuditResultRecord.id.desc()).first()
    if audit is None:
        raise InvalidTrainingTaskRequest("mistake source audit is not passed")
    kp_ids = [row[0] for row in db.query(QuestionKPLinkRecord.kp_id).filter(
        QuestionKPLinkRecord.question_version_id == source.question_version_id,
        QuestionKPLinkRecord.status == "active",
    ).all()]
    if not kp_ids:
        raise InvalidTrainingTaskRequest("mistake source has no active knowledge points")
    return {
        "mistake_id": mistake.id,
        "source_question_version_id": source.question_version_id,
        "source_question_id": source.question_id,
        "source_stem": source.stem,
        "source_question_type": source.question_type,
        "source_difficulty": source.standard_difficulty,
        "kp_ids": kp_ids,
        "attempt_item_id": attempt_item.attempt_item_id,
        "source_audit_id": audit.audit_id,
    }


def _variation_task_id(user_id: int, source: dict[str, Any], request: dict[str, Any]) -> str:
    semantics = {
        "owner_user_id": user_id,
        "source_mistake_id": source["mistake_id"],
        "source_question_version_id": source["source_question_version_id"],
        "title": request.get("title", ""),
        "query": request.get("query", ""),
        "inputs": request.get("inputs", {}),
        "options": request.get("options", {}),
    }
    digest = hashlib.sha256(
        json.dumps(semantics, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return f"MV_{digest}"


def _same_session_variation_publisher(
    db: Session, publisher: Any | None,
) -> Callable[..., Any] | None:
    if publisher is None:
        return None
    if isinstance(publisher, VariationRepository) and publisher._session is db:
        return publisher
    raise InvalidTrainingTaskRequest("variation publisher must use the same database session")


def _reserve_variation_task(
    db: Session,
    *,
    task_id: str,
    user_id: int,
    title: str,
) -> tuple[dict[str, Any] | None, str | None]:
    now = utc_now()
    owner = uuid4().hex
    expires_at = now + VARIATION_CLAIM_LEASE
    existing = get_training_task_result(db, user_id, task_id)
    if existing is None:
        db.add(TrainingTaskRecord(
            task_id=task_id,
            user_id=user_id,
            task_type="mistake_variation",
            title=title,
            status="in_progress",
            claim_owner=owner,
            claim_expires_at=expires_at,
            artifact_type="question_variation",
            artifact_json="{}",
            evidence_pack_id="",
            evidence_pack_json="{}",
            audit_json="{}",
            trace_json="[]",
            learning_updates_json="{}",
        ))
        try:
            db.commit()
            return None, owner
        except IntegrityError:
            db.rollback()
            existing = get_training_task_result(db, user_id, task_id)

    if existing is None:
        return None, None
    if existing["status"] not in {"failed", "in_progress"}:
        return existing, None
    claimed = db.query(TrainingTaskRecord).filter(
        TrainingTaskRecord.task_id == task_id,
        TrainingTaskRecord.user_id == user_id,
        (
            (TrainingTaskRecord.status == "failed")
            | (
                (TrainingTaskRecord.status == "in_progress")
                & (
                    (TrainingTaskRecord.claim_expires_at.is_(None))
                    | (TrainingTaskRecord.claim_expires_at <= now)
                )
            )
        ),
    ).update({
        "status": "in_progress",
        "claim_owner": owner,
        "claim_expires_at": expires_at,
    }, synchronize_session=False)
    db.commit()
    if claimed == 1:
        return None, owner
    return get_training_task_result(db, user_id, task_id), None


def _renew_variation_claim(
    db: Session, *, task_id: str, user_id: int, claim_owner: str,
    commit: bool = False,
) -> None:
    renewed = db.query(TrainingTaskRecord).filter_by(
        task_id=task_id, user_id=user_id, status="in_progress",
        claim_owner=claim_owner,
    ).update({"claim_expires_at": utc_now() + VARIATION_CLAIM_LEASE})
    if renewed != 1:
        db.rollback()
        raise TrainingTaskExecutionError("variation claim ownership was lost")
    if commit:
        db.commit()
    else:
        db.flush()


@contextmanager
def _variation_claim_heartbeat(
    db: Session, *, task_id: str, user_id: int, claim_owner: str,
):
    stop = Event()
    failure: list[Exception] = []
    heartbeat_session_factory = sessionmaker(bind=db.get_bind())

    def heartbeat() -> None:
        while not stop.wait(VARIATION_HEARTBEAT_INTERVAL_SECONDS):
            heartbeat_db = heartbeat_session_factory()
            try:
                _renew_variation_claim(
                    heartbeat_db, task_id=task_id, user_id=user_id,
                    claim_owner=claim_owner, commit=True,
                )
            except TrainingTaskExecutionError as exc:
                failure.append(exc)
                return
            except Exception as exc:
                heartbeat_db.rollback()
                failure.append(exc)
                return
            finally:
                heartbeat_db.close()

    thread = Thread(target=heartbeat, name=f"variation-heartbeat-{task_id}", daemon=True)
    thread.start()
    stopped = False

    def stop_and_check() -> None:
        nonlocal stopped
        if not stopped:
            stop.set()
            thread.join()
            stopped = True
        if failure:
            raise TrainingTaskExecutionError("variation claim heartbeat failed") from failure[0]

    body_failure: BaseException | None = None
    try:
        yield stop_and_check
    except BaseException as exc:
        body_failure = exc
    finally:
        stop_and_check()
    if body_failure is not None:
        raise body_failure


def _mark_variation_task_failed(
    db: Session, *, task_id: str, user_id: int, claim_owner: str
) -> None:
    db.query(TrainingTaskRecord).filter_by(
        task_id=task_id, user_id=user_id, claim_owner=claim_owner,
    ).update({"status": "failed", "claim_owner": None, "claim_expires_at": None})
    db.commit()


def _create_generation_task(
    db: Session,
    *,
    user_id: int,
    request: dict[str, Any],
    runtime: Any | None,
    orchestration_runner: OrchestrationRunner,
    variation_publisher: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    task_type, title, query, inputs, options = _validate_generation_request(request)
    if task_type == "mistake_variation":
        raise InvalidTrainingTaskRequest("mistake variation must use the audited variation service")
    task_id = _task_id()
    value = TrainingOrchestrationInput(
        task_id=task_id,
        user_id=user_id,
        task_type=task_type,
        title=title,
        query=query,
        inputs=dict(inputs),
        options=dict(options),
    )

    try:
        publisher = variation_publisher
        if task_type == "mistake_variation" and publisher is None:
            repository = VariationRepository(session=db)

            def publisher(**kwargs):
                content = kwargs
                return repository.publish_variation(
                    variation_set_id=f"VS_{task_id}",
                    question_version_id=content["artifact_source_id"],
                    question_id=f"variation:{task_id}",
                    owner_user_id=content["owner_user_id"],
                    source_mistake_id=content["source_mistake_id"],
                    source_question_version_id=content["source_question_version_id"],
                    audit_id=content["audit_id"],
                    stem=content["stem"],
                    question_type=content["question_type"],
                    difficulty=content["difficulty"],
                    kp_ids=tuple(content["kp_ids"]),
                )
        result = execute_training_orchestration(
            db=db,
            value=value,
            runtime=runtime,
            runner=orchestration_runner,
            variation_publisher=publisher,
        )
        if task_type == "paper_generation":
            result = generate_and_publish_paper(
                db=db,
                user_id=user_id,
                orchestration_result=result,
                need_audit=options.get("need_audit", True),
            )
        result = {**result, "next_actions": _next_actions()}
        _persist_task(db, user_id=user_id, inputs=inputs, result=result)
        db.commit()
        return result
    except Exception:
        db.rollback()
        raise


def _has_mistake_record(payload: dict[str, Any]) -> bool:
    mistake = payload.get("mistake_record")
    if mistake is None:
        return False
    if not isinstance(mistake, dict):
        raise TrainingTaskExecutionError("invalid mistake record")
    return bool(mistake)


def _knowledge_points(inputs: dict[str, Any]) -> list[str]:
    raw_points = inputs.get("knowledge_points", [])
    if not isinstance(raw_points, list):
        return []
    return [_text(point) for point in raw_points if _text(point)]


def _build_evidence_pack(task_id: str, inputs: dict[str, Any], query: str) -> dict[str, Any]:
    knowledge_points = _knowledge_points(inputs)
    source_id = _text(inputs.get("question_id"), task_id)
    summary = _text(inputs.get("rubric")) or _text(inputs.get("stem")) or query or "练习提交"
    return {
        "pack_id": f"EP_{task_id}",
        "source_scope": "training_workspace_submission",
        "source_id": source_id,
        "items": [
            {
                "source_scope": "submission",
                "source_id": source_id,
                "summary": summary,
                "kp_ids": knowledge_points,
                "confidence": 0.8,
            }
        ],
        "kp_ids": knowledge_points,
        "resolved_kp_ids": knowledge_points,
        "confidence": 0.8,
    }


def _normalize_trace(raw_trace: Any, *, status: str, summary: str) -> list[dict[str, str]]:
    trace: list[dict[str, str]] = []
    if isinstance(raw_trace, list):
        for index, item in enumerate(raw_trace, start=1):
            if not isinstance(item, dict):
                continue
            action = _text(item.get("action"), "完成练习批改步骤")
            trace.append(
                {
                    "step_id": _text(item.get("step_id"), f"s{index}"),
                    "agent": _text(item.get("agent"), "training_workspace_facade"),
                    "action": action,
                    "status": _text(item.get("status"), status),
                    "summary": _text(item.get("summary"), action),
                }
            )
    if trace:
        return trace
    return [
        {
            "step_id": "s1",
            "agent": "training_workspace_facade",
            "action": "create_training_task",
            "status": status,
            "summary": summary,
        }
    ]


def _next_actions() -> list[dict[str, str]]:
    return [
        {
            "type": "generate_variation",
            "label": "生成错题变式",
            "task_type": "mistake_variation",
        },
        {
            "type": "generate_handout",
            "label": "查看补救讲义",
            "task_type": "handout_generation",
        },
    ]


def _score(payload: dict[str, Any]) -> float | None:
    grading = payload.get("grading")
    if not isinstance(grading, dict):
        return None
    value = grading.get("score")
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _persist_practice_outcome(
    db: Session,
    *,
    user_id: int,
    inputs: dict[str, Any],
    payload: dict[str, Any],
    mistake_recorded: bool,
) -> None:
    grading = payload["grading"]
    question_id = grading.get("question_id") or inputs.get("question_id") or "manual-question"
    knowledge_points = inputs.get("knowledge_points") or []
    score = float(grading.get("score") or 0)
    is_correct = bool(grading.get("is_correct"))
    timestamp = datetime.now(timezone.utc).replace(tzinfo=None)

    db.add(
        QuestionAttempt(
            user_id=user_id,
            question_id=question_id,
            answer=inputs.get("student_answer", ""),
            is_correct=is_correct,
            score=score,
            kp_ids_json=json.dumps(knowledge_points, ensure_ascii=False),
            feedback=grading.get("analysis", ""),
            created_at=timestamp,
        )
    )
    db.add(
        LearningActivityRecord(
            user_id=user_id,
            activity_type="question_attempt",
            resource_id=question_id,
            resource_type="question",
            duration_minutes=10,
            completion_status="completed" if is_correct else "needs_review",
            score=score,
            payload_json=json.dumps(
                {**inputs, "grading": grading},
                ensure_ascii=False,
            ),
            created_at=timestamp,
        )
    )
    if not mistake_recorded:
        return

    mistake = payload["mistake_record"]
    db.add(
        MistakeRecord(
            user_id=user_id,
            question_id=question_id,
            kp_ids_json=json.dumps(knowledge_points, ensure_ascii=False),
            error_type=(
                mistake.get("error_type")
                or grading.get("error_type")
                or "练习错因"
            ),
            summary=mistake.get("content") or grading.get("analysis", ""),
            status="active",
            created_at=timestamp,
            updated_at=timestamp,
        )
    )


def _persist_task(
    db: Session,
    *,
    user_id: int,
    inputs: dict[str, Any],
    result: dict[str, Any],
    expected_claim_owner: str | None = None,
) -> None:
    artifact = result["artifact"]
    task_values = {
        "task_type": result["task_type"],
        "title": result["title"],
        "status": result["status"],
        "artifact_type": artifact["artifact_type"],
        "artifact_json": json.dumps(artifact, ensure_ascii=False),
        "evidence_pack_id": _text(result["evidence_pack"].get("pack_id") or result["evidence_pack"].get("source_id")),
        "evidence_pack_json": json.dumps(result["evidence_pack"], ensure_ascii=False),
        "audit_json": json.dumps(result["audit"], ensure_ascii=False),
        "trace_json": json.dumps(result["trace"], ensure_ascii=False),
        "learning_updates_json": json.dumps(result["learning_updates"], ensure_ascii=False),
    }
    if expected_claim_owner is not None:
        task_values = {**task_values, "claim_owner": None, "claim_expires_at": None}
        updated = db.query(TrainingTaskRecord).filter_by(
            task_id=result["task_id"], user_id=user_id,
            claim_owner=expected_claim_owner,
        ).update(task_values, synchronize_session=False)
        if updated != 1:
            raise TrainingTaskExecutionError("variation claim ownership was lost")
        existing = True
    else:
        existing = db.query(TrainingTaskRecord).filter_by(task_id=result["task_id"], user_id=user_id).one_or_none()
    if existing is None:
        db.add(TrainingTaskRecord(task_id=result["task_id"], user_id=user_id, **task_values))
    elif expected_claim_owner is None:
        for field, value in task_values.items():
            setattr(existing, field, value)
    db.add(
        LearningActivityRecord(
            user_id=user_id,
            activity_type="training_workspace_task",
            resource_id=result["task_id"],
            resource_type="training_task",
            duration_minutes=0,
            completion_status=result["status"],
            score=_score(artifact["content"]),
            payload_json=json.dumps(
                {
                    "task_id": result["task_id"],
                    "task_type": result["task_type"],
                    "status": result["status"],
                    "run_id": result.get("orchestration_run_id", ""),
                },
                ensure_ascii=False,
            ),
        )
    )
    learning_task = db.query(LearningTask).filter_by(
        task_id=result["task_id"],
        user_id=user_id,
    ).one_or_none()
    completed_at = utc_now() if result["status"] == "completed" else None
    if learning_task is None:
        db.add(LearningTask(
            task_id=result["task_id"],
            user_id=user_id,
            task_type=result["task_type"],
            resource_ids_json=json.dumps([result["task_id"]], ensure_ascii=False),
            task_content=result["title"],
            expected_output=artifact["artifact_type"],
            status=result["status"],
            completed_at=completed_at,
        ))
    else:
        learning_task.status = result["status"]
        learning_task.completed_at = completed_at
        learning_task.version += 1
    rebuild_system_data(db, user_id=user_id)
    db.add(
        AgentEvent(
            user_id=user_id,
            session_id=None,
            agent_name="training_workspace_facade",
            event_type=result["task_type"],
            input_summary="training workspace task",
            output_summary=result["summary"],
            payload=json.dumps(
                {
                    "task_id": result["task_id"],
                    "task_type": result["task_type"],
                    "status": result["status"],
                    "run_id": result.get("orchestration_run_id", ""),
                },
                ensure_ascii=False,
            ),
        )
    )


def create_training_task(
    db: Session,
    user_id: int,
    request: dict[str, Any],
    runtime: Any | None = None,
    orchestration_runner: OrchestrationRunner = run_agent_orchestration,
    grading_runner: Callable[..., dict[str, Any]] | None = None,
    variation_publisher: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    request = _validate_training_task_request(request)
    requested_task_type = request.get("task_type")
    if not isinstance(requested_task_type, str):
        raise InvalidTrainingTaskRequest("task_type has invalid type")
    if requested_task_type == "mistake_variation":
        raw_inputs = request.get("inputs", {})
        mistake_id = raw_inputs.get("mistake_id")
        if isinstance(mistake_id, bool) or not isinstance(mistake_id, int) or mistake_id <= 0:
            raise InvalidTrainingTaskRequest("mistake_id has invalid type")
        action = raw_inputs.get("action", "generate")
        if action not in {"generate", "answer"}:
            raise InvalidTrainingTaskRequest("inputs.action is invalid")
        if action == "answer":
            allowed = {"action", "mistake_id", "question_version_id", "student_answer", "request_id"}
            unknown = set(raw_inputs) - allowed
            if unknown:
                raise InvalidTrainingTaskRequest(f"inputs.{sorted(unknown)[0]} is not accepted")
            for field in ("question_version_id", "student_answer"):
                if not isinstance(raw_inputs.get(field), str) or not raw_inputs[field].strip():
                    raise InvalidTrainingTaskRequest(f"inputs.{field} is required")
            request_id = raw_inputs.get("request_id")
            if not isinstance(request_id, str) or not request_id.strip() or len(request_id) > 120:
                raise InvalidTrainingTaskRequest("inputs.request_id must be between 1 and 120 characters")
            semantics = f"{user_id}:{raw_inputs['question_version_id']}:{mistake_id}:{request_id.strip()}"
            task_id = f"MVA_{hashlib.sha256(semantics.encode()).hexdigest()}"
            reserved, claim_owner = _reserve_variation_task(
                db, task_id=task_id, user_id=user_id,
                title=_text(request.get("title"), "变式批改")[:200],
            )
            if reserved is not None:
                return reserved
            if claim_owner is None:
                raise InvalidTrainingTaskRequest("variation answer claim could not be acquired")
            try:
                with _variation_claim_heartbeat(
                    db, task_id=task_id, user_id=user_id, claim_owner=claim_owner,
                ) as stop_heartbeat:
                    applied = apply_mistake_variations(
                        db, user_id, mistake_id, 1,
                        answer={
                            "question_version_id": raw_inputs["question_version_id"],
                            "student_answer": raw_inputs["student_answer"],
                        },
                        grading_runner=grading_runner or grade_practice_submission,
                        answer_request_id=task_id,
                        before_persist=lambda: (
                            stop_heartbeat(),
                            _renew_variation_claim(
                                db, task_id=task_id, user_id=user_id,
                                claim_owner=claim_owner,
                            ),
                        ),
                    )
                    result = {
                        "task_id": task_id, "task_type": "mistake_variation",
                        "status": "completed", "title": _text(request.get("title"), "变式批改")[:200],
                        "summary": "错题变式批改已完成。",
                        "artifact": {"artifact_type": "grading_result", "title": "错题变式批改", "content": applied},
                        "evidence_pack": {"source_scope": "mistake_variation", "source_id": str(mistake_id), "items": [], "resolved_kp_ids": []},
                        "audit": applied["grading"]["audit"], "trace": [],
                        "learning_updates": {"activity_recorded": True, "mistake_recorded": False, "mastery_updates": [], "review_tasks": [], "profile_suggestions": [], "writeback": applied["grading"]["writeback"]},
                        "next_actions": _next_actions(),
                    }
                    _persist_task(
                        db, user_id=user_id, inputs=raw_inputs, result=result,
                        expected_claim_owner=claim_owner,
                    )
                    db.commit()
                return get_training_task_result(db, user_id, task_id) or result
            except Exception:
                db.rollback()
                _mark_variation_task_failed(
                    db, task_id=task_id, user_id=user_id, claim_owner=claim_owner,
                )
                raise
        variation_count = raw_inputs.get("variation_count", request.get("options", {}).get("variation_count", 1))
        try:
            source = _authorized_variation_inputs(db, user_id, raw_inputs)
        except InvalidTrainingTaskRequest as exc:
            raise MistakeVariationNotFound("mistake was not found") from exc
        task_id = _variation_task_id(user_id, source, request)
        publisher = _same_session_variation_publisher(db, variation_publisher)
        reserved, claim_owner = _reserve_variation_task(
            db,
            task_id=task_id,
            user_id=user_id,
            title=_text(request.get("title"), "错题变式")[:200],
        )
        if reserved is not None:
            return reserved
        if claim_owner is None:
            raise InvalidTrainingTaskRequest("variation claim could not be acquired")
        try:
            with _variation_claim_heartbeat(
                db, task_id=task_id, user_id=user_id, claim_owner=claim_owner,
            ) as stop_heartbeat:
                applied = apply_mistake_variations(
                    db,
                    user_id,
                    mistake_id,
                    variation_count,
                    runner=orchestration_runner,
                    runtime=runtime,
                    grading_runner=grading_runner or grade_practice_submission,
                    variation_publisher=publisher,
                    task_id_prefix=task_id,
                    before_persist=lambda: (
                        stop_heartbeat(),
                        _renew_variation_claim(
                            db, task_id=task_id, user_id=user_id,
                            claim_owner=claim_owner,
                        ),
                    ),
                )
        except ValueError as exc:
            db.rollback()
            _mark_variation_task_failed(db, task_id=task_id, user_id=user_id, claim_owner=claim_owner)
            raise InvalidTrainingTaskRequest(str(exc)) from exc
        except Exception:
            db.rollback()
            _mark_variation_task_failed(db, task_id=task_id, user_id=user_id, claim_owner=claim_owner)
            raise
        result = {
            "task_id": task_id,
            "task_type": "mistake_variation",
            "status": "completed",
            "title": _text(request.get("title"), "错题变式")[:200],
            "summary": "错题变式已生成。",
            "artifact": {"artifact_type": "question_variation", "title": "错题变式", "content": applied},
            "evidence_pack": {"source_scope": "mistake_variation", "source_id": str(mistake_id), "items": [], "resolved_kp_ids": []},
            "audit": {"decision": "pass"},
            "trace": [],
            "learning_updates": {"activity_recorded": True, "mistake_recorded": False, "mastery_updates": [], "review_tasks": [], "profile_suggestions": []},
            "next_actions": _next_actions(),
        }
        audit_ids = [row[0] for row in db.query(VariationSetRecord.audit_id).filter(
            VariationSetRecord.variation_set_id.like(f"VS_{task_id}:%"),
            VariationSetRecord.owner_user_id == user_id,
            VariationSetRecord.source_mistake_id == mistake_id,
            VariationSetRecord.status == "published",
        ).all()]
        result = {**result, "audit": {
            "decision": "pass", "status": "completed",
            "audit_ids": audit_ids,
        }}
        try:
            _persist_task(
                db, user_id=user_id, inputs=raw_inputs, result=result,
                expected_claim_owner=claim_owner,
            )
            db.commit()
            return get_training_task_result(db, user_id, task_id) or result
        except Exception:
            db.rollback()
            _mark_variation_task_failed(db, task_id=task_id, user_id=user_id, claim_owner=claim_owner)
            raise
    if requested_task_type in {"handout_generation", "knowledge_card_generation", "paper_generation"}:
        return _create_generation_task(
            db,
            user_id=user_id,
            request=request,
            runtime=runtime,
            orchestration_runner=orchestration_runner,
            variation_publisher=variation_publisher,
        )

    raw_inputs = request.get("inputs", {})
    task_id = _task_id()
    task_type = _text(request.get("task_type"), "unknown")[:80]

    if task_type != "practice_grading":
        raise InvalidTrainingTaskRequest("unsupported task type")
    inputs = _validate_practice_inputs(raw_inputs)

    title = _text(request.get("title"), "练习批改")[:200]
    query = _text(request.get("query"))
    evidence_pack = _build_evidence_pack(task_id, inputs, query)

    command = from_workspace_request(
        user_id,
        {**request, "task_id": task_id, "inputs": inputs},
        profile={},
        memories=[],
        request_id=task_id,
    )
    result: dict[str, Any] = {}

    def persist_workspace_projection(grading_result) -> None:
        grading_payload = grading_result.grading_payload or {}
        audit_payload = grading_result.audit or {}
        summary = _text(grading_payload.get("feedback"), "练习批改已完成。")
        grading_payload = {**grading_payload, "analysis": summary}
        writeback = asdict(grading_result.writeback) if grading_result.writeback else None
        if writeback:
            writeback = {
                **writeback,
                "mistake_ids": list(writeback["mistake_ids"]),
                "mastery_updates": list(writeback["mastery_updates"]),
                "review_task_ids": list(writeback["review_task_ids"]),
            }
        learning_updates = {
            "activity_recorded": True,
            "mistake_recorded": bool(writeback and writeback["mistake_ids"]),
            "mastery_updates": writeback["mastery_updates"] if writeback else [],
            "review_tasks": writeback["review_task_ids"] if writeback else [],
            "profile_suggestions": [],
            "writeback": writeback,
        }
        result.update({
            "task_id": task_id,
            "task_type": task_type,
            "status": "completed",
            "title": title,
            "summary": summary,
            "attempt_id": grading_result.attempt_id,
            "attempt_item_id": grading_result.attempt_item_id,
            "grading_artifact_id": grading_result.grading_artifact_id,
            "grading_artifact_version": grading_result.grading_artifact_version,
            "audit_id": grading_result.audit_id,
            "artifact": {
                "artifact_type": "grading_result",
                "title": title,
                "content": {"grading": grading_payload},
                "grading_ids": {
                    "attempt_id": grading_result.attempt_id,
                    "attempt_item_id": grading_result.attempt_item_id,
                    "grading_artifact_id": grading_result.grading_artifact_id,
                    "grading_artifact_version": grading_result.grading_artifact_version,
                    "audit_id": grading_result.audit_id,
                },
            },
            "evidence_pack": evidence_pack,
            "audit": audit_payload,
            "trace": _normalize_trace(None, status="completed", summary=summary),
            "learning_updates": learning_updates,
            "next_actions": _next_actions(),
        })
        _persist_task(db, user_id=user_id, inputs=inputs, result=result)

    apply_practice_grading(
        db,
        command,
        runner=grading_runner or grade_practice_submission,
        before_commit=persist_workspace_projection,
    )
    return result


def _parse_task_json(value: str | None, fallback: Any) -> Any:
    if not value:
        return fallback
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return fallback
    return parsed if isinstance(parsed, type(fallback)) else fallback


def _task_detail_summary(task: TrainingTaskRecord, artifact: dict[str, Any]) -> str:
    if task.task_type == "practice_grading":
        grading = artifact["content"].get("grading", {})
        return _text(
            grading.get("analysis") if isinstance(grading, dict) else None,
            "练习批改已完成。" if task.status == "completed" else "练习批改未完成，请重试。",
        )
    if task.task_type == "handout_generation":
        return "讲义已生成。" if task.status == "completed" else "讲义未完成，请检查后重试。"
    if task.task_type == "knowledge_card_generation":
        return "知识卡已生成。" if task.status == "completed" else "知识卡未完成，请检查后重试。"
    if task.task_type == "paper_generation":
        return "试卷已生成。" if task.status == "completed" else "试卷未完成，请调整组卷条件。"
    return "培训任务已完成。" if task.status == "completed" else "培训任务未完成，请重试。"


def get_training_task_result(
    db: Session,
    user_id: int,
    task_id: str,
) -> dict[str, Any] | None:
    task = (
        db.query(TrainingTaskRecord)
        .filter_by(task_id=task_id, user_id=user_id)
        .one_or_none()
    )
    if task is None:
        return None

    artifact = _parse_task_json(task.artifact_json, {})
    if (
        not artifact
        or not isinstance(artifact.get("artifact_type"), str)
        or not isinstance(artifact.get("title"), str)
        or not isinstance(artifact.get("content"), dict)
    ):
        artifact = {
            "artifact_type": task.artifact_type,
            "title": task.title,
            "content": {},
        }
    summary = _task_detail_summary(task, artifact)
    evidence_pack = _parse_task_json(task.evidence_pack_json, {})
    if not _is_valid_evidence_snapshot(evidence_pack):
        evidence_pack = _evidence_pack_placeholder(task)
    trace = _parse_task_json(task.trace_json, [])
    grading_ids = artifact.get("grading_ids", {}) if task.task_type == "practice_grading" else {}
    if not isinstance(grading_ids, dict):
        grading_ids = {}
    orchestration_run_id = ""
    if trace and isinstance(trace[0], dict) and trace[0].get("step_id") == "orchestration":
        run_id = trace[0].get("run_id")
        if isinstance(run_id, str):
            orchestration_run_id = run_id
    return {
        "task_id": task.task_id,
        "task_type": task.task_type,
        "status": task.status,
        **{
            field: grading_ids[field]
            for field in (
                "attempt_id",
                "attempt_item_id",
                "grading_artifact_id",
                "grading_artifact_version",
                "audit_id",
            )
            if field in grading_ids
        },
        "title": task.title,
        "summary": summary,
        "artifact": artifact,
        "evidence_pack": evidence_pack,
        "audit": _parse_task_json(task.audit_json, {}),
        "trace": trace,
        "orchestration_run_id": orchestration_run_id,
        "learning_updates": _parse_task_json(task.learning_updates_json, {}),
        "next_actions": _next_actions(),
    }


def get_training_workspace_modules() -> dict[str, object]:
    return {
        "default_task_type": "practice_grading",
        "modules": deepcopy(TRAINING_MODULES),
    }
