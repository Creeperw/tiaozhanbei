"""正式题库发题的版本记录身份约定回归测试。

知识图谱投影写入的 ``QuestionVersionRecord`` 使用带命名空间后缀的
``question_version_id``（``<题目ID>:atlas:<摘要>``），而发题路径原先按裸
``question_id`` 去查这条记录。查不到就当成新题插入 ``version=1``，撞上
``(question_id, version)`` 唯一约束，整个发题请求失败 —— 学员在
「知识点特训」里提交不了当天练习。

这里用真实的表定义（含唯一约束）加内存 SQLite 复现，只把
``database.SessionLocal`` 指向测试库。
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from APP.backend import database
from APP.backend.core_learning_service import resolve_controlled_practice_submission
from competition_app.integrations.backend_handoff import BackendHandoffRuntime


QUESTION_ID = "generated_判断题__3b344ea45af3"
ATLAS_VERSION_ID = f"{QUESTION_ID}:atlas:e720cfa4788d48e1"
ATLAS_SOURCE_KIND = "formal-content:knowledge-atlas-2026-07-18"
NEW_QUESTION_ID = "possible_new__brandnew"


def _issue_payload(question_id: str, stem: str = "特训题干") -> dict:
    return {
        "question_id": question_id,
        "question_type": "true_false",
        "stem": stem,
        "standard_answer": "错误",
        "analysis": "特训解析",
        "options": [],
        "kp_ids": ["KP_1"],
        "kp_names": {"KP_1": "阴阳转化"},
    }


@pytest.fixture()
def handoff(monkeypatch):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    database.Base.metadata.create_all(bind=engine)
    database.ensure_runtime_schema_for(engine)
    session_factory = sessionmaker(
        bind=engine, autoflush=False, expire_on_commit=False
    )
    monkeypatch.setattr(database, "SessionLocal", session_factory)

    with session_factory() as db:
        db.add(database.KnowledgePoint(
            kp_id="KP_1",
            name="阴阳转化",
            source="formal-content:test",
            status="active",
        ))
        # 知识图谱投影写入的版本记录：命名空间 ID，来源带 formal-content: 前缀。
        db.add(database.QuestionVersionRecord(
            question_version_id=ATLAS_VERSION_ID,
            question_id=QUESTION_ID,
            version=1,
            question_type="true_false",
            stem="投影题干",
            answer="正确",
            analysis="投影解析",
            source_kind=ATLAS_SOURCE_KIND,
            status="active",
        ))
        db.add(database.QuestionKPLinkRecord(
            question_version_id=ATLAS_VERSION_ID,
            kp_id="KP_1",
            is_primary=True,
            status="active",
        ))
        db.commit()

    runtime = BackendHandoffRuntime(
        app=FastAPI(),
        root=Path("/tmp"),
        runtime_root=Path("/tmp"),
        database_backend="sqlite",
    )
    return SimpleNamespace(runtime=runtime, session=session_factory)


def test_issue_reuses_the_atlas_version_record(handoff):
    issued = handoff.runtime.issue_formal_practice(
        "learner-1", _issue_payload(QUESTION_ID)
    )

    assert issued["available"] is True
    with handoff.session() as db:
        rows = db.query(database.QuestionVersionRecord).filter_by(
            question_id=QUESTION_ID,
        ).all()
        # 只允许图谱投影写的那一条；再插一条 version=1 就是原缺陷。
        assert len(rows) == 1
        assert rows[0].question_version_id == ATLAS_VERSION_ID
        assert rows[0].stem == "特训题干"
        assert rows[0].answer == "错误"
        # 复用已有记录时不得改写来源标记，否则该题会从每日任务题目池
        # （按 formal-content: 前缀筛选）里消失。
        assert rows[0].source_kind == ATLAS_SOURCE_KIND


def test_issue_keeps_kp_links_on_the_reused_version_id(handoff):
    handoff.runtime.issue_formal_practice("learner-1", _issue_payload(QUESTION_ID))

    with handoff.session() as db:
        links = db.query(database.QuestionKPLinkRecord).filter_by(
            kp_id="KP_1",
        ).all()
        # 链接只能有一套，且必须挂在版本记录自己的 ID 上，否则发题与
        # 每日任务题目池的 join 会断掉。
        assert len(links) == 1
        assert links[0].question_version_id == ATLAS_VERSION_ID


def test_grading_resolves_the_same_version_record_the_issue_path_wrote(handoff):
    handoff.runtime.issue_formal_practice("learner-1", _issue_payload(QUESTION_ID))

    # 发题路径已经把题目投影进 QuestionBankItem，批改侧据此解析版本记录。
    with handoff.session() as db:
        resolved = resolve_controlled_practice_submission(
            db,
            {"question_id": QUESTION_ID, "student_answer": "错误"},
        )

    assert resolved is not None
    # 发题与批改必须落在同一条版本记录上，否则批改会按另一个版本判分。
    assert resolved["question_version_id"] == ATLAS_VERSION_ID


def test_issue_creates_a_record_for_a_question_without_one(handoff):
    issued = handoff.runtime.issue_formal_practice(
        "learner-1", _issue_payload(NEW_QUESTION_ID)
    )

    assert issued["available"] is True
    with handoff.session() as db:
        row = db.query(database.QuestionVersionRecord).filter_by(
            question_id=NEW_QUESTION_ID,
        ).one()
        # 没有既有记录的题仍然新建，且沿用裸 ID 与正式题库来源标记。
        assert row.question_version_id == NEW_QUESTION_ID
        assert row.version == 1
        assert row.source_kind == "formal_question_bank"
        links = db.query(database.QuestionKPLinkRecord).filter_by(
            question_version_id=NEW_QUESTION_ID,
        ).all()
        assert len(links) == 1
