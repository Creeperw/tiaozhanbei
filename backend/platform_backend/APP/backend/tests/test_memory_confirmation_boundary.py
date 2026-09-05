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

        # 查询需与记忆内容相关（混合检索只注入相关记忆）；两条记忆标题均为"资源偏好"
        context = retrieve_user_context(db, 1, "资源偏好")

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


def test_auto_confirmed_important_memory_writes_active_directly() -> None:
    engine, factory = session_factory()
    db = factory()
    try:
        persisted = save_extracted_memories(
            db,
            1,
            {
                "important_short_term": [
                    {
                        "title": "学习方向",
                        "content": "用户明确将学习重点转向中医内科。",
                        "importance": "important",
                        "requires_confirmation": False,
                        "category": "long_term",
                    }
                ],
                "non_important_candidates": [],
                "summary": "提取一条确定性长期偏好。",
            },
            source="auto_extract",
            session_id=None,
        )

        assert len(persisted["auto_confirmed"]) == 1
        assert persisted["auto_confirmed"][0]["id"]
        memory = db.query(database.PersonalizationMemory).one()
        assert memory.is_active is True
        assert memory.category == "long_term"
        assert memory.importance == "important"
        assert memory.source == "auto_extract"
        assert memory.expires_at is None
        assert "中医内科" in memory.content
        assert db.query(database.MemoryCandidate).count() == 0
    finally:
        db.close()
        engine.dispose()


def test_auto_confirmed_skips_exact_duplicate_content() -> None:
    engine, factory = session_factory()
    db = factory()
    try:
        db.add(
            database.PersonalizationMemory(
                user_id=1,
                category="long_term",
                title="旧标题",
                content="用户每天学习 60 分钟。",
                source="candidate_promote",
            )
        )
        db.commit()

        persisted = save_extracted_memories(
            db,
            1,
            {
                "important_short_term": [
                    {
                        "title": "学习时长",
                        "content": "用户每天学习 60 分钟。",
                        "requires_confirmation": False,
                        "category": "long_term",
                    }
                ],
                "summary": "重复内容不应重复沉淀。",
            },
            source="auto_extract",
            session_id=None,
        )

        assert len(persisted["auto_confirmed"]) == 1
        assert persisted["auto_confirmed"][0]["duplicate"] is True
        rows = db.query(database.PersonalizationMemory).all()
        assert len(rows) == 1  # 复用既有记忆，不新增
        assert rows[0].title == "学习时长"  # 标题被刷新
        assert db.query(database.MemoryCandidate).count() == 0
    finally:
        db.close()
        engine.dispose()


def test_conflicting_update_falls_back_to_candidates() -> None:
    engine, factory = session_factory()
    db = factory()
    try:
        db.add(
            database.PersonalizationMemory(
                user_id=1,
                category="long_term",
                title="备考安排",
                content="用户正在备考执业医师考试。",
                source="candidate_promote",
            )
        )
        db.commit()

        persisted = save_extracted_memories(
            db,
            1,
            {
                "important_short_term": [
                    {
                        "title": "备考安排更新",
                        "content": "用户决定不再备考，把时间让给工作。",
                        "requires_confirmation": False,
                        "category": "long_term",
                    }
                ],
                "summary": "更新类信息需要确认。",
            },
            source="auto_extract",
            session_id=None,
        )

        # 与既有正式记忆语义冲突（同主题不同值）→ 不直接沉淀，退回候选池
        assert persisted["auto_confirmed"] == []
        assert db.query(database.PersonalizationMemory).count() == 1
        candidate = db.query(database.MemoryCandidate).one()
        assert candidate.status == "pending"
        assert "不再备考" in candidate.content
    finally:
        db.close()
        engine.dispose()