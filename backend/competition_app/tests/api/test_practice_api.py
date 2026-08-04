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
            }],
        }

    def ensure_hierarchy(self) -> None:
        return None

    def ensure_questions(self) -> None:
        return None

    def resolve_topic(self, query: str, limit: int = 8) -> list[dict]:
        assert query == "四君子汤"
        return [{"kp_id": "KP_SJZT", "kp": self.kps["KP_SJZT"]}][:limit]


class BroadFormalQuestionStore:
    def __init__(self) -> None:
        self.kps = {
            "KP_1": {"kp_id": "KP_1", "kp_lv3": "知识点一"},
            "KP_2": {"kp_id": "KP_2", "kp_lv3": "知识点二"},
            "KP_CASE": {"kp_id": "KP_CASE", "kp_lv3": "案例辨析"},
        }
        self.questions_by_kp = {
            "KP_1": [{
                "question_id": "FORMAL_Q_1",
                "question_type": "单项选择题",
                "question_content": "第一道客观题",
                "options": [{"option_id": "A", "content": "甲"}],
                "answer": ["A"],
                "kp_ids": ["KP_1"],
            }],
            "KP_2": [{
                "question_id": "FORMAL_Q_2",
                "question_type": "单项选择题",
                "question_content": "第二道客观题",
                "options": [{"option_id": "B", "content": "乙"}],
                "answer": ["B"],
                "kp_ids": ["KP_2"],
            }],
            "KP_CASE": [{
                "question_id": "FORMAL_CASE_1",
                "question_type": "临床案例问答",
                "question_content": "分析案例",
                "answer": "辨证依据",
                "kp_ids": ["KP_CASE"],
            }],
        }

    def ensure_hierarchy(self) -> None:
        return None

    def ensure_questions(self) -> None:
        return None

    def resolve_topic(self, query: str, limit: int = 8) -> list[dict]:
        return []


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
                "request_id": "issued-once",
                "source_scope": "formal_question_bank",
            },
        }

    def issue_cached_public_practice(self, learner_id: str, *, kp_id, mode) -> dict:
        raise AssertionError("formal delivery should be used before the database cache")


class PersonalizedPracticeRuntime(PracticeRuntime):
    def __init__(self, selection_context: dict) -> None:
        super().__init__()
        self.selection_context = selection_context

    def load_practice_selection_context(self, learner_id: str) -> dict:
        return self.selection_context

    def load_learning_context(self, learner_id: str) -> dict:
        return {"user_profile": {"learning_goal": "宽泛考试目标"}}

    def resume_formal_practice_claim(
        self,
        learner_id: str,
        *,
        question_id: str,
        request_id: str,
    ) -> dict | None:
        if question_id != "FORMAL_Q_1":
            return None
        return {
            "available": True,
            "question": {
                "question_id": question_id,
                "question_type": "single_choice",
                "stem": "第一道客观题",
                "options": [],
                "kp_ids": ["KP_1", "KP_2"],
                "kp_names": ["知识点一", "知识点一", "KP_2"],
                "request_id": request_id,
                "source_scope": "formal_question_bank",
            },
        }


class StrictTargetPracticeRuntime(PracticeRuntime):
    def __init__(self) -> None:
        super().__init__()
        self.cached_requests = []

    def issue_cached_public_practice(
        self,
        learner_id: str,
        *,
        kp_id,
        mode,
        difficulty=None,
        difficulty_min=None,
        difficulty_max=None,
    ) -> dict:
        self.cached_requests.append((learner_id, kp_id, mode))
        return {"available": False, "kp_id": kp_id, "question": None}


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
    assert body["question"]["difficulty"] is None
    assert body["question"]["difficulty_source"] is None
    assert body["difficulty_available"] is False
    assert body["available_difficulties"] == []
    assert "answer" not in body["question"]
    assert runtime.issued[0][1]["standard_answer"] == "A"
    assert runtime.issued[0][1]["difficulty"] is None
    assert runtime.issued[0][1]["difficulty_source"] is None


def test_practice_next_does_not_fall_back_to_another_kp_for_explicit_target(tmp_path: Path) -> None:
    container = ApplicationContainer.build(
        Settings(mode="stub"),
        snapshot_root=tmp_path,
        include_backend_handoff=False,
    )
    runtime = StrictTargetPracticeRuntime()
    container.backend_handoff_runtime = runtime
    container.knowledge_backend = SimpleNamespace(map=BroadFormalQuestionStore())

    with TestClient(create_app(container, auth_required=True)) as client:
        registered = client.post(
            "/api/v1/auth/register",
            json={"username": "strict-kp-practice", "password": "correct-horse-2026"},
        )
        response = client.get(
            "/api/v1/workshop/practice/next",
            params={"mode": "case", "scope": "public", "kp_id": "KP_1"},
        )

    assert registered.status_code == 201
    assert response.status_code == 200
    body = response.json()
    assert body["available"] is False
    assert body["kp_id"] == "KP_1"
    assert body["question"] is None
    assert body["difficulty_available"] is False
    assert body["available_difficulties"] == []
    assert runtime.issued == []
    assert len(runtime.cached_requests) == 1
    assert runtime.cached_requests[0][1:] == ("KP_1", "case")


