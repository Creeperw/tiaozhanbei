from __future__ import annotations

import importlib
import json
import os
import sys
import threading
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any
from urllib.parse import quote_plus

from fastapi import FastAPI

from competition_app.config import Settings


_IMPORT_LOCK = threading.RLock()


@contextmanager
def _temporary_environment(values: dict[str, str]):
    previous = {name: os.environ.get(name) for name in values}
    os.environ.update(values)
    try:
        yield
    finally:
        for name, value in previous.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


@dataclass
class BackendHandoffRuntime:
    """Loaded frontend-backend contract hosted inside the main ASGI process."""

    app: FastAPI
    root: Path
    runtime_root: Path
    database_backend: str
    _started: bool = False
    _lifespan: object | None = field(default=None, init=False, repr=False)

    @property
    def route_count(self) -> int:
        return len(self.app.routes)

    def status(self) -> dict[str, object]:
        return {
            "enabled": True,
            "mounted": True,
            "source": str(self.root),
            "runtime_root": str(self.runtime_root),
            "database_backend": self.database_backend,
            "route_count": self.route_count,
            "started": self._started,
        }

    async def startup(self) -> None:
        if self._started:
            return
        self._lifespan = self.app.router.lifespan_context(self.app)
        await self._lifespan.__aenter__()
        self._started = True

    async def shutdown(self) -> None:
        if not self._started:
            return
        if self._lifespan is not None:
            await self._lifespan.__aexit__(None, None, None)
        self._lifespan = None
        self._started = False

    def load_learning_context(self, external_user_id: str) -> dict[str, Any]:
        """Build a server-owned behavior context for the host application's user."""

        database = importlib.import_module("APP.backend.database")
        auth = importlib.import_module("APP.backend.auth")
        diagnosis = importlib.import_module("APP.backend.diagnosis_agent_service")
        learning_targets = importlib.import_module("APP.backend.learning_target_service")
        memory = importlib.import_module("APP.backend.memory_agent_service")
        system_data = importlib.import_module("APP.backend.system_data_service")
        db = database.SessionLocal()
        try:
            user = auth._get_or_create_host_user(  # noqa: SLF001 - integration boundary
                db, SimpleNamespace(user_id=external_user_id)
            )
            stored_profile = diagnosis.get_or_create_profile(db, user.id, commit=False)
            snapshot = system_data.rebuild_system_data(db, user_id=user.id)
            profile = diagnosis.build_learning_profile(db, user.id)
            behavior_window = diagnosis.build_l3_behavior_window(db, user.id)
            diagnosis_report = diagnosis.build_diagnosis_snapshot(
                db, user.id, persist=False
            )
            learner_brief = memory.build_learner_context_brief(db, user.id)
            learning_target = learning_targets.serialize_learning_target(
                learning_targets.get_active_learning_target(db, user.id)
            )
            trends = system_data.build_learning_trends(db, user_id=user.id, days=7)

            mastery_rows = (
                db.query(database.LearnerKnowledgeMastery)
                .filter(database.LearnerKnowledgeMastery.user_id == user.id)
                .order_by(database.LearnerKnowledgeMastery.updated_at.desc())
                .limit(100)
                .all()
            )
            completed_attempts = self._load_completed_question_attempts(
                database, db, user.id, external_user_id
            )
            system_payload = system_data.system_data_payload(snapshot)
            system_payload.update(
                {
                    "behavior_window": behavior_window,
                    "question_accuracy": {
                        "value": profile.get("question_accuracy", 0.0),
                        "unit": "ratio",
                    },
                    "review_stability": {
                        "value": profile.get("review_stability", 0.0),
                        "unit": "ratio",
                    },
                }
            )
            brief_payload = learner_brief.model_dump(mode="json")
            user_profile = dict(brief_payload.get("profile", {}))
            try:
                survey = json.loads(stored_profile.survey_json or "{}")
            except (TypeError, ValueError):
                survey = {}
            confirmed_profile = survey.get("agent_confirmed_profile") if isinstance(survey, dict) else {}
            if isinstance(confirmed_profile, dict):
                user_profile.update(
                    {
                        str(key): value
                        for key, value in confirmed_profile.items()
                        if str(key).strip() and value not in (None, "")
                    }
                )
            if learning_target:
                goal_name = str(learning_target.get("exam_name") or "").strip()
                target_type = str(learning_target.get("target_type") or "").strip()
                goal_type = (
                    "credential"
                    if target_type == "certification"
                    else "admission"
                    if target_type == "graduate_entrance_exam"
                    else target_type
                )
                if goal_name:
                    # The active target is an explicit, persisted user choice and is
                    # more reliable than the legacy free-text profile placeholder.
                    user_profile["learning_goal"] = goal_name
                    user_profile["goals"] = {
                        "goal_type": goal_type or "learning",
                        "goal_name": goal_name,
                    }
            report_payload = diagnosis_report.model_dump(mode="json")
            db.commit()
            return {
                "source": "frontend_backend",
                "calculated_at": system_payload.get("calculated_at"),
                "user_profile": user_profile,
                "learning_target": learning_target,
                "learning_profile": {
                    **profile,
                    "current_status": {
                        "status_code": diagnosis_report.stage_id or "T0",
                        "status_name": diagnosis_report.stage_name or "稳定学习",
                        "confidence": diagnosis_report.confidence or 0.0,
                        "evidence": [diagnosis_report.summary]
                        if diagnosis_report.summary
                        else [],
                    },
                    "behavior_metrics": behavior_window,
                },
                "system_data": system_payload,
                "question_attempt": completed_attempts,
                "mastery": [
                    {
                        "kp_id": row.kp_id,
                        "mastery": float(row.mastery or 0.0),
                        "confidence": float(row.confidence or 0.0),
                        "wrong_count": int(row.wrong_count or 0),
                        "review_count": int(row.review_count or 0),
                        "mastery_status": row.mastery_status,
                        "last_review_at": row.last_review_at.isoformat()
                        if row.last_review_at
                        else None,
                        "next_review_at": row.next_review_at.isoformat()
                        if row.next_review_at
                        else None,
                    }
                    for row in mastery_rows
                ],
                "learning_trends": trends,
                "diagnosis": report_payload,
            }
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def update_learning_profile(
        self,
        external_user_id: str,
        updates: dict[str, Any],
        execution_id: str | None = None,
    ) -> dict[str, Any]:
        """Persist user-confirmed planning context with an auditable boundary."""

        allowed_fields = {"learning_goal", "learning_background", "time_constraints"}
        normalized = {
            str(key): value
            for key, value in updates.items()
            if key in allowed_fields and value not in (None, "")
        }
        if set(updates) - allowed_fields:
            raise PermissionError("profile update contains unsupported fields")
        if not normalized:
            return self.load_learning_context(external_user_id).get("user_profile", {})

        database = importlib.import_module("APP.backend.database")
        auth = importlib.import_module("APP.backend.auth")
        diagnosis = importlib.import_module("APP.backend.diagnosis_agent_service")
        profile_service = importlib.import_module("APP.backend.learner_profile_service")
        db = database.SessionLocal()
        try:
            user = auth._get_or_create_host_user(  # noqa: SLF001 - integration boundary
                db, SimpleNamespace(user_id=external_user_id)
            )
            profile = diagnosis.get_or_create_profile(db, user.id, commit=False)
            locked_fields = profile_service.get_locked_profile_fields(profile)
            confirmed = {
                key: value for key, value in normalized.items() if key not in locked_fields
            }
            mapped = {
                key: value
                for key, value in confirmed.items()
                if key in {"learning_goal", "time_constraints"}
            }
            if mapped:
                profile_service.apply_learner_profile_update(
                    profile, mapped, source="diagnosis_agent"
                )
            try:
                survey = json.loads(profile.survey_json or "{}")
            except (TypeError, ValueError):
                survey = {}
            if not isinstance(survey, dict):
                survey = {}
            stored = survey.get("agent_confirmed_profile")
            if not isinstance(stored, dict):
                stored = {}
            stored.update(confirmed)
            survey["agent_confirmed_profile"] = stored
            profile.survey_json = json.dumps(survey, ensure_ascii=False)
            db.add(
                database.AgentEvent(
                    user_id=user.id,
                    agent_name="diagnosis_agent",
                    event_type="profile_confirmed_writeback",
                    input_summary="用户在规划追问中确认画像信息",
                    output_summary="已写入：" + "、".join(sorted(confirmed)),
                    payload=json.dumps(
                        {
                            "execution_id": execution_id,
                            "fields": sorted(confirmed),
                            "skipped_locked_fields": sorted(set(normalized) - set(confirmed)),
                        },
                        ensure_ascii=False,
                    ),
                )
            )
            db.commit()
            payload = profile_service.build_learner_profile_payload(profile)
            payload.update(stored)
            return payload
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    @staticmethod
    def workshop_overview() -> dict[str, Any]:
        service = importlib.import_module("APP.backend.learning_workshop_service")
        return service.workshop_overview()

    def _workshop_user(self, db, external_user_id: str):
        auth = importlib.import_module("APP.backend.auth")
        return auth._get_or_create_host_user(  # noqa: SLF001 - integration boundary
            db, SimpleNamespace(user_id=external_user_id)
        )

    def list_knowledge_cards(
        self, external_user_id: str, *, offset: int = 0, limit: int = 50
    ) -> dict[str, Any]:
        database = importlib.import_module("APP.backend.database")
        service = importlib.import_module("APP.backend.learning_workshop_service")
        db = database.SessionLocal()
        try:
            user = self._workshop_user(db, external_user_id)
            return service.list_knowledge_cards(
                db, user_id=user.id, offset=offset, limit=limit
            )
        finally:
            db.close()

    def get_knowledge_card(
        self, external_user_id: str, card_id: str
    ) -> dict[str, Any] | None:
        database = importlib.import_module("APP.backend.database")
        service = importlib.import_module("APP.backend.learning_workshop_service")
        db = database.SessionLocal()
        try:
            user = self._workshop_user(db, external_user_id)
            return service.get_knowledge_card(db, user_id=user.id, card_id=card_id)
        finally:
            db.close()

    def save_knowledge_card(
        self,
        external_user_id: str,
        *,
        kp_id: str,
        title: str,
        resource_bundle: dict[str, Any],
        source_execution_id: str = "",
    ) -> dict[str, Any]:
        database = importlib.import_module("APP.backend.database")
        service = importlib.import_module("APP.backend.learning_workshop_service")
        db = database.SessionLocal()
        try:
            user = self._workshop_user(db, external_user_id)
            return service.upsert_knowledge_card(
                db,
                user_id=user.id,
                kp_id=kp_id,
                title=title,
                resource_bundle=resource_bundle,
                source_execution_id=source_execution_id,
            )
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def publish_agent_paper(
        self,
        external_user_id: str,
        *,
        execution_id: str,
        paper: dict[str, Any],
        blueprint: dict[str, Any],
        evidence_pack: dict[str, Any],
    ) -> dict[str, Any]:
        database = importlib.import_module("APP.backend.database")
        service = importlib.import_module("APP.backend.learning_workshop_service")
        db = database.SessionLocal()
        try:
            user = self._workshop_user(db, external_user_id)
            return service.publish_agent_paper(
                db,
                user_id=user.id,
                execution_id=execution_id,
                paper=paper,
                blueprint=blueprint,
                evidence_pack=evidence_pack,
            )
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def list_papers(
        self, external_user_id: str, *, offset: int = 0, limit: int = 50
    ) -> dict[str, Any]:
        database = importlib.import_module("APP.backend.database")
        db = database.SessionLocal()
        try:
            user = self._workshop_user(db, external_user_id)
            query = db.query(database.PaperInstanceRecord).filter_by(learner_id=user.id)
            total = query.count()
            rows = query.order_by(database.PaperInstanceRecord.created_at.desc()).offset(offset).limit(limit).all()
            return {
                "schema_version": "1.0",
                "items": [
                    {
                        "paper_id": row.paper_id,
                        "title": row.title,
                        "status": row.status,
                        "duration_minutes": int(row.duration_minutes or 60),
                        "created_at": row.created_at.isoformat() if row.created_at else None,
                    }
                    for row in rows
                ],
                "total": total,
                "offset": offset,
                "limit": limit,
            }
        finally:
            db.close()

    def get_paper(self, external_user_id: str, paper_id: str) -> dict[str, Any]:
        database = importlib.import_module("APP.backend.database")
        service = importlib.import_module("APP.backend.paper_submission_service")
        db = database.SessionLocal()
        try:
            user = self._workshop_user(db, external_user_id)
            return service.get_owned_paper(db, user.id, paper_id)
        finally:
            db.close()

    def save_paper_answers(
        self, external_user_id: str, paper_id: str, answers: dict[str, str]
    ) -> dict[str, Any]:
        database = importlib.import_module("APP.backend.database")
        service = importlib.import_module("APP.backend.paper_submission_service")
        db = database.SessionLocal()
        try:
            user = self._workshop_user(db, external_user_id)
            return service.save_paper_answers(db, user.id, paper_id, answers)
        finally:
            db.close()

    def submit_paper(
        self, external_user_id: str, paper_id: str, request_id: str
    ) -> dict[str, Any]:
        database = importlib.import_module("APP.backend.database")
        service = importlib.import_module("APP.backend.paper_submission_service")
        db = database.SessionLocal()
        try:
            user = self._workshop_user(db, external_user_id)
            return service.submit_paper(db, user.id, paper_id, request_id)
        finally:
            db.close()

    @staticmethod
    def _json_list(value: str | None) -> list[str]:
        import json

        try:
            payload = json.loads(value or "[]")
        except (TypeError, ValueError):
            return []
        return [str(item) for item in payload] if isinstance(payload, list) else []

    @classmethod
    def _load_completed_question_attempts(
        cls,
        database,
        db,
        user_id: int,
        external_user_id: str,
    ) -> list[dict[str, Any]]:
        """Read only graded question submissions; generated resources are not attempts."""

        attempts: list[dict[str, Any]] = []
        seen_attempt_ids: set[str] = set()
        seen_request_ids: set[str] = set()

        graded_rows = (
            db.query(
                database.LearningAttemptRecord,
                database.LearningAttemptItemRecord,
                database.GradingResultRecord,
            )
            .join(
                database.LearningAttemptItemRecord,
                database.LearningAttemptItemRecord.attempt_id
                == database.LearningAttemptRecord.attempt_id,
            )
            .join(
                database.GradingResultRecord,
                database.GradingResultRecord.attempt_item_id
                == database.LearningAttemptItemRecord.attempt_item_id,
            )
            .filter(
                database.LearningAttemptRecord.learner_id == user_id,
                database.GradingResultRecord.status == "reviewed",
            )
            .order_by(
                database.LearningAttemptItemRecord.created_at.desc(),
                database.GradingResultRecord.version.desc(),
            )
            .limit(200)
            .all()
        )
        for attempt, item, grading in graded_rows:
            source_id = f"HANDOFF_ITEM_{item.attempt_item_id}"
            if source_id in seen_attempt_ids:
                continue
            seen_attempt_ids.add(source_id)
            if attempt.request_id:
                seen_request_ids.add(str(attempt.request_id))
            kp_ids = cls._json_list(grading.kp_ids_json)
            if not kp_ids:
                kp_ids = cls._kp_snapshot_ids(item.kp_snapshot_json)
            if not kp_ids:
                continue
            answered_at = attempt.submitted_at or item.created_at
            attempts.append(
                {
                    "attempt_id": source_id,
                    "user_id": external_user_id,
                    "question_id": item.question_version_id,
                    "submitted_answer": item.submitted_answer,
                    "is_correct": bool(grading.is_correct),
                    "score": grading.score,
                    "max_score": grading.max_score,
                    "answered_at": answered_at.isoformat() if answered_at else None,
                    "kp_ids": kp_ids,
                    "hint_used": bool(item.hint_used),
                    "feedback": grading.error_reason,
                }
            )

        core_rows = (
            db.query(database.LearningQuestionAttempt, database.LearningQuestion)
            .join(
                database.LearningQuestion,
                database.LearningQuestion.question_id
                == database.LearningQuestionAttempt.question_id,
            )
            .filter(database.LearningQuestionAttempt.user_id == user_id)
            .order_by(database.LearningQuestionAttempt.answered_at.desc())
            .limit(100)
            .all()
        )
        for row, question in core_rows:
            if row.request_id and str(row.request_id) in seen_request_ids:
                continue
            source_id = f"HANDOFF_CORE_{row.attempt_id}"
            kp_ids = cls._json_list(question.kp_ids_json)
            if source_id in seen_attempt_ids or not kp_ids:
                continue
            seen_attempt_ids.add(source_id)
            attempts.append(
                {
                    "attempt_id": source_id,
                    "user_id": external_user_id,
                    "question_id": row.question_id,
                    "submitted_answer": cls._json_list(row.submitted_answer_json),
                    "is_correct": bool(row.is_correct),
                    "score": row.score,
                    "answered_at": row.answered_at.isoformat()
                    if row.answered_at
                    else None,
                    "kp_ids": kp_ids,
                    "feedback": row.reason_for_mistake,
                }
            )

        legacy_rows = (
            db.query(database.QuestionAttempt)
            .filter(database.QuestionAttempt.user_id == user_id)
            .order_by(database.QuestionAttempt.created_at.desc())
            .limit(100)
            .all()
        )
        for row in legacy_rows:
            source_id = f"HANDOFF_ATTEMPT_{row.id}"
            kp_ids = cls._json_list(row.kp_ids_json)
            if source_id in seen_attempt_ids or not kp_ids:
                continue
            seen_attempt_ids.add(source_id)
            attempts.append(
                {
                    "attempt_id": source_id,
                    "user_id": external_user_id,
                    "question_id": row.question_id,
                    "submitted_answer": row.answer,
                    "is_correct": bool(row.is_correct),
                    "score": row.score,
                    "answered_at": row.created_at.isoformat()
                    if row.created_at
                    else None,
                    "kp_ids": kp_ids,
                    "feedback": row.feedback,
                }
            )

        attempts.sort(key=lambda item: str(item.get("answered_at") or ""), reverse=True)
        return attempts[:100]

    @staticmethod
    def _kp_snapshot_ids(value: str | None) -> list[str]:
        import json

        try:
            payload = json.loads(value or "[]")
        except (TypeError, ValueError):
            return []
        if not isinstance(payload, list):
            return []
        values = []
        for item in payload:
            kp_id = item.get("kp_id") if isinstance(item, dict) else item
            if kp_id is not None and str(kp_id).strip():
                values.append(str(kp_id).strip())
        return list(dict.fromkeys(values))


