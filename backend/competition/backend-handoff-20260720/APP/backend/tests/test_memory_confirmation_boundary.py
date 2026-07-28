from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from APP.backend import database
from APP.backend.health_memory import (
    resolve_personalization_conflicts,
    retrieve_user_context,
    save_extracted_memories,
)


def session_factory():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    database.Base.metadata.create_all(bind=engine)
    return engine, sessionmaker(bind=engine)


def test_semantic_conflict_resolution_does_not_supersede_memories() -> None:
    engine, factory = session_factory()
    db = factory()
    try:
        db.add_all(
            [
                database.PersonalizationMemory(
                    user_id=1,
                    category="preference",
                    title="学习时长",
                    content="每天最多学习二十分钟。",
                    conflict_key="time_budget:daily",
                ),
                database.PersonalizationMemory(
                    user_id=1,
                    category="preference",
                    title="学习时长",
                    content="每天可以学习一小时。",
                    conflict_key="time_budget:daily",
                ),
            ]
        )
        db.commit()

        changed = resolve_personalization_conflicts(db, 1)
        rows = db.query(database.PersonalizationMemory).order_by(
            database.PersonalizationMemory.id
        ).all()

        assert changed == 0
        assert [row.is_active for row in rows] == [True, True]
        assert [row.superseded_by for row in rows] == [None, None]
    finally:
        db.close()
        engine.dispose()


def test_context_retrieval_is_read_only_for_conflicting_memories() -> None:
    engine, factory = session_factory()
    db = factory()
    try:
        db.add_all(
            [
                database.PersonalizationMemory(
                    user_id=1,
                    category="preference",
                    title="资源偏好",
                    content="喜欢对比表。",
                ),
                database.PersonalizationMemory(
                    user_id=1,
                    category="preference",
                    title="资源偏好",
                    content="本次不要表格。",
                ),
            ]
        )
        db.commit()

        context = retrieve_user_context(db, 1, "这次怎么学习")

        assert "喜欢对比表" in context
        assert "本次不要表格" in context
        assert db.query(database.PersonalizationMemory).filter_by(
            user_id=1, is_active=True
        ).count() == 2
    finally:
        db.close()
        engine.dispose()


def test_agent_extraction_creates_candidates_instead_of_active_memories() -> None:
    engine, factory = session_factory()
    db = factory()
    try:
        persisted = save_extracted_memories(
            db,
            1,
            {
                "important_short_term": [
                    {
                        "title": "每日时间",
                        "content": "以后每天只能学习三十分钟。",
                        "importance": "important",
                    }
                ],
                "non_important_candidates": [
                    {
                        "title": "学习偏好",
                        "content": "喜欢先看对比表再做题。",
                    }
                ],
                "summary": "提取两条待确认信息。",
            },
            source="auto_extract",
            session_id=None,
        )

        assert persisted["important_short_term"]
        assert db.query(database.PersonalizationMemory).count() == 0
        candidates = db.query(database.MemoryCandidate).order_by(
            database.MemoryCandidate.id
        ).all()
        assert len(candidates) == 2
        assert {candidate.status for candidate in candidates} == {"pending"}
        assert any("等待用户确认" in candidate.reason for candidate in candidates)
    finally:
        db.close()
        engine.dispose()