def test_practice_next_collects_broad_candidates_and_skips_attempted_question(tmp_path: Path) -> None:
    container = ApplicationContainer.build(
        Settings(mode="stub"),
        snapshot_root=tmp_path,
        include_backend_handoff=False,
    )
    runtime = PersonalizedPracticeRuntime({
        "attempt_history": {"FORMAL_Q_1": {"attempt_count": 1}},
        "active_claims": [],
    })
    container.backend_handoff_runtime = runtime
    container.knowledge_backend = SimpleNamespace(map=BroadFormalQuestionStore())

    with TestClient(create_app(container, auth_required=True)) as client:
        client.post(
            "/api/v1/auth/register",
            json={"username": "broad-practice", "password": "correct-horse-2026"},
        )
        response = client.get(
            "/api/v1/workshop/practice/next",
            params={"mode": "objective", "scope": "public"},
        )

    assert response.status_code == 200
    assert response.json()["question"]["question_id"] == "FORMAL_Q_2"
    assert response.json()["selection"]["strategy"] == "current_learning_adaptive_v1"


def test_practice_next_prioritizes_current_task_and_keeps_modes_isolated(tmp_path: Path) -> None:
    container = ApplicationContainer.build(
        Settings(mode="stub"),
        snapshot_root=tmp_path,
        include_backend_handoff=False,
    )
    runtime = PersonalizedPracticeRuntime({
        "current_task_kp_ids": ["KP_2"],
        "mastery": {
            "KP_1": {"mastery": 0.1, "confidence": 1.0},
            "KP_2": {"mastery": 0.8, "confidence": 1.0},
        },
        "attempt_history": {},
        "active_claims": [],
    })
    container.backend_handoff_runtime = runtime
    container.knowledge_backend = SimpleNamespace(map=BroadFormalQuestionStore())

    with TestClient(create_app(container, auth_required=True)) as client:
        client.post(
            "/api/v1/auth/register",
            json={"username": "task-practice", "password": "correct-horse-2026"},
        )
        objective = client.get(
            "/api/v1/workshop/practice/next",
            params={"mode": "objective", "scope": "public"},
        )
        case = client.get(
            "/api/v1/workshop/practice/next",
            params={"mode": "case", "scope": "public"},
        )

    assert objective.json()["question"]["question_id"] == "FORMAL_Q_2"
    assert objective.json()["selection"]["reason"] == "current_task"
    assert case.json()["question"]["question_id"] == "FORMAL_CASE_1"


def test_practice_next_resumes_latest_unfinished_claim_on_refresh(tmp_path: Path) -> None:
    container = ApplicationContainer.build(
        Settings(mode="stub"),
        snapshot_root=tmp_path,
        include_backend_handoff=False,
    )
    runtime = PersonalizedPracticeRuntime({
        "attempt_history": {},
        "active_claims": [{"question_id": "FORMAL_Q_1", "request_id": "claim-1"}],
        "latest_active_claim": {"question_id": "FORMAL_Q_1", "request_id": "claim-1"},
    })
    container.backend_handoff_runtime = runtime
    container.knowledge_backend = SimpleNamespace(map=BroadFormalQuestionStore())

    with TestClient(create_app(container, auth_required=True)) as client:
        client.post(
            "/api/v1/auth/register",
            json={"username": "resume-practice", "password": "correct-horse-2026"},
        )
        response = client.get(
            "/api/v1/workshop/practice/next",
            params={"mode": "objective", "scope": "public"},
        )

    assert response.status_code == 200
    assert response.json()["question"]["question_id"] == "FORMAL_Q_1"
    assert response.json()["question"]["request_id"] == "claim-1"
    assert response.json()["question"]["kp_names"] == ["知识点一"]
    assert runtime.issued == []