def _validate_handoff_root(root: Path) -> None:
    required = (
        root / "APP" / "__init__.py",
        root / "APP" / "backend" / "main.py",
        root / "APP" / "backend" / "database.py",
        root / "APP" / "backend" / "routers" / "vl_chat_routes.py",
    )
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        raise FileNotFoundError("前端后端交接包不完整：" + "; ".join(missing))


def _database_environment(settings: Settings, runtime_root: Path) -> tuple[dict[str, str], str]:
    if settings.mysql_password:
        password = quote_plus(settings.mysql_password)
        username = quote_plus(settings.mysql_user)
        database = settings.backend_handoff_mysql_database
        url = (
            f"mysql+pymysql://{username}:{password}@{settings.mysql_host}:"
            f"{settings.mysql_port}/{database}?charset=utf8mb4"
        )
        return (
            {
                "USE_SQLITE": "false",
                "DATABASE_URL": url,
                "MYSQL_HOST": settings.mysql_host,
                "MYSQL_PORT": str(settings.mysql_port),
                "MYSQL_USER": settings.mysql_user,
                "MYSQL_PASSWORD": settings.mysql_password,
                "MYSQL_DATABASE": database,
            },
            f"mysql:{database}",
        )
    sqlite_path = (runtime_root / "frontend_backend.sqlite3").resolve()
    return (
        {
            "USE_SQLITE": "true",
            "SQLITE_PATH": str(sqlite_path),
            "DATABASE_URL": f"sqlite:///{sqlite_path}",
        },
        "sqlite",
    )


