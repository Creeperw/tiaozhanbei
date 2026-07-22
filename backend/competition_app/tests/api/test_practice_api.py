from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from competition_app.api.app import create_app
from competition_app.application.container import ApplicationContainer
from competition_app.config import Settings


class FormalQuestionStore:
    def __init__(self) -> None:
        self.kps = {"KP_SJZT": {"kp_id": "KP_SJZT", "kp_lv3": "四君子汤"}}
        self.questions_by_kp = {
            "KP_SJZT": [{
                "question_id": "FORMAL_Q_1",
                "question_type": "单项选择题",
                "question_content": "四君子汤的君药是？",
                "options": [
                    {"option_id": "A", "content": "人参"},
                    {"option_id": "B", "content": "白术"},
                ],
                "answer": ["A"],
                "explanation": "人参益气健脾，为君药。",
                "kp_ids": ["KP_SJZT"],
                "difficulty": 1,
            }],
        }

    def ensure_hierarchy(self) -> None:
        return None

    def ensure_questions(self) -> None:
        return None

    def resolve_topic(self, query: str, limit: int = 8) -> list[dict]:
        assert query == "四君子汤"
        return [{"kp_id": "KP_SJZT", "kp": self.kps["KP_SJZT"]}][:limit]


class PracticeRuntime:
    def __init__(self) -> None:
        self.app = FastAPI()
        self.issued = []

    async def startup(self) -> None:
        return None

    async def shutdown(self) -> None:
        return None

    def issue_personal_practice(self, learner_id: str, *, kp_id, mode) -> dict:
        return {"available": False, "kp_id": kp_id, "question": None}

    def load_learning_context(self, learner_id: str) -> dict:
        return {"user_profile": {"short_term_goal": "四君子汤"}, "question_attempt": []}

    def issue_formal_practice(self, learner_id: str, question: dict) -> dict:
        self.issued.append((learner_id, question))
        return {
            "available": True,
            "kp_id": "KP_SJZT",
            "question": {
                "question_id": question["question_id"],
                "question_type": question["question_type"],
                "stem": question["stem"],
                "options": question["options"],
                "kp_ids": question["kp_ids"],
                "difficulty": question["difficulty"],
                "request_id": "issued-once",
                "source_scope": "formal_question_bank",
            },
        }

    def issue_cached_public_practice(self, learner_id: str, *, kp_id, mode) -> dict:
        raise AssertionError("formal delivery should be used before the database cache")


def test_practice_next_uses_complete_formal_bank_without_exposing_answer(tmp_path: Path) -> None:
    container = ApplicationContainer.build(
        Settings(mode="stub"),
        snapshot_root=tmp_path,
        include_backend_handoff=False,
    )
    runtime = PracticeRuntime()
    container.backend_handoff_runtime = runtime
    container.knowledge_backend = SimpleNamespace(map=FormalQuestionStore())

    with TestClient(create_app(container, auth_required=True)) as client:
        registered = client.post(
            "/api/v1/auth/register",
            json={"username": "formal-practice", "password": "correct-horse-2026"},
        )
        response = client.get(
            "/api/v1/workshop/practice/next",
            params={"mode": "objective", "scope": "public", "topic": "四君子汤"},
        )

    assert registered.status_code == 201
    assert response.status_code == 200
    body = response.json()
    assert body["question"]["question_id"] == "FORMAL_Q_1"
    assert body["question"]["question_type"] == "single_choice"
    assert body["question"]["options"][0]["option_id"] == "A"
    assert body["question"]["source_scope"] == "formal_question_bank"
    assert "answer" not in body["question"]
    assert runtime.issued[0][1]["standard_answer"] == "A"
