import unittest
from threading import Event
from unittest.mock import Mock

from APP.backend.question_ingestion_worker import QuestionIngestionWorker


class _Session:
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


class QuestionIngestionWorkerTests(unittest.TestCase):
    def test_worker_passes_session_factory_to_service_factory_positionally(self):
        ran = Event()
        service = Mock()
        service.run_next.side_effect = lambda _db: ran.set()
        service_factory = Mock(return_value=service)
        session_factory = Mock(return_value=_Session())
        worker = QuestionIngestionWorker(
            session_factory,
            service_factory=service_factory,
            poll_seconds=0.01,
        )

        worker.start()
        self.assertTrue(ran.wait(timeout=1))
        worker.stop()

        service_factory.assert_called_with(session_factory)


if __name__ == "__main__":
    unittest.main()