def _model_environment(settings: Settings) -> dict[str, str]:
    """Project only the authoritative main model stack into legacy modules."""

    return {
        "LLM_MODE": "local",
        "LLM_API_KEY": settings.dashscope_api_key or "",
        "LLM_API_BASE_URL": settings.chat_base_url,
        "LLM_API_MODEL": settings.chat_model,
        "PLANNER_EXECUTOR_BASE_URL": settings.chat_base_url,
        "PLANNER_EXECUTOR_MODEL": settings.chat_model,
        "MANAGER_REVIEWER_BASE_URL": settings.chat_base_url,
        "MANAGER_REVIEWER_MODEL": settings.chat_model,
        # The delivered RAG implementation only supports a local model path.
        # Main knowledge tools own remote embeddings, so duplicate loading stays off.
        "EMBEDDING_MODE": "disabled",
        "EMBEDDING_MODEL_ID": settings.embedding_model,
        # Voice migration is intentionally out of scope for this integration phase.
        "VOICE_MODE": "disabled",
    }


def _assert_app_package(root: Path, module: ModuleType) -> None:
    module_path = Path(getattr(module, "__file__", "")).resolve()
    expected = (root / "APP").resolve()
    if expected not in module_path.parents and module_path != expected:
        raise RuntimeError(
            f"Python 包 APP 已由其他路径占用：{module_path}；期望路径：{expected}"
        )


