import json
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session

from APP.backend import database as m
from APP.backend.current_learning_state_service import project_textbook, read_completion_records


class Atlas:
    def __init__(self):
        self.chapters_by_book = {"中医学基础": [{"sections": [
            {"id": f"SEC_{i}", "name": f"小节{i}", "chapter_id": "CH_1",
             "chapter_name": "章一", "order_index": i} for i in range(20)
        ]}]}
        self.questions_by_kp = {"KP_14": [{"question_id": "Q1"}]}

    def ensure_hierarchy(self):
        pass

    def ensure_questions(self):
        pass

    def section_detail(self, section_id, recommendation_limit=1):
        number = section_id.split("_")[1]
        return {"knowledge_points": [{"kp_id": f"KP_{number}", "name": f"知识{number}"}],
                "resource_state": "recommended", "section_videos": [],
                "recommended_videos": [{"bvid": "BV1", "duration_seconds": 68}]}


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    m.Base.metadata.create_all(engine)
    with Session(engine) as session:
        session.add_all([m.UserModel(id=i, username=f"state{i}", email=f"{i}@local.test",
                                    hashed_password="unused") for i in (1, 2)])
        session.commit()
        yield session
    engine.dispose()


def add_record(db, number, *, user=1, exam="EXAM_A", demo=False, at=1, resource=None, payload=None):
    data = payload if payload is not None else {
        "book": "中医学基础", "exam_track_id": exam, "section_id": f"SEC_{number}",
        "chapter_id": "CH_1", "source": "authorized_demo_progress" if demo else "unspecified",
        "is_demo": demo,
    }
    row = m.LearningActivityRecord(
        user_id=user, activity_type="textbook_section_completed", resource_type="textbook_section",
        resource_id=resource or f"SEC_{number}", payload_json=json.dumps(data),
        completion_status="completed", created_at=datetime(2026, 9, at),
    )
    db.add(row)
    db.commit()
    return row


def test_duplicate_demo_and_late_backfill_do_not_move_forward_position_back(db):
    add_record(db, 13, demo=True)
    add_record(db, 13, at=2)
    add_record(db, 3, demo=True, at=3)
    add_record(db, 19, user=2)
    add_record(db, 18, exam="EXAM_B")
    result = project_textbook("中医学基础", Atlas(), read_completion_records(db, 1, "EXAM_A"), [], limit=3)
    assert result["completed_count"] == 2
    assert result["latest_recorded_completion"]["section_id"] == "SEC_3"
    assert result["furthest_completed_section"]["section_id"] == "SEC_13"
    assert result["next_candidates"][0]["section_id"] == "SEC_14"
    assert result["sections"][0]["section_id"] == "SEC_14"
    assert result["earlier_gaps"][0]["section_id"] == "SEC_0"
    assert result["last_visited_section"] is None
    assert result["completed_sections"][0]["evidence"][0]["verified_assessment"] is False
    assert result["completed_sections"][0]["evidence"][0]["basis"] == "authorized_demo"
    assert result["sections"][0]["knowledge_points"][0]["mastery"] is None


def test_mastery_does_not_imply_completion_and_bad_records_are_not_mapped(db):
    add_record(db, 1, payload=[])
    add_record(db, 2, resource="OLD_TOPIC")
    records = read_completion_records(db, 1, "EXAM_A")
    result = project_textbook("中医学基础", Atlas(), records,
                              [{"kp_id": "KP_14", "score": 1}], section_id="SEC_14")
    assert result["completed_count"] == 0
    assert len(result["unmapped_records"]) == 1
    assert result["sections"][0]["status"] == "no_completion_record"
    assert result["sections"][0]["knowledge_points"][0]["mastery"]["score"] == 1
    with pytest.raises(ValueError):
        project_textbook("中医学基础", Atlas(), records, [], section_id="OTHER")


