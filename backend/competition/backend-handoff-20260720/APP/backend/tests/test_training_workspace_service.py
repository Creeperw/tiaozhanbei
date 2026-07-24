import json
import sqlite3
import tempfile
import time
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import Mock, patch

from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from APP.backend import database
from APP.backend.agent_contracts import (
    DiagnosisReport,
    EvidenceItem,
    EvidencePack,
    ExpertArtifact,
    LearnerContextBrief,
)
from APP.backend.mistake_variation_service import MistakeVariationNotFound
from APP.backend.question_repository import QuestionVersionView
from APP.backend.variation_repository import PublishedVariation, VariationRepository
from APP.backend.tool_runtime import ToolInvocationResult, build_default_tool_runtime
from APP.backend.training_workspace_service import (
    InvalidTrainingTaskRequest,
    TrainingTaskExecutionError,
    create_training_task,
    get_training_task_result,
    get_training_workspace_modules,
)


class DeterministicGenerationRuntime:
    _TOOL_NAMES = {
        "build_learner_context_brief",
        "build_diagnosis_snapshot",
        "build_evidence_pack",
        "generate_handout",
        "generate_knowledge_card",
        "audit_artifact",
    }

    def __init__(self, failure_tool: str | None = None, empty_result_tool: str | None = None):
        self.failure_tool = failure_tool
        self.empty_result_tool = empty_result_tool
        self.calls: list[tuple[str, str, dict[str, Any]]] = []

    def tool_names(self) -> set[str]:
        return set(self._TOOL_NAMES)

    def execute(self, tool_name: str, agent_name: str, **kwargs: Any) -> ToolInvocationResult:
        self.calls.append((tool_name, agent_name, kwargs))
        if tool_name == self.failure_tool:
            return ToolInvocationResult(
                tool_name=tool_name,
                agent_name=agent_name,
                status="failed",
                input_summary=f"{tool_name} input",
                output_summary=f"{tool_name} failed",
                error=f"fake_failure:{tool_name}",
            )
        if tool_name == self.empty_result_tool:
            return ToolInvocationResult(
                tool_name=tool_name,
                agent_name=agent_name,
                status="success",
                input_summary=f"{tool_name} input",
                output_summary=f"{tool_name} returned no result",
            )

        if tool_name == "build_learner_context_brief":
            result: Any = LearnerContextBrief(
                learner_id=str(kwargs["user_id"]),
                learner_group="deterministic-test-group",
                goal="deterministic training goal",
                source_scope="deterministic_runtime",
                source_id="learner-context:1",
                kp_ids=["kp:formal:001"],
                confidence=0.9,
            )
        elif tool_name == "build_diagnosis_snapshot":
            result = DiagnosisReport(
                diagnosis_id="diagnosis:1",
                stage_id="T3",
                stage_name="deterministic-test-stage",
                summary="deterministic diagnosis",
                source_scope="diagnosis_agent",
                source_id="diagnosis:1",
                kp_ids=["kp:formal:001"],
                confidence=0.9,
            )
        elif tool_name == "build_evidence_pack":
            result: Any = EvidencePack(
                source_scope="knowledge_base_agent",
                source_id="EP_fake",
                items=[
                    EvidenceItem(
                        source_scope="demo",
                        source_id="demo-source",
                        summary="四君子汤证型依据",
                        kp_ids=list(kwargs["learner_context"].kp_ids),
                        confidence=0.9,
                    )
                ],
                kp_ids=list(kwargs["learner_context"].kp_ids),
                resolved_kp_ids=list(kwargs["learner_context"].kp_ids),
                confidence=0.9,
            )
        elif tool_name in {"generate_handout", "generate_knowledge_card"}:
            artifact_type = "handout" if tool_name == "generate_handout" else "knowledge_card"
            result = ExpertArtifact(
                artifact_type=artifact_type,
                title=f"fake {artifact_type}",
                content={"body": "generated", "kp_ids": ["kp:formal:001"]},
                source_scope=agent_name,
                source_id=f"artifact:{artifact_type}",
                kp_ids=["kp:formal:001"],
                confidence=0.9,
            )
        elif tool_name == "audit_artifact":
            result = {
                "decision": "pass",
                "reason": "fake audit passed",
                "source_scope": "audit_agent",
                "source_id": kwargs["artifact"].source_id,
                "kp_ids": ["kp:formal:001"],
                "confidence": 0.9,
            }
        else:
            raise AssertionError(f"unexpected tool: {tool_name}")

        return ToolInvocationResult(
            tool_name=tool_name,
            agent_name=agent_name,
            status="success",
            result=result,
            input_summary=f"{tool_name} input",
            output_summary=f"{tool_name} output",
        )


class TrainingWorkspacePersistenceTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        database.Base.metadata.create_all(bind=self.engine)
        self.Session = sessionmaker(bind=self.engine)

    def tearDown(self):
        self.engine.dispose()

    def test_persists_training_task_record_and_reads_it_by_task_id(self):
        session = self.Session()
        try:
            record = database.TrainingTaskRecord(
                task_id="TT_test_001",
                user_id=1,
                task_type="practice_grading",
                title="练习批改",
                status="completed",
                artifact_type="grading_result",
                artifact_json="{}",
                evidence_pack_id="",
                audit_json="{}",
                trace_json="[]",
                learning_updates_json="{}",
            )
            session.add(record)
            session.commit()

            persisted = (
                session.query(database.TrainingTaskRecord)
                .filter_by(task_id="TT_test_001")
                .one()
            )

            self.assertEqual(persisted.task_id, "TT_test_001")
            self.assertEqual(persisted.task_type, "practice_grading")
            self.assertEqual(persisted.artifact_json, "{}")
        finally:
            session.close()

    def test_runtime_schema_adds_evidence_snapshot_to_existing_training_task_table(self):
        engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        try:
            with engine.begin() as connection:
                connection.execute(text("""
                    CREATE TABLE training_task_records (
                        id INTEGER PRIMARY KEY,
                        task_id VARCHAR(120),
                        user_id INTEGER,
                        task_type VARCHAR(80),
                        title VARCHAR(200),
                        status VARCHAR(50),
                        artifact_type VARCHAR(80),
                        artifact_json TEXT,
                        evidence_pack_id VARCHAR(120),
                        audit_json TEXT,
                        trace_json TEXT,
                        learning_updates_json TEXT,
                        created_at DATETIME,
                        updated_at DATETIME
                    )
                """))
                connection.execute(text("""
                    INSERT INTO training_task_records (task_id, user_id, task_type)
                    VALUES ('TT_legacy', 1, 'practice_grading')
                """))
            database.Base.metadata.create_all(bind=engine)

            with patch.object(database, "engine", engine):
                database.ensure_runtime_schema()

            columns = {column["name"] for column in inspect(engine).get_columns("training_task_records")}
            self.assertIn("evidence_pack_json", columns)
            with engine.begin() as connection:
                default_value = connection.execute(text("""
                    SELECT evidence_pack_json FROM training_task_records
                    WHERE task_id = 'TT_legacy'
                """)).scalar_one()
            self.assertEqual(default_value, "{}")
        finally:
            engine.dispose()

    def test_add_column_race_ignores_duplicate_only_after_reinspect_finds_column(self):
        connection = Mock()
        duplicate = OperationalError(
            "ALTER TABLE training_task_records ADD COLUMN evidence_pack_json TEXT",
            {},
            sqlite3.OperationalError("duplicate column name: evidence_pack_json"),
        )
        connection.execute.side_effect = duplicate
        inspector = Mock()
        inspector.get_columns.return_value = [{"name": "evidence_pack_json"}]

        with patch.object(database, "inspect", return_value=inspector):
            added = database._add_column_if_missing_after_race(
                connection,
                "training_task_records",
                "evidence_pack_json",
                "ALTER TABLE training_task_records ADD COLUMN evidence_pack_json TEXT",
            )

        self.assertFalse(added)

    def test_add_column_race_reraises_duplicate_when_reinspect_still_lacks_column(self):
        connection = Mock()
        duplicate = OperationalError(
            "ALTER TABLE training_task_records ADD COLUMN evidence_pack_json TEXT",
            {},
            sqlite3.OperationalError("duplicate column name: evidence_pack_json"),
        )
        connection.execute.side_effect = duplicate
        inspector = Mock()
        inspector.get_columns.return_value = [{"name": "task_id"}]

        with patch.object(database, "inspect", return_value=inspector):
            with self.assertRaises(OperationalError) as raised:
                database._add_column_if_missing_after_race(
                    connection,
                    "training_task_records",
                    "evidence_pack_json",
                    "ALTER TABLE training_task_records ADD COLUMN evidence_pack_json TEXT",
                )

        self.assertIs(raised.exception, duplicate)

    def test_add_column_helper_reraises_non_duplicate_database_errors(self):
        connection = Mock()
        failure = OperationalError(
            "ALTER TABLE training_task_records ADD COLUMN evidence_pack_json TEXT",
            {},
            sqlite3.OperationalError("database is locked"),
        )
        connection.execute.side_effect = failure

        with self.assertRaises(OperationalError) as raised:
            database._add_column_if_missing_after_race(
                connection,
                "training_task_records",
                "evidence_pack_json",
                "ALTER TABLE training_task_records ADD COLUMN evidence_pack_json TEXT",
            )

        self.assertIs(raised.exception, failure)


class TrainingWorkspaceFacadeTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        database.Base.metadata.create_all(bind=self.engine)
        self.Session = sessionmaker(bind=self.engine)
        self.request = {
            "task_type": "practice_grading",
            "title": "四君子汤练习批改",
            "query": "请批改这道方剂辨证题",
            "inputs": {
                "question_id": "demo-sijunzi-001",
                "question_type": "short_answer",
                "stem": "四君子汤主治的核心证型是什么？请简要说明。",
                "student_answer": "中焦虚寒证",
                "standard_answer": "脾胃气虚证",
                "rubric": "答出脾胃气虚证并能说明气虚、纳差、乏力等证据为满分。",
                "knowledge_points": ["四君子汤", "脾胃气虚证"],
                "difficulty": 2,
            },
            "options": {},
        }

    def tearDown(self):
        self.engine.dispose()

    def assert_no_task_records(self, db):
        for model in (
            database.TrainingTaskRecord,
            database.QuestionAttempt,
            database.LearningActivityRecord,
            database.MistakeRecord,
            database.AgentEvent,
            database.EvidencePackRecord,
        ):
            with self.subTest(model=model.__name__):
                self.assertEqual(db.query(model).count(), 0)

    def test_variation_workspace_projection_failure_rolls_back_private_publication(self):
        with self.Session() as db:
            db.add(database.UserModel(id=1, username="variation-atomic-owner", hashed_password="hash"))
            db.add(database.QuestionVersionRecord(question_version_id="QV_ATOMIC_SOURCE", question_id="Q_ATOMIC_SOURCE", version=1, stem="原题"))
            db.add(database.LearningAttemptRecord(attempt_id="ATT_ATOMIC", learner_id=1, attempt_type="practice"))
            db.add(database.LearningAttemptItemRecord(attempt_item_id="ITEM_ATOMIC", attempt_id="ATT_ATOMIC", question_version_id="QV_ATOMIC_SOURCE"))
            db.add(database.GradingResultRecord(artifact_id="GRADE_ATOMIC", attempt_item_id="ITEM_ATOMIC", version=1))
            db.add(database.AuditResultRecord(audit_id="AUD_ATOMIC", source_artifact_id="GRADE_ATOMIC", source_artifact_version=1, decision="pass", status="completed"))
            db.add(database.MistakeRecord(id=92, user_id=1, question_id="Q_ATOMIC_SOURCE", attempt_item_id="ITEM_ATOMIC", question_version_id="QV_ATOMIC_SOURCE", status="active"))
            db.add(database.QuestionKPLinkRecord(question_version_id="QV_ATOMIC_SOURCE", kp_id="KP_1"))
            db.commit()

            def runner(**kwargs):
                context = kwargs["request"].task_context
                return {"status": "success", "run_id": "RUN_ATOMIC", "steps": [], "final": {
                    "artifact": {"artifact_type": "question_variation", "title": "变式", "source_id": "QV_ATOMIC_VARIATION", "content": {
                        "stem": "安全变式", "question_type": "short_answer", "difficulty": 2,
                        "kp_ids": ["KP_1"], "source_mistake_id": 92,
                        "source_question_version_id": context.source_question_version_id,
                        "source_question_id": context.source_question_id,
                        "answer": "可信答案", "analysis": "可信解析",
                    }},
                    "evidence_pack": {"pack_id": "EP_ATOMIC", "source_scope": "mistake_variation", "source_id": context.source_question_version_id, "resolved_kp_ids": ["KP_1"], "items": []},
                    "audit": {"decision": "pass", "source_scope": "audit_agent", "source_id": "QV_ATOMIC_VARIATION"},
                }}

            with patch("APP.backend.training_workspace_service._persist_task", side_effect=RuntimeError("workspace projection failed")):
                with self.assertRaisesRegex(RuntimeError, "workspace projection failed"):
                    create_training_task(db, 1, {"task_type": "mistake_variation", "title": "变式", "query": "生成变式", "inputs": {"mistake_id": 92, "variation_count": 1}, "options": {}}, orchestration_runner=runner)

        with self.Session() as db:
            self.assertEqual(db.query(database.VariationSetRecord).count(), 0)
            self.assertEqual(db.query(database.VariationQuestionVersionRecord).count(), 0)
            self.assertEqual(db.query(database.QuestionVersionRecord).filter_by(source_kind="variation").count(), 0)
            self.assertEqual(db.query(database.TrainingTaskRecord).filter_by(status="failed").count(), 1)

    def test_long_running_variation_renews_lease_before_competitor_retries(self):
        with self.Session() as db:
            db.add(database.UserModel(id=1, username="renew-owner", hashed_password="hash"))
            db.add(database.QuestionVersionRecord(question_version_id="QV_RENEW", question_id="Q_RENEW", version=1, stem="原题"))
            db.add(database.LearningAttemptRecord(attempt_id="ATT_RENEW", learner_id=1, attempt_type="practice"))
            db.add(database.LearningAttemptItemRecord(attempt_item_id="ITEM_RENEW", attempt_id="ATT_RENEW", question_version_id="QV_RENEW"))
            db.add(database.GradingResultRecord(artifact_id="GRADE_RENEW", attempt_item_id="ITEM_RENEW", version=1))
            db.add(database.AuditResultRecord(audit_id="AUD_RENEW", source_artifact_id="GRADE_RENEW", source_artifact_version=1, decision="pass", status="completed"))
            db.add(database.MistakeRecord(id=101, user_id=1, question_id="Q_RENEW", attempt_item_id="ITEM_RENEW", question_version_id="QV_RENEW", status="active"))
            db.add(database.QuestionKPLinkRecord(question_version_id="QV_RENEW", kp_id="KP_1"))
            db.commit()
            request = {"task_type": "mistake_variation", "title": "变式", "query": "生成", "inputs": {"mistake_id": 101}, "options": {}}
            competitor = {}

            renewed_expiries = []

            def runner(**kwargs):
                task = db.query(database.TrainingTaskRecord).one()
                renewed_expiries.append(task.claim_expires_at)
                with self.Session() as competing_db:
                    competitor.update(create_training_task(competing_db, 1, request, orchestration_runner=Mock()))
                return {"status": "success", "run_id": "RUN_RENEW", "steps": [], "final": {
                    "artifact": {"artifact_type": "question_variation", "title": "变式", "source_id": "QV_RENEW_VARIATION", "content": {"stem": "续租变式", "question_type": "short_answer", "difficulty": 2, "kp_ids": ["KP_1"], "source_mistake_id": 101, "source_question_version_id": "QV_RENEW", "answer": "答案", "analysis": "解析"}},
                    "evidence_pack": {"pack_id": "EP", "source_scope": "mistake_variation", "source_id": "QV_RENEW", "resolved_kp_ids": ["KP_1"], "items": []},
                    "audit": {"decision": "pass", "source_scope": "audit_agent", "source_id": "QV_RENEW_VARIATION"},
                }}

            result = create_training_task(db, 1, request, orchestration_runner=runner)

        self.assertEqual(result["status"], "completed")
        self.assertGreater(renewed_expiries[0], datetime.utcnow() + timedelta(minutes=4))
        self.assertEqual(competitor["status"], "in_progress")

    def test_blocking_variation_runner_keeps_claim_alive_past_lease(self):
        with self.Session() as db:
            db.add(database.UserModel(id=1, username="heartbeat-owner", hashed_password="hash"))
            db.add(database.QuestionVersionRecord(question_version_id="QV_HEARTBEAT", question_id="Q_HEARTBEAT", version=1, stem="原题"))
            db.add(database.LearningAttemptRecord(attempt_id="ATT_HEARTBEAT", learner_id=1, attempt_type="practice"))
            db.add(database.LearningAttemptItemRecord(attempt_item_id="ITEM_HEARTBEAT", attempt_id="ATT_HEARTBEAT", question_version_id="QV_HEARTBEAT"))
            db.add(database.GradingResultRecord(artifact_id="GRADE_HEARTBEAT", attempt_item_id="ITEM_HEARTBEAT", version=1))
            db.add(database.AuditResultRecord(audit_id="AUD_HEARTBEAT", source_artifact_id="GRADE_HEARTBEAT", source_artifact_version=1, decision="pass", status="completed"))
            db.add(database.MistakeRecord(id=102, user_id=1, question_id="Q_HEARTBEAT", attempt_item_id="ITEM_HEARTBEAT", question_version_id="QV_HEARTBEAT", status="active"))
            db.add(database.QuestionKPLinkRecord(question_version_id="QV_HEARTBEAT", kp_id="KP_1"))
            db.commit()
            request = {"task_type": "mistake_variation", "title": "变式", "query": "生成", "inputs": {"mistake_id": 102}, "options": {}}
            competitor = {}

            def runner(**_):
                time.sleep(0.18)
                with self.Session() as competing_db:
                    task = competing_db.query(database.TrainingTaskRecord).one()
                    competitor.update({
                        "status": task.status,
                        "claim_expires_at": task.claim_expires_at,
                        "observed_at": datetime.utcnow(),
                    })
                return {"status": "success", "run_id": "RUN_HEARTBEAT", "steps": [], "final": {
                    "artifact": {"artifact_type": "question_variation", "title": "变式", "source_id": "QV_HEARTBEAT_VARIATION", "content": {"stem": "心跳变式", "question_type": "short_answer", "difficulty": 2, "kp_ids": ["KP_1"], "source_mistake_id": 102, "source_question_version_id": "QV_HEARTBEAT", "answer": "答案", "analysis": "解析"}},
                    "evidence_pack": {"pack_id": "EP", "source_scope": "mistake_variation", "source_id": "QV_HEARTBEAT", "resolved_kp_ids": ["KP_1"], "items": []},
                    "audit": {"decision": "pass", "source_scope": "audit_agent", "source_id": "QV_HEARTBEAT_VARIATION"},
                }}

            publication = PublishedVariation(
                variation_set_id="VS_HEARTBEAT", owner_user_id=1,
                question_version_id="QV_HEARTBEAT_VARIATION",
                scope="user", status="published",
            )
            with patch("APP.backend.training_workspace_service.VARIATION_CLAIM_LEASE", timedelta(milliseconds=90)), patch("APP.backend.training_workspace_service.VARIATION_HEARTBEAT_INTERVAL_SECONDS", 0.02), patch.object(VariationRepository, "publish_variation", return_value=publication):
                result = create_training_task(db, 1, request, orchestration_runner=runner)

        self.assertEqual(result["status"], "completed")
        self.assertEqual(competitor["status"], "in_progress")
        self.assertGreater(competitor["claim_expires_at"], competitor["observed_at"])

    def test_multiple_blocking_candidates_do_not_lock_heartbeat_or_allow_reclaim(self):
        with self.Session() as db:
            db.add(database.UserModel(id=1, username="candidate-owner", hashed_password="hash"))
            db.add(database.QuestionVersionRecord(question_version_id="QV_CANDIDATES", question_id="Q_CANDIDATES", version=1, stem="原题"))
            db.add(database.LearningAttemptRecord(attempt_id="ATT_CANDIDATES", learner_id=1, attempt_type="practice"))
            db.add(database.LearningAttemptItemRecord(attempt_item_id="ITEM_CANDIDATES", attempt_id="ATT_CANDIDATES", question_version_id="QV_CANDIDATES"))
            db.add(database.GradingResultRecord(artifact_id="GRADE_CANDIDATES", attempt_item_id="ITEM_CANDIDATES", version=1))
            db.add(database.AuditResultRecord(audit_id="AUD_CANDIDATES", source_artifact_id="GRADE_CANDIDATES", source_artifact_version=1, decision="pass", status="completed"))
            db.add(database.MistakeRecord(id=103, user_id=1, question_id="Q_CANDIDATES", attempt_item_id="ITEM_CANDIDATES", question_version_id="QV_CANDIDATES", status="active"))
            db.add(database.QuestionKPLinkRecord(question_version_id="QV_CANDIDATES", kp_id="KP_1"))
            db.commit()
            request = {"task_type": "mistake_variation", "title": "变式", "query": "生成", "inputs": {"mistake_id": 103, "variation_count": 2}, "options": {}}
            calls = 0
            competitor_runner = Mock()
            competitor = {}

            def runner(**kwargs):
                nonlocal calls
                calls += 1
                if calls == 2:
                    time.sleep(0.18)
                    with self.Session() as competing_db:
                        self.assertEqual(competing_db.query(database.VariationSetRecord).count(), 0)
                        self.assertEqual(competing_db.query(database.VariationRubricRecord).count(), 0)
                        self.assertEqual(competing_db.query(database.QuestionVersionRecord).filter_by(source_kind="variation").count(), 0)
                        self.assertEqual(competing_db.query(database.GradingResultRecord).filter_by(status="audited_variation_candidate").count(), 0)
                        competitor.update(create_training_task(competing_db, 1, request, orchestration_runner=competitor_runner))
                source_id = f"QV_CANDIDATES_VARIATION_{calls}"
                return {"status": "success", "run_id": f"RUN_CANDIDATES_{calls}", "steps": [], "final": {
                    "artifact": {"artifact_type": "question_variation", "title": "变式", "source_id": source_id, "content": {"stem": f"候选变式 {calls}", "question_type": "short_answer", "difficulty": 2, "kp_ids": ["KP_1"], "source_mistake_id": 103, "source_question_version_id": "QV_CANDIDATES", "answer": "答案", "analysis": "解析"}},
                    "evidence_pack": {"pack_id": "EP", "source_scope": "mistake_variation", "source_id": "QV_CANDIDATES", "resolved_kp_ids": ["KP_1"], "items": []},
                    "audit": {"decision": "pass", "source_scope": "audit_agent", "source_id": source_id},
                }}

            with patch("APP.backend.training_workspace_service.VARIATION_CLAIM_LEASE", timedelta(milliseconds=90)), patch("APP.backend.training_workspace_service.VARIATION_HEARTBEAT_INTERVAL_SECONDS", 0.02):
                result = create_training_task(db, 1, request, orchestration_runner=runner)

        self.assertEqual(result["status"], "completed")
        self.assertEqual(competitor["status"], "in_progress")
        competitor_runner.assert_not_called()
        with self.Session() as db:
            self.assertEqual(db.query(database.VariationSetRecord).count(), 2)
            self.assertEqual(db.query(database.QuestionVersionRecord).filter_by(source_kind="variation").count(), 2)

    def test_sustained_heartbeat_failure_fails_closed_without_publication(self):
        with self.Session() as db:
            db.add(database.UserModel(id=1, username="failed-heartbeat-owner", hashed_password="hash"))
            db.add(database.QuestionVersionRecord(question_version_id="QV_FAILED_HEARTBEAT", question_id="Q_FAILED_HEARTBEAT", version=1, stem="原题"))
            db.add(database.LearningAttemptRecord(attempt_id="ATT_FAILED_HEARTBEAT", learner_id=1, attempt_type="practice"))
            db.add(database.LearningAttemptItemRecord(attempt_item_id="ITEM_FAILED_HEARTBEAT", attempt_id="ATT_FAILED_HEARTBEAT", question_version_id="QV_FAILED_HEARTBEAT"))
            db.add(database.GradingResultRecord(artifact_id="GRADE_FAILED_HEARTBEAT", attempt_item_id="ITEM_FAILED_HEARTBEAT", version=1))
            db.add(database.AuditResultRecord(audit_id="AUD_FAILED_HEARTBEAT", source_artifact_id="GRADE_FAILED_HEARTBEAT", source_artifact_version=1, decision="pass", status="completed"))
            db.add(database.MistakeRecord(id=104, user_id=1, question_id="Q_FAILED_HEARTBEAT", attempt_item_id="ITEM_FAILED_HEARTBEAT", question_version_id="QV_FAILED_HEARTBEAT", status="active"))
            db.add(database.QuestionKPLinkRecord(question_version_id="QV_FAILED_HEARTBEAT", kp_id="KP_1"))
            db.commit()
            request = {"task_type": "mistake_variation", "title": "变式", "query": "生成", "inputs": {"mistake_id": 104}, "options": {}}

            def runner(**_):
                time.sleep(0.12)
                return {"status": "success", "run_id": "RUN_FAILED_HEARTBEAT", "steps": [], "final": {}}

            with patch("APP.backend.training_workspace_service.VARIATION_CLAIM_LEASE", timedelta(milliseconds=80)), patch("APP.backend.training_workspace_service.VARIATION_HEARTBEAT_INTERVAL_SECONDS", 0.01), patch("APP.backend.training_workspace_service._renew_variation_claim", side_effect=OperationalError("UPDATE", {}, sqlite3.OperationalError("database is locked"))):
                with self.assertRaisesRegex(TrainingTaskExecutionError, "heartbeat failed"):
                    create_training_task(db, 1, request, orchestration_runner=runner)

        with self.Session() as db:
            task = db.query(database.TrainingTaskRecord).one()
            self.assertEqual(task.status, "failed")
            self.assertIsNone(task.claim_owner)
            self.assertEqual(db.query(database.VariationSetRecord).count(), 0)
            self.assertEqual(db.query(database.GradingResultRecord).filter_by(status="audited_variation_candidate").count(), 0)

    def test_stale_variation_claim_can_be_reclaimed(self):
        from APP.backend.training_workspace_service import (
            _authorized_variation_inputs,
            _variation_task_id,
        )

        with self.Session() as db:
            db.add(database.UserModel(id=1, username="lease-owner", hashed_password="hash"))
            db.add(database.QuestionVersionRecord(question_version_id="QV_LEASE", question_id="Q_LEASE", version=1, stem="原题"))
            db.add(database.LearningAttemptRecord(attempt_id="ATT_LEASE", learner_id=1, attempt_type="practice"))
            db.add(database.LearningAttemptItemRecord(attempt_item_id="ITEM_LEASE", attempt_id="ATT_LEASE", question_version_id="QV_LEASE"))
            db.add(database.GradingResultRecord(artifact_id="GRADE_LEASE", attempt_item_id="ITEM_LEASE", version=1))
            db.add(database.AuditResultRecord(audit_id="AUD_LEASE", source_artifact_id="GRADE_LEASE", source_artifact_version=1, decision="pass", status="completed"))
            db.add(database.MistakeRecord(id=98, user_id=1, question_id="Q_LEASE", attempt_item_id="ITEM_LEASE", question_version_id="QV_LEASE", status="active"))
            db.add(database.QuestionKPLinkRecord(question_version_id="QV_LEASE", kp_id="KP_1"))
            db.commit()
            request = {"task_type": "mistake_variation", "title": "变式", "query": "生成变式", "inputs": {"mistake_id": 98}, "options": {}}
            task_id = _variation_task_id(1, _authorized_variation_inputs(db, 1, request["inputs"]), request)
            db.add(database.TrainingTaskRecord(
                task_id=task_id, user_id=1, task_type="mistake_variation", title="变式",
                status="in_progress", claim_owner="dead-worker",
                claim_expires_at=datetime.utcnow() - timedelta(seconds=1),
                artifact_type="question_variation", artifact_json="{}",
                evidence_pack_json="{}", audit_json="{}", trace_json="[]",
                learning_updates_json="{}",
            ))
            db.commit()
            payload = {"status": "success", "run_id": "RUN_LEASE", "steps": [], "final": {
                "artifact": {"artifact_type": "question_variation", "title": "变式", "source_id": "QV_LEASE_VARIATION", "content": {
                    "stem": "租约恢复变式", "question_type": "short_answer", "difficulty": 2,
                    "kp_ids": ["KP_1"], "source_mistake_id": 98,
                    "source_question_version_id": "QV_LEASE", "answer": "答案", "analysis": "解析",
                }},
                "evidence_pack": {"pack_id": "EP", "source_scope": "mistake_variation", "source_id": "QV_LEASE", "resolved_kp_ids": ["KP_1"], "items": []},
                "audit": {"decision": "pass", "source_scope": "audit_agent", "source_id": "QV_LEASE_VARIATION"},
            }}
            runner = Mock(return_value=payload)
            first = create_training_task(db, 1, request, orchestration_runner=runner)
            second = create_training_task(db, 1, request, orchestration_runner=runner)

            self.assertEqual(first, second)
            runner.assert_called_once()
            self.assertEqual(db.query(database.VariationSetRecord).count(), 1)

    def test_variation_answer_action_uses_private_authority_and_commits(self):
        with self.Session() as db:
            db.add(database.UserModel(id=1, username="answer-owner", hashed_password="hash"))
            db.add(database.QuestionVersionRecord(question_version_id="QV_ANSWER", question_id="Q_ANSWER", version=1, stem="原题"))
            db.add(database.LearningAttemptRecord(attempt_id="ATT_ANSWER", learner_id=1, attempt_type="practice"))
            db.add(database.LearningAttemptItemRecord(attempt_item_id="ITEM_ANSWER", attempt_id="ATT_ANSWER", question_version_id="QV_ANSWER"))
            db.add(database.GradingResultRecord(artifact_id="GRADE_ANSWER", attempt_item_id="ITEM_ANSWER", version=1))
            db.add(database.AuditResultRecord(audit_id="AUD_ANSWER", source_artifact_id="GRADE_ANSWER", source_artifact_version=1, decision="pass", status="completed"))
            db.add(database.MistakeRecord(id=99, user_id=1, question_id="Q_ANSWER", attempt_item_id="ITEM_ANSWER", question_version_id="QV_ANSWER", status="active"))
            db.add(database.QuestionKPLinkRecord(question_version_id="QV_ANSWER", kp_id="KP_1"))
            db.commit()
            generated = create_training_task(db, 1, {"task_type": "mistake_variation", "title": "变式", "query": "生成变式", "inputs": {"mistake_id": 99}, "options": {}}, orchestration_runner=lambda **_: {"status": "success", "run_id": "RUN_ANSWER", "steps": [], "final": {
                "artifact": {"artifact_type": "question_variation", "title": "变式", "source_id": "QV_ANSWER_VARIATION", "content": {"stem": "变式题", "question_type": "short_answer", "difficulty": 2, "kp_ids": ["KP_1"], "source_mistake_id": 99, "source_question_version_id": "QV_ANSWER", "answer": "正确答案", "analysis": "权威解析"}},
                "evidence_pack": {"pack_id": "EP", "source_scope": "mistake_variation", "source_id": "QV_ANSWER", "resolved_kp_ids": ["KP_1"], "items": []},
                "audit": {"decision": "pass", "source_scope": "audit_agent", "source_id": "QV_ANSWER_VARIATION"},
            }})
            question = generated["artifact"]["content"]["questions"][0]
            answer_request = {"task_type": "mistake_variation", "title": "作答", "query": "提交答案", "inputs": {"action": "answer", "mistake_id": 99, "question_version_id": question["question_version_id"], "student_answer": "正确答案", "request_id": "submission-99-1"}, "options": {}}
            self.assertNotIn("audit_id", question)
            result = create_training_task(db, 1, answer_request, grading_runner=self.pass_grading_runner)
            replay = create_training_task(db, 1, answer_request, grading_runner=self.pass_grading_runner)

            self.assertEqual(replay, result)
            self.assertTrue(result["artifact"]["content"]["grading"]["grading"]["is_correct"])
            self.assertEqual(db.query(database.MistakeRecord).filter_by(id=99).one().status, "active")
            db.expire_all()
            self.assertEqual(db.query(database.LearningAttemptRecord).count(), 2)

        with self.Session() as db:
            self.assertEqual(db.query(database.LearningAttemptRecord).count(), 2)
            self.assertEqual(db.query(database.GradingResultRecord).count(), 3)
            self.assertEqual(db.query(database.AuditResultRecord).count(), 3)

    def test_variation_answer_requires_bounded_request_id(self):
        for request_id in (None, "", "x" * 121):
            with self.subTest(request_id=request_id), self.Session() as db:
                inputs = {"action": "answer", "mistake_id": 99, "question_version_id": "QV", "student_answer": "x"}
                if request_id is not None:
                    inputs["request_id"] = request_id
                with self.assertRaisesRegex(InvalidTrainingTaskRequest, "request_id"):
                    create_training_task(db, 1, {"task_type": "mistake_variation", "inputs": inputs, "options": {}})

    def test_interleaving_duplicate_variation_answer_has_one_authoritative_writeback(self):
        with self.Session() as db:
            db.add(database.UserModel(id=1, username="answer-race-owner", hashed_password="hash"))
            db.add(database.QuestionVersionRecord(question_version_id="QV_RACE_SOURCE", question_id="Q_RACE", version=1, stem="原题"))
            db.add(database.LearningAttemptRecord(attempt_id="ATT_RACE_SOURCE", learner_id=1, attempt_type="practice"))
            db.add(database.LearningAttemptItemRecord(attempt_item_id="ITEM_RACE_SOURCE", attempt_id="ATT_RACE_SOURCE", question_version_id="QV_RACE_SOURCE"))
            db.add(database.GradingResultRecord(artifact_id="GRADE_RACE_SOURCE", attempt_item_id="ITEM_RACE_SOURCE", version=1))
            db.add(database.AuditResultRecord(audit_id="AUD_RACE_SOURCE", source_artifact_id="GRADE_RACE_SOURCE", source_artifact_version=1, decision="pass", status="completed"))
            db.add(database.MistakeRecord(id=100, user_id=1, question_id="Q_RACE", attempt_item_id="ITEM_RACE_SOURCE", question_version_id="QV_RACE_SOURCE", status="active"))
            db.add(database.QuestionKPLinkRecord(question_version_id="QV_RACE_SOURCE", kp_id="KP_1"))
            db.commit()
            generated = create_training_task(db, 1, {"task_type": "mistake_variation", "title": "变式", "query": "生成变式", "inputs": {"mistake_id": 100}, "options": {}}, orchestration_runner=lambda **_: {"status": "success", "run_id": "RUN_RACE", "steps": [], "final": {
                "artifact": {"artifact_type": "question_variation", "title": "变式", "source_id": "QV_RACE_VARIATION", "content": {"stem": "变式题", "question_type": "short_answer", "difficulty": 2, "kp_ids": ["KP_1"], "source_mistake_id": 100, "source_question_version_id": "QV_RACE_SOURCE", "answer": "答案", "analysis": "解析"}},
                "evidence_pack": {"pack_id": "EP", "source_scope": "mistake_variation", "source_id": "QV_RACE_SOURCE", "resolved_kp_ids": ["KP_1"], "items": []},
                "audit": {"decision": "pass", "source_scope": "audit_agent", "source_id": "QV_RACE_VARIATION"},
            }})
            question = generated["artifact"]["content"]["questions"][0]
            request = {"task_type": "mistake_variation", "title": "作答", "query": "提交", "inputs": {"action": "answer", "mistake_id": 100, "question_version_id": question["question_version_id"], "student_answer": "答案", "request_id": "race-submission-1"}, "options": {}}
            competitor = {}
            calls = 0

            def runner(**kwargs):
                nonlocal calls
                calls += 1
                if calls == 1:
                    with self.Session() as competing_db:
                        competitor.update(create_training_task(competing_db, 1, request, grading_runner=runner))
                return self.pass_grading_runner(**kwargs)

            result = create_training_task(db, 1, request, grading_runner=runner)

        self.assertEqual(calls, 1)
        self.assertIn(competitor["status"], {"in_progress", "completed"})
        with self.Session() as db:
            self.assertEqual(db.query(database.LearningWritebackReceipt).count(), 1)
            answer_attempt = db.query(database.LearningAttemptRecord).filter_by(request_id=result["task_id"]).one()
            answer_item = db.query(database.LearningAttemptItemRecord).filter_by(attempt_id=answer_attempt.attempt_id).one()
            self.assertEqual(db.query(database.GradingResultRecord).filter_by(attempt_item_id=answer_item.attempt_item_id).count(), 1)
            self.assertEqual(db.query(database.TrainingTaskRecord).filter_by(task_id=result["task_id"]).count(), 1)

    def test_blocking_variation_answer_keeps_claim_alive_and_writes_back_once(self):
        with self.Session() as db:
            db.add(database.UserModel(id=1, username="answer-heartbeat-owner", hashed_password="hash"))
            db.add(database.QuestionVersionRecord(question_version_id="QV_ANSWER_HEARTBEAT_SOURCE", question_id="Q_ANSWER_HEARTBEAT", version=1, stem="原题"))
            db.add(database.LearningAttemptRecord(attempt_id="ATT_ANSWER_HEARTBEAT_SOURCE", learner_id=1, attempt_type="practice"))
            db.add(database.LearningAttemptItemRecord(attempt_item_id="ITEM_ANSWER_HEARTBEAT_SOURCE", attempt_id="ATT_ANSWER_HEARTBEAT_SOURCE", question_version_id="QV_ANSWER_HEARTBEAT_SOURCE"))
            db.add(database.GradingResultRecord(artifact_id="GRADE_ANSWER_HEARTBEAT_SOURCE", attempt_item_id="ITEM_ANSWER_HEARTBEAT_SOURCE", version=1))
            db.add(database.AuditResultRecord(audit_id="AUD_ANSWER_HEARTBEAT_SOURCE", source_artifact_id="GRADE_ANSWER_HEARTBEAT_SOURCE", source_artifact_version=1, decision="pass", status="completed"))
            db.add(database.MistakeRecord(id=105, user_id=1, question_id="Q_ANSWER_HEARTBEAT", attempt_item_id="ITEM_ANSWER_HEARTBEAT_SOURCE", question_version_id="QV_ANSWER_HEARTBEAT_SOURCE", status="active"))
            db.add(database.QuestionKPLinkRecord(question_version_id="QV_ANSWER_HEARTBEAT_SOURCE", kp_id="KP_1"))
            db.commit()
            generated = create_training_task(db, 1, {"task_type": "mistake_variation", "inputs": {"mistake_id": 105}, "options": {}}, orchestration_runner=lambda **_: {"status": "success", "run_id": "RUN_ANSWER_HEARTBEAT_SOURCE", "steps": [], "final": {
                "artifact": {"artifact_type": "question_variation", "title": "变式", "source_id": "QV_ANSWER_HEARTBEAT_VARIATION", "content": {"stem": "变式题", "question_type": "short_answer", "difficulty": 2, "kp_ids": ["KP_1"], "source_mistake_id": 105, "source_question_version_id": "QV_ANSWER_HEARTBEAT_SOURCE", "answer": "答案", "analysis": "解析"}},
                "evidence_pack": {"pack_id": "EP", "source_scope": "mistake_variation", "source_id": "QV_ANSWER_HEARTBEAT_SOURCE", "resolved_kp_ids": ["KP_1"], "items": []},
                "audit": {"decision": "pass", "source_scope": "audit_agent", "source_id": "QV_ANSWER_HEARTBEAT_VARIATION"},
            }})
            question = generated["artifact"]["content"]["questions"][0]
            request = {"task_type": "mistake_variation", "inputs": {"action": "answer", "mistake_id": 105, "question_version_id": question["question_version_id"], "student_answer": "答案", "request_id": "answer-heartbeat-1"}, "options": {}}
            competitor = {}
            competitor_runner = Mock()

            def runner(**kwargs):
                time.sleep(0.18)
                with self.Session() as competing_db:
                    competitor.update(create_training_task(competing_db, 1, request, grading_runner=competitor_runner))
                return self.pass_grading_runner(**kwargs)

            with patch("APP.backend.training_workspace_service.VARIATION_CLAIM_LEASE", timedelta(milliseconds=90)), patch("APP.backend.training_workspace_service.VARIATION_HEARTBEAT_INTERVAL_SECONDS", 0.02):
                result = create_training_task(db, 1, request, grading_runner=runner)

        self.assertEqual(result["status"], "completed")
        self.assertEqual(competitor["status"], "in_progress")
        competitor_runner.assert_not_called()
        with self.Session() as db:
            self.assertEqual(db.query(database.LearningWritebackReceipt).count(), 1)
            answer_attempt = db.query(database.LearningAttemptRecord).filter_by(request_id=result["task_id"]).one()
            answer_item = db.query(database.LearningAttemptItemRecord).filter_by(attempt_id=answer_attempt.attempt_id).one()
            self.assertEqual(db.query(database.GradingResultRecord).filter_by(attempt_item_id=answer_item.attempt_item_id).count(), 1)

    def test_variation_answer_rejects_client_answer_key_and_rubric(self):
        for forbidden in ("standard_answer", "rubric"):
            with self.subTest(forbidden=forbidden), self.Session() as db:
                with self.assertRaisesRegex(InvalidTrainingTaskRequest, forbidden):
                    create_training_task(db, 1, {"task_type": "mistake_variation", "inputs": {"action": "answer", "mistake_id": 99, "question_version_id": "QV", "student_answer": "x", forbidden: "client authority"}, "options": {}})

    def test_reentrant_variation_claim_returns_in_progress_without_republishing(self):
        with self.Session() as db:
            db.add(database.UserModel(id=1, username="variation-claim-owner", hashed_password="hash"))
            db.add(database.QuestionVersionRecord(question_version_id="QV_CLAIM", question_id="Q_CLAIM", version=1, stem="原题"))
            db.add(database.LearningAttemptRecord(attempt_id="ATT_CLAIM", learner_id=1, attempt_type="practice"))
            db.add(database.LearningAttemptItemRecord(attempt_item_id="ITEM_CLAIM", attempt_id="ATT_CLAIM", question_version_id="QV_CLAIM"))
            db.add(database.GradingResultRecord(artifact_id="GRADE_CLAIM", attempt_item_id="ITEM_CLAIM", version=1))
            db.add(database.AuditResultRecord(audit_id="AUD_CLAIM", source_artifact_id="GRADE_CLAIM", source_artifact_version=1, decision="pass", status="completed"))
            db.add(database.MistakeRecord(id=94, user_id=1, question_id="Q_CLAIM", attempt_item_id="ITEM_CLAIM", question_version_id="QV_CLAIM", status="active"))
            db.add(database.QuestionKPLinkRecord(question_version_id="QV_CLAIM", kp_id="KP_1"))
            db.commit()
            request = {"task_type": "mistake_variation", "title": "变式", "query": "生成变式", "inputs": {"mistake_id": 94}, "options": {}}
            runner_calls = []
            reentrant_result = {}

            def runner(**kwargs):
                runner_calls.append(kwargs)
                with self.Session() as competing_db:
                    reentrant_result.update(create_training_task(competing_db, 1, request, orchestration_runner=Mock()))
                context = kwargs["request"].task_context
                return {"status": "success", "run_id": "RUN_CLAIM", "steps": [], "final": {
                    "artifact": {"artifact_type": "question_variation", "title": "变式", "source_id": "QV_CLAIM_VARIATION", "content": {
                        "stem": "幂等变式", "question_type": "short_answer", "difficulty": 2, "kp_ids": ["KP_1"],
                        "source_mistake_id": 94, "source_question_version_id": context.source_question_version_id,
                        "answer": "可信答案", "analysis": "可信解析",
                    }},
                    "evidence_pack": {"pack_id": "EP_CLAIM", "source_scope": "mistake_variation", "source_id": "QV_CLAIM", "resolved_kp_ids": ["KP_1"], "items": []},
                    "audit": {"decision": "pass", "source_scope": "audit_agent", "source_id": "QV_CLAIM_VARIATION"},
                }}

            result = create_training_task(db, 1, request, orchestration_runner=runner)

        self.assertEqual(len(runner_calls), 1)
        self.assertEqual(reentrant_result["status"], "in_progress")
        self.assertEqual(reentrant_result["task_id"], result["task_id"])
        with self.Session() as db:
            self.assertEqual(db.query(database.TrainingTaskRecord).count(), 1)
            self.assertEqual(db.query(database.VariationSetRecord).count(), 1)

    def test_rejects_external_variation_publisher_before_runner_or_side_effect(self):
        with self.Session() as db:
            db.add(database.UserModel(id=1, username="external-publisher-owner", hashed_password="hash"))
            db.add(database.QuestionVersionRecord(question_version_id="QV_EXTERNAL", question_id="Q_EXTERNAL", version=1, stem="原题"))
            db.add(database.LearningAttemptRecord(attempt_id="ATT_EXTERNAL", learner_id=1, attempt_type="practice"))
            db.add(database.LearningAttemptItemRecord(attempt_item_id="ITEM_EXTERNAL", attempt_id="ATT_EXTERNAL", question_version_id="QV_EXTERNAL"))
            db.add(database.GradingResultRecord(artifact_id="GRADE_EXTERNAL", attempt_item_id="ITEM_EXTERNAL", version=1))
            db.add(database.AuditResultRecord(audit_id="AUD_EXTERNAL", source_artifact_id="GRADE_EXTERNAL", source_artifact_version=1, decision="pass", status="completed"))
            db.add(database.MistakeRecord(id=95, user_id=1, question_id="Q_EXTERNAL", attempt_item_id="ITEM_EXTERNAL", question_version_id="QV_EXTERNAL", status="active"))
            db.add(database.QuestionKPLinkRecord(question_version_id="QV_EXTERNAL", kp_id="KP_1"))
            db.commit()
            runner = Mock()
            external = Mock()

            with self.assertRaisesRegex(InvalidTrainingTaskRequest, "same database session"):
                create_training_task(db, 1, {"task_type": "mistake_variation", "title": "变式", "query": "生成变式", "inputs": {"mistake_id": 95}, "options": {}}, orchestration_runner=runner, variation_publisher=external)

            runner.assert_not_called()
            external.assert_not_called()
            self.assertEqual(db.query(database.TrainingTaskRecord).count(), 0)

    def test_same_session_variation_publisher_rolls_back_with_workspace_failure(self):
        with self.Session() as db:
            db.add(database.UserModel(id=1, username="bound-publisher-owner", hashed_password="hash"))
            db.add(database.QuestionVersionRecord(question_version_id="QV_BOUND", question_id="Q_BOUND", version=1, stem="原题"))
            db.add(database.LearningAttemptRecord(attempt_id="ATT_BOUND", learner_id=1, attempt_type="practice"))
            db.add(database.LearningAttemptItemRecord(attempt_item_id="ITEM_BOUND", attempt_id="ATT_BOUND", question_version_id="QV_BOUND"))
            db.add(database.GradingResultRecord(artifact_id="GRADE_BOUND", attempt_item_id="ITEM_BOUND", version=1))
            db.add(database.AuditResultRecord(audit_id="AUD_BOUND", source_artifact_id="GRADE_BOUND", source_artifact_version=1, decision="pass", status="completed"))
            db.add(database.MistakeRecord(id=96, user_id=1, question_id="Q_BOUND", attempt_item_id="ITEM_BOUND", question_version_id="QV_BOUND", status="active"))
            db.add(database.QuestionKPLinkRecord(question_version_id="QV_BOUND", kp_id="KP_1"))
            db.commit()

            def runner(**kwargs):
                context = kwargs["request"].task_context
                return {"status": "success", "run_id": "RUN_BOUND", "steps": [], "final": {
                    "artifact": {"artifact_type": "question_variation", "title": "变式", "source_id": "QV_BOUND_VARIATION", "content": {
                        "stem": "绑定变式", "question_type": "short_answer", "difficulty": 2, "kp_ids": ["KP_1"],
                        "source_mistake_id": 96, "source_question_version_id": context.source_question_version_id,
                        "answer": "可信答案", "analysis": "可信解析",
                    }},
                    "evidence_pack": {"pack_id": "EP_BOUND", "source_scope": "mistake_variation", "source_id": "QV_BOUND", "resolved_kp_ids": ["KP_1"], "items": []},
                    "audit": {"decision": "pass", "source_scope": "audit_agent", "source_id": "QV_BOUND_VARIATION"},
                }}

            with patch("APP.backend.training_workspace_service._persist_task", side_effect=RuntimeError("workspace projection failed")):
                with self.assertRaisesRegex(RuntimeError, "workspace projection failed"):
                    create_training_task(db, 1, {"task_type": "mistake_variation", "title": "变式", "query": "生成变式", "inputs": {"mistake_id": 96}, "options": {}}, orchestration_runner=runner, variation_publisher=VariationRepository(session=db))

        with self.Session() as db:
            self.assertEqual(db.query(database.VariationSetRecord).count(), 0)
            self.assertEqual(db.query(database.TrainingTaskRecord).filter_by(status="failed").count(), 1)

    def test_identical_variation_retry_returns_persisted_task_without_duplicates(self):
        with self.Session() as db:
            db.add(database.UserModel(id=1, username="variation-retry-owner", hashed_password="hash"))
            db.add(database.QuestionVersionRecord(question_version_id="QV_RETRY", question_id="Q_RETRY", version=1, stem="原题"))
            db.add(database.LearningAttemptRecord(attempt_id="ATT_RETRY", learner_id=1, attempt_type="practice"))
            db.add(database.LearningAttemptItemRecord(attempt_item_id="ITEM_RETRY", attempt_id="ATT_RETRY", question_version_id="QV_RETRY"))
            db.add(database.GradingResultRecord(artifact_id="GRADE_RETRY", attempt_item_id="ITEM_RETRY", version=1))
            db.add(database.AuditResultRecord(audit_id="AUD_RETRY", source_artifact_id="GRADE_RETRY", source_artifact_version=1, decision="pass", status="completed"))
            db.add(database.MistakeRecord(id=93, user_id=1, question_id="Q_RETRY", attempt_item_id="ITEM_RETRY", question_version_id="QV_RETRY", status="active"))
            db.add(database.QuestionKPLinkRecord(question_version_id="QV_RETRY", kp_id="KP_1"))
            db.commit()
            calls = []

            def runner(**kwargs):
                calls.append(kwargs)
                return {"status": "success", "run_id": "RUN_RETRY", "steps": [], "final": {
                    "artifact": {"artifact_type": "question_variation", "title": "变式", "source_id": "AGENT_ID", "content": {"stem": "重试变式", "question_type": "short_answer", "difficulty": 2, "kp_ids": ["KP_1"], "source_mistake_id": 93, "source_question_version_id": "QV_RETRY", "answer": "可信答案", "analysis": "可信解析"}},
                    "evidence_pack": {"pack_id": "EP_RETRY", "source_scope": "mistake_variation", "source_id": "QV_RETRY", "resolved_kp_ids": ["KP_1"], "items": []},
                    "audit": {"decision": "pass", "source_scope": "audit_agent", "source_id": "AGENT_ID"},
                }}

            request = {"task_type": "mistake_variation", "title": "变式", "query": "生成变式", "inputs": {"mistake_id": 93, "variation_count": 1}, "options": {}}
            first = create_training_task(db, 1, request, orchestration_runner=runner)
            second = create_training_task(db, 1, request, orchestration_runner=runner)

            self.assertEqual(len(first["audit"]["audit_ids"]), 1)
            self.assertEqual(second, first)
            self.assertEqual(len(calls), 1)
            self.assertEqual(db.query(database.VariationSetRecord).count(), 1)
            self.assertEqual(db.query(database.VariationQuestionVersionRecord).count(), 1)

    def test_legacy_generation_helper_cannot_dispatch_mistake_variation(self):
        from APP.backend.training_workspace_service import _create_generation_task

        runner = Mock()
        publisher = Mock()
        with self.Session() as db, self.assertRaisesRegex(InvalidTrainingTaskRequest, "audited variation service"):
            _create_generation_task(
                db, user_id=1,
                request={"task_type": "mistake_variation", "title": "变式", "query": "生成", "inputs": {"mistake_id": 1}, "options": {}},
                runtime=None, orchestration_runner=runner,
                variation_publisher=publisher,
            )
        runner.assert_not_called()
        publisher.assert_not_called()

    def test_production_workspace_publishes_only_safe_currently_audited_variation(self):
        with self.Session() as db:
            db.add(database.UserModel(id=1, username="variation-owner", hashed_password="hash"))
            db.add(database.QuestionVersionRecord(question_version_id="QV_SOURCE", question_id="Q_SOURCE", version=1, stem="原题"))
            db.add(database.LearningAttemptRecord(attempt_id="ATT_SOURCE", learner_id=1, attempt_type="practice"))
            db.add(database.LearningAttemptItemRecord(attempt_item_id="ITEM_SOURCE", attempt_id="ATT_SOURCE", question_version_id="QV_SOURCE"))
            db.add(database.GradingResultRecord(artifact_id="GRADE_SOURCE", attempt_item_id="ITEM_SOURCE", version=1))
            db.add(database.AuditResultRecord(audit_id="AUD_OLD_SOURCE", source_artifact_id="GRADE_SOURCE", source_artifact_version=1, decision="pass", status="completed"))
            db.add(database.MistakeRecord(id=91, user_id=1, question_id="Q_SOURCE", attempt_item_id="ITEM_SOURCE", question_version_id="QV_SOURCE", status="active"))
            db.add(database.QuestionKPLinkRecord(question_version_id="QV_SOURCE", kp_id="KP_1"))
            db.commit()
            payload = {"status": "success", "run_id": "RUN_1", "steps": [], "final": {
                "artifact": {"artifact_type": "question_variation", "title": "变式", "source_id": "QV_VARIATION", "content": {
                    "stem": "安全变式题干", "question_type": "single_choice", "difficulty": 2, "kp_ids": ["KP_1"],
                    "source_mistake_id": 91, "source_question_version_id": "QV_SOURCE",
                    "answer": "SENTINEL_ANSWER", "analysis": "SENTINEL_ANALYSIS",
                    "nested": {"reference_answer": "SENTINEL_REFERENCE"},
                }},
                "evidence_pack": {"pack_id": "EP_1", "source_scope": "mistake_variation", "source_id": "QV_SOURCE", "resolved_kp_ids": ["KP_1"], "items": []},
                "audit": {"decision": "pass", "source_scope": "audit_agent", "source_id": "QV_VARIATION", "audit_id": "AUD_OLD_SOURCE"},
            }}
            result = create_training_task(db, 1, {"task_type": "mistake_variation", "title": "变式", "query": "生成变式", "inputs": {"mistake_id": 91}, "options": {}}, orchestration_runner=lambda **_: payload)
            detail = get_training_task_result(db, 1, result["task_id"])
            variation = db.query(database.VariationSetRecord).one()
            current_audit = db.query(database.AuditResultRecord).filter(database.AuditResultRecord.audit_id != "AUD_OLD_SOURCE").one()
            persisted = db.query(database.TrainingTaskRecord).one().artifact_json
            selected = VariationRepository(self.Session).select_owned_question_versions(1)

        self.assertEqual(result["status"], "completed")
        self.assertEqual(selected[0].stem, "安全变式题干")
        self.assertEqual(selected[0].kp_ids, ("KP_1",))
        self.assertEqual(variation.audit_id, current_audit.audit_id)
        self.assertEqual(current_audit.source_artifact_id, "QV_VARIATION")
        self.assertNotEqual(variation.audit_id, "AUD_OLD_SOURCE")
        for sentinel in ("SENTINEL_ANSWER", "SENTINEL_REFERENCE", "SENTINEL_ANALYSIS"):
            self.assertNotIn(sentinel, json.dumps(result, ensure_ascii=False))
            self.assertNotIn(sentinel, persisted)
            self.assertNotIn(sentinel, json.dumps(detail, ensure_ascii=False))

    def test_variation_rejects_passed_source_audits_that_are_not_completed(self):
        for status in ("cancelled", "failed", "pending"):
            with self.subTest(status=status), self.Session() as db:
                user_id = ("cancelled", "failed", "pending").index(status) + 1
                mistake_id = ("cancelled", "failed", "pending").index(status) + 91
                suffix = status.upper()
                source_question_version_id = f"QV_SOURCE_{suffix}"
                attempt_id = f"ATT_SOURCE_{suffix}"
                attempt_item_id = f"ITEM_SOURCE_{suffix}"
                grade_artifact_id = f"GRADE_SOURCE_{suffix}"
                db.add(database.UserModel(id=user_id, username=f"variation-owner-{status}", hashed_password="hash"))
                db.add(database.QuestionVersionRecord(question_version_id=source_question_version_id, question_id=f"Q_SOURCE_{suffix}", version=1, stem="原题"))
                db.add(database.LearningAttemptRecord(attempt_id=attempt_id, learner_id=user_id, attempt_type="practice"))
                db.add(database.LearningAttemptItemRecord(attempt_item_id=attempt_item_id, attempt_id=attempt_id, question_version_id=source_question_version_id))
                db.add(database.GradingResultRecord(artifact_id=grade_artifact_id, attempt_item_id=attempt_item_id, version=1))
                db.add(database.AuditResultRecord(audit_id=f"AUD_SOURCE_{suffix}", source_artifact_id=grade_artifact_id, source_artifact_version=1, decision="pass", status=status))
                db.add(database.MistakeRecord(id=mistake_id, user_id=user_id, question_id=f"Q_SOURCE_{suffix}", attempt_item_id=attempt_item_id, question_version_id=source_question_version_id, status="active"))
                db.add(database.QuestionKPLinkRecord(question_version_id=source_question_version_id, kp_id="KP_1"))
                db.commit()
                expected_audit_count = db.query(database.AuditResultRecord).count()
                runner = Mock()
                publisher = Mock()

                with self.assertRaises(MistakeVariationNotFound):
                    create_training_task(
                        db,
                        user_id,
                        {"task_type": "mistake_variation", "title": "变式", "query": "生成变式", "inputs": {"mistake_id": mistake_id}, "options": {}},
                        orchestration_runner=runner,
                        variation_publisher=publisher,
                    )

                runner.assert_not_called()
                publisher.assert_not_called()
                self.assertEqual(db.query(database.TrainingTaskRecord).count(), 0)
                self.assertEqual(db.query(database.VariationSetRecord).count(), 0)
                self.assertEqual(db.query(database.AuditResultRecord).count(), expected_audit_count)

    def test_rejected_generation_creates_no_variation_or_candidate_audit(self):
        with self.Session() as db:
            result = create_training_task(db, 1, {"task_type": "handout_generation", "title": "拒绝", "query": "拒绝", "inputs": {}, "options": {}}, orchestration_runner=lambda **_: {"status": "rejected", "final": {"audit": {"decision": "reject"}}})
            self.assertEqual(result["status"], "failed")
            self.assertEqual(db.query(database.VariationSetRecord).count(), 0)
            self.assertEqual(db.query(database.AuditResultRecord).count(), 0)

    def test_deterministic_generation_runtime_supports_context_prefetch_tools(self):
        runtime = DeterministicGenerationRuntime()
        db = object()

        learner_context = runtime.execute(
            "build_learner_context_brief",
            "memory_agent",
            db=db,
            user_id=1,
        )
        diagnosis = runtime.execute(
            "build_diagnosis_snapshot",
            "diagnosis_agent",
            db=db,
            user_id=1,
            persist=False,
        )

        self.assertEqual(learner_context.status, "success")
        self.assertEqual(
            runtime.tool_names(),
            {
                "build_learner_context_brief",
                "build_diagnosis_snapshot",
                "build_evidence_pack",
                "generate_handout",
                "generate_knowledge_card",
                "audit_artifact",
            },
        )
        self.assertIsInstance(learner_context.result, LearnerContextBrief)
        self.assertEqual(learner_context.result.learner_id, "1")
        self.assertEqual(learner_context.result.kp_ids, ["kp:formal:001"])
        self.assertEqual(diagnosis.status, "success")
        self.assertIsInstance(diagnosis.result, DiagnosisReport)
        self.assertEqual(diagnosis.result.source_scope, "diagnosis_agent")
        self.assertEqual(
            [(name, agent, kwargs) for name, agent, kwargs in runtime.calls],
            [
                ("build_learner_context_brief", "memory_agent", {"db": db, "user_id": 1}),
                ("build_diagnosis_snapshot", "diagnosis_agent", {"db": db, "user_id": 1, "persist": False}),
            ],
        )

    @staticmethod
    def pass_grading_runner(*, profile, memories, submission):
        return {
            "score": 90,
            "max_score": 100,
            "is_correct": True,
            "error_types": [],
            "error_reason": "",
            "confidence": 0.9,
            "feedback": "回答正确。",
        }

    @staticmethod
    def wrong_grading_runner(*, profile, memories, submission):
        return {
            "score": 20,
            "max_score": 100,
            "is_correct": False,
            "error_types": ["证型-方剂匹配错误"],
            "error_reason": "将脾胃气虚证误判为中焦虚寒证。",
            "confidence": 0.9,
            "feedback": "请复习四君子汤与理中丸的证型区别。",
        }

    @staticmethod
    def failing_grading_runner(**_):
        raise RuntimeError("runner failed")

    def test_workspace_runner_failure_retains_authoritative_attempt_only(self):
        with self.Session() as db:
            with self.assertRaisesRegex(RuntimeError, "runner failed"):
                create_training_task(
                    db,
                    1,
                    self.request,
                    grading_runner=self.failing_grading_runner,
                )

            self.assertEqual(db.query(database.LearningAttemptRecord).count(), 1)
            self.assertEqual(db.query(database.LearningAttemptItemRecord).count(), 1)
            self.assertEqual(db.query(database.TrainingTaskRecord).count(), 0)
            self.assertEqual(
                db.query(database.LearningActivityRecord)
                .filter_by(activity_type="training_workspace_task")
                .count(),
                0,
            )
            self.assertEqual(
                db.query(database.AgentEvent)
                .filter_by(agent_name="training_workspace_facade")
                .count(),
                0,
            )
            self.assertEqual(db.query(database.GradingResultRecord).count(), 0)
            self.assertEqual(db.query(database.AuditResultRecord).count(), 0)

    def test_rejects_missing_paper_distribution_before_runtime_execution(self):
        runner = Mock()
        request = {
            "task_type": "paper_generation",
            "title": "缺少配额试卷",
            "query": "测试",
            "inputs": {},
            "options": {
                "question_count": 2,
                "types": ["single_choice", "short_answer"],
                "need_audit": True,
            },
        }
        with self.Session() as db:
            with self.assertRaisesRegex(InvalidTrainingTaskRequest, "distribution"):
                create_training_task(db, 1, request, orchestration_runner=runner)
            self.assert_no_task_records(db)
            self.assertEqual(db.query(database.PaperInstanceRecord).count(), 0)
        runner.assert_not_called()

    def test_rejects_duplicate_paper_types_before_runtime_execution(self):
        runtime = Mock()
        request = {
            "task_type": "paper_generation",
            "title": "重复题型试卷",
            "query": "测试",
            "inputs": {},
            "options": {
                "question_count": 1,
                "types": ["short_answer", "short_answer"],
                "distribution": {"short_answer": 1},
                "need_audit": True,
            },
        }
        with self.Session() as db:
            with self.assertRaisesRegex(InvalidTrainingTaskRequest, "types"):
                create_training_task(db, 1, request, orchestration_runner=runtime)
            self.assert_no_task_records(db)
            self.assertEqual(db.query(database.PaperInstanceRecord).count(), 0)
        runtime.assert_not_called()

    def test_rejected_paper_omits_all_unreviewed_fields_from_response_storage_and_get(self):
        sentinels = {
            "title": "SENTINEL_TOP_TITLE",
            "summary": "SENTINEL_TOP_SUMMARY",
            "trace": "SENTINEL_TOP_TRACE",
            "evidence": "SENTINEL_TOP_EVIDENCE",
            "learning": "SENTINEL_TOP_LEARNING",
            "custom": "SENTINEL_TOP_CUSTOM",
            "artifact": "SENTINEL_ARTIFACT",
        }

        def rejected_runner(**kwargs):
            value = kwargs["value"]
            return {
                "task_id": value.task_id,
                "task_type": value.task_type,
                "status": "completed",
                "title": sentinels["title"],
                "summary": sentinels["summary"],
                "orchestration_run_id": "RUN_REJECT",
                "artifact": {"artifact_type": "paper", "title": sentinels["artifact"], "content": {"body": sentinels["artifact"]}},
                "evidence_pack": {"pack_id": sentinels["evidence"], "items": [{"summary": sentinels["evidence"]}]},
                "audit": {"decision": "reject", "reason": sentinels["artifact"]},
                "trace": [{"summary": sentinels["trace"]}],
                "learning_updates": {"note": sentinels["learning"]},
                "custom_field": sentinels["custom"],
            }

        request = {
            "task_type": "paper_generation", "title": "拒绝试卷", "query": "测试",
            "inputs": {}, "options": {"question_count": 1, "types": ["short_answer"],
                                       "distribution": {"short_answer": 1}, "need_audit": True},
        }
        with self.Session() as db:
            result = create_training_task(db, 1, request, orchestration_runner=rejected_runner)
            task = db.query(database.TrainingTaskRecord).filter_by(task_id=result["task_id"]).one()
            detail = get_training_task_result(db, 1, result["task_id"])
            serialized_values = (
                json.dumps(result, ensure_ascii=False),
                json.dumps({
                    "artifact": task.artifact_json,
                    "evidence": task.evidence_pack_json,
                    "audit": task.audit_json,
                    "trace": task.trace_json,
                    "learning": task.learning_updates_json,
                    "title": task.title,
                }, ensure_ascii=False),
                json.dumps(detail, ensure_ascii=False),
            )
            for serialized in serialized_values:
                for sentinel in sentinels.values():
                    self.assertNotIn(sentinel, serialized)

    def test_paper_needs_clarification_omits_all_unreviewed_fields_from_response_storage_and_get(self):
        sentinels = (
            "SENTINEL_CLARIFY_TITLE", "SENTINEL_CLARIFY_SUMMARY", "SENTINEL_CLARIFY_TRACE",
            "SENTINEL_CLARIFY_EVIDENCE", "SENTINEL_CLARIFY_LEARNING", "SENTINEL_CLARIFY_CUSTOM",
            "SENTINEL_CLARIFY_ARTIFACT",
        )

        def clarification_runner(**kwargs):
            value = kwargs["value"]
            return {
                "task_id": value.task_id, "task_type": value.task_type, "status": "completed",
                "title": sentinels[0], "summary": sentinels[1], "orchestration_run_id": "RUN_CLARIFY",
                "artifact": {"artifact_type": "paper", "title": sentinels[6], "content": {
                    "paper_blueprint": {"question_count": 1, "kp_ids": ["KP_1"],
                                        "types": ["short_answer"],
                                        "distribution": {"short_answer": 1}, "difficulty": 2},
                    "body": sentinels[6],
                }},
                "evidence_pack": {"pack_id": sentinels[3], "items": []},
                "audit": {"decision": "pass", "reason": sentinels[6]},
                "trace": [{"summary": sentinels[2]}],
                "learning_updates": {"note": sentinels[4]},
                "custom_field": sentinels[5],
            }

        request = {
            "task_type": "paper_generation", "title": "澄清试卷", "query": "测试",
            "inputs": {}, "options": {"question_count": 1, "types": ["short_answer"],
                                       "distribution": {"short_answer": 1}, "need_audit": True},
        }
        with self.Session() as db, patch(
            "APP.backend.training_workspace_service.execute_training_orchestration",
            side_effect=lambda **kwargs: clarification_runner(value=kwargs["value"]),
        ), patch(
            "APP.backend.paper_generation_service.QuestionRepository"
        ) as repository_class:
            from APP.backend.question_repository import QuestionShortage
            repository_class.return_value.select.return_value = QuestionShortage(Mock(), 1, 0)
            result = create_training_task(db, 1, request, orchestration_runner=clarification_runner)
            task = db.query(database.TrainingTaskRecord).filter_by(task_id=result["task_id"]).one()
            detail = get_training_task_result(db, 1, result["task_id"])
            serialized_values = (
                json.dumps(result, ensure_ascii=False),
                json.dumps({
                    "artifact": task.artifact_json, "evidence": task.evidence_pack_json,
                    "audit": task.audit_json, "trace": task.trace_json,
                    "learning": task.learning_updates_json, "title": task.title,
                }, ensure_ascii=False),
                json.dumps(detail, ensure_ascii=False),
            )
            for serialized in serialized_values:
                for sentinel in sentinels:
                    self.assertNotIn(sentinel, serialized)
            self.assertEqual(result["status"], "needs_clarification")
            self.assertEqual(db.query(database.PaperInstanceRecord).count(), 0)

    def test_paper_projection_failure_rolls_back_paper_items_and_task(self):
        question = QuestionVersionView(
            "Q1:v1", "Q1", "short_answer", "安全题干", "秘密答案", "秘密解析",
            ("KP_1",), 2, "curated",
        )

        def passing_runner(**kwargs):
            value = kwargs["value"]
            blueprint = {"question_count": 1, "kp_ids": ["KP_1"],
                         "types": ["short_answer"], "distribution": {"short_answer": 1},
                         "difficulty": 2}
            return {
                "task_id": value.task_id, "task_type": value.task_type, "status": "completed",
                "title": value.title, "orchestration_run_id": "RUN_PASS",
                "artifact": {"artifact_type": "paper", "title": value.title,
                             "content": {"paper_blueprint": blueprint}},
                "evidence_pack": {"pack_id": "EP_PASS", "source_scope": "test", "source_id": "EP_PASS", "items": []},
                "audit": {"decision": "pass", "reason": "passed"},
                "trace": [{"step_id": "orchestration", "run_id": "RUN_PASS"}],
                "learning_updates": {},
            }

        request = {
            "task_type": "paper_generation", "title": "事务试卷", "query": "测试",
            "inputs": {}, "options": {"question_count": 1, "types": ["short_answer"],
                                       "distribution": {"short_answer": 1}, "need_audit": True},
        }
        repository = Mock()
        repository.select.return_value = (question,)
        with self.Session() as db, patch(
            "APP.backend.paper_generation_service.QuestionRepository", return_value=repository,
        ), patch(
            "APP.backend.training_workspace_service._persist_task",
            side_effect=RuntimeError("workspace projection failed"),
        ):
            with self.assertRaisesRegex(RuntimeError, "workspace projection failed"):
                create_training_task(db, 1, request, orchestration_runner=passing_runner)

        with self.Session() as db:
            self.assertEqual(db.query(database.PaperInstanceRecord).count(), 0)
            self.assertEqual(db.query(database.PaperItemRecord).count(), 0)
            self.assertEqual(db.query(database.TrainingTaskRecord).count(), 0)

    def test_workspace_projection_failure_rolls_back_transaction_b_but_preserves_a(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "workspace-projection-failure.db"
            engine = create_engine(f"sqlite:///{path}")
            event.listen(engine, "connect", lambda conn, _: conn.execute("PRAGMA foreign_keys=ON"))
            database.Base.metadata.create_all(bind=engine)
            Session = sessionmaker(bind=engine)
            with Session() as db:
                db.add(database.UserModel(id=1, username="workspace-learner", hashed_password="x"))
                db.commit()
            try:
                with Session() as db, patch(
                    "APP.backend.training_workspace_service._persist_task",
                    side_effect=RuntimeError("workspace projection failed"),
                ):
                    with self.assertRaisesRegex(RuntimeError, "workspace projection failed"):
                        create_training_task(
                            db,
                            1,
                            self.request,
                            grading_runner=self.pass_grading_runner,
                        )
                engine.dispose()

                restarted = create_engine(f"sqlite:///{path}")
                try:
                    with sessionmaker(bind=restarted)() as db:
                        self.assertEqual(db.query(database.LearningAttemptRecord).count(), 1)
                        self.assertEqual(db.query(database.LearningAttemptItemRecord).count(), 1)
                        for model in (
                            database.EvidencePackRecord,
                            database.GradingResultRecord,
                            database.AuditResultRecord,
                            database.LearningWritebackReceipt,
                            database.TrainingTaskRecord,
                            database.LearningActivityRecord,
                            database.MistakeRecord,
                            database.KnowledgeMasteryState,
                            database.MasteryHistoryRecord,
                            database.LearnerKPReviewState,
                            database.ReviewTaskRecord,
                            database.AgentEvent,
                        ):
                            with self.subTest(model=model.__name__):
                                self.assertEqual(db.query(model).count(), 0)
                finally:
                    restarted.dispose()
            finally:
                engine.dispose()

    def test_workspace_practice_projects_authoritative_ids_and_learning_updates(self):
        with self.Session() as db:
            result = create_training_task(
                db,
                1,
                self.request,
                grading_runner=self.pass_grading_runner,
            )

            self.assertEqual(result["status"], "completed")
            self.assertTrue(result["attempt_id"])
            self.assertTrue(result["grading_artifact_id"])
            self.assertEqual(result["learning_updates"]["writeback"]["status"], "applied")
            self.assertEqual(db.query(database.LearningAttemptRecord).count(), 1)
            self.assertEqual(db.query(database.LearningAttemptItemRecord).count(), 1)
            self.assertEqual(db.query(database.GradingResultRecord).count(), 1)
            self.assertEqual(db.query(database.TrainingTaskRecord).count(), 1)

    def test_workspace_practice_detail_returns_authoritative_grading_ids(self):
        with self.Session() as db:
            created = create_training_task(
                db,
                1,
                self.request,
                grading_runner=self.pass_grading_runner,
            )
            detail = get_training_task_result(db, 1, created["task_id"])

            self.assertEqual(
                {field: detail[field] for field in (
                    "attempt_id",
                    "attempt_item_id",
                    "grading_artifact_id",
                    "grading_artifact_version",
                    "audit_id",
                )},
                {field: created[field] for field in (
                    "attempt_id",
                    "attempt_item_id",
                    "grading_artifact_id",
                    "grading_artifact_version",
                    "audit_id",
                )},
            )

    def test_wraps_practice_grading_and_persists_workspace_ledger(self):
        with self.Session() as db:
            result = create_training_task(
                db,
                1,
                self.request,
                grading_runner=self.wrong_grading_runner,
            )

            self.assertEqual(result["task_type"], "practice_grading")
            self.assertEqual(result["status"], "completed")
            self.assertEqual(result["artifact"]["artifact_type"], "grading_result")
            self.assertIn("grading", result["artifact"]["content"])
            self.assertTrue(result["trace"])
            self.assertEqual(result["trace"][0]["agent"], "training_workspace_facade")
            self.assertTrue(result["learning_updates"]["activity_recorded"])
            self.assertTrue(result["learning_updates"]["mistake_recorded"])
            self.assertEqual(
                db.query(database.MistakeRecord)
                .filter_by(
                    user_id=1,
                    question_id="demo-sijunzi-001",
                    status="active",
                )
                .count(),
                1,
            )
            self.assertEqual(
                [action["task_type"] for action in result["next_actions"]],
                ["mistake_variation", "handout_generation"],
            )
            self.assertEqual(result["audit"]["decision"], "pass")
            self.assertEqual(
                result["evidence_pack"]["resolved_kp_ids"],
                ["四君子汤", "脾胃气虚证"],
            )

            task = db.query(database.TrainingTaskRecord).filter_by(task_id=result["task_id"]).one()
            self.assertEqual(task.user_id, 1)
            self.assertEqual(task.artifact_type, "grading_result")
            self.assertEqual(task.evidence_pack_id, result["evidence_pack"]["pack_id"])
            self.assertEqual(json.loads(task.evidence_pack_json), result["evidence_pack"])
            self.assertEqual(json.loads(task.artifact_json), result["artifact"])
            self.assertEqual(json.loads(task.trace_json), result["trace"])
            self.assertEqual(db.query(database.EvidencePackRecord).count(), 1)
            self.assertEqual(
                db.query(database.LearningActivityRecord)
                .filter_by(
                    user_id=1,
                    activity_type="training_workspace_task",
                    resource_type="training_task",
                    resource_id=result["task_id"],
                )
                .count(),
                1,
            )
            self.assertEqual(db.query(database.QuestionAttempt).count(), 0)
            self.assertEqual(
                db.query(database.LearningActivityRecord)
                .filter_by(activity_type="question_attempt")
                .count(),
                0,
            )
            self.assertEqual(db.query(database.MistakeRecord).count(), 1)
            event = db.query(database.AgentEvent).filter_by(
                user_id=1,
                agent_name="training_workspace_facade",
                event_type="practice_grading",
            ).one()
            self.assertEqual(json.loads(event.payload)["task_id"], result["task_id"])

    def test_default_runtime_audits_default_chinese_generation_tasks_against_real_evidence_items(self):
        for task_type, artifact_type in (
            ("handout_generation", "handout"),
            ("knowledge_card_generation", "knowledge_card"),
        ):
            with self.subTest(task_type=task_type):
                engine = create_engine(
                    "sqlite://",
                    connect_args={"check_same_thread": False},
                    poolclass=StaticPool,
                )
                Session = sessionmaker(bind=engine)
                database.Base.metadata.create_all(bind=engine)
                try:
                    with Session() as db:
                        db.add_all([
                            database.KnowledgePoint(
                                kp_id="KP_FJ_001",
                                name="四君子汤",
                                aliases_json="[]",
                                description="四君子汤主治脾胃气虚证，核心治法是益气健脾。",
                            ),
                            database.KnowledgePoint(
                                kp_id="KP_ZD_021",
                                name="脾胃气虚证",
                                aliases_json="[]",
                                description="脾胃气虚证可见食少、乏力、便溏等表现。",
                            ),
                            database.KnowledgePoint(
                                kp_id="KP_INACTIVE_018",
                                name="理中丸",
                                aliases_json="[]",
                                description="理中丸偏于温中祛寒，适用于中焦虚寒证。",
                                status="inactive",
                            ),
                        ])
                        db.commit()

                        result = create_training_task(
                            db,
                            1,
                            {
                                "task_type": task_type,
                                "title": "个性化讲义" if task_type == "handout_generation" else "知识卡片",
                                "query": "围绕四君子汤与脾胃气虚证完成 15 分钟复习",
                                "inputs": {
                                    "knowledge_points": ["四君子汤", "脾胃气虚证"],
                                    "difficulty": 2,
                                    "duration_minutes": 15,
                                },
                                "options": {"save_activity": True, "need_audit": True},
                            },
                            runtime=build_default_tool_runtime(),
                        )

                        evidence_items = result["evidence_pack"]["items"]
                        self.assertEqual(result["status"], "completed")
                        self.assertEqual(result["artifact"]["artifact_type"], artifact_type)
                        self.assertEqual(result["audit"]["decision"], "pass")
                        self.assertEqual(
                            {(item["source_scope"], item["source_id"]) for item in evidence_items},
                            {("knowledge_point", "KP_FJ_001"), ("knowledge_point", "KP_ZD_021")},
                        )
                        claims = result["artifact"]["content"]["claims"]
                        self.assertEqual(
                            {(claim["text"], tuple(claim["evidence_ids"])) for claim in claims},
                            {
                                (item["summary"], (item["source_id"],))
                                for item in evidence_items
                            },
                        )
                        self.assertFalse(any("理中丸" in claim["text"] for claim in claims))
                finally:
                    engine.dispose()

    def test_generation_tasks_use_runtime_pipeline_and_persist_without_practice_writes(self):
        for task_type, artifact_type, generation_tool, agent_name in (
            ("handout_generation", "handout", "generate_handout", "expert_handout"),
            ("knowledge_card_generation", "knowledge_card", "generate_knowledge_card", "expert_knowledge_card"),
        ):
            with self.subTest(task_type=task_type):
                runtime = DeterministicGenerationRuntime()
                request = {
                    "task_type": task_type,
                    "title": "四君子汤复习资料",
                    "query": "四君子汤与脾胃气虚证",
                    "inputs": {"kp_ids": ["kp:formal:001"]},
                    "options": {"difficulty": 2},
                }
                with self.Session() as db:
                    result = create_training_task(db, 1, request, runtime=runtime)

                    self.assertEqual(result["status"], "completed")
                    self.assertEqual(result["artifact"]["artifact_type"], artifact_type)
                    self.assertEqual(result["audit"]["decision"], "pass")
                    self.assertEqual(result["evidence_pack"]["source_id"], "EP_fake")
                    self.assertEqual(result["evidence_pack"]["items"][0]["confidence"], 0.9)
                    self.assertEqual(
                        [(name, agent) for name, agent, _ in runtime.calls],
                        [
                            ("build_learner_context_brief", "memory_agent"),
                            ("build_learner_context_brief", "memory_agent"),
                            ("build_diagnosis_snapshot", "diagnosis_agent"),
                            ("build_evidence_pack", "knowledge_base_agent"),
                            (generation_tool, agent_name),
                            ("audit_artifact", "audit_agent"),
                        ],
                    )
                    self.assertEqual(
                        runtime.calls[3][2]["learner_context"].kp_ids,
                        ["kp:formal:001"],
                    )
                    self.assertEqual(
                        [step["action"] for step in result["trace"]],
                        [
                            "execute_plan",
                            "build_context",
                            "build_diagnosis_snapshot",
                            "build_evidence_pack",
                            generation_tool,
                            "review_artifact",
                            "publication_gate",
                        ],
                    )
                    self.assertTrue(all(step["status"] == "success" for step in result["trace"]))
                    self.assertEqual(
                        db.query(database.TrainingTaskRecord).filter_by(task_id=result["task_id"]).count(),
                        1,
                    )
                    self.assertEqual(
                        db.query(database.LearningActivityRecord).filter_by(
                            user_id=1,
                            activity_type="training_workspace_task",
                            resource_id=result["task_id"],
                        ).count(),
                        1,
                    )
                    self.assertEqual(db.query(database.AgentEvent).filter_by(event_type=task_type).count(), 1)
                    self.assertEqual(db.query(database.QuestionAttempt).count(), 0)
                    self.assertEqual(db.query(database.MistakeRecord).count(), 0)

    def test_generation_artifacts_expose_content_payload_and_roundtrip_detail(self):
        cases = (
            (
                "handout_generation",
                "handout",
                {"sections": [{"title": "方剂要点", "body": "四君子汤益气健脾。"}]},
            ),
            (
                "knowledge_card_generation",
                "knowledge_card",
                {"front": "四君子汤主治何证？", "back": "脾胃气虚证。", "memory_anchor": "四味君臣佐使"},
            ),
        )
        for task_type, artifact_type, content in cases:
            with self.subTest(task_type=task_type), self.Session() as db:
                runtime = DeterministicGenerationRuntime()
                generated = ExpertArtifact(
                    artifact_type=artifact_type,
                    title=f"生成的{artifact_type}",
                    content=content,
                    source_scope="expert_agent",
                    source_id=f"artifact:{artifact_type}",
                    kp_ids=["kp:formal:001"],
                    confidence=0.9,
                )
                original_execute = runtime.execute

                def execute(tool_name, agent_name, **kwargs):
                    result = original_execute(tool_name, agent_name, **kwargs)
                    if tool_name in {"generate_handout", "generate_knowledge_card"}:
                        return result.model_copy(update={"result": generated})
                    return result

                runtime.execute = execute
                created = create_training_task(
                    db,
                    1,
                    {
                        "task_type": task_type,
                        "title": "四君子汤复习资料",
                        "query": "四君子汤与脾胃气虚证",
                        "inputs": {"kp_ids": ["kp:formal:001"]},
                        "options": {},
                    },
                    runtime=runtime,
                )
                detail = get_training_task_result(db, 1, created["task_id"])

                self.assertEqual(created["artifact"]["artifact_type"], artifact_type)
                self.assertEqual(created["artifact"]["title"], f"生成的{artifact_type}")
                self.assertEqual(created["artifact"]["content"], content)
                self.assertNotIn("content", created["artifact"]["content"])
                self.assertEqual(detail["artifact"], created["artifact"])
                self.assertEqual(detail["evidence_pack"], created["evidence_pack"])
                self.assertEqual(detail["evidence_pack"]["items"], created["evidence_pack"]["items"])
                self.assertEqual(detail["evidence_pack"]["resolved_kp_ids"], ["kp:formal:001"])
                self.assertEqual(detail["evidence_pack"]["items"][0]["confidence"], 0.9)

    def test_generation_task_type_cannot_be_overridden_by_generated_artifact_type(self):
        runtime = DeterministicGenerationRuntime()
        original_execute = runtime.execute

        def execute(tool_name, agent_name, **kwargs):
            result = original_execute(tool_name, agent_name, **kwargs)
            if tool_name == "generate_handout":
                generated = ExpertArtifact(
                    artifact_type="knowledge_card",
                    title="错误类型产物",
                    content={"sections": [{"title": "内容", "body": "仍可展示"}]},
                    source_scope="expert_agent",
                    source_id="artifact:mismatched",
                    kp_ids=["kp:formal:001"],
                    confidence=0.9,
                )
                return result.model_copy(update={"result": generated})
            return result

        runtime.execute = execute
        with self.Session() as db:
            result = create_training_task(
                db,
                1,
                {
                    "task_type": "handout_generation",
                    "title": "四君子汤复习资料",
                    "query": "四君子汤与脾胃气虚证",
                    "inputs": {"kp_ids": ["kp:formal:001"]},
                    "options": {},
                },
                runtime=runtime,
            )

        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["artifact"], {
            "artifact_type": "handout",
            "title": "四君子汤复习资料",
            "content": {},
        })
        self.assertEqual(result["audit"]["decision"], "pass")
        self.assertIn("artifact_type", result["audit"]["reason"])
        self.assertEqual(
            [name for name, _, _ in runtime.calls],
            [
                "build_learner_context_brief",
                "build_learner_context_brief",
                "build_diagnosis_snapshot",
                "build_evidence_pack",
                "generate_handout",
                "audit_artifact",
            ],
        )
        self.assertEqual(result["trace"][-1]["action"], "publication_gate")
        self.assertIn("artifact_type", result["trace"][-1]["summary"])

        with self.Session() as db:
            task = db.query(database.TrainingTaskRecord).filter_by(task_id=result["task_id"]).one()
            activity = db.query(database.LearningActivityRecord).filter_by(resource_id=result["task_id"]).one()
            self.assertEqual(task.status, "failed")
            self.assertEqual(json.loads(task.audit_json), result["audit"])
            self.assertEqual(activity.completion_status, "failed")

    def test_generation_only_completes_when_audit_decision_is_pass(self):
        for decision, expected_status in (
            ("revise", "failed"),
            ("reject", "failed"),
            ("human_review", "needs_human_review"),
            ("needs_human_review", "needs_human_review"),
        ):
            with self.subTest(decision=decision):
                runtime = DeterministicGenerationRuntime()
                original_execute = runtime.execute

                def execute(tool_name, agent_name, **kwargs):
                    result = original_execute(tool_name, agent_name, **kwargs)
                    if tool_name == "audit_artifact":
                        return result.model_copy(update={"result": {**result.result, "decision": decision, "reason": f"fake {decision}"}})
                    return result

                runtime.execute = execute
                with self.Session() as db:
                    result = create_training_task(
                        db,
                        1,
                        {
                            "task_type": "handout_generation",
                            "title": "四君子汤复习资料",
                            "query": "四君子汤与脾胃气虚证",
                            "inputs": {"kp_ids": ["kp:formal:001"]},
                            "options": {},
                        },
                        runtime=runtime,
                    )

                    self.assertEqual(result["status"], expected_status)
                    self.assertEqual(result["audit"]["decision"], decision)
                    self.assertIn("audit", result["audit"]["reason"])
                    self.assertEqual(runtime.calls[-1][2]["artifact"].artifact_type, "handout")
                    self.assertEqual(runtime.calls[-1][2]["artifact"].title, "fake handout")
                    self.assertEqual(result["artifact"], {
                        "artifact_type": "handout",
                        "title": "四君子汤复习资料",
                        "content": {},
                    })
                    task = db.query(database.TrainingTaskRecord).filter_by(task_id=result["task_id"]).one()
                    activity = db.query(database.LearningActivityRecord).filter_by(resource_id=result["task_id"]).one()
                    self.assertEqual(task.status, expected_status)
                    self.assertEqual(json.loads(task.audit_json), result["audit"])
                    self.assertEqual(activity.completion_status, expected_status)

    def test_generation_invalid_audit_payload_and_runtime_exception_persist_structured_failure(self):
        for audit_result in (
            None,
            [],
            {"reason": "missing decision"},
            {"decision": "", "reason": "empty decision"},
            {"decision": "   ", "reason": "blank decision"},
            {"decision": "unknown", "reason": "unknown decision"},
        ):
            with self.subTest(audit_result=audit_result):
                runtime = DeterministicGenerationRuntime()
                original_execute = runtime.execute

                def execute(tool_name, agent_name, **kwargs):
                    result = original_execute(tool_name, agent_name, **kwargs)
                    if tool_name == "audit_artifact":
                        return result.model_copy(update={"result": audit_result})
                    return result

                runtime.execute = execute
                with self.Session() as db:
                    result = create_training_task(
                        db,
                        1,
                        {
                            "task_type": "handout_generation",
                            "title": "四君子汤复习资料",
                            "query": "四君子汤与脾胃气虚证",
                            "inputs": {},
                            "options": {},
                        },
                        runtime=runtime,
                    )

                    self.assertEqual(result["status"], "failed")
                    self.assertEqual(
                        result["audit"]["decision"],
                        "unknown" if audit_result == {"decision": "unknown", "reason": "unknown decision"} else "failed",
                    )
                    self.assertIn("audit", result["audit"]["reason"])
                    task = db.query(database.TrainingTaskRecord).filter_by(task_id=result["task_id"]).one()
                    self.assertEqual(task.status, "failed")
                    self.assertEqual(json.loads(task.audit_json), result["audit"])

        class ExplodingRuntime(DeterministicGenerationRuntime):
            def execute(self, tool_name, agent_name, **kwargs):
                if tool_name == "audit_artifact":
                    raise RuntimeError("unexpected audit runtime failure")
                return super().execute(tool_name, agent_name, **kwargs)

        with self.Session() as db:
            before_counts = {
                model: db.query(model).count()
                for model in (
                    database.TrainingTaskRecord,
                    database.QuestionAttempt,
                    database.LearningActivityRecord,
                    database.MistakeRecord,
                    database.AgentEvent,
                    database.EvidencePackRecord,
                )
            }
            result = create_training_task(
                db,
                1,
                {
                    "task_type": "handout_generation",
                    "title": "四君子汤复习资料",
                    "query": "四君子汤与脾胃气虚证",
                    "inputs": {},
                    "options": {},
                },
                runtime=ExplodingRuntime(),
            )
            self.assertEqual(result["status"], "failed")
            self.assertEqual(result["audit"]["decision"], "failed")
            self.assertEqual(db.query(database.TrainingTaskRecord).filter_by(task_id=result["task_id"]).count(), 1)

    def test_generation_normalizes_supported_audit_decisions_without_mutating_runtime_payload(self):
        for decision, expected_status, expected_decision in (
            (" pass ", "failed", "pass"),
            (" ReViSe ", "failed", "ReViSe"),
        ):
            with self.subTest(decision=decision), self.Session() as db:
                runtime = DeterministicGenerationRuntime()
                audit_payload = {"decision": decision, "reason": "保留原始审核原因"}
                original_execute = runtime.execute

                def execute(tool_name, agent_name, **kwargs):
                    result = original_execute(tool_name, agent_name, **kwargs)
                    if tool_name == "audit_artifact":
                        return result.model_copy(update={"result": {**result.result, **audit_payload}})
                    return result

                runtime.execute = execute
                result = create_training_task(
                    db,
                    1,
                    {
                        "task_type": "handout_generation",
                        "title": "四君子汤复习资料",
                        "query": "四君子汤与脾胃气虚证",
                        "inputs": {},
                        "options": {},
                    },
                    runtime=runtime,
                )

                self.assertEqual(result["status"], expected_status)
                self.assertEqual(result["audit"]["decision"], expected_decision)
                self.assertIn("audit", result["audit"]["reason"])
                self.assertEqual(audit_payload, {"decision": decision, "reason": "保留原始审核原因"})
                persisted = db.query(database.TrainingTaskRecord).filter_by(task_id=result["task_id"]).one()
                self.assertEqual(json.loads(persisted.audit_json), result["audit"])

    def test_generation_unknown_normalized_audit_decision_falls_back_to_needs_review(self):
        runtime = DeterministicGenerationRuntime()
        audit_payload = {"decision": " UnKnOwN ", "reason": "未知决策"}
        original_execute = runtime.execute

        def execute(tool_name, agent_name, **kwargs):
            result = original_execute(tool_name, agent_name, **kwargs)
            if tool_name == "audit_artifact":
                return result.model_copy(update={"result": audit_payload})
            return result

        runtime.execute = execute
        with self.Session() as db:
            result = create_training_task(
                db,
                1,
                {
                    "task_type": "handout_generation",
                    "title": "四君子汤复习资料",
                    "query": "四君子汤与脾胃气虚证",
                    "inputs": {},
                    "options": {},
                },
                runtime=runtime,
            )

        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["audit"]["decision"], "UnKnOwN")
        self.assertEqual(audit_payload["decision"], " UnKnOwN ")

    def test_generation_runtime_exception_persists_failed_workspace_result(self):
        class SideEffectThenExplodingRuntime(DeterministicGenerationRuntime):
            def execute(self, tool_name, agent_name, **kwargs):
                if tool_name == "build_evidence_pack":
                    kwargs["db"].add(
                        database.AgentEvent(
                            user_id=1,
                            session_id=None,
                            agent_name="side_effect_runtime",
                            event_type="runtime_side_effect",
                            input_summary="before failure",
                            output_summary="before failure",
                            payload="{}",
                        )
                    )
                    raise RuntimeError("runtime side effect failure")
                return super().execute(tool_name, agent_name, **kwargs)

        request = {
            "task_type": "handout_generation",
            "title": "四君子汤复习资料",
            "query": "四君子汤与脾胃气虚证",
            "inputs": {},
            "options": {},
        }
        db = self.Session()
        try:
            result = create_training_task(db, 1, request, runtime=SideEffectThenExplodingRuntime())
            self.assertEqual(result["status"], "failed")
            self.assertEqual(result["audit"]["decision"], "failed")
            self.assertEqual(db.query(database.AgentEvent).filter_by(event_type="runtime_side_effect").count(), 1)
        finally:
            db.close()

        with self.Session() as verification_db:
            self.assertEqual(verification_db.query(database.TrainingTaskRecord).count(), 1)
            self.assertEqual(verification_db.query(database.AgentEvent).filter_by(event_type="runtime_side_effect").count(), 1)

    def test_generation_maps_input_controls_to_generation_tool_with_option_overrides(self):
        for task_type, generation_tool in (
            ("handout_generation", "generate_handout"),
            ("knowledge_card_generation", "generate_knowledge_card"),
        ):
            with self.subTest(task_type=task_type):
                runtime = DeterministicGenerationRuntime()
                request = {
                    "task_type": task_type,
                    "title": "四君子汤复习资料",
                    "query": "四君子汤与脾胃气虚证",
                    "inputs": {"difficulty": 2, "duration_minutes": 15},
                    "options": {},
                }
                with self.Session() as db:
                    create_training_task(db, 1, request, runtime=runtime)

                generation_input = next(
                    kwargs for tool, _, kwargs in runtime.calls if tool == generation_tool
                )
                self.assertEqual(generation_input["request"]["difficulty"], 2)
                self.assertEqual(generation_input["request"]["expected_duration_min"], 15)

                override_runtime = DeterministicGenerationRuntime()
                override_request = {
                    **request,
                    "options": {"difficulty": 4, "expected_duration_min": 30},
                }
                with self.Session() as db:
                    create_training_task(db, 1, override_request, runtime=override_runtime)

                override_generation_input = next(
                    kwargs for tool, _, kwargs in override_runtime.calls if tool == generation_tool
                )
                self.assertEqual(override_generation_input["request"]["difficulty"], 4)
                self.assertEqual(override_generation_input["request"]["expected_duration_min"], 30)

    def test_generation_passes_textual_knowledge_prompts_without_fabricating_kp_ids(self):
        runtime = DeterministicGenerationRuntime()
        request = {
            "task_type": "handout_generation",
            "title": "四君子汤复习资料",
            "query": "四君子汤辨证复习",
            "inputs": {"knowledge_points": ["四君子汤", "脾胃气虚证"]},
            "options": {},
        }

        with self.Session() as db:
            create_training_task(db, 1, request, runtime=runtime)

        evidence_input = next(kwargs for tool, _, kwargs in runtime.calls if tool == "build_evidence_pack")
        self.assertEqual(
            evidence_input["query"],
            "四君子汤辨证复习",
        )
        self.assertEqual(evidence_input["learner_context"].kp_ids, ["kp:formal:001"])

    def test_generation_preserves_formal_kp_ids_alongside_textual_knowledge_prompts(self):
        runtime = DeterministicGenerationRuntime()
        request = {
            "task_type": "knowledge_card_generation",
            "title": "四君子汤复习资料",
            "query": "四君子汤辨证复习",
            "inputs": {
                "kp_ids": ["KP_FJ_001"],
                "knowledge_points": ["四君子汤"],
            },
            "options": {},
        }

        with self.Session() as db:
            create_training_task(db, 1, request, runtime=runtime)

        evidence_input = next(kwargs for tool, _, kwargs in runtime.calls if tool == "build_evidence_pack")
        generation_input = next(kwargs for tool, _, kwargs in runtime.calls if tool == "generate_knowledge_card")
        self.assertEqual(evidence_input["learner_context"].kp_ids, ["kp:formal:001"])
        self.assertEqual(evidence_input["query"], "四君子汤辨证复习")
        self.assertEqual(generation_input["request"]["kp_ids"], ["KP_FJ_001"])

    def test_generation_evidence_failure_returns_stable_empty_evidence_pack(self):
        for runtime in (
            DeterministicGenerationRuntime(failure_tool="build_evidence_pack"),
            DeterministicGenerationRuntime(empty_result_tool="build_evidence_pack"),
        ):
            with self.subTest(runtime=runtime):
                request = {
                    "task_type": "handout_generation",
                    "title": "四君子汤复习资料",
                    "query": "四君子汤与脾胃气虚证",
                    "inputs": {"kp_ids": ["kp:formal:001"]},
                    "options": {},
                }
                with self.Session() as db:
                    result = create_training_task(db, 1, request, runtime=runtime)

                self.assertEqual(result["status"], "failed")
                self.assertEqual(
                    result["evidence_pack"],
                    {
                        "pack_id": "",
                        "source_scope": "",
                        "source_id": "",
                        "resolved_kp_ids": [],
                        "items": [],
                    },
                )

    def test_generation_tool_failure_persists_failed_task_and_stops_pipeline(self):
        for failure_tool, expected_calls in (
            ("build_evidence_pack", ["build_learner_context_brief", "build_learner_context_brief", "build_diagnosis_snapshot", "build_evidence_pack"]),
            ("generate_handout", ["build_learner_context_brief", "build_learner_context_brief", "build_diagnosis_snapshot", "build_evidence_pack", "generate_handout"]),
            ("audit_artifact", ["build_learner_context_brief", "build_learner_context_brief", "build_diagnosis_snapshot", "build_evidence_pack", "generate_handout", "audit_artifact"]),
        ):
            with self.subTest(failure_tool=failure_tool):
                runtime = DeterministicGenerationRuntime(failure_tool=failure_tool)
                request = {
                    "task_type": "handout_generation",
                    "title": "四君子汤复习资料",
                    "query": "四君子汤与脾胃气虚证",
                    "inputs": {"kp_ids": ["kp:formal:001"]},
                    "options": {},
                }
                with self.Session() as db:
                    result = create_training_task(db, 1, request, runtime=runtime)

                    self.assertEqual(result["status"], "failed")
                    self.assertEqual(result["audit"]["decision"], "failed")
                    self.assertEqual(
                        [name for name, _, _ in runtime.calls],
                        expected_calls,
                    )
                    self.assertEqual(result["trace"][-1]["status"], "failed")
                    detail = get_training_task_result(db, 1, result["task_id"])
                    self.assertEqual(
                        detail["evidence_pack"]["pack_id"],
                        result["evidence_pack"]["pack_id"],
                    )
                    self.assertEqual(detail["evidence_pack"]["items"], [])
                    self.assertEqual(
                        db.query(database.TrainingTaskRecord).filter_by(task_id=result["task_id"], status="failed").count(),
                        1,
                    )
                    self.assertEqual(db.query(database.QuestionAttempt).count(), 0)
                    self.assertEqual(db.query(database.MistakeRecord).count(), 0)

    def test_rejects_invalid_generation_boundary_types_before_runtime_execution(self):
        invalid_requests = (
            {"task_type": ["handout_generation"]},
            {"task_type": "handout_generation", "title": 1},
            {"task_type": "handout_generation", "query": []},
            {"task_type": "handout_generation", "inputs": []},
            {"task_type": "handout_generation", "inputs": {"kp_ids": ["kp:formal:001", 2]}},
            {"task_type": "handout_generation", "options": []},
        )
        for request in invalid_requests:
            with self.subTest(request=request):
                runtime = DeterministicGenerationRuntime()
                with self.Session() as db:
                    with self.assertRaises(InvalidTrainingTaskRequest):
                        create_training_task(db, 1, request, runtime=runtime)
                    self.assert_no_task_records(db)
                self.assertEqual(runtime.calls, [])

    def test_hydrates_persisted_task_result_for_its_owner_only(self):
        with self.Session() as db:
            created = create_training_task(db, 1, self.request)

            result = get_training_task_result(db, 1, created["task_id"])

            self.assertIsNotNone(result)
            self.assertEqual(result["task_id"], created["task_id"])
            self.assertEqual(result["task_type"], created["task_type"])
            self.assertEqual(result["artifact"]["artifact_type"], "grading_result")
            self.assertEqual(result["trace"], created["trace"])
            self.assertEqual(result["learning_updates"], created["learning_updates"])
            self.assertEqual(result["evidence_pack"], created["evidence_pack"])
            self.assertTrue(result["evidence_pack"]["items"])
            self.assertEqual(result["evidence_pack"]["kp_ids"], ["四君子汤", "脾胃气虚证"])
            self.assertEqual(result["evidence_pack"]["resolved_kp_ids"], ["四君子汤", "脾胃气虚证"])
            self.assertEqual(result["evidence_pack"]["confidence"], 0.8)
            self.assertIsNone(get_training_task_result(db, 2, created["task_id"]))

    def test_task_detail_uses_task_type_specific_summaries_without_regressing_practice(self):
        cases = (
            ("handout_generation", "handout", "讲义已生成。"),
            ("knowledge_card_generation", "knowledge_card", "知识卡已生成。"),
        )
        with self.Session() as db:
            for task_type, artifact_type, expected_summary in cases:
                created = create_training_task(
                    db,
                    1,
                    {
                        "task_type": task_type,
                        "title": "四君子汤复习资料",
                        "query": "四君子汤与脾胃气虚证",
                        "inputs": {"kp_ids": ["kp:formal:001"]},
                        "options": {},
                    },
                    runtime=DeterministicGenerationRuntime(),
                )
                detail = get_training_task_result(db, 1, created["task_id"])

                self.assertEqual(detail["artifact"]["artifact_type"], artifact_type)
                self.assertEqual(detail["summary"], expected_summary)

            practice = create_training_task(db, 1, self.request)
            practice_detail = get_training_task_result(db, 1, practice["task_id"])
            learning_task = db.query(database.LearningTask).filter_by(
                user_id=1,
                task_id=practice["task_id"],
            ).one()

            self.assertEqual(practice_detail["summary"], practice["summary"])
            self.assertEqual(learning_task.task_type, "practice_grading")
            self.assertEqual(learning_task.status, "completed")

    def test_falls_back_for_corrupted_task_detail_json(self):
        with self.Session() as db:
            created = create_training_task(db, 1, self.request)
            task = db.query(database.TrainingTaskRecord).filter_by(task_id=created["task_id"]).one()
            task.artifact_json = '{"artifact_type": "grading_result", "title": "损坏", "content": []}'
            task.audit_json = "[]"
            task.trace_json = "not-json"
            task.learning_updates_json = "[]"
            task.evidence_pack_json = "not-json"
            db.commit()

            result = get_training_task_result(db, 1, created["task_id"])

            self.assertEqual(
                result["artifact"],
                {
                    "artifact_type": "grading_result",
                    "title": created["title"],
                    "content": {},
                },
            )
            self.assertEqual(result["audit"], {})
            self.assertEqual(result["trace"], [])
            self.assertEqual(result["learning_updates"], {})
            self.assertEqual(
                result["evidence_pack"],
                {
                    "pack_id": task.evidence_pack_id,
                    "source_scope": "training_workspace_task",
                    "source_id": task.task_id,
                    "items": [],
                    "kp_ids": [],
                    "resolved_kp_ids": [],
                    "confidence": 0.0,
                },
            )

    def test_task_detail_rejects_invalid_evidence_snapshot_shapes(self):
        invalid_snapshots = (
            "not-json",
            json.dumps([]),
            json.dumps({}),
            json.dumps({"unexpected": True}),
            json.dumps({"pack_id": "EP_wrong", "items": "invalid"}),
            json.dumps({"items": []}),
        )
        with self.Session() as db:
            created = create_training_task(db, 1, self.request)
            task = db.query(database.TrainingTaskRecord).filter_by(task_id=created["task_id"]).one()
            expected = {
                "pack_id": task.evidence_pack_id,
                "source_scope": "training_workspace_task",
                "source_id": task.task_id,
                "items": [],
                "kp_ids": [],
                "resolved_kp_ids": [],
                "confidence": 0.0,
            }

            for evidence_pack_json in invalid_snapshots:
                with self.subTest(evidence_pack_json=evidence_pack_json):
                    task.evidence_pack_json = evidence_pack_json
                    db.commit()
                    self.assertEqual(
                        get_training_task_result(db, 1, created["task_id"])["evidence_pack"],
                        expected,
                    )

    def test_task_detail_preserves_valid_evidence_snapshot_extra_fields_and_formal_kp_ids(self):
        snapshot = {
            "source_id": "evidence-source",
            "kp_ids": ["KP_FJ_001"],
            "resolved_kp_ids": ["KP_FJ_001"],
            "future_field": {"enabled": True},
        }
        with self.Session() as db:
            created = create_training_task(db, 1, self.request)
            task = db.query(database.TrainingTaskRecord).filter_by(task_id=created["task_id"]).one()
            task.evidence_pack_json = json.dumps(snapshot)
            db.commit()

            evidence_pack = get_training_task_result(db, 1, created["task_id"])["evidence_pack"]

        self.assertEqual(evidence_pack, snapshot)
        self.assertEqual(evidence_pack["kp_ids"], ["KP_FJ_001"])

    def test_propagates_unexpected_grading_errors_without_persisting_task(self):
        for error in (
            RuntimeError("unexpected failure"),
            TypeError("unexpected type failure"),
            ValueError("unexpected value failure"),
        ):
            with self.subTest(error_type=type(error).__name__):
                with patch(
                    "APP.backend.training_workspace_service.grade_practice_submission",
                    side_effect=error,
                ):
                    with self.Session() as db:
                        with self.assertRaisesRegex(type(error), str(error)):
                            create_training_task(db, 1, self.request)
                        self.assert_no_task_records(db)


    def test_rejects_invalid_nested_practice_inputs_before_grading(self):
        invalid_inputs = {
            "question_id": 123,
            "question_type": ["short_answer"],
            "stem": 123,
            "student_answer": {"text": "中焦虚寒证"},
            "standard_answer": ["脾胃气虚证"],
            "rubric": True,
            "knowledge_points": ["四君子汤", 2],
            "difficulty": True,
        }
        for field, value in invalid_inputs.items():
            with self.subTest(field=field):
                request = {
                    **self.request,
                    "inputs": {**self.request["inputs"], field: value},
                }
                with patch(
                    "APP.backend.training_workspace_service.grade_practice_submission"
                ) as grading:
                    with self.Session() as db:
                        with self.assertRaisesRegex(InvalidTrainingTaskRequest, field):
                            create_training_task(db, 1, request)
                        self.assert_no_task_records(db)
                    grading.assert_not_called()

    def test_rejects_resource_limit_violations_before_grading_or_runtime(self):
        cases = {
            "payload is too large": {**self.request, "inputs": {**self.request["inputs"], "extra": "x" * 70000}},
            "maximum nesting depth": {**self.request, "inputs": {"stem": "题目", "nested": [[[[[[]]]]]]}},
            "object has too many keys": {**self.request, "inputs": {"stem": "题目", **{f"k{i}": i for i in range(50)}}},
            "list has too many items": {**self.request, "inputs": {"stem": "题目", "extra": list(range(51))}},
            "inputs.stem is too long": {**self.request, "inputs": {**self.request["inputs"], "stem": "题" * 8001}},
            "title is too long": {**self.request, "title": "题" * 201},
            "task_type is too long": {**self.request, "task_type": "x" * 81},
            "knowledge_points has too many items": {**self.request, "inputs": {**self.request["inputs"], "knowledge_points": ["知识点"] * 21}},
            "knowledge_points item is too long": {**self.request, "inputs": {**self.request["inputs"], "knowledge_points": ["知" * 121]}},
        }
        for message, request in cases.items():
            with self.subTest(message=message), patch(
                "APP.backend.training_workspace_service.grade_practice_submission"
            ) as grading:
                with self.Session() as db:
                    with self.assertRaisesRegex(InvalidTrainingTaskRequest, message):
                        create_training_task(db, 1, request)
                    self.assert_no_task_records(db)
                grading.assert_not_called()

    def test_rejects_unknown_or_invalid_options_before_runtime(self):
        cases = (
            ({"unexpected": True}, "unknown option"),
            ({"difficulty": True}, "options.difficulty has invalid type"),
            ({"difficulty": 0}, "options.difficulty must be between 1 and 5"),
            ({"difficulty": 6}, "options.difficulty must be between 1 and 5"),
            ({"expected_duration_min": True}, "options.expected_duration_min has invalid type"),
            ({"duration_minutes": 0}, "options.duration_minutes must be between 1 and 180"),
            ({"duration_minutes": 181}, "options.duration_minutes must be between 1 and 180"),
            ({"save_activity": 1}, "options.save_activity has invalid type"),
            ({"need_audit": "yes"}, "options.need_audit has invalid type"),
        )
        for options, message in cases:
            with self.subTest(options=options):
                runtime = DeterministicGenerationRuntime()
                request = {
                    "task_type": "handout_generation",
                    "query": "四君子汤",
                    "inputs": {},
                    "options": options,
                }
                with self.Session() as db:
                    with self.assertRaisesRegex(InvalidTrainingTaskRequest, message):
                        create_training_task(db, 1, request, runtime=runtime)
                    self.assert_no_task_records(db)
                self.assertEqual(runtime.calls, [])

    def test_accepts_boundary_values_for_generation_controls(self):
        runtime = DeterministicGenerationRuntime()
        request = {
            "task_type": "handout_generation",
            "title": "题" * 200,
            "query": "问" * 8000,
            "inputs": {"kp_ids": ["K" * 120] * 20},
            "options": {
                "difficulty": 5,
                "duration_minutes": 180,
                "save_activity": False,
                "need_audit": True,
            },
        }
        with self.Session() as db:
            result = create_training_task(db, 1, request, runtime=runtime)

        self.assertEqual(result["status"], "completed")

    def test_rejects_empty_stem_after_trimming(self):
        request = {
            **self.request,
            "inputs": {**self.request["inputs"], "stem": "   "},
        }
        with self.Session() as db:
            with self.assertRaisesRegex(InvalidTrainingTaskRequest, "stem"):
                create_training_task(db, 1, request)
            self.assert_no_task_records(db)

    def test_rejects_overlong_question_id_before_grading(self):
        request = {
            **self.request,
            "inputs": {**self.request["inputs"], "question_id": "q" * 121},
        }
        with patch(
            "APP.backend.training_workspace_service.grade_practice_submission"
        ) as grading:
            with self.Session() as db:
                with self.assertRaisesRegex(InvalidTrainingTaskRequest, "question_id is too long"):
                    create_training_task(db, 1, request)
                self.assert_no_task_records(db)
            grading.assert_not_called()








    def test_rolls_back_and_propagates_commit_failure(self):
        def fail_commit(_connection):
            raise RuntimeError("database commit failed")

        event.listen(self.engine, "commit", fail_commit, once=True)
        with self.Session() as db:
            with self.assertRaisesRegex(RuntimeError, "database commit failed"):
                create_training_task(db, 1, self.request)

        with self.Session() as db:
            self.assert_no_task_records(db)


