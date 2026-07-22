from __future__ import annotations

import importlib
import json
import os
import re
import sys
import threading
from collections import Counter
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any
from urllib.parse import quote_plus
from uuid import uuid4

from fastapi import FastAPI

from competition_app.config import Settings


_IMPORT_LOCK = threading.RLock()


def _normalize_profile_memory_value(field: str, value: Any) -> Any:
    """Normalize legacy profile prose before it becomes shared agent context."""

    if not isinstance(value, str):
        return value
    text = value.strip()
    if field != "learning_goal" or not text:
        return text
    if "中医" in text and "执业医师" in text:
        return "中医执业医师资格考试"
    if any(token in text for token in ("请结合", "给我制定", "重新制定", "规划")):
        match = re.search(r"(?:我要|我想|目标是|准备)(?:考取|报考|参加)?([^，。；\n]+)", text)
        if match:
            return match.group(1).strip("：:，。； ")
    return text


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

    def record_login_activity(self, external_user_id: str) -> dict[str, Any]:
        database = importlib.import_module("APP.backend.database")
        system_data = importlib.import_module("APP.backend.system_data_service")
        db = database.SessionLocal()
        try:
            user = self._workshop_user(db, external_user_id)
            snapshot = system_data.record_login_activity(db, user_id=user.id)
            db.commit()
            return system_data.system_data_payload(snapshot)
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def get_checkin_status(self, external_user_id: str, *, days: int = 7) -> dict[str, Any]:
        database = importlib.import_module("APP.backend.database")
        checkin = importlib.import_module("APP.backend.checkin_service")
        db = database.SessionLocal()
        try:
            user = self._workshop_user(db, external_user_id)
            return checkin.build_checkin_status(db, user.id, days=days)
        finally:
            db.close()

    def record_daily_checkin(self, external_user_id: str) -> dict[str, Any]:
        database = importlib.import_module("APP.backend.database")
        checkin = importlib.import_module("APP.backend.checkin_service")
        system_data = importlib.import_module("APP.backend.system_data_service")
        db = database.SessionLocal()
        try:
            user = self._workshop_user(db, external_user_id)
            result = checkin.record_daily_checkin(db, user.id)
            snapshot = system_data.rebuild_system_data(db, user_id=user.id)
            db.commit()
            return {**result, "system_data": system_data.system_data_payload(snapshot)}
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

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
                normalized_confirmed = {
                    str(key): _normalize_profile_memory_value(str(key), value)
                    for key, value in confirmed_profile.items()
                    if str(key).strip() and value not in (None, "")
                }
                repaired = {
                    key: value for key, value in normalized_confirmed.items()
                    if value != confirmed_profile.get(key)
                }
                if repaired:
                    profile_service = importlib.import_module("APP.backend.learner_profile_service")
                    profile_service.apply_learner_profile_update(
                        stored_profile,
                        {key: value for key, value in repaired.items() if key in {
                            "display_name", "learner_group", "learning_goal", "time_constraints",
                        }},
                        source="memory_agent",
                    )
                    survey["agent_confirmed_profile"] = normalized_confirmed
                    stored_profile.survey_json = json.dumps(survey, ensure_ascii=False)
                    db.add(database.AgentEvent(
                        user_id=user.id,
                        agent_name="memory_agent",
                        event_type="profile_memory_normalized",
                        input_summary="清理历史画像中的任务指令式文本",
                        output_summary="已归一化：" + "、".join(sorted(repaired)),
                        payload=json.dumps({"fields": sorted(repaired)}, ensure_ascii=False),
                    ))
                confirmed_profile = normalized_confirmed
                user_profile.update(
                    {
                        str(key): value
                        for key, value in confirmed_profile.items()
                        if str(key).strip() and value not in (None, "")
                    }
                )
            if learning_target:
                goal_name = _normalize_profile_memory_value(
                    "learning_goal", str(learning_target.get("exam_name") or "").strip()
                )
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

    def load_review_dashboard(self, external_user_id: str, *, history_limit: int = 100) -> dict[str, Any]:
        """Return user-owned mastery, review state and history for presentation."""

        database = importlib.import_module("APP.backend.database")
        auth = importlib.import_module("APP.backend.auth")
        db = database.SessionLocal()
        try:
            user = auth._get_or_create_host_user(  # noqa: SLF001 - integration boundary
                db, SimpleNamespace(user_id=external_user_id)
            )
            mastery_rows = (
                db.query(database.KnowledgeMasteryState)
                .filter(database.KnowledgeMasteryState.learner_id == user.id)
                .order_by(database.KnowledgeMasteryState.updated_at.desc())
                .all()
            )
            review_rows = (
                db.query(database.LearnerKPReviewState)
                .filter(database.LearnerKPReviewState.learner_id == user.id)
                .all()
            )
            history_rows = (
                db.query(database.MasteryHistoryRecord)
                .filter(database.MasteryHistoryRecord.learner_id == user.id)
                .order_by(database.MasteryHistoryRecord.calculated_at.desc())
                .limit(max(1, min(history_limit, 500)))
                .all()
            )
            task_rows = (
                db.query(database.ReviewTaskRecord)
                .filter(database.ReviewTaskRecord.learner_id == user.id)
                .order_by(database.ReviewTaskRecord.created_at.desc())
                .limit(200)
                .all()
            )
            kp_ids = {
                str(row.kp_id) for row in [*mastery_rows, *review_rows, *history_rows]
                if str(getattr(row, "kp_id", "") or "").strip()
            }
            kp_names = {
                str(row.kp_id): str(row.name or row.kp_id)
                for row in db.query(database.KnowledgePoint).filter(
                    database.KnowledgePoint.kp_id.in_(kp_ids)
                ).all()
            } if kp_ids else {}
            review_by_kp = {str(row.kp_id): row for row in review_rows}
            mastery = []
            for row in mastery_rows:
                kp_id = str(row.kp_id)
                review = review_by_kp.get(kp_id)
                mastery.append({
                    "kp_id": kp_id,
                    "kp_name": kp_names.get(kp_id, kp_id),
                    "mastery_score": float(row.mastery_score or 0.0),
                    "mastery_confidence": float(row.mastery_confidence or 0.0),
                    "attempt_count": int(row.attempt_count or 0),
                    "last_assessed_at": row.last_assessed_at.isoformat() if row.last_assessed_at else None,
                    "review_stage": review.review_stage if review else "new",
                    "retention_estimate": float(review.retention_estimate or 0.0) if review else None,
                    "last_review_at": review.last_review_at.isoformat() if review and review.last_review_at else None,
                    "next_review_at": review.next_review_at.isoformat() if review and review.next_review_at else None,
                    "requires_remediation": bool(review.requires_remediation) if review else False,
                })
            history = [{
                "history_id": row.history_id,
                "kp_id": str(row.kp_id or ""),
                "kp_name": kp_names.get(str(row.kp_id or ""), str(row.kp_id or "")),
                "mastery_score": float(row.mastery_score or 0.0),
                "mastery_confidence": float(row.mastery_confidence or 0.0),
                "trigger_attempt_item_id": row.trigger_attempt_item_id,
                "calculated_at": row.calculated_at.isoformat() if row.calculated_at else None,
            } for row in history_rows]
            tasks = [{
                "review_task_id": row.review_task_id,
                "kp_id": row.primary_kp_id,
                "kp_name": kp_names.get(str(row.primary_kp_id), str(row.primary_kp_id)),
                "review_type": row.review_type,
                "status": row.status,
                "scheduled_at": row.scheduled_at.isoformat() if row.scheduled_at else None,
                "created_at": row.created_at.isoformat() if row.created_at else None,
            } for row in task_rows]
            return {
                "schema_version": "1.0",
                "learner_id": external_user_id,
                "mastery": mastery,
                "mastery_history": history,
                "review_states": [{
                    "kp_id": str(row.kp_id),
                    "kp_name": kp_names.get(str(row.kp_id), str(row.kp_id)),
                    "review_stage": row.review_stage,
                    "retention_estimate": float(row.retention_estimate or 0.0),
                    "last_review_at": row.last_review_at.isoformat() if row.last_review_at else None,
                    "next_review_at": row.next_review_at.isoformat() if row.next_review_at else None,
                    "requires_remediation": bool(row.requires_remediation),
                    "status": row.status,
                } for row in review_rows],
                "review_tasks": tasks,
            }
        finally:
            db.close()

    def load_learning_insights(
        self,
        external_user_id: str,
        *,
        days: int = 30,
        plan_context: dict[str, Any] | None = None,
        run_automation: bool = True,
    ) -> dict[str, Any]:
        """Return the stable learning-insight contract and run idempotent automation."""

        database = importlib.import_module("APP.backend.database")
        governance = importlib.import_module("APP.backend.learning_governance_service")
        db = database.SessionLocal()
        try:
            user = self._workshop_user(db, external_user_id)
            if run_automation:
                cycle = governance.run_automation_cycle(
                    db,
                    user.id,
                    plan_context=plan_context or {},
                    days=days,
                )
                result = cycle["insights"]
                result["automation"] = {
                    "intervention": cycle.get("intervention"),
                    "plan_review": cycle.get("plan_review"),
                }
            else:
                result = governance.build_learning_insights(db, user.id, days=days)
            db.commit()
            return result
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def load_resource_match_report(
        self,
        external_user_id: str,
        *,
        plan_context: dict[str, Any] | None = None,
        limit: int = 12,
    ) -> dict[str, Any]:
        database = importlib.import_module("APP.backend.database")
        governance = importlib.import_module("APP.backend.learning_governance_service")
        db = database.SessionLocal()
        try:
            user = self._workshop_user(db, external_user_id)
            insights = governance.build_learning_insights(db, user.id, days=30)
            report = governance.build_resource_match_report(
                db,
                user.id,
                insights=insights,
                plan_context=plan_context or {},
                limit=limit,
            )
            db.commit()
            return report
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def list_notifications(
        self,
        external_user_id: str,
        *,
        status: str = "all",
        limit: int = 50,
    ) -> dict[str, Any]:
        database = importlib.import_module("APP.backend.database")
        governance = importlib.import_module("APP.backend.learning_governance_service")
        db = database.SessionLocal()
        try:
            user = self._workshop_user(db, external_user_id)
            return governance.list_notifications(db, user.id, status=status, limit=limit)
        finally:
            db.close()

    def update_notification_status(
        self,
        external_user_id: str,
        notification_id: str,
        status: str,
    ) -> dict[str, Any]:
        database = importlib.import_module("APP.backend.database")
        governance = importlib.import_module("APP.backend.learning_governance_service")
        db = database.SessionLocal()
        try:
            user = self._workshop_user(db, external_user_id)
            result = governance.update_notification_status(
                db, user.id, notification_id, status
            )
            db.commit()
            return result
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def get_notification_preferences(self, external_user_id: str) -> dict[str, Any]:
        database = importlib.import_module("APP.backend.database")
        governance = importlib.import_module("APP.backend.learning_governance_service")
        db = database.SessionLocal()
        try:
            user = self._workshop_user(db, external_user_id)
            row = governance.get_notification_preferences(db, user.id)
            result = governance.serialize_notification_preferences(row)
            db.commit()
            return result
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def update_notification_preferences(
        self,
        external_user_id: str,
        updates: dict[str, Any],
    ) -> dict[str, Any]:
        database = importlib.import_module("APP.backend.database")
        governance = importlib.import_module("APP.backend.learning_governance_service")
        db = database.SessionLocal()
        try:
            user = self._workshop_user(db, external_user_id)
            result = governance.update_notification_preferences(db, user.id, updates)
            db.commit()
            return result
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def list_interventions(self, external_user_id: str, *, limit: int = 30) -> dict[str, Any]:
        database = importlib.import_module("APP.backend.database")
        governance = importlib.import_module("APP.backend.learning_governance_service")
        db = database.SessionLocal()
        try:
            user = self._workshop_user(db, external_user_id)
            return governance.list_interventions(db, user.id, limit=limit)
        finally:
            db.close()

    def submit_intervention_feedback(
        self,
        external_user_id: str,
        intervention_id: int,
        action: str,
        reason: str = "",
    ) -> dict[str, Any]:
        database = importlib.import_module("APP.backend.database")
        governance = importlib.import_module("APP.backend.learning_governance_service")
        db = database.SessionLocal()
        try:
            user = self._workshop_user(db, external_user_id)
            result = governance.record_intervention_feedback(
                db, user.id, intervention_id, action, reason
            )
            db.commit()
            return result
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def list_plan_reviews(self, external_user_id: str, *, limit: int = 30) -> dict[str, Any]:
        database = importlib.import_module("APP.backend.database")
        governance = importlib.import_module("APP.backend.learning_governance_service")
        db = database.SessionLocal()
        try:
            user = self._workshop_user(db, external_user_id)
            return governance.list_plan_reviews(db, user.id, limit=limit)
        finally:
            db.close()

    def run_plan_review(
        self,
        external_user_id: str,
        *,
        plan_context: dict[str, Any] | None = None,
        trigger_type: str = "manual",
    ) -> dict[str, Any]:
        database = importlib.import_module("APP.backend.database")
        governance = importlib.import_module("APP.backend.learning_governance_service")
        db = database.SessionLocal()
        try:
            user = self._workshop_user(db, external_user_id)
            insights = governance.build_learning_insights(db, user.id, days=30)
            result = governance.run_plan_review(
                db,
                user.id,
                insights=insights,
                plan_context=plan_context or {},
                trigger_type=trigger_type,
            )
            db.commit()
            return result
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def decide_plan_review(
        self,
        external_user_id: str,
        review_id: str,
        decision: str,
    ) -> dict[str, Any]:
        database = importlib.import_module("APP.backend.database")
        governance = importlib.import_module("APP.backend.learning_governance_service")
        db = database.SessionLocal()
        try:
            user = self._workshop_user(db, external_user_id)
            result = governance.decide_plan_review(
                db, user.id, review_id, decision
            )
            db.commit()
            return result
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def load_learning_activity_summary(
        self,
        external_user_id: str,
        *,
        days: int = 30,
        recent_limit: int = 20,
    ) -> dict[str, Any]:
        """Return user-isolated behavior metrics and their observable source rows."""

        if days not in {7, 30, 90}:
            raise ValueError("days must be one of: 7, 30, 90")
        if recent_limit < 1 or recent_limit > 100:
            raise ValueError("recent_limit must be between 1 and 100")

        database = importlib.import_module("APP.backend.database")
        system_data = importlib.import_module("APP.backend.system_data_service")
        time_utils = importlib.import_module("APP.backend.time_utils")
        db = database.SessionLocal()
        try:
            user = self._workshop_user(db, external_user_id)
            calculated_at = time_utils.utc_now()
            window_start = calculated_at - timedelta(days=days)
            snapshot = system_data.rebuild_system_data(
                db,
                user_id=user.id,
                now=calculated_at,
            )
            trends = system_data.build_learning_trends(
                db,
                user_id=user.id,
                days=days,
                now=calculated_at,
            )
            tasks = (
                db.query(database.LearningTask)
                .filter(
                    database.LearningTask.user_id == user.id,
                    database.LearningTask.created_at >= window_start,
                    database.LearningTask.created_at <= calculated_at,
                )
                .all()
            )
            focus_sessions = (
                db.query(database.LearningFocusSession)
                .filter(
                    database.LearningFocusSession.user_id == user.id,
                    database.LearningFocusSession.started_at >= window_start,
                    database.LearningFocusSession.started_at <= calculated_at,
                )
                .all()
            )
            activities = (
                db.query(database.LearningActivityRecord)
                .filter(
                    database.LearningActivityRecord.user_id == user.id,
                    database.LearningActivityRecord.created_at >= window_start,
                    database.LearningActivityRecord.created_at <= calculated_at,
                )
                .order_by(
                    database.LearningActivityRecord.created_at.desc(),
                    database.LearningActivityRecord.id.desc(),
                )
                .all()
            )
            task_statuses = Counter(str(row.status or "unknown") for row in tasks)
            focus_statuses = Counter(str(row.status or "unknown") for row in focus_sessions)
            activity_types = Counter(str(row.activity_type or "unknown") for row in activities)
            db.commit()
            return {
                "schema_version": "1.0",
                "window_days": days,
                "calculated_at": system_data.system_data_payload(snapshot).get("calculated_at"),
                "system_data": system_data.system_data_payload(snapshot),
                "trends": trends,
                "counters": {
                    "learning_tasks": {
                        "total": len(tasks),
                        "by_status": dict(sorted(task_statuses.items())),
                    },
                    "focus_sessions": {
                        "total": len(focus_sessions),
                        "active_seconds": sum(max(0, int(row.active_seconds or 0)) for row in focus_sessions),
                        "by_status": dict(sorted(focus_statuses.items())),
                    },
                    "activities": {
                        "total": len(activities),
                        "by_type": dict(sorted(activity_types.items())),
                    },
                },
                "recent_activities": [
                    {
                        "activity_id": row.id,
                        "activity_type": row.activity_type,
                        "resource_type": row.resource_type,
                        "resource_id": row.resource_id,
                        "completion_status": row.completion_status,
                        "score": float(row.score) if row.score is not None else None,
                        "duration_minutes": int(row.duration_minutes or 0),
                        "created_at": row.created_at.isoformat() if row.created_at else None,
                    }
                    for row in activities[:recent_limit]
                ],
                "collection": {
                    "task_completion": "learning_tasks",
                    "focus_time": "learning_focus_sessions heartbeat",
                    "resource_click": "dashboard recommendation view and click",
                    "graded_learning": "question, paper and case submission activities",
                },
            }
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def issue_personal_practice(
        self,
        external_user_id: str,
        *,
        kp_id: str | None,
        mode: str,
    ) -> dict[str, Any]:
        """Issue an owned uploaded question through the existing controlled route."""

        database = importlib.import_module("APP.backend.database")
        routes = importlib.import_module("APP.backend.routers.training_routes")
        db = database.SessionLocal()
        try:
            user = self._workshop_user(db, external_user_id)
            return routes.next_practice_question(
                kp_id=kp_id,
                scope="user",
                mode=mode,
                current_user=user,
                db=db,
            )
        finally:
            db.close()

    def issue_cached_public_practice(
        self,
        external_user_id: str,
        *,
        kp_id: str | None,
        mode: str,
    ) -> dict[str, Any]:
        """Fallback for stub mode and already projected formal questions."""

        database = importlib.import_module("APP.backend.database")
        routes = importlib.import_module("APP.backend.routers.training_routes")
        db = database.SessionLocal()
        try:
            user = self._workshop_user(db, external_user_id)
            return routes.next_practice_question(
                kp_id=kp_id,
                scope="public",
                mode=mode,
                current_user=user,
                db=db,
            )
        finally:
            db.close()

    def issue_formal_practice(
        self,
        external_user_id: str,
        question: dict[str, Any],
    ) -> dict[str, Any]:
        """Persist one server-owned authority snapshot and issue a one-use claim.

        The 93k public bank remains read-only in the knowledge delivery. Only the
        selected question is projected into the learning database so grading,
        mistakes, review scheduling and variation generation share one chain.
        """

        database = importlib.import_module("APP.backend.database")
        db = database.SessionLocal()
        try:
            user = self._workshop_user(db, external_user_id)
            question_id = str(question.get("question_id") or "").strip()
            stem = str(question.get("stem") or "").strip()
            answer = str(question.get("standard_answer") or "").strip()
            question_type = str(question.get("question_type") or "short_answer").strip()
            analysis = str(question.get("analysis") or "").strip()
            options = question.get("options") or []
            kp_ids = list(dict.fromkeys(
                str(value).strip()
                for value in question.get("kp_ids") or []
                if str(value).strip()
            ))
            if not question_id or not stem or not answer or not kp_ids:
                raise ValueError("formal practice question is incomplete")
            try:
                difficulty = max(1, min(5, int(float(question.get("difficulty") or 2))))
            except (TypeError, ValueError):
                difficulty = 2
            kp_names = {
                str(key): str(value)
                for key, value in (question.get("kp_names") or {}).items()
                if str(key).strip() and str(value).strip()
            }
            for kp_id in kp_ids:
                row = db.query(database.KnowledgePoint).filter_by(kp_id=kp_id).one_or_none()
                if row is None:
                    db.add(database.KnowledgePoint(
                        kp_id=kp_id,
                        name=kp_names.get(kp_id, kp_id),
                        source="formal_question_bank",
                        status="active",
                    ))
                elif row.status != "active":
                    row.status = "active"

                core_kp = db.query(database.LearningKnowledgePoint).filter_by(kp_id=kp_id).one_or_none()
                if core_kp is None:
                    db.add(database.LearningKnowledgePoint(kp_id=kp_id))

            bank = db.query(database.QuestionBankItem).filter_by(question_id=question_id).one_or_none()
            if bank is None:
                bank = database.QuestionBankItem(question_id=question_id)
                db.add(bank)
            bank.stem = stem
            bank.answer = answer
            bank.analysis = analysis
            bank.kp_ids_json = json.dumps(kp_ids, ensure_ascii=False)
            bank.question_type = question_type
            bank.difficulty = difficulty
            bank.quality_score = 1.0
            bank.source = "formal_question_bank"
            bank.status = "active"

            core = db.query(database.LearningQuestion).filter_by(question_id=question_id).one_or_none()
            if core is None:
                core = database.LearningQuestion(question_id=question_id)
                db.add(core)
            core.question_type = question_type
            core.question_content = stem
            core.options_json = json.dumps(options, ensure_ascii=False)
            raw_answer = question.get("raw_answer")
            core.answer_json = json.dumps(
                raw_answer if isinstance(raw_answer, list) else [answer],
                ensure_ascii=False,
            )
            core.explanation = analysis
            core.difficulty = difficulty
            core.kp_ids_json = json.dumps(kp_ids, ensure_ascii=False)

            version = db.query(database.QuestionVersionRecord).filter_by(
                question_version_id=question_id,
            ).one_or_none()
            if version is None:
                version = database.QuestionVersionRecord(
                    question_version_id=question_id,
                    question_id=question_id,
                    version=1,
                )
                db.add(version)
            version.question_type = question_type
            version.stem = stem
            version.answer = answer
            version.analysis = analysis
            version.standard_difficulty = difficulty
            version.source_kind = "formal_question_bank"
            version.status = "active"
            db.flush()
            existing_links = {
                row.kp_id: row
                for row in db.query(database.QuestionKPLinkRecord).filter_by(
                    question_version_id=question_id,
                ).all()
            }
            for index, kp_id in enumerate(kp_ids):
                link = existing_links.get(kp_id)
                if link is None:
                    db.add(database.QuestionKPLinkRecord(
                        question_version_id=question_id,
                        kp_id=kp_id,
                        is_primary=index == 0,
                        status="active",
                    ))
                else:
                    link.status = "active"
                    link.is_primary = index == 0

            request_id = str(uuid4())
            db.add(database.CorePracticeSubmissionClaim(
                user_id=user.id,
                request_id=request_id,
                question_id=question_id,
            ))
            db.commit()
            return {
                "available": True,
                "kp_id": kp_ids[0] if len(kp_ids) == 1 else None,
                "question": {
                    "question_id": question_id,
                    "question_type": question_type,
                    "stem": stem,
                    "options": options,
                    "kp_ids": kp_ids,
                    "difficulty": difficulty,
                    "request_id": request_id,
                    "source_scope": "formal_question_bank",
                },
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

        allowed_fields = {
            "display_name", "learner_group", "learning_goal",
            "learning_background", "time_constraints",
        }
        normalized = {
            str(key): _normalize_profile_memory_value(str(key), value)
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
                key: value for key, value in confirmed.items()
                if key in {"display_name", "learner_group", "learning_goal", "time_constraints"}
            }
            if mapped:
                profile_service.apply_learner_profile_update(
                    profile, mapped, source="memory_agent"
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
                    agent_name="memory_agent",
                    event_type="profile_confirmed_writeback",
                    input_summary="记忆管理智能体提炼用户已明确表达的画像信息",
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

    def extract_and_update_learning_profile(
        self,
        external_user_id: str,
        user_text: str,
        execution_id: str | None = None,
    ) -> dict[str, Any]:
        """Let Memory Agent distill explicit stable profile facts before planning."""

        text_value = str(user_text or "").strip()
        markers = (
            "我是", "我叫", "昵称", "专业", "零基础", "学过", "基础",
            "想考", "准备考", "目标", "每天", "每周", "小时", "分钟",
        )
        if not text_value or not any(marker in text_value for marker in markers):
            return {}
        try:
            health_llm = importlib.import_module("APP.backend.health_llm")
            health_utils = importlib.import_module("APP.backend.health_utils")
            client = health_llm.build_llm_client("manager")
            response = client.chat(
                [
                    {
                        "role": "system",
                        "content": (
                            "你是记忆管理智能体。只提炼用户在本段文字中明确陈述、可供后续学习系统复用的稳定画像事实。"
                            "不得猜测，不得把用户的任务指令整句保存为学习目标。只返回JSON："
                            "display_name、learner_group、learning_goal、learning_background、time_constraints、"
                            "confidence_by_field。没有明确事实的字段返回空字符串。"
                            "learning_background应压缩为基础程度、专业背景和已学内容；learning_goal只保留目标考试或学习方向。"
                        ),
                    },
                    {"role": "user", "content": text_value},
                ],
                temperature=0.0,
                max_tokens=700,
                extra_body={"response_format": {"type": "json_object"}},
            )
            parsed = health_utils.extract_json_object(response)
            confidences = parsed.get("confidence_by_field")
            if not isinstance(confidences, dict):
                confidences = {}
            allowed = {
                "display_name", "learner_group", "learning_goal",
                "learning_background", "time_constraints",
            }
            updates = {}
            for key in allowed:
                value = parsed.get(key)
                confidence = confidences.get(key, 0.0)
                if (
                    isinstance(value, str) and value.strip()
                    and isinstance(confidence, (int, float)) and not isinstance(confidence, bool)
                    and float(confidence) >= 0.75
                ):
                    updates[key] = value.strip()[:1000]
            if not updates:
                return {}
            return self.update_learning_profile(
                external_user_id, updates, execution_id
            )
        except Exception:
            # Profile extraction enriches context but must not make the user's
            # primary workflow unavailable when the model is temporarily down.
            return {}

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

    def set_paper_timer_paused(
        self, external_user_id: str, paper_id: str, *, paused: bool
    ) -> dict[str, Any]:
        database = importlib.import_module("APP.backend.database")
        service = importlib.import_module("APP.backend.paper_submission_service")
        db = database.SessionLocal()
        try:
            user = self._workshop_user(db, external_user_id)
            operation = service.pause_paper_timer if paused else service.resume_paper_timer
            return operation(db, user.id, paper_id)
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
                    "completion_status": "completed",
                    "grading_status": "reviewed",
                    "audit_decision": "pass",
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
                    "completion_status": "completed",
                    "grading_status": "accepted",
                    "audit_decision": "pass",
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
                    "completion_status": "completed",
                    "grading_status": "accepted",
                    "audit_decision": "pass",
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