def test_no_flush_write_or_commit_and_frontend_predicate_matches(db):
    add_record(db, 4)
    db.add(m.LearningActivityRecord(user_id=1, activity_type="pending_should_not_flush"))
    statements = []
    listener = lambda conn, cursor, statement, params, context, many: statements.append(statement)
    event.listen(db.get_bind(), "before_cursor_execute", listener)
    try:
        with patch.object(db, "flush", side_effect=AssertionError("flush forbidden")), \
                patch.object(db, "commit", side_effect=AssertionError("commit forbidden")):
            rows = read_completion_records(db, 1, "EXAM_A", "中医学基础")
            assert len(rows) == 1
            assert rows[0][0].resource_id == "SEC_4"
    finally:
        event.remove(db.get_bind(), "before_cursor_execute", listener)
        db.rollback()
    assert all(statement.lstrip().upper().startswith("SELECT") for statement in statements)


def test_resource_failure_preserves_section_as_unknown_not_zero(db):
    atlas = Atlas()
    atlas.section_detail = lambda *args, **kwargs: (_ for _ in ()).throw(OSError("missing"))
    result = project_textbook("中医学基础", atlas, [], [], limit=1)
    assert result["total_sections"] == 20
    assert result["sections"][0]["resources"]["availability"] == "unavailable"


def test_readonly_handoff_never_creates_identity_and_rejects_exam_mismatch(db):
    from competition_app.integrations.backend_handoff import BackendHandoffRuntime
    from contextlib import contextmanager

    @contextmanager
    def session():
        yield db

    runtime = object.__new__(BackendHandoffRuntime)
    with patch.object(m, "SessionLocal", session):
        assert runtime.load_current_learning_facts("missing", exam_track_id="EXAM_A", books=[])["reason"] == "identity_missing"
        assert db.query(m.ExternalIdentityLink).count() == 0
        db.add(m.ExternalIdentityLink(provider="competition_app", external_user_id="LEARNER", user_id=1))
        db.commit()
        with pytest.raises(PermissionError):
            runtime.load_current_learning_facts("LEARNER", exam_track_id="EXAM_A", books=[])


def test_handoff_reads_current_exam_mastery_and_exact_task_version_without_writes(db):
    from competition_app.integrations.backend_handoff import BackendHandoffRuntime
    from APP.backend import knowledge_atlas_service
    from contextlib import contextmanager

    db.add(m.ExternalIdentityLink(provider="competition_app", external_user_id="LEARNER", user_id=1))
    db.add(m.UserLearningTarget(user_id=1, target_type="exam", exam_track_id="EXAM_A",
                               exam_name_snapshot="考试A", is_active=True))
    for exam, score in [("EXAM_A", .6), ("EXAM_B", .99)]:
        db.add(m.LearnerExamProgressState(progress_state_id=exam, learner_id=1, exam_track_id=exam,
                                         kp_id="KP_14", mastery_score=score, attempt_count=1))
    for version, status in [(1, "completed"), (2, "pending")]:
        db.add(m.DailyTaskItemRecord(task_item_id=f"ITEM{version}", host_task_id="TASK",
                                    host_task_version=version, user_id=1, kp_id="KP_14", status=status))
    db.commit()
    add_record(db, 13, demo=True)

    @contextmanager
    def session():
        yield db

    runtime = object.__new__(BackendHandoffRuntime)
    with patch.object(m, "SessionLocal", session), patch.object(knowledge_atlas_service, "atlas_service", Atlas()), \
            patch.object(db, "flush", side_effect=AssertionError("flush forbidden")), \
            patch.object(db, "commit", side_effect=AssertionError("commit forbidden")):
        state = runtime.load_current_learning_facts(
            "LEARNER", exam_track_id="EXAM_A", books=["中医学基础"], task={"task_id": "TASK", "version": 2})
        assert state["availability"] == "available"
        assert state["books"][0]["completed_count"] == 1
        assert state["books"][0]["sections"][0]["knowledge_points"][0]["mastery"]["score"] == .6
        assert state["task_execution"][0]["task_item_id"] == "ITEM2"
        assert state["task_execution"][0]["status"] == "pending"
        with pytest.raises(ValueError):
            runtime.load_current_learning_facts("LEARNER", exam_track_id="EXAM_A", books=["中医学基础"], book_id="其他教材")