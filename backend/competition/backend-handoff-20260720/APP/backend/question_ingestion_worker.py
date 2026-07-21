from __future__ import annotations

import logging
from threading import Event, Thread
from typing import Callable

from sqlalchemy.orm import sessionmaker

from APP.backend.question_ingestion_task_service import QuestionIngestionTaskService


logger = logging.getLogger(__name__)


class QuestionIngestionWorker:
    def __init__(
        self,
        session_factory: sessionmaker,
        service_factory: Callable[[sessionmaker], QuestionIngestionTaskService] | None = None,
        poll_seconds: float = 0.5,
    ):
        self._session_factory = session_factory
        self._service_factory = service_factory or (
            lambda factory: QuestionIngestionTaskService(session_factory=factory)
        )
        self._poll_seconds = poll_seconds
        self._stop_event = Event()
        self._wake_event = Event()
        self._thread: Thread | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = Thread(target=self._run, name="question-ingestion-worker", daemon=True)
        self._thread.start()

    def wake(self) -> None:
        self._wake_event.set()

    def stop(self) -> None:
        self._stop_event.set()
        self._wake_event.set()
        if self._thread:
            self._thread.join(timeout=5)

    def _run(self) -> None:
        while not self._stop_event.is_set():
            try:
                with self._session_factory() as db:
                    result = self._service_factory(self._session_factory).run_next(db)
            except Exception:
                logger.exception("Question ingestion worker iteration failed")
                result = None
            if result is None:
                self._wake_event.wait(self._poll_seconds)
                self._wake_event.clear()
