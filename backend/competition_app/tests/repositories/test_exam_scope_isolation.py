from contextlib import contextmanager
from datetime import datetime, timezone

from competition_app.config import Settings
from competition_app.contracts.learning_plan import LearningPlanResult, LongTermPlan
from competition_app.db.bootstrap import DatabaseBootstrap
from competition_app.exam_scope import bind_exam_workspace, reset_exam_workspace
from competition_app.repositories.learning_plan import (
    InMemoryLearningPlanRepository,
    SqlLearningPlanRepository,
)
from competition_app.repositories.runtime import InMemoryConversationRepository


NOW = datetime(2026, 8, 3, tzinfo=timezone.utc)
TCM = "EXAM_2025_TCM_PHYSICIAN"
INTEGRATED_ASSISTANT = "EXAM_2025_INTEGRATED_ASSISTANT"


@contextmanager
def exam_scope(learner_id: str, exam_track_id: str):
    token = bind_exam_workspace(
        learner_id,
        {"exam_track_id": exam_track_id, "exam_name": exam_track_id},
    )
    try:
        yield
    finally:
        reset_exam_workspace(token)


def plan(learner_id: str, marker: str, plan_id: str) -> LearningPlanResult:
    return LearningPlanResult(
        long_term_plan=LongTermPlan(
            plan_id=plan_id,
            learner_id=learner_id,
            content=marker,
            version=1,
            status="active",
            created_at=NOW,
            updated_at=NOW,
        ),
        generated_scope="long_term",
    )


def test_in_memory_plan_and_conversation_state_are_isolated_by_exam() -> None:
    learner_id = "LEARNER_MULTI_EXAM"
    plans = InMemoryLearningPlanRepository()
    conversations = InMemoryConversationRepository()

    with exam_scope(learner_id, TCM):
        plans.save_current(learner_id, plan(learner_id, "中医执业医师", "LONG_TCM"))
        conversations.create_session("CHAT_TCM", learner_id, "中医计划")

    with exam_scope(learner_id, INTEGRATED_ASSISTANT):
        assert plans.get_current(learner_id) is None
        assert conversations.list_sessions(learner_id) == []
        plans.save_current(
            learner_id,
            plan(learner_id, "中西医结合执业助理医师", "LONG_INTEGRATED"),
        )
        conversations.create_session("CHAT_INTEGRATED", learner_id, "中西医计划")

    with exam_scope(learner_id, TCM):
        assert plans.get_current(learner_id).long_term_plan.plan_id == "LONG_TCM"
        assert [item["id"] for item in conversations.list_sessions(learner_id)] == [
            "CHAT_TCM"
        ]


def test_sql_repository_quarantines_legacy_plan_and_migrates_only_by_plan_evidence(
    tmp_path,
) -> None:
    learner_id = "LEARNER_LEGACY_ROUTE"
    settings = Settings(
        mode="stub",
        use_sqlite=True,
        sqlite_path=tmp_path / "exam-scope.sqlite3",
    )
    engine = DatabaseBootstrap(settings).ensure_database()
    repository = SqlLearningPlanRepository(engine)

    # Legacy singleton state predates exam workspaces.
    repository.save_current(
        learner_id,
        plan(
            learner_id,
            "中西医结合执业助理医师资格考试长期规划",
            "LONG_LEGACY_INTEGRATED",
        ),
    )

    with exam_scope(learner_id, TCM):
        # The active target must never relabel the old, contradictory plan.
        assert repository.get_current(learner_id) is None
        repository.save_current(
            learner_id,
            plan(learner_id, "中医执业医师资格考试长期规划", "LONG_TCM_NEW"),
        )

    with exam_scope(learner_id, INTEGRATED_ASSISTANT):
        restored = repository.get_current(learner_id)
        assert restored.long_term_plan.plan_id == "LONG_LEGACY_INTEGRATED"

    with exam_scope(learner_id, TCM):
        restored = repository.get_current(learner_id)
        assert restored.long_term_plan.plan_id == "LONG_TCM_NEW"

    engine.dispose()