class TrainingWorkspaceModuleTests(unittest.TestCase):
    def test_returns_three_ordered_product_modules(self):
        response = get_training_workspace_modules()
        modules = response["modules"]

        self.assertEqual(response["default_task_type"], "question_training")
        self.assertEqual(
            [module["key"] for module in modules],
            [
                "question_training",
                "knowledge_cards",
                "paper_workspace",
            ],
        )
        required_fields = {
            "key",
            "label",
            "description",
            "enabled",
            "badge",
            "recommended",
        }
        self.assertTrue(all(required_fields <= set(module) for module in modules))
        question_training = next(
            module for module in modules if module["key"] == "question_training"
        )
        self.assertEqual(
            question_training["capabilities"],
            ["practice_grading", "case_training", "mistake_variation"],
        )
        self.assertEqual(
            [module["enabled"] for module in modules],
            [True, True, True],
        )

    def test_returns_a_copy_that_cannot_mutate_future_responses(self):
        first_response = get_training_workspace_modules()
        first_response["modules"][0]["label"] = "污染"
        first_response["modules"].append({"key": "unexpected"})

        second_response = get_training_workspace_modules()

        self.assertEqual(second_response["modules"][0]["label"], "题目训练")
        self.assertEqual(len(second_response["modules"]), 3)


if __name__ == "__main__":
    unittest.main()
