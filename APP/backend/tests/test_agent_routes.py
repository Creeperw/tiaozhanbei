import json
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from APP.backend import database
from APP.backend.agent_orchestrator_service import PlanValidationError
from APP.backend.auth import get_current_user
from APP.backend.database import get_db
from APP.backend.routers.agent_routes import _extract_daily_minutes


class AgentRouteHelpersTests(unittest.TestCase):
    def test_extract_daily_minutes_prefers_explicit_daily_minutes_in_free_text(self):
        self.assertEqual(_extract_daily_minutes("工作日每天 30 分钟，周末 2 小时"), 30)

    def test_extract_daily_minutes_converts_explicit_daily_hours(self):
        self.assertEqual(_extract_daily_minutes("每天 1.5 小时，周末弹性安排"), 90)

    def test_extract_daily_minutes_ignores_non_daily_free_text_numbers(self):
        self.assertIsNone(_extract_daily_minutes("每周 3 次，每次 15 分钟"))

    def test_extract_daily_minutes_keeps_numeric_value_compatibility(self):
        self.assertEqual(_extract_daily_minutes("45"), 45)



class AgentRoutesOpenApiTests(unittest.TestCase):
    def test_agent_routes_are_registered_in_openapi(self):
        from APP.backend.main import app

        paths = app.openapi()["paths"]

        self.assertIn("/agent/context/brief", paths)
        self.assertIn("/agent/plan/generate", paths)
        self.assertIn("/agent/plan/summary", paths)
        self.assertIn("/agent/diagnosis/report", paths)
        self.assertIn("/agent/trace/recent", paths)
        self.assertIn("/agent/cross-validate", paths)
        self.assertIn("/agent/orchestrate", paths)


