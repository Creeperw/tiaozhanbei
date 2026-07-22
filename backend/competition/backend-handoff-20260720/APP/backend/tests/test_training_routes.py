import asyncio
import json
import unittest
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event
from tempfile import TemporaryDirectory
from unittest.mock import patch

from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from starlette.requests import Request

from APP.backend import database
from APP.backend.auth import get_current_user
from APP.backend.database import get_db
from APP.backend.routers import training_routes, training_workspace_routes


class TrainingRoutesOpenApiTests(unittest.TestCase):
    def test_phase4_training_routes_are_registered_in_openapi(self):
        from APP.backend.main import app

        paths = app.openapi()["paths"]

        self.assertIn("/training/practice/grade", paths)
        self.assertIn("/v1/workshop/practice/next", paths)
        self.assertIn("/v1/workshop/practice/grade", paths)
        self.assertIn("/v1/workshop/practice/mistakes", paths)
        self.assertIn("/v1/workshop/practice/mistakes/{mistake_id}", paths)
        self.assertIn("/v1/workshop/practice/mistakes/{mistake_id}/answer-context", paths)
        self.assertIn("/training/workspace/modules", paths)
        self.assertIn("/training/workspace/tasks", paths)
        self.assertIn("200", paths["/training/workspace/tasks"]["post"]["responses"])
        self.assertNotIn("501", paths["/training/workspace/tasks"]["post"]["responses"])
        self.assertIn("400", paths["/training/workspace/tasks"]["post"]["responses"])
        self.assertIn("429", paths["/training/workspace/tasks"]["post"]["responses"])
        self.assertIn("Retry-After", paths["/training/workspace/tasks"]["post"]["responses"]["429"]["headers"])
        request_schema = paths["/training/workspace/tasks"]["post"]["requestBody"]["content"]["application/json"]["schema"]
        self.assertEqual(request_schema["title"], "TrainingTaskRequest")
        self.assertIn("maxLength", request_schema["properties"]["query"])
        self.assertIn("/training/workspace/tasks/{task_id}", paths)
        self.assertIn("/training/workspace/papers/{paper_id}", paths)
        self.assertIn("/training/workspace/papers/{paper_id}/answers", paths)
        self.assertIn("/training/workspace/papers/{paper_id}/submit", paths)
        self.assertIn("404", paths["/training/workspace/tasks/{task_id}"]["get"]["responses"])
        self.assertNotIn("501", paths["/training/workspace/tasks/{task_id}"]["get"]["responses"])
        self.assertIn("/training/plan/summary", paths)
        self.assertIn("/training/report", paths)
        self.assertIn("/training/onboarding/survey", paths)
        self.assertIn("/training/onboarding/status", paths)
        self.assertIn("/training/onboarding/group-templates", paths)
        self.assertIn("/training/onboarding/dismiss", paths)
        self.assertIn("/training/diagnosis/summary", paths)