class LabeledDifficultyQuestionStore:
    """Formal bank with real difficulty labels on some questions."""

    def __init__(self) -> None:
        self.kps = {
            "KP_1": {"kp_id": "KP_1", "kp_lv3": "知识点一"},
            "KP_2": {"kp_id": "KP_2", "kp_lv3": "知识点二"},
        }
        self.questions_by_kp = {
            "KP_1": [
                {
                    "question_id": "FORMAL_D2",
                    "question_type": "单项选择题",
                    "question_content": "难度二题",
                    "options": [{"option_id": "A", "content": "甲"}],
                    "answer": ["A"],
                    "kp_ids": ["KP_1"],
                    "difficulty": 2,
                    "difficulty_source": "curated_question_bank",
                },
                {
                    "question_id": "FORMAL_D3",
                    "question_type": "单项选择题",
                    "question_content": "难度三题",
                    "options": [{"option_id": "B", "content": "乙"}],
                    "answer": ["B"],
                    "kp_ids": ["KP_1"],
                    "difficulty": 3,
                    "difficulty_source": "curated_question_bank",
                },
            ],
            "KP_2": [
                {
                    "question_id": "FORMAL_NOLABEL",
                    "question_type": "单项选择题",
                    "question_content": "未标注难度题",
                    "options": [{"option_id": "C", "content": "丙"}],
                    "answer": ["C"],
                    "kp_ids": ["KP_2"],
                },
            ],
        }

    def ensure_hierarchy(self) -> None:
        return None

    def ensure_questions(self) -> None:
        return None

    def resolve_topic(self, query: str, limit: int = 8) -> list[dict]:
        return [{"kp_id": "KP_1", "kp": self.kps["KP_1"]}][:limit]


def test_practice_next_reports_difficulty_coverage_and_filters_by_level(tmp_path: Path) -> None:
    container = ApplicationContainer.build(
        Settings(mode="stub"),
        snapshot_root=tmp_path,
        include_backend_handoff=False,
    )
    runtime = PersonalizedPracticeRuntime({
        "attempt_history": {},
        "active_claims": [],
    })
    container.backend_handoff_runtime = runtime
    container.knowledge_backend = SimpleNamespace(map=LabeledDifficultyQuestionStore())

    with TestClient(create_app(container, auth_required=True)) as client:
        client.post(
            "/api/v1/auth/register",
            json={"username": "labeled-practice", "password": "correct-horse-2026"},
        )
        response = client.get(
            "/api/v1/workshop/practice/next",
            params={"mode": "objective", "scope": "public", "difficulty": 2},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["difficulty_available"] is True
    assert body["available_difficulties"] == [2, 3]
    assert body["question"]["question_id"] == "FORMAL_D2"
    assert body["question"]["difficulty"] == 2
    assert body["question"]["difficulty_source"] == "curated_question_bank"
    assert runtime.issued[0][1]["difficulty"] == 2
    assert runtime.issued[0][1]["difficulty_source"] == "curated_question_bank"


def test_practice_next_difficulty_range_matches_real_labels_only(tmp_path: Path) -> None:
    container = ApplicationContainer.build(
        Settings(mode="stub"),
        snapshot_root=tmp_path,
        include_backend_handoff=False,
    )
    runtime = PersonalizedPracticeRuntime({
        "attempt_history": {},
        "active_claims": [],
    })
    container.backend_handoff_runtime = runtime
    container.knowledge_backend = SimpleNamespace(map=LabeledDifficultyQuestionStore())

    with TestClient(create_app(container, auth_required=True)) as client:
        client.post(
            "/api/v1/auth/register",
            json={"username": "range-practice", "password": "correct-horse-2026"},
        )
        response = client.get(
            "/api/v1/workshop/practice/next",
            params={"mode": "objective", "scope": "public", "difficulty_min": 3, "difficulty_max": 3},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["question"]["question_id"] == "FORMAL_D3"
    # The unlabelled question never satisfies a difficulty filter.
    assert body["question"]["question_id"] != "FORMAL_NOLABEL"


def test_practice_next_rejects_combined_or_inverted_difficulty(tmp_path: Path) -> None:
    container = ApplicationContainer.build(
        Settings(mode="stub"),
        snapshot_root=tmp_path,
        include_backend_handoff=False,
    )
    runtime = PersonalizedPracticeRuntime({
        "attempt_history": {},
        "active_claims": [],
    })
    container.backend_handoff_runtime = runtime
    container.knowledge_backend = SimpleNamespace(map=LabeledDifficultyQuestionStore())

    with TestClient(create_app(container, auth_required=True)) as client:
        client.post(
            "/api/v1/auth/register",
            json={"username": "invalid-difficulty", "password": "correct-horse-2026"},
        )
        combined = client.get(
            "/api/v1/workshop/practice/next",
            params={"mode": "objective", "scope": "public", "difficulty": 2, "difficulty_min": 1},
        )
        inverted = client.get(
            "/api/v1/workshop/practice/next",
            params={"mode": "objective", "scope": "public", "difficulty_min": 4, "difficulty_max": 2},
        )

    assert combined.status_code == 422
    assert inverted.status_code == 422