class AgentRoutesBehaviorTests(unittest.TestCase):
    def setUp(self):
        from APP.backend.main import app

        self.app = app
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        database.Base.metadata.create_all(bind=self.engine)
        self.Session = sessionmaker(bind=self.engine)

        db = self.Session()
        try:
            db.add(database.UserModel(id=1, username="learner", email="learner@example.com", hashed_password="x"))
            db.add(
                database.UserProfile(
                    user_id=1,
                    display_name="Alice",
                    constitution="跨专业进阶群体",
                    health_goals="4 周内掌握脾胃气虚证与四君子汤",
                    diet_restrictions="每天 45 分钟",
                    exercise_preferences="知识卡、错题复盘、案例辨证",
                    medical_history="证型到方剂匹配薄弱",
                    custom_needs="需要循序渐进规划",
                )
            )
            db.add_all(
                [
                    database.PersonalizationMemory(
                        user_id=1,
                        category="short_term",
                        title="近期薄弱点",
                        content="最近错在脾胃气虚证和中焦虚寒证辨析",
                        confidence=0.86,
                    ),
                    database.PersonalizationMemory(
                        user_id=1,
                        category="long_term",
                        title="长期目标",
                        content="备考中医执业医师",
                        confidence=0.9,
                    ),
                    database.PersonalizationMemory(
                        user_id=1,
                        category="preference",
                        title="资源偏好",
                        content="偏好知识卡和错题变式",
                        confidence=0.82,
                    ),
                ]
            )
            db.add(
                database.LearnerKnowledgeMastery(
                    user_id=1,
                    kp_id="KP_FJ_001",
                    mastery=0.58,
                    confidence=0.74,
                    wrong_count=3,
                    review_count=2,
                    mastery_status="weak",
                )
            )
            db.add(
                database.LearningPlanRecord(
                    user_id=1,
                    plan_type="weekly",
                    title="四君子汤专题突破",
                    summary="本周完成知识卡、短练和错题复盘",
                    status="active",
                    payload_json='{"daily_tasks": 3}',
                )
            )
            db.add(
                database.LearningActivityRecord(
                    user_id=1,
                    activity_type="practice",
                    resource_id="q_sijunzi_001",
                    resource_type="question",
                    duration_minutes=25,
                    completion_status="completed",
                    score=60.0,
                    payload_json='{"kp_id": "KP_FJ_001"}',
                )
            )
            db.add(
                database.LearningInterventionRecord(
                    user_id=1,
                    t_stage="T1",
                    action="generate_mistake_review_card",
                    reason="高耗低效，需要错题复盘",
                    effect_status="pending",
                    cooldown_hours=24,
                )
            )
            db.add(
                database.AgentEvent(
                    user_id=1,
                    agent_name="planner_agent",
                    event_type="plan_summary",
                    input_summary="weekly planning",
                    output_summary="建议先补证型与方剂匹配",
                    payload='{"goal": "四君子汤专题突破"}',
                )
            )
            db.add(
                database.AgentEvent(
                    user_id=1,
                    agent_name="diagnosis_agent",
                    event_type="diagnosis",
                    input_summary="weekly diagnosis",
                    output_summary="近期学习节奏下降",
                    payload='{"stage_id": "T2"}',
                )
            )
            db.add(
                database.QuestionAttempt(
                    user_id=1,
                    question_id="Q1",
                    answer="理中丸",
                    is_correct=False,
                    score=40,
                    kp_ids_json='["KP_FJ_001", "KP_ZD_021"]',
                    feedback="将脾胃气虚证误判为中焦虚寒证",
                )
            )
            db.add(
                database.DbSession(
                    id="session-owned-1",
                    user_id=1,
                    title="Owned session",
                )
            )
            db.add(
                database.DbSession(
                    id="session-other-user",
                    user_id=2,
                    title="Other user session",
                )
            )
            db.add(
                database.AgentEvent(
                    user_id=1,
                    session_id="session-owned-1",
                    agent_name="expert_agent",
                    event_type="artifact_generated",
                    input_summary="expert artifact",
                    output_summary="artifact and evidence generated",
                    payload='{"artifact_source_id": "artifact-cross-001", "evidence_pack_source_id": "PACK_CROSS_001", "artifact-cross-context": true, "PACK_CROSS_CONTEXT": true}',
                )
            )
            db.commit()
        finally:
            db.close()

        def override_db():
            session = self.Session()
            try:
                yield session
            finally:
                session.close()

        def override_user():
            return database.UserModel(id=1, username="learner", email="learner@example.com", hashed_password="x")

        self.app.dependency_overrides[get_db] = override_db
        self.app.dependency_overrides[get_current_user] = override_user
        self.client = TestClient(self.app)

    def tearDown(self):
        self.app.dependency_overrides.clear()
        self.engine.dispose()

    def _valid_artifact(self, source_id="artifact-cross-001"):
        return {
            "artifact_type": "handout",
            "title": "交叉校验讲义",
            "content": {
                "schema_version": "v1",
                "source_ids": ["SRC_FJ_001", "SRC_COMPARE_001"],
                "kp_ids": ["KP_FJ_001", "KP_ZD_021"],
                "difficulty": 2,
                "claims": [
                    {"text": "四君子汤主治脾胃气虚证", "evidence_ids": ["SRC_FJ_001"]},
                    {"text": "理中丸偏于中焦虚寒证", "evidence_ids": ["SRC_COMPARE_001"]},
                ],
                "sections": [{"title": "讲解", "bullets": ["围绕证型与方剂匹配展开。"]}],
            },
            "source_scope": "expert_handout",
            "source_id": source_id,
            "kp_ids": ["KP_FJ_001", "KP_ZD_021"],
            "risk_notes": [],
            "confidence": 0.92,
            "agent_trace": [],
        }

    def _valid_evidence_pack(self, source_id="PACK_CROSS_001"):
        return {
            "source_scope": "knowledge_base_agent",
            "source_id": source_id,
            "kp_ids": ["KP_FJ_001", "KP_ZD_021"],
            "resolved_kp_ids": ["KP_FJ_001", "KP_ZD_021"],
            "confidence": 0.97,
            "items": [
                {
                    "source_scope": "knowledge_base",
                    "source_id": "SRC_FJ_001",
                    "summary": "四君子汤主治脾胃气虚证。",
                    "kp_ids": ["KP_FJ_001", "KP_ZD_021"],
                    "confidence": 0.99,
                    "risk_notes": [],
                    "agent_trace": [],
                },
                {
                    "source_scope": "knowledge_base",
                    "source_id": "SRC_COMPARE_001",
                    "summary": "理中丸偏于中焦虚寒证。",
                    "kp_ids": ["KP_FJ_001", "KP_ZD_021"],
                    "confidence": 0.96,
                    "risk_notes": [],
                    "agent_trace": [],
                },
            ],
            "risk_notes": [],
            "agent_trace": [],
        }

    def _add_cross_validate_event(self, artifact, evidence_pack):
        with self.Session() as db:
            db.add(database.AgentEvent(
                user_id=1,
                session_id="session-owned-1",
                agent_name="expert_agent",
                event_type="artifact_generated",
                input_summary="expert artifact",
                output_summary="artifact and evidence generated",
                payload=json.dumps({"artifact": artifact, "evidence_pack": evidence_pack}, ensure_ascii=False),
            ))
            db.commit()

    def test_get_context_brief_returns_global_agent_memory_payload(self):
        response = self.client.get("/agent/context/brief")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["learner_id"], "1")
        self.assertEqual(payload["learner_group"], "跨专业进阶群体")
        self.assertEqual(payload["goal"], "4 周内掌握脾胃气虚证与四君子汤")
        self.assertEqual(payload["profile"]["learning_goal"], "4 周内掌握脾胃气虚证与四君子汤")
        self.assertEqual(payload["short_term_memory"]["active_items"][0]["title"], "近期薄弱点")
        self.assertTrue(payload["learning_state"]["weak_kp_ids"])

    def test_generate_plan_creates_agent_execution_plan_payload(self):
        response = self.client.post("/agent/plan/generate", json={})

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["plan_summary"]["goal"], "4 周内掌握脾胃气虚证与四君子汤")
        self.assertEqual(payload["plan_summary"]["learner_group"], "跨专业进阶群体")
        self.assertTrue(payload["daily_tasks"])
        self.assertTrue(payload["weekly_plan"]["focus"])
        self.assertEqual(payload["constraints"]["daily_available_minutes"], 45)

    def test_get_plan_summary_returns_existing_or_generated_plan_payload(self):
        with self.Session() as db:
            before_count = db.query(database.LearningPlanRecord).filter_by(user_id=1, plan_type="diagnosis_driven").count()

        response = self.client.get("/agent/plan/summary")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["plan_summary"]["goal"], "4 周内掌握脾胃气虚证与四君子汤")
        self.assertTrue(payload["daily_tasks"])
        self.assertNotIn("record_id", payload)
        with self.Session() as db:
            after_count = db.query(database.LearningPlanRecord).filter_by(user_id=1, plan_type="diagnosis_driven").count()
        self.assertEqual(after_count, before_count)

    def test_get_plan_summary_does_not_refresh_stale_diagnosis_driven_record(self):
        with self.Session() as db:
            stale = database.LearningPlanRecord(
                user_id=1,
                plan_type="diagnosis_driven",
                title="旧计划",
                summary="旧摘要",
                status="active",
                payload_json='{"plan_summary": {"goal": "旧目标", "learner_group": "旧群体"}, "weekly_plan": {"focus": "旧周目标", "acceptance": "旧验收"}, "daily_tasks": [], "constraints": {"daily_available_minutes": 30}}',
            )
            db.add(stale)
            db.commit()
            stale_id = stale.id

        response = self.client.get("/agent/plan/summary")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["record_id"], stale_id)
        self.assertEqual(payload["plan_summary"]["goal"], "旧目标")
        self.assertEqual(payload["weekly_plan"]["focus"], "旧周目标")

        with self.Session() as db:
            stale_after = db.get(database.LearningPlanRecord, stale_id)
            self.assertEqual(stale_after.summary, "旧摘要")
            self.assertIn("旧周目标", stale_after.payload_json)

    def test_generate_plan_without_persist_does_not_write_plan_record(self):
        with self.Session() as db:
            before_count = db.query(database.LearningPlanRecord).filter_by(user_id=1, plan_type="diagnosis_driven").count()

        response = self.client.post("/agent/plan/generate", json={"persist": False})

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["daily_tasks"])
        with self.Session() as db:
            after_count = db.query(database.LearningPlanRecord).filter_by(user_id=1, plan_type="diagnosis_driven").count()
        self.assertEqual(after_count, before_count)

    def test_get_diagnosis_report_returns_report_page_payload(self):
        response = self.client.get("/agent/diagnosis/report")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertIn("diagnosis", payload)
        self.assertIn("learning_profile", payload)
        self.assertIn("agent_trace", payload)
        self.assertTrue(payload["learner_overview"]["learner_group"])
        self.assertTrue(payload["mastery_radar"])
        self.assertIn(payload["diagnosis"]["stage_id"], ["T0", "T1", "T2", "T4", "T5"])

    def test_get_recent_trace_returns_recent_agent_events(self):
        response = self.client.get("/agent/trace/recent")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertIn("items", payload)
        self.assertGreaterEqual(len(payload["items"]), 2)
        agent_names = [item["agent_name"] for item in payload["items"]]
        self.assertIn("diagnosis_agent", agent_names)
        self.assertIn("planner_agent", agent_names)

    def test_orchestrate_returns_unified_agent_run_payload(self):
        response = self.client.post(
            "/agent/orchestrate",
            json={
                "query": "我想学习四君子汤，并做几道题巩固",
                "requested_outputs": ["knowledge_card", "quiz"],
                "persist": True,
            },
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["run_id"].startswith("run_"))
        self.assertIn(payload["status"], ["success", "degraded", "needs_revision", "rejected", "human_review"])
        self.assertIn("execution_plan", payload)
        self.assertIn("steps", payload)
        self.assertIn("final", payload)
        self.assertIn("learner_context", payload["final"])
        self.assertIn("diagnosis", payload["final"])
        self.assertIn("evidence_pack", payload["final"])
        self.assertIn("artifact", payload["final"])
        self.assertIn("audit", payload["final"])
        self.assertTrue(any(step["agent_name"] == "audit_agent" for step in payload["steps"]))

    def test_orchestrate_returns_422_for_invalid_plan_validation(self):
        with patch(
            "APP.backend.routers.agent_routes.run_agent_orchestration",
            side_effect=PlanValidationError("Unknown agent: missing_agent", code="unknown_agent"),
        ):
            response = self.client.post(
                "/agent/orchestrate",
                json={"query": "bad plan"},
            )

        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["code"], "unknown_agent")
        self.assertIn("Unknown agent", response.json()["detail"])

    def test_orchestrate_returns_422_for_high_risk_plan_without_audit(self):
        with patch(
            "APP.backend.routers.agent_routes.run_agent_orchestration",
            side_effect=PlanValidationError("High-risk TCM content requires audit_agent", code="missing_required_audit"),
        ):
            response = self.client.post(
                "/agent/orchestrate",
                json={"query": "讲解四君子汤对孕妇的禁忌和剂量"},
            )

        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["code"], "missing_required_audit")
        self.assertIn("audit_agent", response.json()["detail"])

    def test_orchestrate_maps_runtime_failure_to_http_error(self):
        with patch(
            "APP.backend.routers.agent_routes.run_agent_orchestration",
            return_value={
                "run_id": "run_failed",
                "status": "failed",
                "error": "planner execution failed",
                "error_code": "planner_execution_failed",
                "steps": [],
                "final": {},
            },
        ):
            response = self.client.post(
                "/agent/orchestrate",
                json={"query": "帮我规划两周学习路径"},
            )

        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.json()["code"], "planner_execution_failed")
        self.assertIn("planner execution failed", response.json()["detail"])

    def test_orchestrate_normalizes_unhandled_runtime_exception(self):
        error_client = TestClient(self.app, raise_server_exceptions=False)
        with patch(
            "APP.backend.routers.agent_routes.run_agent_orchestration",
            side_effect=RuntimeError("secret planner failure"),
        ):
            response = error_client.post(
                "/agent/orchestrate",
                json={"query": "帮我规划两周学习路径"},
            )

        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.json()["code"], "orchestration_failed")
        self.assertEqual(response.json()["detail"], "Agent orchestration failed")
        self.assertNotIn("secret planner failure", response.text)

    def test_cross_validate_returns_review_decision_and_summary(self):
        artifact = self._valid_artifact()
        evidence_pack = self._valid_evidence_pack()
        self._add_cross_validate_event(artifact, evidence_pack)

        response = self.client.post(
            "/agent/cross-validate",
            json={
                "session_id": "session-owned-1",
                "artifact": artifact,
                "evidence_pack": evidence_pack,
            },
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["review"]["decision"], "pass")
        self.assertEqual(payload["summary"]["decision"], "pass")
        self.assertGreaterEqual(payload["summary"]["overall_score"], 0.95)
        self.assertEqual(payload["summary"]["needs_human_review"], False)

    def test_cross_validate_requires_platform_artifact_session_binding(self):
        response = self.client.post(
            "/agent/cross-validate",
            json={
                "artifact": {
                    "artifact_type": "handout",
                    "title": "伪造讲义",
                    "content": {"schema_version": "v1", "claims": []},
                    "source_scope": "expert_handout",
                    "source_id": "client-artifact",
                    "kp_ids": ["KP_FJ_001"],
                    "risk_notes": [],
                    "confidence": 0.92,
                    "agent_trace": [],
                },
                "evidence_pack": {
                    "source_scope": "knowledge_base_agent",
                    "source_id": "PACK_CLIENT",
                    "kp_ids": ["KP_FJ_001"],
                    "resolved_kp_ids": ["KP_FJ_001"],
                    "confidence": 0.97,
                    "items": [],
                    "risk_notes": [],
                    "agent_trace": [],
                },
            },
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("session", response.json()["detail"])

    def test_cross_validate_rejects_mutated_platform_artifact(self):
        artifact = self._valid_artifact()
        evidence_pack = self._valid_evidence_pack()
        self._add_cross_validate_event(artifact, evidence_pack)
        mutated_artifact = dict(artifact)
        mutated_content = dict(artifact["content"])
        mutated_content["claims"] = [{"text": "篡改后的结论", "evidence_ids": ["SRC_FJ_001"]}]
        mutated_artifact["content"] = mutated_content

        response = self.client.post(
            "/agent/cross-validate",
            json={
                "session_id": "session-owned-1",
                "artifact": mutated_artifact,
                "evidence_pack": evidence_pack,
            },
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("artifact", response.json()["detail"])

    def test_cross_validate_rejects_foreign_session_id(self):
        response = self.client.post(
            "/agent/cross-validate",
            json={
                "session_id": "session-other-user",
                "artifact": {
                    "artifact_type": "handout",
                    "title": "交叉校验讲义",
                    "content": {
                        "schema_version": "v1",
                        "source_ids": ["SRC_FJ_001"],
                        "kp_ids": ["KP_FJ_001"],
                        "difficulty": 2,
                        "claims": [
                            {"text": "四君子汤主治脾胃气虚证", "evidence_ids": ["SRC_FJ_001"]},
                        ],
                    },
                    "source_scope": "expert_handout",
                    "source_id": "artifact-cross-002",
                    "kp_ids": ["KP_FJ_001"],
                    "risk_notes": [],
                    "confidence": 0.92,
                    "agent_trace": [],
                },
                "evidence_pack": {
                    "source_scope": "knowledge_base_agent",
                    "source_id": "PACK_CROSS_002",
                    "kp_ids": ["KP_FJ_001"],
                    "resolved_kp_ids": ["KP_FJ_001"],
                    "confidence": 0.97,
                    "items": [
                        {
                            "source_scope": "knowledge_base",
                            "source_id": "SRC_FJ_001",
                            "summary": "四君子汤主治脾胃气虚证。",
                            "kp_ids": ["KP_FJ_001"],
                            "confidence": 0.99,
                            "risk_notes": [],
                            "agent_trace": [],
                        }
                    ],
                    "risk_notes": [],
                    "agent_trace": [],
                },
            },
        )

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["detail"], "Session not found")
    def test_cross_validate_ignores_client_supplied_context(self):
        captured = {}

        def fake_cross_validate_output(**kwargs):
            captured.update(kwargs)

            class Review:
                def model_dump(self):
                    return {"decision": "pass", "source_scope": "audit_agent", "source_id": "mock", "confidence": 1.0}

            return Review(), {"decision": "pass", "overall_score": 1.0, "needs_human_review": False}

        artifact = self._valid_artifact("artifact-cross-context")
        evidence_pack = self._valid_evidence_pack("PACK_CROSS_CONTEXT")
        self._add_cross_validate_event(artifact, evidence_pack)

        with patch("APP.backend.routers.agent_routes.cross_validate_output", side_effect=fake_cross_validate_output):
            response = self.client.post(
                "/agent/cross-validate",
                json={
                    "session_id": "session-owned-1",
                    "learner_context": {
                        "learner_id": "999",
                        "learner_group": "伪造群体",
                        "goal": "伪造目标",
                        "source_scope": "client",
                        "source_id": "fake",
                        "confidence": 1.0,
                    },
                    "diagnosis_report": {
                        "diagnosis_id": "fake",
                        "stage_id": "T5",
                        "stage_name": "伪造诊断",
                        "summary": "client supplied",
                    },
                    "artifact": artifact,
                    "evidence_pack": evidence_pack,
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(captured["learner_context"].learner_id, "1")
        self.assertEqual(captured["learner_context"].learner_group, "跨专业进阶群体")
        self.assertNotEqual(captured["learner_context"].learner_id, "999")
        self.assertNotEqual(captured["diagnosis_report"].stage_name, "伪造诊断")


if __name__ == "__main__":
    unittest.main()