class TrainingRoutesBehaviorTests(unittest.TestCase):
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
        training_workspace_routes.training_task_limiter.reset()
        self.client = TestClient(self.app)

    def tearDown(self):
        training_workspace_routes.training_task_limiter.reset()
        self.app.dependency_overrides.clear()
        self.engine.dispose()

    def test_onboarding_group_templates_include_required_groups_and_questions(self):
        response = self.client.get("/training/onboarding/group-templates")

        self.assertEqual(response.status_code, 200)
        body = response.json()
        group_keys = [item["key"] for item in body["groups"]]
        self.assertEqual(group_keys, ["cross_professional", "academic", "public_interest"])
        self.assertEqual(body["required_fields"], ["learner_group"])
        self.assertTrue(body["questions"])
        first_question = body["questions"][0]
        self.assertIn("key", first_question)
        self.assertIn("options", first_question)
        self.assertIn("default_by_group", first_question)
        cross = body["groups"][0]
        self.assertIn("在职碎片化", cross["tags"])
        self.assertIn("default_profile", cross)

    def test_workspace_modules_returns_three_product_modules_and_default_task_type(self):
        response = self.client.get("/training/workspace/modules")

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(len(body["modules"]), 3)
        self.assertEqual(body["default_task_type"], "question_training")
        question_training = next(
            module for module in body["modules"] if module["key"] == "question_training"
        )
        self.assertTrue(question_training["enabled"])
        self.assertEqual(
            question_training["capabilities"],
            ["practice_grading", "case_training", "mistake_variation"],
        )
        self.assertEqual(
            question_training["practice_modes"],
            ["objective_practice", "case_short_answer", "ai_patient_simulation", "mistake_history"],
        )

    def test_workspace_mistake_variation_returns_404_for_missing_or_unowned_source(self):
        for mistake_id in (999, 91):
            with self.subTest(mistake_id=mistake_id):
                response = self.client.post(
                    "/training/workspace/tasks",
                    json={
                        "task_type": "mistake_variation",
                        "title": "错题变式",
                        "query": "生成错题变式",
                        "inputs": {"mistake_id": mistake_id, "variation_count": 1},
                        "options": {},
                    },
                )
                self.assertEqual(response.status_code, 404)

    def test_workspace_task_post_wraps_practice_grading_in_unified_result(self):
        response = self.client.post(
            "/training/workspace/tasks",
            json={
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
            },
        )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["task_id"].startswith("TT_"))
        self.assertEqual(body["task_type"], "practice_grading")
        self.assertEqual(body["status"], "completed")
        self.assertEqual(body["artifact"]["artifact_type"], "grading_result")
        self.assertIn("grading", body["artifact"]["content"])
        self.assertIn("evidence_pack", body)
        self.assertIn("audit", body)
        self.assertIn("trace", body)
        self.assertIn("learning_updates", body)
        self.assertIn("next_actions", body)
        self.assertIn("system_data", body)
        self.assertIn("task_completion_rate", body["system_data"])

        with self.Session() as db:
            persisted = db.query(database.TrainingTaskRecord).filter_by(task_id=body["task_id"]).one()
            self.assertEqual(persisted.user_id, 1)
            self.assertEqual(persisted.artifact_type, "grading_result")
            snapshot = db.query(database.SystemData).filter_by(user_id=1).one()
            rates = json.loads(snapshot.task_completion_rate_json)
            self.assertEqual(rates["value"], 1.0)

    def test_workspace_task_get_returns_owned_task_and_404s_when_missing_or_owned_by_another_user(self):
        created = self.client.post(
            "/training/workspace/tasks",
            json={
                "task_type": "practice_grading",
                "inputs": {
                    "question_id": "task-detail-001",
                    "stem": "四君子汤主治的核心证型是什么？",
                    "student_answer": "中焦虚寒证",
                    "standard_answer": "脾胃气虚证",
                },
            },
        )
        self.assertEqual(created.status_code, 200)
        task_id = created.json()["task_id"]

        detail = self.client.get(f"/training/workspace/tasks/{task_id}")
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(detail.json()["task_id"], task_id)

        missing = self.client.get("/training/workspace/tasks/TT_missing")
        self.assertEqual(missing.status_code, 404)

        with self.Session() as db:
            db.query(database.TrainingTaskRecord).filter_by(task_id=task_id).update({"user_id": 2})
            db.commit()

        inaccessible = self.client.get(f"/training/workspace/tasks/{task_id}")
        self.assertEqual(inaccessible.status_code, 404)

    def test_workspace_paper_routes_enforce_ownership_and_never_return_standard_answers(self):
        with self.Session() as db:
            db.add_all((
                database.PaperInstanceRecord(paper_id="PAPER_ROUTE", task_id="TASK_ROUTE", learner_id=1, title="试卷"),
                database.PaperItemRecord(paper_item_id="PI_ROUTE", paper_id="PAPER_ROUTE", position=1, question_id="Q_ROUTE", question_version_id="QV_ROUTE", stem_snapshot="公开题干"),
                database.QuestionVersionRecord(question_version_id="QV_ROUTE", question_id="Q_ROUTE", version=1, stem="公开题干", answer="秘密答案", status="active"),
            ))
            db.commit()

        read = self.client.get("/training/workspace/papers/PAPER_ROUTE")
        saved = self.client.put("/training/workspace/papers/PAPER_ROUTE/answers", json={"answers": {"PI_ROUTE": "我的答案"}})
        self.assertEqual(read.status_code, 200)
        self.assertEqual(saved.status_code, 200)
        self.assertNotIn("秘密答案", read.text)
        self.assertNotIn("秘密答案", saved.text)

        self.app.dependency_overrides[get_current_user] = lambda: database.UserModel(id=2, username="other", hashed_password="x")
        self.assertEqual(self.client.get("/training/workspace/papers/PAPER_ROUTE").status_code, 404)

    def test_workspace_paper_post_and_get_preserve_task_and_run_ids_without_answers(self):
        orchestration = {
            "status": "success",
            "run_id": "RUN_ROUTE_1",
            "execution_plan": {"objective": "组卷", "status": "completed", "assigned_agents": ["expert_paper", "audit_agent"]},
            "steps": [],
            "final": {
                "artifact": {
                    "artifact_type": "paper", "title": "四君子汤测试卷", "source_id": "artifact:paper:route",
                    "content": {"paper_blueprint": {"question_count": 1, "kp_ids": ["KP_1"], "types": ["short_answer"], "distribution": {"short_answer": 1}, "difficulty": 2}},
                },
                "evidence_pack": {"pack_id": "EP_ROUTE", "source_scope": "knowledge", "source_id": "EP_ROUTE", "resolved_kp_ids": ["KP_1"], "items": [{"source_scope": "knowledge_point", "source_id": "KP_1", "summary": "依据", "kp_ids": ["KP_1"]}]},
                "audit": {"decision": "pass", "reason": "passed", "source_scope": "audit_agent", "source_id": "artifact:paper:route"},
            },
        }
        with self.Session() as db:
            db.add(database.QuestionBankItem(question_id="Q_ROUTE", stem="冻结题干", answer="秘密答案", analysis="秘密解析", kp_ids_json='["KP_1"]', question_type="short_answer", difficulty=2, status="active"))
            db.commit()
        normalized = {
            "task_id": "ignored", "task_type": "paper_generation", "status": "completed", "title": "四君子汤测试卷",
            "summary": "蓝图审核通过", "artifact": orchestration["final"]["artifact"],
            "evidence_pack": orchestration["final"]["evidence_pack"], "audit": orchestration["final"]["audit"],
            "trace": [{"step_id": "orchestration", "run_id": "RUN_ROUTE_1"}], "learning_updates": {},
            "next_actions": [], "orchestration_run_id": "RUN_ROUTE_1",
        }
        with patch("APP.backend.training_workspace_service.execute_training_orchestration", return_value=normalized):
            response = self.client.post(
                "/training/workspace/tasks",
                json={"task_type": "paper_generation", "title": "四君子汤测试卷", "query": "四君子汤", "inputs": {"kp_ids": ["KP_1"], "question_count": 1, "types": ["short_answer"], "distribution": {"short_answer": 1}}, "options": {"need_audit": True}},
            )

        self.assertEqual(response.status_code, 200)
        created = response.json()
        self.assertEqual(created["orchestration_run_id"], "RUN_ROUTE_1")
        detail = self.client.get(f"/training/workspace/tasks/{created['task_id']}")
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(detail.json()["task_id"], created["task_id"])
        self.assertEqual(detail.json()["orchestration_run_id"], "RUN_ROUTE_1")
        self.assertNotIn("秘密答案", json.dumps(detail.json(), ensure_ascii=False))

    def test_workspace_task_post_rejects_missing_stem(self):
        response = self.client.post(
            "/training/workspace/tasks",
            json={"task_type": "practice_grading", "inputs": {}},
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["detail"], "stem is required")
        with self.Session() as db:
            self.assertEqual(db.query(database.TrainingTaskRecord).count(), 0)

    def test_workspace_task_route_schema_rejects_overlong_top_level_strings(self):
        for field, value in (
            ("task_type", "x" * 81),
            ("title", "题" * 201),
            ("query", "问" * 8001),
        ):
            with self.subTest(field=field):
                response = self.client.post(
                    "/training/workspace/tasks",
                    json={
                        "task_type": "practice_grading",
                        "title": "",
                        "query": "",
                        "inputs": {"stem": "题目"},
                        field: value,
                    },
                )
                self.assertEqual(response.status_code, 422)

        with self.Session() as db:
            self.assertEqual(db.query(database.TrainingTaskRecord).count(), 0)

    def test_workspace_task_route_returns_400_for_service_deep_validation(self):
        with patch(
            "APP.backend.training_workspace_service.grade_practice_submission"
        ) as grading:
            response = self.client.post(
                "/training/workspace/tasks",
                json={
                    "task_type": "practice_grading",
                    "inputs": {"stem": "题目", "nested": [[[[[[]]]]]]},
                },
            )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["detail"], "maximum nesting depth exceeded")
        grading.assert_not_called()
        with self.Session() as db:
            self.assertEqual(db.query(database.TrainingTaskRecord).count(), 0)

    def test_training_task_body_reader_rejects_oversized_content_length_without_consuming_stream(self):
        receive_calls = 0

        async def receive():
            nonlocal receive_calls
            receive_calls += 1
            return {"type": "http.request", "body": b"{}", "more_body": False}

        request = Request(
            {
                "type": "http",
                "method": "POST",
                "path": "/training/workspace/tasks",
                "headers": [(b"content-length", b"65537")],
            },
            receive,
        )

        with self.assertRaises(HTTPException) as raised:
            asyncio.run(training_workspace_routes._read_training_task_request(request))

        self.assertEqual(raised.exception.status_code, 413)
        self.assertEqual(receive_calls, 0)

    def test_training_task_body_reader_stops_when_chunked_body_exceeds_limit(self):
        messages = iter(
            (
                {"type": "http.request", "body": b"a" * 40000, "more_body": True},
                {"type": "http.request", "body": b"b" * 30000, "more_body": True},
                {"type": "http.request", "body": b"not-consumed", "more_body": False},
            )
        )
        receive_calls = 0

        async def receive():
            nonlocal receive_calls
            receive_calls += 1
            return next(messages)

        request = Request(
            {
                "type": "http",
                "method": "POST",
                "path": "/training/workspace/tasks",
                "headers": [],
            },
            receive,
        )

        with self.assertRaises(HTTPException) as raised:
            asyncio.run(training_workspace_routes._read_training_task_request(request))

        self.assertEqual(raised.exception.status_code, 413)
        self.assertEqual(receive_calls, 2)

    def test_training_task_body_reader_rejects_a_single_oversized_chunk_before_buffering_it(self):
        messages = iter(
            (
                {"type": "http.request", "body": b"a" * 65537, "more_body": True},
                {"type": "http.request", "body": b"not-consumed", "more_body": False},
            )
        )
        receive_calls = 0

        async def receive():
            nonlocal receive_calls
            receive_calls += 1
            return next(messages)

        request = Request(
            {"type": "http", "method": "POST", "path": "/training/workspace/tasks", "headers": []},
            receive,
        )

        with self.assertRaises(HTTPException) as raised:
            asyncio.run(training_workspace_routes._read_training_task_request(request))

        self.assertEqual(raised.exception.status_code, 413)
        self.assertEqual(receive_calls, 1)

    def test_training_task_body_reader_parses_valid_json_and_rejects_bad_or_empty_json(self):
        async def read_body(body):
            sent = False

            async def receive():
                nonlocal sent
                if sent:
                    return {"type": "http.request", "body": b"", "more_body": False}
                sent = True
                return {"type": "http.request", "body": body, "more_body": False}

            request = Request(
                {"type": "http", "method": "POST", "path": "/training/workspace/tasks", "headers": []},
                receive,
            )
            return await training_workspace_routes._read_training_task_request(request)

        model = asyncio.run(
            read_body(json.dumps({"task_type": "practice_grading", "inputs": {"stem": "题目"}}).encode())
        )
        self.assertEqual(model.task_type, "practice_grading")

        for body in (b"", b"{invalid"):
            with self.subTest(body=body), self.assertRaises(HTTPException) as raised:
                asyncio.run(read_body(body))
            self.assertEqual(raised.exception.status_code, 400)

        with self.assertRaises(HTTPException) as raised:
            asyncio.run(read_body(b"[]"))
        self.assertEqual(raised.exception.status_code, 422)

    def test_workspace_task_post_rejects_body_over_64_kib_at_route_entry(self):
        response = self.client.post(
            "/training/workspace/tasks",
            content=b" " * 65537,
            headers={"content-type": "application/json"},
        )

        self.assertEqual(response.status_code, 413)
        with self.Session() as db:
            self.assertEqual(db.query(database.TrainingTaskRecord).count(), 0)

    def test_workspace_task_post_rejects_non_object_json_with_422(self):
        response = self.client.post("/training/workspace/tasks", json=[])

        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["code"], "validation_error")
        self.assertIsInstance(response.json()["field_errors"], list)

    def test_training_task_limiter_enforces_concurrency_window_and_reset(self):
        now = [100.0]
        limiter = training_workspace_routes.TrainingTaskLimiter(
            max_requests=2,
            window_seconds=60,
            clock=lambda: now[0],
        )

        first = limiter.acquire(1)
        self.assertTrue(first.allowed)
        concurrent = limiter.acquire(1)
        self.assertFalse(concurrent.allowed)
        self.assertEqual(concurrent.retry_after, 1)

        limiter.release(1)
        second = limiter.acquire(1)
        self.assertTrue(second.allowed)
        limiter.release(1)
        limited = limiter.acquire(1)
        self.assertFalse(limited.allowed)
        self.assertEqual(limited.retry_after, 60)

        other_user = limiter.acquire(2)
        self.assertTrue(other_user.allowed)
        limiter.release(2)
        now[0] = 161.0
        self.assertTrue(limiter.acquire(1).allowed)
        limiter.release(1)
        limiter.reset()
        now[0] = 100.0
        self.assertTrue(limiter.acquire(1).allowed)

    def test_training_task_limiter_allows_only_one_concurrent_request_per_user(self):
        limiter = training_workspace_routes.TrainingTaskLimiter()
        barrier = Barrier(3)

        def acquire():
            barrier.wait()
            return limiter.acquire(1)

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(acquire) for _ in range(2)]
            barrier.wait()
            decisions = [future.result() for future in futures]

        self.assertEqual(sum(decision.allowed for decision in decisions), 1)
        self.assertEqual(sum(not decision.allowed for decision in decisions), 1)
        limiter.release(1)

    def test_training_task_limiter_prunes_expired_users_before_enforcing_capacity(self):
        now = [100.0]
        limiter = training_workspace_routes.TrainingTaskLimiter(
            max_requests=1,
            window_seconds=60,
            max_tracked_users=3,
            clock=lambda: now[0],
        )
        for user_id in range(1, 4):
            self.assertTrue(limiter.acquire(user_id).allowed)
            limiter.release(user_id)
        self.assertEqual(limiter.tracked_user_count, 3)

        now[0] = 161.0
        self.assertTrue(limiter.acquire(4).allowed)
        self.assertEqual(limiter.tracked_user_count, 1)

    def test_training_task_limiter_rejects_new_user_at_capacity_without_tracking_it(self):
        limiter = training_workspace_routes.TrainingTaskLimiter(max_tracked_users=2)
        self.assertTrue(limiter.acquire(1).allowed)
        self.assertTrue(limiter.acquire(2).allowed)

        rejected = limiter.acquire(3)

        self.assertFalse(rejected.allowed)
        self.assertGreaterEqual(rejected.retry_after, 1)
        self.assertEqual(limiter.tracked_user_count, 2)

    def test_training_task_limiter_preserves_active_users_and_release_removes_empty_entries(self):
        now = [100.0]
        limiter = training_workspace_routes.TrainingTaskLimiter(
            max_requests=1,
            window_seconds=60,
            max_tracked_users=2,
            clock=lambda: now[0],
        )
        self.assertTrue(limiter.acquire(1).allowed)
        self.assertTrue(limiter.acquire(2).allowed)
        limiter.release(2)
        now[0] = 161.0

        self.assertTrue(limiter.acquire(3).allowed)
        self.assertEqual(limiter.tracked_user_count, 2)
        self.assertFalse(limiter.acquire(1).allowed)

        limiter.release(1)
        self.assertEqual(limiter.tracked_user_count, 1)
        limiter.reset()
        self.assertEqual(limiter.tracked_user_count, 0)

    def test_workspace_task_post_returns_429_with_retry_after_and_releases_in_finally(self):
        payload = {
            "task_type": "practice_grading",
            "inputs": {"stem": "四君子汤主治什么证型？"},
        }
        acquired = training_workspace_routes.training_task_limiter.acquire(1)
        self.assertTrue(acquired.allowed)
        try:
            response = self.client.post("/training/workspace/tasks", json=payload)
        finally:
            training_workspace_routes.training_task_limiter.release(1)

        self.assertEqual(response.status_code, 429)
        self.assertEqual(response.headers["Retry-After"], "1")
        with self.Session() as db:
            self.assertEqual(db.query(database.TrainingTaskRecord).count(), 0)

        with patch(
            "APP.backend.routers.training_workspace_routes.create_training_task",
            side_effect=RuntimeError("runtime failed"),
        ):
            failed = TestClient(self.app, raise_server_exceptions=False).post(
                "/training/workspace/tasks",
                json=payload,
            )
        self.assertEqual(failed.status_code, 500)
        recovered = self.client.post("/training/workspace/tasks", json=payload)
        self.assertEqual(recovered.status_code, 200)

    def test_workspace_task_rejects_concurrent_request_without_consuming_its_stream(self):
        acquired = training_workspace_routes.training_task_limiter.acquire(1)
        self.assertTrue(acquired.allowed)
        receive_calls = 0

        async def receive():
            nonlocal receive_calls
            receive_calls += 1
            return {"type": "http.request", "body": b"{}", "more_body": False}

        request = Request(
            {"type": "http", "method": "POST", "path": "/training/workspace/tasks", "headers": []},
            receive,
        )
        user = database.UserModel(id=1, username="learner", email="learner@example.com", hashed_password="x")
        try:
            with self.assertRaises(HTTPException) as raised:
                asyncio.run(training_workspace_routes.create_workspace_task(request, user, None))
        finally:
            training_workspace_routes.training_task_limiter.release(1)

        self.assertEqual(raised.exception.status_code, 429)
        self.assertEqual(receive_calls, 0)

    def test_workspace_task_invalid_json_releases_active_slot_and_counts_toward_window(self):
        for _ in range(10):
            response = self.client.post(
                "/training/workspace/tasks",
                content=b"{invalid",
                headers={"content-type": "application/json"},
            )
            self.assertEqual(response.status_code, 400)

        rejected = self.client.post(
            "/training/workspace/tasks",
            content=b"{invalid",
            headers={"content-type": "application/json"},
        )
        self.assertEqual(rejected.status_code, 429)

        training_workspace_routes.training_task_limiter.reset()
        parsed_error = self.client.post(
            "/training/workspace/tasks",
            content=b"{invalid",
            headers={"content-type": "application/json"},
        )
        self.assertEqual(parsed_error.status_code, 400)
        self.assertTrue(training_workspace_routes.training_task_limiter.acquire(1).allowed)
        training_workspace_routes.training_task_limiter.release(1)

    def test_workspace_task_schema_error_releases_active_slot(self):
        response = self.client.post(
            "/training/workspace/tasks",
            json={"task_type": 123},
        )

        self.assertEqual(response.status_code, 422)
        self.assertTrue(training_workspace_routes.training_task_limiter.acquire(1).allowed)
        training_workspace_routes.training_task_limiter.release(1)

    def test_workspace_task_413_counts_and_releases_active_slot(self):
        for _ in range(9):
            response = self.client.post(
                "/training/workspace/tasks",
                content=b"{invalid",
                headers={"content-type": "application/json"},
            )
            self.assertEqual(response.status_code, 400)

        oversized = self.client.post(
            "/training/workspace/tasks",
            content=b" " * 65537,
            headers={"content-type": "application/json"},
        )
        self.assertEqual(oversized.status_code, 413)

        rejected = self.client.post(
            "/training/workspace/tasks",
            json={"task_type": "practice_grading", "inputs": {"stem": "题目"}},
        )
        self.assertEqual(rejected.status_code, 429)

    def test_workspace_get_and_legacy_post_are_not_training_workspace_limited(self):
        acquired = training_workspace_routes.training_task_limiter.acquire(1)
        self.assertTrue(acquired.allowed)
        try:
            modules = self.client.get("/training/workspace/modules")
            legacy = self.client.post(
                "/training/practice/grade",
                json={
                    "question_id": "legacy-1",
                    "stem": "四君子汤主治什么证型？",
                    "student_answer": "脾胃气虚证",
                    "standard_answer": "脾胃气虚证",
                },
            )
        finally:
            training_workspace_routes.training_task_limiter.release(1)

        self.assertEqual(modules.status_code, 200)
        self.assertEqual(legacy.status_code, 200)

    def test_workspace_task_post_rejects_invalid_nested_input_types(self):
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
        valid_inputs = {
            "question_id": "demo-sijunzi-001",
            "question_type": "short_answer",
            "stem": "四君子汤主治的核心证型是什么？",
            "student_answer": "中焦虚寒证",
            "standard_answer": "脾胃气虚证",
            "rubric": "答出脾胃气虚证得分。",
            "knowledge_points": ["四君子汤", "脾胃气虚证"],
            "difficulty": 2,
        }
        for field, value in invalid_inputs.items():
            with self.subTest(field=field):
                response = self.client.post(
                    "/training/workspace/tasks",
                    json={
                        "task_type": "practice_grading",
                        "inputs": {**valid_inputs, field: value},
                    },
                )
                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.json()["detail"], f"{field} has invalid type")

        with self.Session() as db:
            self.assertEqual(db.query(database.TrainingTaskRecord).count(), 0)

    def test_workspace_task_post_does_not_convert_commit_failure_to_400(self):
        client = TestClient(self.app, raise_server_exceptions=False)
        with patch(
            "APP.backend.routers.training_workspace_routes.create_training_task",
            side_effect=RuntimeError("database commit failed"),
        ):
            response = client.post(
                "/training/workspace/tasks",
                json={
                    "task_type": "practice_grading",
                    "inputs": {"stem": "四君子汤主治什么证型？"},
                },
            )

        self.assertEqual(response.status_code, 500)

    def test_onboarding_choice_defaults_are_present_in_options(self):
        response = self.client.get("/training/onboarding/group-templates")

        self.assertEqual(response.status_code, 200)
        body = response.json()
        questions = body["questions"]
        questions_by_key = {question["key"]: question for question in questions}

        for question in questions:
            if question["type"] not in {"single_choice", "multi_choice"}:
                continue

            allowed_values = {
                option["value"] if isinstance(option, dict) else option
                for option in question["options"]
            }
            for default_value in question["default_by_group"].values():
                if question["type"] == "single_choice":
                    self.assertIn(default_value, allowed_values)
                else:
                    self.assertIsInstance(default_value, list)
                    for item in default_value:
                        self.assertIn(item, allowed_values)

        difficulties_defaults = questions_by_key["current_difficulties"]["default_by_group"]
        for group in body["groups"]:
            self.assertEqual(
                group["default_profile"]["current_difficulties"],
                difficulties_defaults[group["key"]],
            )

    def test_onboarding_submit_applies_group_defaults_when_answers_missing(self):
        response = self.client.post(
            "/training/onboarding/survey",
            json={
                "learner_group": "public_interest",
                "goals": {},
                "preferences": {},
            },
        )

        self.assertEqual(response.status_code, 200)
        baseline = response.json()["l0_baseline"]
        self.assertEqual(baseline["daily_available_minutes"], 20)
        self.assertEqual(baseline["preferred_time_slot"], "碎片化不固定")
        self.assertEqual(
            baseline["resource_preference"],
            ["知识卡片", "药食同源科普", "生活场景问答"],
        )

    def test_onboarding_submit_requires_learner_group(self):
        response = self.client.post(
            "/training/onboarding/survey",
            json={
                "preferences": {},
            },
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["detail"], "请选择学习群体")

    def test_onboarding_submit_and_status_return_field_sources_for_defaulted_answers(self):
        response = self.client.post(
            "/training/onboarding/survey",
            json={
                "learner_group": "public_interest",
                "preferences": {
                    "daily_available_minutes": 30,
                },
            },
        )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["field_sources"]["preferences.daily_available_minutes"], "user_confirmed")
        self.assertEqual(body["field_sources"]["preferences.preferred_time_slot"], "defaulted")
        self.assertEqual(body["field_sources"]["preferences.resource_preference"], "defaulted")
        self.assertEqual(body["field_sources"]["goals.current_difficulties"], "defaulted")
        self.assertEqual(body["field_sources"]["learner_group"], "user_confirmed")

        status = self.client.get("/training/onboarding/status")
        self.assertEqual(status.status_code, 200)
        status_body = status.json()
        self.assertEqual(status_body["survey_answers"]["learner_group"], "public_interest")
        self.assertEqual(status_body["survey_answers"]["learner_group_title"], "大众兴趣群体")
        self.assertEqual(status_body["field_sources"], body["field_sources"])

    def test_onboarding_dismiss_hides_popup_without_marking_completed(self):
        dismiss = self.client.post("/training/onboarding/dismiss")

        self.assertEqual(dismiss.status_code, 200)
        self.assertEqual(
            dismiss.json(),
            {"status": "dismissed", "needs_survey_popup": False},
        )

        status = self.client.get("/training/onboarding/status")
        self.assertEqual(status.status_code, 200)
        self.assertEqual(status.json()["status"], "pending")
        self.assertFalse(status.json()["needs_survey_popup"])
        self.assertEqual(status.json()["survey_answers"], {})

    def test_onboarding_submit_preserves_group_key_in_status_survey_and_exposes_title(self):
        response = self.client.post(
            "/training/onboarding/survey",
            json={
                "learner_group": "public_interest",
                "goals": {},
                "preferences": {},
            },
        )

        self.assertEqual(response.status_code, 200)
        status = self.client.get("/training/onboarding/status")
        self.assertEqual(status.status_code, 200)
        status_body = status.json()
        self.assertEqual(status_body["survey_answers"]["learner_group"], "public_interest")
        self.assertEqual(status_body["survey_answers"]["learner_group_title"], "大众兴趣群体")
        self.assertIn("大众兴趣", status_body["learner_group"])

    def test_onboarding_submit_keeps_explicit_daily_minutes_while_filling_other_defaults(self):
        response = self.client.post(
            "/training/onboarding/survey",
            json={
                "learner_group": "public_interest",
                "goals": {
                    "current_difficulties": [],
                },
                "preferences": {
                    "daily_available_minutes": 30,
                    "preferred_time_slot": "",
                    "resource_preference": [],
                },
            },
        )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        baseline = body["l0_baseline"]
        self.assertEqual(baseline["daily_available_minutes"], 30)
        self.assertEqual(baseline["preferred_time_slot"], "碎片化不固定")
        self.assertEqual(
            baseline["resource_preference"],
            ["知识卡片", "药食同源科普", "生活场景问答"],
        )
        self.assertEqual(baseline["current_difficulties"], "术语记不住、资料太分散")
        self.assertNotIn("[", baseline["current_difficulties"])

        status = self.client.get("/training/onboarding/status")
        self.assertEqual(status.status_code, 200)
        self.assertEqual(
            status.json()["survey_answers"]["current_difficulties"],
            "术语记不住、资料太分散",
        )

    def test_onboarding_submit_preserves_legacy_top_level_answers_when_nested_sections_missing(self):
        response = self.client.post(
            "/training/onboarding/survey",
            json={
                "learner_group": "public_interest",
                "daily_available_minutes": 30,
                "preferred_time_slot": "午休",
                "resource_preference": ["视频微课"],
                "current_difficulties": ["时间不稳定"],
            },
        )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        baseline = body["l0_baseline"]
        self.assertEqual(baseline["daily_available_minutes"], 30)
        self.assertEqual(baseline["preferred_time_slot"], "午休")
        self.assertEqual(baseline["resource_preference"], ["视频微课"])
        self.assertEqual(baseline["current_difficulties"], "时间不稳定")

        status = self.client.get("/training/onboarding/status")
        self.assertEqual(status.status_code, 200)
        survey_answers = status.json()["survey_answers"]
        self.assertEqual(survey_answers["learner_group"], "public_interest")
        self.assertEqual(survey_answers["daily_available_minutes"], 30)
        self.assertEqual(survey_answers["preferred_time_slot"], "午休")
        self.assertEqual(survey_answers["resource_preference"], ["视频微课"])
        self.assertEqual(survey_answers["current_difficulties"], "时间不稳定")

        profile = self.client.get("/personalization/learner-profile")
        self.assertEqual(profile.status_code, 200)
        profile_body = profile.json()
        self.assertIn("30 分钟", profile_body["time_constraints"])
        self.assertIn("午休", profile_body["time_constraints"])
        self.assertEqual(profile_body["survey"]["preferences"]["daily_available_minutes"], 30)

    def test_onboarding_submit_normalizes_current_difficulties_list_without_python_repr(self):
        response = self.client.post(
            "/training/onboarding/survey",
            json={
                "learner_group": "cross_professional",
                "goals": {
                    "current_difficulties": ["术语记不住", "证型容易混淆"],
                },
            },
        )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["l0_baseline"]["current_difficulties"], "术语记不住、证型容易混淆")
        self.assertNotIn("[", body["l0_baseline"]["current_difficulties"])

        status = self.client.get("/training/onboarding/status")
        self.assertEqual(status.status_code, 200)
        self.assertEqual(
            status.json()["survey_answers"]["current_difficulties"],
            "术语记不住、证型容易混淆",
        )
        self.assertNotIn("[", status.json()["survey_answers"]["current_difficulties"])

        with self.Session() as db:
            profile = db.query(database.UserProfile).filter_by(user_id=1).one()
            self.assertEqual(profile.medical_history, "术语记不住、证型容易混淆")
            self.assertNotIn("[", profile.medical_history)

    def test_onboarding_submit_preserves_existing_locked_fields_when_request_omits_locked_fields(self):
        profile_response = self.client.put(
            "/personalization/learner-profile",
            json={
                "learner_group": "学历教育",
                "time_constraints": "晚间20:00–21:00",
                "locked_fields": ["time_constraints"],
                "lock_reason": {"time_constraints": "用户手动确认"},
            },
        )
        self.assertEqual(profile_response.status_code, 200)

        response = self.client.post(
            "/training/onboarding/survey",
            json={
                "learner_group": "academic",
                "goals": {},
                "preferences": {},
            },
        )

        self.assertEqual(response.status_code, 200)
        detail = self.client.get("/personalization/learner-profile")
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(detail.json()["locked_fields"], ["time_constraints"])
        self.assertEqual(detail.json()["time_constraints"], "晚间20:00–21:00")
        self.assertEqual(detail.json()["lock_reason"], {"time_constraints": "用户手动确认"})

    def test_onboarding_submit_unions_existing_and_new_locked_fields(self):
        profile_response = self.client.put(
            "/personalization/learner-profile",
            json={
                "learner_group": "学历教育",
                "resource_preferences": "知识卡片",
                "locked_fields": ["resource_preferences"],
                "lock_reason": {"resource_preferences": "用户手动确认"},
            },
        )
        self.assertEqual(profile_response.status_code, 200)

        response = self.client.post(
            "/training/onboarding/survey",
            json={
                "learner_group": "public_interest",
                "locked_fields": ["preferred_time_slot"],
            },
        )

        self.assertEqual(response.status_code, 200)
        detail = self.client.get("/personalization/learner-profile")
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(
            detail.json()["locked_fields"],
            ["resource_preferences", "time_constraints"],
        )
        self.assertEqual(
            detail.json()["lock_reason"],
            {
                "resource_preferences": "用户手动确认",
                "time_constraints": "用户在学情调查中确认",
            },
        )

    def test_onboarding_submit_maps_survey_locked_fields_to_profile_keys(self):
        response = self.client.post(
            "/training/onboarding/survey",
            json={
                "learner_group": "public_interest",
                "locked_fields": ["preferred_time_slot"],
            },
        )

        self.assertEqual(response.status_code, 200)
        detail = self.client.get("/personalization/learner-profile")
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(detail.json()["locked_fields"], ["time_constraints"])
        self.assertEqual(detail.json()["survey"]["locked_fields"], ["preferred_time_slot"])
        self.assertEqual(detail.json()["survey"]["profile_locked_fields"], ["time_constraints"])

    def test_onboarding_submit_writes_current_time_slot_before_new_lock_takes_effect(self):
        response = self.client.post(
            "/training/onboarding/survey",
            json={
                "learner_group": "public_interest",
                "preferred_time_slot": "午休",
                "locked_fields": ["preferred_time_slot"],
            },
        )

        self.assertEqual(response.status_code, 200)
        detail = self.client.get("/personalization/learner-profile")
        self.assertEqual(detail.status_code, 200)
        self.assertIn("午休", detail.json()["time_constraints"])
        self.assertEqual(detail.json()["locked_fields"], ["time_constraints"])

    def test_onboarding_submit_preserves_legacy_top_level_learning_mode_and_difficulty_preference(self):
        response = self.client.post(
            "/training/onboarding/survey",
            json={
                "learner_group": "public_interest",
                "learning_mode": "问答优先",
                "difficulty_preference": "D4",
            },
        )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["l0_baseline"]["preferred_difficulty"], "D4")

        status = self.client.get("/training/onboarding/status")
        self.assertEqual(status.status_code, 200)
        survey_answers = status.json()["survey_answers"]
        self.assertEqual(survey_answers["learning_mode"], "问答优先")
        self.assertEqual(survey_answers["difficulty_preference"], "D4")

        profile = self.client.get("/personalization/learner-profile")
        self.assertEqual(profile.status_code, 200)
        self.assertIn("问答优先", profile.json()["resource_preferences"])
        self.assertIn("难度偏好 D4", profile.json()["resource_preferences"])

    def test_learning_plan_summary_uses_flat_completed_onboarding_answers(self):
        submit = self.client.post(
            "/training/onboarding/survey",
            json={
                "learner_group": "public_interest",
                "daily_available_minutes": 20,
                "preferred_time_slot": "午休",
            },
        )
        self.assertEqual(submit.status_code, 200)

        plan = self.client.get("/training/plan/summary")
        self.assertEqual(plan.status_code, 200)
        body = plan.json()
        self.assertEqual(body["constraints"]["daily_available_minutes"], 20)
        self.assertEqual(body["constraints"]["preferred_time_slot"], "午休")

    def test_onboarding_target_validation_rolls_back_survey_writes(self):
        response = self.client.post(
            "/training/onboarding/survey",
            json={
                "learner_group": "academic",
                "preferences": {"daily_available_minutes": 45},
                "target_type": "certification",
                "exam_track_id": "missing-track",
            },
        )

        self.assertEqual(response.status_code, 422)
        with self.Session() as db:
            profile = db.query(database.UserProfile).filter(
                database.UserProfile.user_id == 1
            ).first()
            self.assertIsNone(profile)
            self.assertEqual(
                db.query(database.LearningActivityRecord).filter(
                    database.LearningActivityRecord.user_id == 1,
                    database.LearningActivityRecord.activity_type == "onboarding_survey",
                ).count(),
                0,
            )
            self.assertEqual(
                db.query(database.UserLearningTarget).filter(
                    database.UserLearningTarget.user_id == 1
                ).count(),
                0,
            )

    def test_onboarding_submit_status_and_diagnosis_summary(self):
        response = self.client.post(
            "/training/onboarding/survey",
            json={
                "learner_group": "跨专业进阶",
                "background": {
                    "education": "本科",
                    "major_or_role": "健康管理师",
                    "tcm_foundation": "weak",
                },
                "goals": {
                    "long_term_goal": "掌握体质辨识与健康科普",
                    "short_term_goal": "完成九种体质基础入门",
                },
                "preferences": {
                    "daily_available_minutes": 60,
                    "preferred_time_slot": "晚间20:00–21:00",
                    "resource_preference": ["知识卡片", "案例训练"],
                    "learning_mode": "案例优先",
                },
                "special_requirements": {
                    "description": "只接受站内提醒",
                },
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "onboarding_completed")
        self.assertEqual(response.json()["l0_baseline"]["daily_available_minutes"], 60)

        status = self.client.get("/training/onboarding/status")
        self.assertEqual(status.status_code, 200)
        self.assertEqual(status.json()["learner_group"], "跨专业进阶")

        from APP.backend.database import LearningActivityRecord

        with self.Session() as db:
            before_reads = db.query(LearningActivityRecord).count()

        diagnosis = self.client.get("/training/diagnosis/summary")
        self.assertEqual(diagnosis.status_code, 200)
        self.assertIn(diagnosis.json()["diagnosis"]["stage_id"], ["T0", "T1", "T2", "T4", "T5"])
        self.assertIn("preferred_difficulty", diagnosis.json()["learning_profile"])

        diagnosis_again = self.client.get("/training/diagnosis/summary")
        self.assertEqual(diagnosis_again.status_code, 200)
        with self.Session() as db:
            after_reads = db.query(LearningActivityRecord).count()
        self.assertEqual(after_reads, before_reads)

        plan = self.client.get("/training/plan/summary")
        self.assertEqual(plan.status_code, 200)
        plan_payload = plan.json()
        self.assertTrue(plan_payload["phase_plan"])
        self.assertTrue(plan_payload["daily_task_cards"])
        self.assertTrue(plan_payload["weekly_plan"]["focus"])
        self.assertTrue(plan_payload["daily_tasks"])
        self.assertTrue(plan_payload["daily_tasks"][0]["key"])
        self.assertTrue(plan_payload["daily_tasks"][0]["reason"])

        report = self.client.get("/training/report")
        self.assertEqual(report.status_code, 200)
        report_payload = report.json()
        self.assertEqual(report_payload["onboarding_status"]["status"], "onboarding_completed")
        self.assertIn("diagnosis", report_payload)
        self.assertTrue(report_payload["learner_overview"]["learner_group"])
        self.assertTrue(report_payload["mastery_radar"])
        self.assertTrue(report_payload["weak_points"])
        self.assertTrue(report_payload["resource_match"]["recommended_difficulty"])
        self.assertIn(report_payload["t_stage"]["stage_id"], ["T0", "T1", "T2", "T4", "T5"])
        self.assertTrue(report_payload["next_actions"])

    def test_learner_settings_save_analysis_frequency_and_locked_fields(self):
        response = self.client.put(
            "/personalization/learner-settings",
            json={
                "analysis_frequency": "weekly",
                "locked_fields": ["learner_group", "time_constraints"],
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["settings"]["analysis_frequency"], "weekly")
        self.assertEqual(response.json()["locked_fields"], ["learner_group", "time_constraints"])

        detail = self.client.get("/personalization/learner-settings")
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(detail.json()["settings"]["analysis_frequency"], "weekly")

    def test_learner_settings_preserve_existing_settings_when_only_locks_change(self):
        response = self.client.put(
            "/personalization/learner-settings",
            json={
                "analysis_frequency": "weekly",
                "locked_fields": ["learner_group"],
            },
        )
        self.assertEqual(response.status_code, 200)

        with self.Session() as db:
            profile = db.query(database.UserProfile).filter_by(user_id=1).one()
            profile.survey_json = '{"settings": {"analysis_frequency": "weekly", "retention_window": "30d"}}'
            db.commit()

        locks = self.client.put(
            "/personalization/learner-settings",
            json={"locked_fields": ["time_constraints"]},
        )
        self.assertEqual(locks.status_code, 200)
        self.assertEqual(locks.json()["settings"]["analysis_frequency"], "weekly")
        self.assertEqual(locks.json()["settings"]["retention_window"], "30d")

    def test_learner_settings_preserve_existing_lock_reasons_for_retained_fields(self):
        profile_response = self.client.put(
            "/personalization/learner-profile",
            json={
                "time_constraints": "晚间20:00–21:00",
                "locked_fields": ["time_constraints"],
                "lock_reason": {"time_constraints": "用户手动确认"},
            },
        )
        self.assertEqual(profile_response.status_code, 200)

        response = self.client.put(
            "/personalization/learner-settings",
            json={
                "analysis_frequency": "weekly",
                "locked_fields": ["time_constraints", "learner_group"],
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json()["lock_reason"],
            {
                "time_constraints": "用户手动确认",
                "learner_group": "用户在设置页锁定",
            },
        )

        unlocked = self.client.put(
            "/personalization/learner-settings",
            json={"locked_fields": ["learner_group"]},
        )
        self.assertEqual(unlocked.status_code, 200)
        self.assertEqual(unlocked.json()["lock_reason"], {"learner_group": "用户在设置页锁定"})

    def test_learner_profile_saves_locked_fields(self):
        response = self.client.put(
            "/personalization/learner-profile",
            json={
                "learner_group": "学历教育",
                "time_constraints": "晚间20:00–21:00",
                "resource_preferences": "知识卡片",
                "locked_fields": ["time_constraints"],
            },
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["profile"]["time_constraints"], "晚间20:00–21:00")
        self.assertEqual(body["profile"]["locked_fields"], ["time_constraints"])

        detail = self.client.get("/personalization/learner-profile")
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(detail.json()["locked_fields"], ["time_constraints"])

    def test_daily_checkin_records_activity_without_refreshing_system_data(self):
        with self.Session() as db:
            db.add(database.SystemData(
                user_id=1,
                calculated_at=datetime(2026, 7, 15, 8, 0, 0),
            ))
            db.commit()

        response = self.client.post("/training/checkin")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["checked_in"])
        self.assertEqual(body["resource_type"], "checkin")
        self.assertTrue(body["resource_id"].startswith("checkin:"))
        self.assertRegex(body["date"], r"^\d{4}-\d{2}-\d{2}$")
        self.assertEqual(body["status"]["today"], body["date"])
        self.assertTrue(body["status"]["checked_in_today"])
        self.assertEqual(body["status"]["total_checkins"], 1)
        self.assertEqual(body["status"]["streak"], 1)
        self.assertTrue(body["status"]["calendar_days"])
        self.assertTrue(any(day["date"] == body["date"] and day["checked_in"] for day in body["status"]["calendar_days"]))
        self.assertEqual(body["message"], "今日签到成功")

        again = self.client.post("/training/checkin")
        self.assertEqual(again.status_code, 200)
        self.assertTrue(again.json()["already_checked_in"])
        self.assertEqual(again.json()["status"]["total_checkins"], 1)
        self.assertEqual(again.json()["message"], "今日已签到")
        with self.Session() as db:
            snapshot = db.query(database.SystemData).filter_by(user_id=1).one()
            self.assertEqual(snapshot.calculated_at, datetime(2026, 7, 15, 8, 0, 0))

    def test_difficulty_feedback_records_user_response(self):
        response = self.client.post(
            "/training/difficulty-feedback",
            json={
                "notice_id": "NOTICE_DIFFICULTY_DROP_TEST",
                "action": "too_hard",
                "reason": "当前任务仍然偏难",
                "current_difficulty": "D3",
                "suggested_difficulty": "D2",
            },
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["success"])
        self.assertEqual(body["feedback_type"], "difficulty_adjustment")

        with self.Session() as db:
            row = db.query(database.LearningActivityRecord).filter(
                database.LearningActivityRecord.activity_type == "difficulty_feedback"
            ).first()
            self.assertIsNotNone(row)
            self.assertEqual(row.resource_type, "intervention_notice")

    def test_next_practice_question_is_kp_scoped_and_hides_answers(self):
        with self.Session() as db:
            db.add_all([
                database.KnowledgePoint(
                    kp_id="KP_ATLAS_001",
                    name="阴阳学说",
                    status="active",
                ),
                database.QuestionBankItem(
                    question_id="question-atlas-1",
                    stem="阴阳关系的基本特征是什么？",
                    answer="对立制约、互根互用等",
                    analysis="正式解析",
                    kp_ids_json='["KP_ATLAS_001"]',
                    question_type="short_answer",
                    difficulty=2,
                    quality_score=0.9,
                    status="active",
                ),
                database.QuestionBankItem(
                    question_id="question-other-kp",
                    stem="其它知识点题目",
                    answer="秘密答案",
                    kp_ids_json='["KP_OTHER"]',
                    question_type="short_answer",
                    difficulty=2,
                    quality_score=1.0,
                    status="active",
                ),
            ])
            db.commit()

        response = self.client.get(
            "/training/practice/next",
            params={"kp_id": "KP_ATLAS_001"},
        )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["available"])
        self.assertEqual(body["question"]["question_id"], "question-atlas-1")
        self.assertEqual(body["question"]["kp_ids"], ["KP_ATLAS_001"])
        issued_request_id = body["question"]["request_id"]
        self.assertTrue(issued_request_id)
        self.assertNotIn("answer", body["question"])
        self.assertNotIn("analysis", body["question"])
        self.assertNotIn("standard_answer", body["question"])
        with self.Session() as db:
            claim = db.query(database.CorePracticeSubmissionClaim).filter_by(
                user_id=1,
                request_id=issued_request_id,
                question_id="question-atlas-1",
            ).one_or_none()
            self.assertIsNotNone(claim)

        empty_answer = self.client.post(
            "/training/practice/grade",
            json={
                "question_id": "question-atlas-1",
                "stem": "阴阳关系的基本特征是什么？",
                "student_answer": "   ",
                "request_id": issued_request_id,
            },
        )
        self.assertEqual(empty_answer.status_code, 422)
        with self.Session() as db:
            self.assertIsNotNone(db.query(database.CorePracticeSubmissionClaim).filter_by(
                user_id=1,
                request_id=issued_request_id,
            ).one_or_none())

        unauthorized = self.client.post(
            "/training/practice/grade",
            json={
                "question_id": "question-atlas-1",
                "stem": "伪造题干",
                "student_answer": "任意答案",
                "request_id": "not-issued",
            },
        )
        self.assertEqual(unauthorized.status_code, 400)
        self.assertEqual(unauthorized.json()["detail"], "practice request was not issued to this user")

        with patch.object(training_routes, "practice_grading_runner") as runner:
            missing_claim = self.client.post(
                "/training/practice/grade",
                json={
                    "question_id": "question-atlas-1",
                    "stem": "伪造题干",
                    "student_answer": "任意答案",
                },
            )
        self.assertEqual(missing_claim.status_code, 400)
        self.assertEqual(
            missing_claim.json()["detail"],
            "controlled practice requires an issued request_id",
        )
        runner.assert_not_called()

        missing = self.client.get(
            "/training/practice/next",
            params={"kp_id": "KP_WITHOUT_QUESTION"},
        )
        self.assertEqual(missing.status_code, 200)
        self.assertFalse(missing.json()["available"])
        self.assertIsNone(missing.json()["question"])

    def test_stable_practice_endpoint_filters_objective_and_case_questions_without_kp(self):
        with self.Session() as db:
            db.add(database.KnowledgePoint(kp_id="KP_MODE", name="题型筛选", status="active"))
            db.add_all([
                database.QuestionBankItem(
                    question_id="Q_OBJECTIVE",
                    stem="四君子汤由哪些药物组成？",
                    answer="A",
                    analysis="人参、白术、茯苓、炙甘草。",
                    kp_ids_json='["KP_MODE"]',
                    question_type="single_choice",
                    difficulty=1,
                    quality_score=1.0,
                    status="active",
                ),
                database.LearningQuestion(
                    question_id="Q_OBJECTIVE",
                    question_type="single_choice",
                    question_content="四君子汤由哪些药物组成？",
                    options_json='[{"option_id":"A","content":"人参、白术、茯苓、炙甘草"}]',
                    answer_json='["A"]',
                    kp_ids_json='["KP_MODE"]',
                ),
                database.QuestionBankItem(
                    question_id="Q_CASE",
                    stem="分析脾胃气虚证使用四君子汤的依据。",
                    answer="辨证与配伍依据",
                    analysis="案例解析",
                    kp_ids_json='["KP_MODE"]',
                    question_type="case_quiz",
                    difficulty=2,
                    quality_score=0.9,
                    status="active",
                ),
            ])
            db.commit()

        objective = self.client.get("/v1/workshop/practice/next", params={"mode": "objective"})
        case = self.client.get("/v1/workshop/practice/next", params={"mode": "case"})

        self.assertEqual(objective.status_code, 200)
        self.assertEqual(objective.json()["question"]["question_id"], "Q_OBJECTIVE")
        self.assertEqual(objective.json()["question"]["options"][0]["option_id"], "A")
        self.assertEqual(objective.json()["question"]["kp_names"], ["题型筛选"])
        self.assertEqual(case.status_code, 200)
        self.assertEqual(case.json()["question"]["question_id"], "Q_CASE")
        self.assertEqual(case.json()["question"]["question_type"], "case_quiz")

    def test_next_practice_moves_to_an_unattempted_question_after_grading(self):
        with self.Session() as db:
            db.add(database.KnowledgePoint(kp_id="KP_SEQUENCE", name="连续练习", status="active"))
            db.add_all([
                database.QuestionBankItem(
                    question_id="Q_SEQUENCE_1",
                    stem="第一题",
                    answer="A",
                    analysis="第一题解析",
                    kp_ids_json='["KP_SEQUENCE"]',
                    question_type="single_choice",
                    difficulty=1,
                    quality_score=1.0,
                    status="active",
                ),
                database.QuestionBankItem(
                    question_id="Q_SEQUENCE_2",
                    stem="第二题",
                    answer="B",
                    analysis="第二题解析",
                    kp_ids_json='["KP_SEQUENCE"]',
                    question_type="single_choice",
                    difficulty=1,
                    quality_score=0.9,
                    status="active",
                ),
            ])
            db.commit()
        first = self.client.get(
            "/v1/workshop/practice/next",
            params={"mode": "objective", "kp_id": "KP_SEQUENCE"},
        ).json()["question"]
        runner_payload = {
            "grading": {
                "score": 100,
                "is_correct": True,
                "error_type": "",
                "analysis": "回答正确。",
                "standard_answer": "A",
            }
        }
        with patch.object(training_routes, "practice_grading_runner", return_value=runner_payload):
            graded = self.client.post(
                "/v1/workshop/practice/grade",
                json={
                    "question_id": first["question_id"],
                    "stem": first["stem"],
                    "student_answer": "A",
                    "request_id": first["request_id"],
                },
            )

        self.assertEqual(graded.status_code, 200)
        second = self.client.get(
            "/v1/workshop/practice/next",
            params={"mode": "objective", "kp_id": "KP_SEQUENCE"},
        )
        self.assertEqual(second.status_code, 200)
        self.assertEqual(second.json()["question"]["question_id"], "Q_SEQUENCE_2")

    def test_mistake_history_lists_all_owned_mistakes_and_marks_variation_eligibility(self):
        with self.Session() as db:
            db.add(database.UserModel(id=2, username="other-learner", email="other@example.com", hashed_password="x"))
            db.add(database.QuestionBankItem(
                question_id="Q_WRONG",
                stem="四君子汤的功用是什么？",
                answer="益气健脾",
                analysis="功用辨析",
                kp_ids_json='["KP_WRONG"]',
                question_type="fill_blank",
                difficulty=1,
                status="active",
            ))
            db.add(database.QuestionAttempt(
                user_id=1,
                question_id="Q_WRONG",
                answer="温中散寒",
                is_correct=False,
                score=0,
                kp_ids_json='["KP_WRONG"]',
                feedback="功用混淆",
            ))
            db.add_all([
                database.MistakeRecord(
                    id=301,
                    user_id=1,
                    question_id="Q_WRONG",
                    kp_ids_json='["KP_WRONG"]',
                    error_type="知识混淆",
                    summary="将补气剂与温里剂混淆",
                    status="active",
                ),
                database.MistakeRecord(
                    id=302,
                    user_id=2,
                    question_id="Q_FOREIGN",
                    kp_ids_json='["KP_FOREIGN"]',
                    status="active",
                ),
            ])
            db.commit()

        response = self.client.get("/v1/workshop/practice/mistakes", params={"status": "all"})

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["total"], 1)
        self.assertEqual(body["items"][0]["mistake_id"], 301)
        self.assertEqual(body["items"][0]["student_answer"], "温中散寒")
        self.assertEqual(body["items"][0]["score"], 0.0)
        self.assertFalse(body["items"][0]["variation_available"])
        self.assertTrue(body["items"][0]["variation_reason"])
        self.assertTrue(body["items"][0]["answer_context_required"])
        self.assertFalse(body["items"][0]["answer_context_completed"])

        researched = self.client.post(
            "/v1/workshop/practice/mistakes/301/answer-context",
            json={
                "answer_state": "犹豫后作答",
                "reason": "选项辨析困难",
                "notes": "在两个相近功用之间犹豫。",
            },
        )
        self.assertEqual(researched.status_code, 200)
        researched_mistake = researched.json()["mistake"]
        self.assertTrue(researched_mistake["answer_context_completed"])
        self.assertEqual(researched_mistake["error_type"], "选项辨析困难")

    def test_expired_controlled_practice_request_is_rejected_without_running_grader(self):
        with self.Session() as db:
            db.add_all([
                database.KnowledgePoint(kp_id="KP_EXPIRED", name="过期练习", status="active"),
                database.QuestionBankItem(
                    question_id="question-expired",
                    stem="过期凭证还能提交吗？",
                    answer="不能",
                    analysis="过期凭证必须重新获取。",
                    kp_ids_json='["KP_EXPIRED"]',
                    question_type="short_answer",
                    difficulty=2,
                    status="active",
                ),
            ])
            db.commit()
        issued = self.client.get(
            "/training/practice/next",
            params={"kp_id": "KP_EXPIRED"},
        )
        request_id = issued.json()["question"]["request_id"]
        with self.Session() as db:
            claim = db.query(database.CorePracticeSubmissionClaim).filter_by(
                user_id=1,
                request_id=request_id,
            ).one()
            claim.created_at = training_routes._now() - timedelta(minutes=31)
            db.commit()

        with patch.object(training_routes, "practice_grading_runner") as runner:
            response = self.client.post(
                "/training/practice/grade",
                json={
                    "question_id": "question-expired",
                    "stem": "过期凭证还能提交吗？",
                    "student_answer": "不能",
                    "request_id": request_id,
                },
            )

        self.assertEqual(response.status_code, 410)
        self.assertEqual(response.json()["detail"], "practice request expired")
        runner.assert_not_called()
        with self.Session() as db:
            self.assertIsNone(db.query(database.CorePracticeSubmissionClaim).filter_by(
                user_id=1,
                request_id=request_id,
            ).one_or_none())
            self.assertEqual(db.query(database.LearningQuestionAttempt).count(), 0)

    def test_practice_request_id_requires_a_registered_question(self):
        response = self.client.post(
            "/training/practice/grade",
            json={
                "question_id": "manual-question",
                "stem": "手工题",
                "student_answer": "任意答案",
                "standard_answer": "任意答案",
                "request_id": "manual-request-1",
            },
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["detail"], "request_id requires an active registered question")
        with self.Session() as db:
            self.assertEqual(db.query(database.LearningAttemptRecord).count(), 0)

    def test_session_delete_removes_agent_context(self):
        with self.Session() as db:
            db.add(database.DbSession(id="session-core-context", user_id=1, title="测试会话"))
            db.add(database.LearningAgentContext(
                trace_id="trace-core-context",
                user_id=1,
                session_id="session-core-context",
                source_agent="chat_route",
                target_agent="health_workflow",
                purpose="generate_reply",
            ))
            db.commit()

        response = self.client.delete("/sessions/session-core-context")

        self.assertEqual(response.status_code, 200)
        with self.Session() as db:
            self.assertEqual(db.query(database.LearningAgentContext).count(), 0)
            self.assertIsNone(db.query(database.DbSession).filter_by(id="session-core-context").one_or_none())

    def test_legacy_practice_grade_routes_through_authoritative_application_service(self):
        runner_payload = {
            "grading": {
                "question_id": "question-1",
                "score": 100,
                "is_correct": True,
                "error_type": "",
                "analysis": "回答正确。",
                "standard_answer": "脾胃气虚证",
            }
        }
        with self.Session() as db:
            db.add_all([
                database.KnowledgePoint(kp_id="KP_ROUTE_001", name="测试知识点", status="active"),
                database.QuestionBankItem(
                    question_id="question-1",
                    stem="四君子汤主治什么证型？",
                    answer="脾胃气虚证",
                    analysis="回答正确。",
                    kp_ids_json='["KP_ROUTE_001"]',
                    question_type="short_answer",
                    difficulty=2,
                    status="active",
                ),
            ])
            db.commit()
        issued = self.client.get(
            "/training/practice/next",
            params={"kp_id": "KP_ROUTE_001"},
        )
        self.assertEqual(issued.status_code, 200)
        request_id = issued.json()["question"]["request_id"]
        with patch.object(training_routes, "practice_grading_runner", return_value=runner_payload) as runner:
            response = self.client.post(
                "/training/practice/grade",
                json={
                    "question_id": "question-1",
                    "stem": "四君子汤主治什么证型？",
                    "student_answer": "脾胃气虚证",
                    "standard_answer": "脾胃气虚证",
                    "knowledge_points": ["KP_ROUTE_001"],
                    "request_id": request_id,
                    "learner_id": 999,
                },
            )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["grading"]["question_id"], "question-1")
        self.assertTrue(payload["attempt_id"])
        self.assertTrue(payload["attempt_item_id"])
        self.assertTrue(payload["grading_artifact_id"])
        self.assertTrue(payload["audit_id"])
        self.assertEqual(payload["writeback"]["status"], "applied")
        self.assertNotIn("standard_answer", payload["grading"])
        runner.assert_called_once()
        with self.Session() as db:
            attempt = db.query(database.LearningAttemptRecord).filter_by(attempt_id=payload["attempt_id"]).one()
            self.assertEqual(attempt.learner_id, 1)
            core_attempt = db.query(database.LearningQuestionAttempt).filter_by(
                user_id=1,
                question_id="question-1",
            ).one()
            self.assertTrue(core_attempt.is_correct)
            stat = db.query(database.QuestionLearningStat).filter_by(
                user_id=1,
                question_id="question-1",
            ).one()
            self.assertEqual(stat.answer_accuracy, 1.0)
            state = db.query(database.UserKnowledgeState).filter_by(
                user_id=1,
                kp_id="KP_ROUTE_001",
            ).one()
            self.assertEqual(state.knowledge_mastery, 1.0)
            activity = db.query(database.LearningActivityRecord).filter_by(
                user_id=1,
                activity_type="question_attempt",
                resource_id="question-1",
            ).one()
            self.assertEqual(activity.completion_status, "completed")
            snapshot = db.query(database.SystemData).filter_by(user_id=1).one()
            rates = json.loads(snapshot.task_completion_rate_json)
            self.assertEqual(rates["value"], 1.0)

        repeated = self.client.post(
            "/training/practice/grade",
            json={
                "question_id": "question-1",
                "stem": "伪造题干",
                "student_answer": "脾胃气虚证",
                "standard_answer": "伪造答案",
                "knowledge_points": ["伪造知识点"],
                "request_id": request_id,
            },
        )
        self.assertEqual(repeated.status_code, 409)
        with self.Session() as db:
            self.assertEqual(db.query(database.LearningQuestionAttempt).count(), 1)
            self.assertEqual(db.query(database.QuestionLearningStat).one().attempt_count, 1)

    def test_controlled_practice_claim_allows_only_one_concurrent_grading_run(self):
        with TemporaryDirectory() as directory:
            engine = create_engine(
                f"sqlite:///{directory}/controlled-practice.db",
                connect_args={"check_same_thread": False, "timeout": 0.1},
            )
            database.Base.metadata.create_all(bind=engine)
            Session = sessionmaker(bind=engine)
            with Session() as db:
                db.add_all([
                    database.UserModel(id=1, username="concurrent", email="concurrent@example.com", hashed_password="x"),
                    database.KnowledgePoint(kp_id="KP_CONCURRENT", name="并发练习", status="active"),
                    database.QuestionBankItem(
                        question_id="question-concurrent",
                        stem="并发提交应如何处理？",
                        answer="只批改一次",
                        analysis="同一凭证只能消费一次。",
                        kp_ids_json='["KP_CONCURRENT"]',
                        question_type="short_answer",
                        difficulty=2,
                        status="active",
                    ),
                ])
                controlled = training_routes.resolve_controlled_practice_submission(
                    db,
                    {"question_id": "question-concurrent"},
                )
                self.assertIsNotNone(controlled)
                db.add(database.CorePracticeSubmissionClaim(
                    user_id=1,
                    request_id="00000000-0000-0000-0000-000000000001",
                    question_id="question-concurrent",
                ))
                db.commit()

            runner_entered = Event()
            release_runner = Event()
            runner_calls = []

            def blocking_runner(**_kwargs):
                runner_calls.append("00000000-0000-0000-0000-000000000001")
                runner_entered.set()
                self.assertTrue(release_runner.wait(timeout=5))
                return {
                    "grading": {
                        "score": 100,
                        "is_correct": True,
                        "error_type": "",
                        "analysis": "回答正确。",
                        "standard_answer": "只批改一次",
                    },
                }

            request = training_routes.PracticeGradeRequest(
                question_id="question-concurrent",
                stem="伪造题干不应生效",
                student_answer="只批改一次",
                request_id="00000000-0000-0000-0000-000000000001",
            )
            user = database.UserModel(id=1, username="concurrent", email="concurrent@example.com", hashed_password="x")

            def submit():
                with Session() as db:
                    try:
                        training_routes.grade_practice(request, current_user=user, db=db)
                        return 200
                    except HTTPException as exc:
                        return exc.status_code

            with patch.object(training_routes, "practice_grading_runner", side_effect=blocking_runner):
                with ThreadPoolExecutor(max_workers=2) as executor:
                    first = executor.submit(submit)
                    if not runner_entered.wait(timeout=5):
                        first.result(timeout=1)
                        self.fail("first grading request did not reach the runner")
                    second = executor.submit(submit)
                    second_status = second.result(timeout=10)
                    release_runner.set()
                    first_status = first.result(timeout=10)

            self.assertEqual(sorted([first_status, second_status]), [200, 409])
            self.assertEqual(runner_calls, ["00000000-0000-0000-0000-000000000001"])
            with Session() as db:
                self.assertEqual(db.query(database.LearningQuestionAttempt).filter_by(
                    user_id=1,
                    request_id="00000000-0000-0000-0000-000000000001",
                ).count(), 1)
            engine.dispose()

    def test_legacy_practice_grade_preserves_safe_runner_presentation_fields_only(self):
        spoofed = {
            "artifact_id": "spoofed-artifact",
            "attempt_item_id": "spoofed-item",
            "source_artifact_id": "spoofed-source-artifact",
            "source_artifact_version": 999,
            "audit_id": "spoofed-audit",
        }
        runner_payload = {
            "grading": {
                "question_id": "runner-owned-question",
                "score": 40,
                "is_correct": False,
                "error_type": "证型-方剂匹配错误",
                "analysis": "需要复盘辨证与方剂对应关系。",
                "standard_answer": "脾胃气虚证",
                **spoofed,
            },
            "mistake_record": {
                "error_type": "证型-方剂匹配错误",
                "content": "混淆了中焦虚寒证与脾胃气虚证。",
                **spoofed,
            },
            "remediation": {"review_card": {"title": "复习四君子汤"}, **spoofed},
            "agent_trace": [{"agent": "grader", "status": "completed", **spoofed}],
            **spoofed,
            "audit": {"decision": "pass", **spoofed},
        }
        with patch.object(training_routes, "practice_grading_runner", return_value=runner_payload):
            response = self.client.post(
                "/training/practice/grade",
                json={
                    "question_id": "question-safe-presentation",
                    "stem": "四君子汤主治什么证型？",
                    "student_answer": "中焦虚寒证",
                    "standard_answer": "脾胃气虚证",
                    "knowledge_points": ["KP_ROUTE_001"],
                },
            )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["remediation"], runner_payload["remediation"])
        self.assertEqual(payload["agent_trace"], runner_payload["agent_trace"])
        self.assertEqual(payload["mistake_record"], runner_payload["mistake_record"])
        self.assertTrue(payload["attempt_id"])
        self.assertTrue(payload["attempt_item_id"])
        self.assertTrue(payload["grading_artifact_id"])
        self.assertTrue(payload["audit_id"])

        with self.Session() as db:
            persisted_payloads = (
                json.loads(db.query(database.GradingResultRecord).one().payload_json),
                json.loads(db.query(database.AuditResultRecord).one().payload_json),
            )
            for persisted in persisted_payloads:
                serialized = json.dumps(persisted)
                for value in spoofed.values():
                    self.assertNotIn(str(value), serialized)

    def test_legacy_practice_grade_runner_failure_retains_authoritative_attempt_without_b_facts(self):
        with patch.object(training_routes, "practice_grading_runner", side_effect=RuntimeError("runner failed")):
            response = TestClient(self.app, raise_server_exceptions=False).post(
                "/training/practice/grade",
                json={
                    "question_id": "question-runner-failure",
                    "stem": "四君子汤主治什么证型？",
                    "student_answer": "脾胃气虚证",
                    "learner_id": 999,
                },
            )

        self.assertEqual(response.status_code, 500)
        with self.Session() as db:
            attempt = db.query(database.LearningAttemptRecord).filter_by(request_id="legacy-route:question-runner-failure").one()
            self.assertEqual(attempt.learner_id, 1)
            self.assertEqual(db.query(database.LearningAttemptItemRecord).filter_by(attempt_id=attempt.attempt_id).count(), 1)
            self.assertEqual(db.query(database.GradingResultRecord).count(), 0)
            self.assertEqual(db.query(database.AuditResultRecord).count(), 0)
            self.assertEqual(db.query(database.EvidencePackRecord).count(), 0)

    def test_practice_grade_wrong_answer_updates_diagnosis_report_inputs(self):
        response = self.client.post(
            "/training/practice/grade",
            json={
                "question_id": "demo-sijunzi-001",
                "question_type": "short_answer",
                "stem": "四君子汤主治的核心证型是什么？请简要说明。",
                "student_answer": "中焦虚寒证",
                "standard_answer": "脾胃气虚证",
                "rubric": "答出脾胃气虚证并能说明气虚、纳差、乏力等证据为满分。",
                "knowledge_points": ["四君子汤", "脾胃气虚证"],
                "difficulty": 2,
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()["grading"]["is_correct"])

        report = self.client.get("/training/report")
        self.assertEqual(report.status_code, 200)
        self.assertEqual(report.json()["mistake_summary"]["total_mistakes"], 1)
        self.assertLess(report.json()["diagnosis"]["confidence"], 1.0)

        with self.Session() as db:
            self.assertEqual(db.query(database.MistakeRecord).filter(database.MistakeRecord.status == "active").count(), 1)
            self.assertEqual(db.query(database.LearningAttemptRecord).count(), 1)
            self.assertEqual(db.query(database.LearningAttemptItemRecord).count(), 1)
            self.assertEqual(db.query(database.QuestionAttempt).count(), 0)
            self.assertEqual(db.query(database.LearningActivityRecord).filter(database.LearningActivityRecord.activity_type == "question_attempt").count(), 0)


if __name__ == "__main__":
    unittest.main()
