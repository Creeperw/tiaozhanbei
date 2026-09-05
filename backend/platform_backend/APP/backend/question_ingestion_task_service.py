from __future__ import annotations

import json
import uuid
from threading import Event, Thread
from datetime import timedelta
from dataclasses import dataclass
from typing import Any, Callable

from sqlalchemy import or_
from sqlalchemy.orm import Session

from APP.backend.database import QuestionIngestionTaskRecord
from APP.backend.question_ingestion_service import QuestionIngestionService
from APP.backend.pdf_question_ingestion_service import PdfQuestionIngestionService
from APP.backend.time_utils import utc_now


_CLAIM_LEASE = timedelta(minutes=10)
_PDF_CLAIM_LEASE = timedelta(hours=2)


@dataclass(frozen=True)
class QuestionIngestionTaskResult:
    task_id: str
    status: str
    published_question_id: str | None
    outcome_status: str | None
    error_code: str | None = None


class QuestionIngestionTaskService:
    def __init__(
        self,
        ingestion_service_factory: Callable[[], QuestionIngestionService] = QuestionIngestionService,
        pdf_ingestion_service_factory: Callable[[], PdfQuestionIngestionService] = PdfQuestionIngestionService,
        session_factory: Callable[[], Session] | None = None,
    ):
        self._ingestion_service_factory = ingestion_service_factory
        self._pdf_ingestion_service_factory = pdf_ingestion_service_factory
        self._session_factory = session_factory

    def submit(self, db: Session, *, submitted_by_user_id: int, payload: dict[str, Any]) -> QuestionIngestionTaskRecord:
        task = QuestionIngestionTaskRecord(
            task_id=f"QING_{uuid.uuid4().hex[:16]}",
            submitted_by_user_id=submitted_by_user_id,
            payload_json=json.dumps(payload, ensure_ascii=False),
        )
        db.add(task)
        db.commit()
        db.refresh(task)
        return task

    def run_next(self, db: Session) -> QuestionIngestionTaskResult | None:
        now = utc_now()
        candidate = db.query(QuestionIngestionTaskRecord.task_id).filter(
            or_(
                QuestionIngestionTaskRecord.status == "queued",
                (QuestionIngestionTaskRecord.status == "running")
                & (or_(
                    QuestionIngestionTaskRecord.claim_expires_at.is_(None),
                    QuestionIngestionTaskRecord.claim_expires_at < now,
                )),
            )
        ).order_by(QuestionIngestionTaskRecord.created_at, QuestionIngestionTaskRecord.id).first()
        if candidate is None:
            return None
        task_id = candidate.task_id
        task = db.query(QuestionIngestionTaskRecord).filter_by(task_id=task_id).one()
        lease = self._claim_lease(task)
        claimed = db.query(QuestionIngestionTaskRecord).filter(
            QuestionIngestionTaskRecord.task_id == task_id,
            or_(
                QuestionIngestionTaskRecord.status == "queued",
                (QuestionIngestionTaskRecord.status == "running")
                & (or_(
                    QuestionIngestionTaskRecord.claim_expires_at.is_(None),
                    QuestionIngestionTaskRecord.claim_expires_at < now,
                )),
            ),
        ).update(
            {
                "status": "running",
                "started_at": now,
                "claim_expires_at": now + lease,
                "error_code": None,
            },
            synchronize_session=False,
        )
        db.commit()
        if not claimed:
            return None
        return self._run_claimed(db, task_id)

    def run(self, db: Session, task_id: str) -> QuestionIngestionTaskResult:
        now = utc_now()
        task = db.query(QuestionIngestionTaskRecord).filter_by(task_id=task_id).one()
        lease = self._claim_lease(task)
        claimed = db.query(QuestionIngestionTaskRecord).filter_by(task_id=task_id, status="queued").update(
            {"status": "running", "started_at": now, "claim_expires_at": now + lease, "error_code": None},
            synchronize_session=False,
        )
        db.commit()
        if not claimed:
            task = db.query(QuestionIngestionTaskRecord).filter_by(task_id=task_id).one()
            return self._result_from_task(task)
        return self._run_claimed(db, task_id)

    def retry(self, db: Session, task_id: str) -> QuestionIngestionTaskRecord:
        updated = db.query(QuestionIngestionTaskRecord).filter_by(task_id=task_id, status="failed").update(
            {
                "status": "queued",
                "retry_count": QuestionIngestionTaskRecord.retry_count + 1,
                "error_code": None,
                "finished_at": None,
                "claim_expires_at": None,
            },
            synchronize_session=False,
        )
        if not updated:
            db.rollback()
            raise ValueError("Only failed tasks can be retried")
        db.commit()
        return db.query(QuestionIngestionTaskRecord).filter_by(task_id=task_id).one()

    def _run_claimed(self, db: Session, task_id: str) -> QuestionIngestionTaskResult:
        task = db.query(QuestionIngestionTaskRecord).filter_by(task_id=task_id, status="running").one()
        payload = json.loads(task.payload_json)
        db.rollback()
        if payload.get("task_kind") == "pdf":
            return self._run_pdf_claimed(db, task_id, payload)
        try:
            with db.begin():
                task = db.query(QuestionIngestionTaskRecord).filter_by(task_id=task_id, status="running").one()
                result = self._ingestion_service_factory().ingest(db, payload)
                self._complete(task, result)
        except Exception as exc:
            self._fail(db, task_id, exc)
        task = db.query(QuestionIngestionTaskRecord).filter_by(task_id=task_id).one()
        return self._result_from_task(task)

    def _run_pdf_claimed(self, db: Session, task_id: str, payload: dict[str, Any]) -> QuestionIngestionTaskResult:
        stop_renewal = Event()
        renewal_thread = self._start_pdf_renewal(task_id, stop_renewal)
        try:
            db.rollback()
            result = self._pdf_ingestion_service_factory().ingest(db, payload)
            db.commit()
            with db.begin():
                task = db.query(QuestionIngestionTaskRecord).filter_by(task_id=task_id, status="running").one()
                self._complete(task, result)
        except Exception as exc:
            self._fail(db, task_id, exc)
        finally:
            stop_renewal.set()
            if renewal_thread is not None:
                renewal_thread.join(timeout=1)
        task = db.query(QuestionIngestionTaskRecord).filter_by(task_id=task_id).one()
        return self._result_from_task(task)

    def _start_pdf_renewal(self, task_id: str, stop_event: Event) -> Thread | None:
        if self._session_factory is None:
            return None
        thread = Thread(target=self._renew_pdf_claim, args=(task_id, stop_event), daemon=True)
        thread.start()
        return thread

    def _renew_pdf_claim(self, task_id: str, stop_event: Event) -> None:
        while not stop_event.wait(300):
            with self._session_factory() as db:
                updated = db.query(QuestionIngestionTaskRecord).filter_by(
                    task_id=task_id,
                    status="running",
                ).update({"claim_expires_at": utc_now() + _PDF_CLAIM_LEASE}, synchronize_session=False)
                db.commit()
                if not updated:
                    return

    @staticmethod
    def _complete(task: QuestionIngestionTaskRecord, result: dict[str, Any]) -> None:
        task.status = "completed"
        task.published_question_id = str(result.get("question_id") or "") if result.get("stored") else None
        task.result_json = json.dumps(result, ensure_ascii=False)
        task.finished_at = utc_now()
        task.claim_expires_at = None

    @staticmethod
    def _fail(db: Session, task_id: str, exc: Exception) -> None:
        db.rollback()
        task = db.query(QuestionIngestionTaskRecord).filter_by(task_id=task_id).one()
        task.status = "failed"
        task.error_code = type(exc).__name__
        task.finished_at = utc_now()
        task.claim_expires_at = None
        db.commit()

    @staticmethod
    def _claim_lease(task: QuestionIngestionTaskRecord) -> timedelta:
        payload = json.loads(task.payload_json or "{}")
        return _PDF_CLAIM_LEASE if payload.get("task_kind") == "pdf" else _CLAIM_LEASE

    @staticmethod
    def _result_from_task(task: QuestionIngestionTaskRecord) -> QuestionIngestionTaskResult:
        result = json.loads(task.result_json or "{}")
        return QuestionIngestionTaskResult(
            task.task_id,
            task.status,
            task.published_question_id,
            str(result.get("status") or "") or None,
            task.error_code,
        )
