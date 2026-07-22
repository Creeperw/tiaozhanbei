from __future__ import annotations

import json
import uuid
from dataclasses import replace
from datetime import datetime
from random import choice
from typing import Any, Callable

from sqlalchemy.orm import Session, sessionmaker

from APP.backend.case_patient_orchestration import (
    CasePatientOrchestrationRequest,
    orchestrate_case_patient_reply,
)
from APP.backend.case_repository import CaseRepository, CaseSessionView
from APP.backend.case_training_models import CaseDefinitionRecord, CaseSessionRecord, CaseVersionRecord
from APP.backend.case_training_state import CaseTrainingState, transition
from APP.backend.grading_application_service import GradePracticeCommand, apply_practice_grading
from APP.backend.database import LearningActivityRecord
from APP.backend.learning_writeback_service import apply_grading_writeback
from APP.backend.system_data_service import rebuild_system_data


class CaseTrainingStateError(ValueError):
    pass


class CaseTrainingService:
    def __init__(
        self,
        session_factory: sessionmaker,
        *,
        patient_runner: Callable[..., Any],
        patient_auditor: Callable[..., Any],
        grading_runner: Callable[..., dict[str, Any]] | None = None,
        writeback: Callable[..., Any] = apply_grading_writeback,
        clock: Callable[[], datetime] = datetime.utcnow,
    ) -> None:
        self._session_factory = session_factory
        self._repository = CaseRepository(session_factory)
        self._patient_runner = patient_runner
        self._patient_auditor = patient_auditor
        self._grading_runner = grading_runner
        self._writeback = writeback
        self._clock = clock

    def start_session(
        self,
        learner_id: int,
        *,
        selection: str | None = None,
        case_version_id: str | None = None,
        case_type: str | None = None,
        mode: str = "full",
    ) -> dict[str, Any]:
        if mode not in {"full", "diagnosis_only"}:
            raise CaseTrainingStateError("invalid case training mode")
        if selection is None:
            selection = "by_version" if case_version_id else "by_type" if case_type else "random"
        if selection == "by_version":
            if case_type is not None or not case_version_id or not self._case_version_exists(case_version_id):
                raise CaseTrainingStateError("case unavailable")
        elif selection == "by_type":
            if case_version_id is not None or not case_type:
                raise CaseTrainingStateError("case unavailable")
            case_version_id = self._select_case_version(case_type=case_type)
        elif selection == "random":
            if case_version_id is not None or case_type is not None:
                raise CaseTrainingStateError("case unavailable")
            case_version_id = self._select_case_version()
        else:
            raise CaseTrainingStateError("invalid case selection")
        if case_version_id is None:
            raise CaseTrainingStateError("case unavailable")
        now = self._now()
        state = transition(CaseTrainingState(created_at=now), "activate", now=now)
        session_id = f"CS_{uuid.uuid4().hex}"
        self._repository.create_session(
            session_id,
            learner_id,
            case_version_id,
            mode=mode,
            status=state.status,
            expires_at=state.expires_at,
        )
        return self._public_session(self._owned(learner_id, session_id))

    def get_session(self, learner_id: int, session_id: str) -> dict[str, Any] | None:
        view = self._repository.get_owned_session(learner_id, session_id)
        if view is None:
            return None
        view = self._expire_if_needed(learner_id, view)
        return self._public_session(view)

    def ask(self, learner_id: int, session_id: str, message: str) -> dict[str, Any]:
        if not isinstance(message, str) or not message.strip():
            raise CaseTrainingStateError("invalid learner message")
        view = self._expire_if_needed(learner_id, self._owned(learner_id, session_id))
        state = self._state(view)
        try:
            next_state = transition(state, "learner_message", now=self._now())
        except ValueError as exc:
            raise CaseTrainingStateError("invalid case session state") from exc
        orchestration = orchestrate_case_patient_reply(
            CasePatientOrchestrationRequest(
                session_id=view.session_id,
                learner_message=message.strip(),
                conversation=tuple({"role": item.role, "content": item.content} for item in view.messages),
                patient_context=view.patient_context,
            ),
            patient_runner=self._patient_runner,
            auditor=self._patient_auditor,
        )
        if not orchestration.persistable:
            raise CaseTrainingStateError("patient reply unavailable")
        next_sequence = len(view.messages) + 1
        self._repository.append_message(learner_id, session_id, "learner", message.strip(), sequence=next_sequence)
        self._repository.append_message(learner_id, session_id, "patient", orchestration.reply, sequence=next_sequence + 1)
        self._persist_state(learner_id, session_id, next_state)
        return {
            "status": next_state.status,
            "patient_message": {"role": "patient", "content": orchestration.reply},
            "disclaimer": orchestration.disclaimer,
        }

    def request_help(self, learner_id: int, session_id: str, *, help_type: str) -> dict[str, Any]:
        if help_type not in {"hint", "answer"}:
            raise CaseTrainingStateError("invalid help type")
        view = self._expire_if_needed(learner_id, self._owned(learner_id, session_id))
        state = self._state(view)
        try:
            if help_type == "answer":
                next_state = transition(state, "answer_help", now=self._now())
            elif state.status == "help_available" and not state.help_used:
                next_state = replace(state, status="active", help_used=True)
            else:
                raise ValueError("help unavailable")
        except ValueError as exc:
            raise CaseTrainingStateError("help unavailable") from exc
        self._repository.save_help(learner_id, session_id, {"type": help_type})
        self._persist_state(learner_id, session_id, next_state)
        return {
            "status": next_state.status,
            "help_used": next_state.help_used,
            "scoring_enabled": next_state.scoring_enabled,
        }

    def submit(self, learner_id: int, session_id: str, answer: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(answer, dict):
            raise CaseTrainingStateError("invalid case answer")
        view = self._expire_if_needed(learner_id, self._owned(learner_id, session_id))
        state = self._state(view)
        try:
            submitted = transition(state, "submit", now=self._now())
            grading_state = transition(submitted, "start_grading", now=self._now())
        except ValueError as exc:
            raise CaseTrainingStateError("invalid case session state") from exc
        hidden = self._hidden_case_material(view.case_version_id)
        rubric = self._rubric_for_mode(hidden["rubric"], view.mode)
        standard = self._standard_for_mode(hidden["golden_standard"], view.mode)
        if not self._repository.claim_for_grading(
            learner_id,
            session_id,
            learner_messages=grading_state.learner_messages,
            scoring_enabled=grading_state.scoring_enabled,
            help_used=grading_state.help_used,
            expires_at=grading_state.expires_at,
        ):
            raise CaseTrainingStateError("invalid case session state")
        command = GradePracticeCommand(
            learner_id=learner_id,
            source_channel="case_training",
            source_task_id=view.session_id,
            request_id=f"case:{view.session_id}",
            question_version_id=f"case:{view.case_version_id}",
            question_type=f"case_{view.mode}",
            stem=str(view.visible_context.get("chief_complaint") or view.title),
            submitted_answer=json.dumps(answer, ensure_ascii=False, sort_keys=True),
            standard_answer=json.dumps(standard, ensure_ascii=False, sort_keys=True),
            rubric=json.dumps(rubric, ensure_ascii=False, sort_keys=True),
            kp_ids=tuple(str(item) for item in view.visible_context.get("kp_ids", ()) if str(item).strip()),
            difficulty=1,
            duration_sec=None,
            hint_used=view.help_used and view.scoring_enabled,
            profile={},
            memories=(),
            attempt_type="case",
        )
        db = self._session_factory()
        try:
            result = apply_practice_grading(
                db,
                command,
                runner=self._case_grading_runner(standard, rubric),
                writeback=self._writeback if grading_state.scoring_enabled else _skip_writeback,
                atomic=True,
                require_audit=True,
            )
            audit_decision = str((result.audit or {}).get("decision") or "")
            event = {
                "pass": "complete",
                "revise": "request_revision",
                "reject": "reject",
                "needs_human_review": "request_human_review",
                "human_review": "request_human_review",
            }.get(audit_decision, "fail")
            next_state = transition(grading_state, event, now=self._now())
            self._persist_state_in_transaction(db, learner_id, session_id, next_state)
            if next_state.status == "completed":
                score = result.grading_payload.get("score")
                maximum = result.grading_payload.get("max_score")
                db.add(LearningActivityRecord(
                    user_id=learner_id,
                    activity_type="case_training",
                    resource_id=session_id,
                    resource_type="case_session",
                    completion_status="completed",
                    score=float(score) / float(maximum) if isinstance(score, (int, float)) and isinstance(maximum, (int, float)) and maximum else None,
                    payload_json=json.dumps({"task_type": "case_training"}, ensure_ascii=False),
                    created_at=self._now(),
                ))
                rebuild_system_data(db, user_id=learner_id, now=self._now())
            db.commit()
        except CaseTrainingStateError:
            db.rollback()
            self._persist_state(
                learner_id,
                session_id,
                transition(grading_state, "request_human_review", now=self._now()),
            )
            raise
        except Exception:
            db.rollback()
            self._persist_state(
                learner_id,
                session_id,
                transition(grading_state, "fail", now=self._now()),
            )
            raise
        finally:
            db.close()
        return {
            "status": next_state.status,
            "attempt_id": result.attempt_id,
            "attempt_item_id": result.attempt_item_id,
            "grading_artifact_id": result.grading_artifact_id,
            "audit_id": result.audit_id,
            "writeback": _public_writeback(result.writeback),
        }

    def _owned(self, learner_id: int, session_id: str) -> CaseSessionView:
        view = self._repository.get_owned_session(learner_id, session_id)
        if view is None:
            raise CaseTrainingStateError("case session unavailable")
        return view

    def _select_case_version(self, *, case_type: str | None = None) -> str | None:
        candidates = self._repository.available_case_version_ids(case_type=case_type)
        return choice(candidates) if candidates else None

    def _case_version_exists(self, case_version_id: str) -> bool:
        session = self._session_factory()
        try:
            return session.query(CaseVersionRecord).filter_by(case_version_id=case_version_id).one_or_none() is not None
        finally:
            session.close()

    def _hidden_case_material(self, case_version_id: str) -> dict[str, dict]:
        session = self._session_factory()
        try:
            version = session.query(CaseVersionRecord).filter_by(case_version_id=case_version_id).one()
            try:
                golden_standard = json.loads(version.golden_standard_json or "{}")
                rubric = json.loads(version.rubric_json or "{}")
            except (TypeError, ValueError) as exc:
                raise CaseTrainingStateError("case standard unavailable") from exc
            return {"golden_standard": golden_standard, "rubric": rubric}
        finally:
            session.close()

    def _expire_if_needed(self, learner_id: int, view: CaseSessionView) -> CaseSessionView:
        state = self._state(view)
        next_state = transition(state, "expire", now=self._now()) if self._now() >= state.expires_at else state
        if next_state != state:
            self._persist_state(learner_id, view.session_id, next_state)
            return self._owned(learner_id, view.session_id)
        return view

    def _state(self, view: CaseSessionView) -> CaseTrainingState:
        return CaseTrainingState(
            status=view.status,
            learner_messages=view.learner_messages,
            scoring_enabled=view.scoring_enabled,
            help_used=view.help_used,
            created_at=_naive_datetime(view.created_at),
            expires_at=_naive_datetime(view.expires_at),
        )

    def _persist_state(self, learner_id: int, session_id: str, state: CaseTrainingState) -> None:
        self._repository.update_session_state(
            learner_id,
            session_id,
            status=state.status,
            learner_messages=state.learner_messages,
            scoring_enabled=state.scoring_enabled,
            help_used=state.help_used,
            expires_at=state.expires_at,
        )

    def _persist_state_in_transaction(
        self,
        db: Session,
        learner_id: int,
        session_id: str,
        state: CaseTrainingState,
    ) -> None:
        updated = db.query(CaseSessionRecord).filter(
            CaseSessionRecord.session_id == session_id,
            CaseSessionRecord.owner_user_id == learner_id,
            CaseSessionRecord.status == "grading",
        ).update(
            {
                CaseSessionRecord.status: state.status,
                CaseSessionRecord.learner_messages: state.learner_messages,
                CaseSessionRecord.scoring_enabled: int(state.scoring_enabled),
                CaseSessionRecord.help_used: int(state.help_used),
                CaseSessionRecord.expires_at: state.expires_at,
            },
            synchronize_session=False,
        )
        if updated != 1:
            raise CaseTrainingStateError("invalid case session state")

    def _public_session(self, view: CaseSessionView) -> dict[str, Any]:
        visible_context = {
            key: value
            for key, value in view.visible_context.items()
            if key not in {"case_type", "kp_ids"}
        }
        return {
            "session_id": view.session_id,
            "case_version_id": view.case_version_id,
            "title": view.title,
            "mode": view.mode,
            "status": view.status,
            "learner_messages": view.learner_messages,
            "scoring_enabled": view.scoring_enabled,
            "help_used": view.help_used,
            "visible_context": visible_context,
            "messages": [
                {"role": message.role, "sequence": message.sequence, "content": message.content}
                for message in view.messages
            ],
        }

    def _now(self) -> datetime:
        return _naive_datetime(self._clock())

    @staticmethod
    def _standard_for_mode(golden_standard: dict, mode: str) -> dict:
        if not isinstance(golden_standard, dict) or golden_standard.get("schema_version") != "case_standard_v1":
            raise CaseTrainingStateError("case standard unavailable")
        answers = golden_standard.get("answers")
        if not isinstance(answers, dict):
            raise CaseTrainingStateError("case standard unavailable")
        dimensions = answers.get(mode)
        expected = {
            "full": {"syndrome", "formula_name", "formula_composition", "inquiry"},
            "diagnosis_only": {"syndrome", "inquiry"},
        }[mode]
        if not isinstance(dimensions, dict) or set(dimensions) != expected:
            raise CaseTrainingStateError("case standard unavailable")
        normalized = {}
        for name, value in dimensions.items():
            if not isinstance(value, dict) or not value.get("answer"):
                raise CaseTrainingStateError("case standard unavailable")
            normalized[name] = {"answer": value["answer"]}
        return {"schema_version": "case_standard_v1", "mode": mode, "dimensions": normalized}

    def _case_grading_runner(self, standard: dict, rubric: dict) -> Callable[..., dict[str, Any]]:
        runner = self._grading_runner or _default_case_grading

        def grade(**kwargs: Any) -> dict[str, Any]:
            runner_payload = runner(**kwargs)
            submission = kwargs.get("submission")
            if not isinstance(submission, dict):
                raise CaseTrainingStateError("invalid case grading dimensions")
            try:
                answer = json.loads(str(submission.get("submitted_answer") or "{}"))
            except (TypeError, ValueError) as exc:
                raise CaseTrainingStateError("invalid case grading dimensions") from exc
            if not isinstance(answer, dict):
                raise CaseTrainingStateError("invalid case grading dimensions")
            dimension_scores = self._score_case_dimensions(answer, standard, rubric)
            score = sum(item["score"] for item in dimension_scores.values())
            maximum = sum(rubric["dimensions"].values())
            audit = self._case_audit(runner_payload)
            return {
                "score": score,
                "max_score": maximum,
                "is_correct": score == maximum,
                "error_types": [] if score == maximum else ["case_dimension_incomplete"],
                "error_reason": "" if score == maximum else "部分案例评分维度未达标",
                "confidence": float(audit["confidence"]),
                "dimension_scores": dimension_scores,
                "audit": audit,
            }

        return grade

    @staticmethod
    def _score_case_dimensions(answer: dict, standard: dict, rubric: dict) -> dict[str, dict[str, Any]]:
        scores = {}
        for name, maximum in rubric["dimensions"].items():
            expected = standard["dimensions"][name]["answer"]
            matched = _case_answer_matches(answer.get(name), expected)
            scores[name] = {
                "score": maximum if matched else 0,
                "reason": "维度答案符合标准" if matched else "维度答案未满足标准",
                "evidence": ["learner_submission"],
            }
        return scores

    @staticmethod
    def _case_audit(payload: Any) -> dict[str, Any]:
        source = payload.get("audit") if isinstance(payload, dict) else None
        if not isinstance(source, dict):
            return {"decision": "needs_human_review", "reason": "case grading requires an explicit audit", "confidence": 0.0}
        decision = source.get("decision")
        if decision not in {"pass", "revise", "reject", "needs_human_review", "human_review"}:
            return {"decision": "needs_human_review", "reason": "case grading audit is invalid", "confidence": 0.0}
        confidence = source.get("confidence", 0.0)
        if isinstance(confidence, bool) or not isinstance(confidence, (int, float)) or not 0 <= confidence <= 1:
            confidence = 0.0
        return {"decision": decision, "reason": "case grading audit completed", "confidence": confidence}

    @staticmethod
    def _rubric_for_mode(rubric: dict, mode: str) -> dict:
        dimensions = rubric.get(mode)
        if not isinstance(dimensions, dict):
            raise CaseTrainingStateError("case rubric unavailable")
        expected_dimensions = {
            "full": {"syndrome": 50, "formula_name": 15, "formula_composition": 25, "inquiry": 10},
            "diagnosis_only": {"syndrome": 70, "inquiry": 30},
        }[mode]
        if dimensions != expected_dimensions:
            raise CaseTrainingStateError("case rubric unavailable")
        return {
            "rubric_version": "case_training_v1",
            "mode": mode,
            "dimensions": dict(dimensions),
        }


def _case_answer_matches(actual: Any, expected: Any) -> bool:
    if isinstance(expected, list):
        if not isinstance(actual, list):
            return False
        return {_normalize_case_answer(value) for value in actual} >= {
            _normalize_case_answer(value) for value in expected
        }
    return _normalize_case_answer(actual) == _normalize_case_answer(expected)


def _normalize_case_answer(value: Any) -> str:
    return str(value or "").strip().replace(" ", "")


def _naive_datetime(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value.replace(tzinfo=None)


def _default_case_grading(**kwargs: Any) -> dict[str, Any]:
    return {
        "score": 0,
        "max_score": 100,
        "is_correct": False,
        "error_types": ["case_grading_unavailable"],
        "error_reason": "case grading requires an approved runner",
        "confidence": 0,
        "audit": {"decision": "needs_human_review", "confidence": 0},
    }


def _skip_writeback(*args: Any, **kwargs: Any) -> None:
    return None


def _public_writeback(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    return {
        "status": value.status,
        "receipt_id": value.receipt_id,
        "mistake_ids": list(value.mistake_ids),
        "mastery_updates": list(value.mastery_updates),
        "review_task_ids": list(value.review_task_ids),
    }