def load_backend_handoff(settings: Settings) -> BackendHandoffRuntime | None:
    """Load the delivered backend once, with isolated persistence and runtime paths."""

    if not settings.backend_handoff_enabled:
        return None
    root = settings.backend_handoff_root.resolve()
    runtime_root = settings.backend_handoff_runtime_root.resolve()
    _validate_handoff_root(root)
    runtime_root.mkdir(parents=True, exist_ok=True)

    knowledge_paths_root = settings.knowledge_handoff_root.resolve()
    knowledge_component = knowledge_paths_root / "知识库管理组件"
    database_env, database_backend = _database_environment(settings, runtime_root)
    environment = {
        **database_env,
        **_model_environment(settings),
        "BACKEND_RUNTIME_ROOT": str(runtime_root),
        "SECRET_KEY": settings.backend_handoff_secret_key,
        "EXA_API_KEY": settings.exa_api_key or "",
        "KNOWLEDGE_ATLAS_DATA_ROOT": str(
            knowledge_component / "data" / "backend_delivery"
        ),
        "KNOWLEDGE_ATLAS_VIDEO_ROOT": str(
            knowledge_paths_root / "bilibili_video_page" / "runtime"
        ),
        "OFFICIAL_EXAM_DATA_DIR": str(
            knowledge_component / "data" / "backend_delivery" / "08_exam_learning_path_2025"
        ),
        "KNOWLEDGE_DATA_SOURCE_PATH": str(knowledge_component / "data"),
        "VDB_STORE_ROOT": str(settings.question_vector_store_root.resolve()),
    }

    with _IMPORT_LOCK:
        root_text = str(root)
        if root_text not in sys.path:
            sys.path.insert(0, root_text)
        with _temporary_environment(environment):
            package = importlib.import_module("APP")
            _assert_app_package(root, package)
            module = importlib.import_module("APP.backend.main")
        delivered_app = getattr(module, "app", None)
        if not isinstance(delivered_app, FastAPI):
            raise TypeError("交接包 APP.backend.main 未导出 FastAPI app")
    return BackendHandoffRuntime(
        app=delivered_app,
        root=root,
        runtime_root=runtime_root,
        database_backend=database_backend,
    )
