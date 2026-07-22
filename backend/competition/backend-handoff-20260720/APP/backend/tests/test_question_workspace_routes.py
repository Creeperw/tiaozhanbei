import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from APP.backend import database
from APP.backend.auth import get_current_user
from APP.backend.database import get_db


STRUCTURED_MARKDOWN = """# 个人题集

## 题目 1
- 题型：简答题
- 题干：阴阳相互关系的基本内容是什么？
- 答案：对立制约、互根互用、消长平衡、相互转化。
- 解析：阴阳双方既对立又相互依存。
- 知识点：KP_YINYANG
"""


class FakeFaissModule:
    @staticmethod
    def normalize_L2(vectors):
        return vectors

    class IndexFlatIP:
        def __init__(self, dimension):
            self.d = dimension
            self.ntotal = 0

        def add(self, vectors):
            self.ntotal = len(vectors)

    @staticmethod
    def write_index(index, path):
        Path(path).write_bytes(f"{index.d}:{index.ntotal}".encode("ascii"))


class QuestionWorkspaceRoutesTests(unittest.TestCase):
    def setUp(self):
        from APP.backend.main import app
        from APP.backend.routers import question_workspace_routes

        self.app = app
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        database.Base.metadata.create_all(bind=self.engine)
        self.Session = sessionmaker(bind=self.engine)
        with self.Session() as db:
            db.add_all([
                database.UserModel(id=1, username="owner", email="owner@example.com", hashed_password="x"),
                database.UserModel(id=2, username="other", email="other@example.com", hashed_password="x"),
            ])
            db.commit()

        self.current_user_id = 1

        def override_db():
            with self.Session() as session:
                yield session

        def override_user():
            with self.Session() as db:
                return db.query(database.UserModel).filter_by(id=self.current_user_id).one()

        self.temp_dir = tempfile.TemporaryDirectory()
        self.upload_root_patch = patch.object(
            question_workspace_routes,
            "QUESTION_WORKSPACE_UPLOAD_ROOT",
            Path(self.temp_dir.name),
        )
        self.upload_root_patch.start()
        self.app.dependency_overrides[get_db] = override_db
        self.app.dependency_overrides[get_current_user] = override_user
        self.client = TestClient(self.app)

    def tearDown(self):
        self.app.dependency_overrides.clear()
        self.upload_root_patch.stop()
        self.engine.dispose()
        self.temp_dir.cleanup()

    def upload_markdown(self):
        return self.client.post(
            "/question-workspace/imports",
            files={"file": ("questions.md", STRUCTURED_MARKDOWN.encode("utf-8"), "text/markdown")},
        )

    def test_runtime_schema_creates_persistent_question_workspace_tables(self):
        with tempfile.TemporaryDirectory() as directory:
            engine = create_engine(f"sqlite:///{Path(directory) / 'runtime.db'}")
            database.ensure_runtime_schema_for(engine)
            tables = set(inspect(engine).get_table_names())
            self.assertTrue({
                "user_question_import_jobs",
                "user_question_items",
                "user_question_practice_claims",
            } <= tables)
            database.ensure_runtime_schema_for(engine)
            engine.dispose()

    def test_routes_are_registered_and_upload_creates_persistent_preview(self):
        paths = self.app.openapi()["paths"]
        self.assertIn("/question-workspace/imports", paths)
        self.assertIn("/question-workspace/imports/{job_id}", paths)
        self.assertIn("/question-workspace/items/{question_id}/confirm", paths)

        response = self.upload_markdown()

        self.assertEqual(response.status_code, 201)
        body = response.json()
        self.assertEqual(body["status"], "preview_ready")
        self.assertEqual(body["item_count"], 1)
        job_id = body["job_id"]
        with self.Session() as db:
            job = db.query(database.UserQuestionImportJob).filter_by(job_id=job_id).one()
            self.assertEqual(job.owner_user_id, 1)
            self.assertEqual(job.status, "preview_ready")
            item = db.query(database.UserQuestionItem).filter_by(job_id=job_id).one()
            self.assertEqual(item.owner_user_id, 1)
            self.assertEqual(item.status, "preview_ready")
            self.assertEqual(db.query(database.QuestionBankItem).count(), 0)

    def test_job_and_items_are_hidden_from_other_users(self):
        uploaded = self.upload_markdown()
        self.assertEqual(uploaded.status_code, 201)
        job_id = uploaded.json()["job_id"]

        self.current_user_id = 2
        self.assertEqual(
            self.client.get(f"/question-workspace/imports/{job_id}").status_code,
            404,
        )
        self.assertEqual(
            self.client.get(f"/question-workspace/imports/{job_id}/items").status_code,
            404,
        )

    def test_confirmation_activates_only_the_personal_question(self):
        uploaded = self.upload_markdown()
        question_id = uploaded.json()["items"][0]["question_id"]

        before = self.client.get("/question-workspace/questions")
        self.assertEqual(before.status_code, 200)
        self.assertEqual(before.json()["items"], [])

        confirmed = self.client.post(f"/question-workspace/items/{question_id}/confirm")

        self.assertEqual(confirmed.status_code, 200)
        self.assertEqual(confirmed.json()["status"], "active")
        active = self.client.get("/question-workspace/questions").json()["items"]
        self.assertEqual([item["question_id"] for item in active], [question_id])
        with self.Session() as db:
            self.assertEqual(db.query(database.QuestionBankItem).count(), 0)
            self.assertEqual(db.query(database.LearningQuestion).count(), 0)

        self.current_user_id = 2
        self.assertEqual(self.client.get("/question-workspace/questions").json()["items"], [])
        self.assertEqual(
            self.client.post(f"/question-workspace/items/{question_id}/confirm").status_code,
            404,
        )

    def test_duplicate_content_is_rejected_per_owner_without_public_side_effects(self):
        first = self.upload_markdown()
        second = self.upload_markdown()

        self.assertEqual(first.status_code, 201)
        self.assertEqual(second.status_code, 409)
        with self.Session() as db:
            self.assertEqual(db.query(database.UserQuestionImportJob).count(), 2)
            failed = db.query(database.UserQuestionImportJob).filter_by(status="failed").one()
            self.assertEqual(failed.error_message, "文件中包含已导入的重复题目")
            self.assertFalse(Path(failed.stored_path).exists())
            self.assertEqual(db.query(database.UserQuestionItem).count(), 1)
            self.assertEqual(db.query(database.QuestionBankItem).count(), 0)
            self.assertEqual(db.query(database.LearningQuestion).count(), 0)

    def test_import_history_is_owner_scoped_and_filterable_after_new_session(self):
        successful = self.upload_markdown()
        self.assertEqual(successful.status_code, 201)
        failed = self.client.post(
            "/question-workspace/imports",
            files={"file": ("broken.pdf", b"not a real pdf", "application/pdf")},
        )
        self.assertEqual(failed.status_code, 422)
        with self.Session() as db:
            db.add(database.UserQuestionImportJob(
                job_id="UQJ_OTHER",
                owner_user_id=2,
                original_filename="other.md",
                stored_path="private/other.md",
                content_type="text/markdown",
                file_size=10,
                status="preview_ready",
            ))
            db.commit()

        history = self.client.get("/question-workspace/imports")
        self.assertEqual(history.status_code, 200)
        self.assertEqual(history.json()["page"]["total"], 2)
        self.assertEqual(
            {item["status"] for item in history.json()["items"]},
            {"preview_ready", "failed"},
        )
        self.assertNotIn("stored_path", history.json()["items"][0])

        failed_only = self.client.get(
            "/question-workspace/imports",
            params={"status": "failed"},
        )
        self.assertEqual(failed_only.status_code, 200)
        self.assertEqual(failed_only.json()["page"]["total"], 1)
        self.assertEqual(failed_only.json()["items"][0]["error_message"], "PDF 解析失败")
        self.assertEqual(
            self.client.get(
                "/question-workspace/imports",
                params={"status": "unknown"},
            ).status_code,
            422,
        )

    def test_invalid_pdf_persists_failed_import_job_without_items(self):
        response = self.client.post(
            "/question-workspace/imports",
            files={"file": ("questions.pdf", b"not a real pdf", "application/pdf")},
        )

        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["detail"], "PDF 解析失败")
        with self.Session() as db:
            job = db.query(database.UserQuestionImportJob).one()
            self.assertEqual(job.status, "failed")
            self.assertEqual(job.owner_user_id, 1)
            self.assertNotIn(str(Path(self.temp_dir.name)), job.error_message)
            self.assertFalse(Path(job.stored_path).exists())
            self.assertEqual(db.query(database.UserQuestionItem).count(), 0)

    def test_empty_personal_index_rebuild_removes_stale_index_directory(self):
        from APP.backend import question_workspace_service

        index_root = Path(self.temp_dir.name) / "indexes"
        target_dir = index_root / "1" / "题库"
        target_dir.mkdir(parents=True)
        (target_dir / "metadata.jsonl").write_text("stale", encoding="utf-8")
        model = Mock()
        with self.Session() as db, patch(
            "APP.backend.rag_core.rag_service",
            SimpleNamespace(model=model),
        ):
            result = question_workspace_service.sync_personal_question_index(
                db,
                owner_user_id=1,
                index_root=index_root,
            )

        self.assertEqual(result["status"], "empty")
        self.assertFalse(target_dir.exists())
        model.encode.assert_not_called()

    def test_personal_index_rebuilds_are_serialized_per_owner(self):
        from APP.backend import question_workspace_service

        with self.Session() as db:
            db.add(database.UserQuestionImportJob(
                job_id="UQJ_CONCURRENT",
                owner_user_id=1,
                original_filename="concurrent.md",
                stored_path="concurrent.md",
                content_type="text/markdown",
                file_size=1,
                status="preview_ready",
            ))
            db.add(database.UserQuestionItem(
                question_id="UQ_CONCURRENT",
                job_id="UQJ_CONCURRENT",
                owner_user_id=1,
                question_type="简答题",
                stem="并发重建题目",
                answer="答案",
                analysis="",
                content_hash="concurrent-hash",
                status="active",
            ))
            db.commit()

        first_encode_started = threading.Event()
        release_first_encode = threading.Event()

        class BlockingModel:
            def __init__(self):
                self.calls = 0

            def encode(self, texts, *, convert_to_numpy):
                import numpy as np

                self.calls += 1
                if self.calls == 1:
                    first_encode_started.set()
                    release_first_encode.wait(timeout=2)
                return np.ones((len(texts), 2), dtype="float32")

        model = BlockingModel()
        index_root = Path(self.temp_dir.name) / "indexes"

        def rebuild():
            with self.Session() as db:
                return question_workspace_service.sync_personal_question_index(
                    db,
                    owner_user_id=1,
                    index_root=index_root,
                )

        with patch(
            "APP.backend.rag_core.rag_service",
            SimpleNamespace(model=model),
        ), patch.dict("sys.modules", {"faiss": FakeFaissModule()}), ThreadPoolExecutor(
            max_workers=2
        ) as executor:
            first = executor.submit(rebuild)
            self.assertTrue(first_encode_started.wait(timeout=2))
            with self.Session() as db:
                db.query(database.UserQuestionItem).filter_by(
                    question_id="UQ_CONCURRENT"
                ).update({"status": "inactive"})
                db.commit()
            second = executor.submit(rebuild)
            release_first_encode.set()
            first.result(timeout=3)
            self.assertEqual(second.result(timeout=3)["status"], "empty")

        self.assertFalse((index_root / "1" / "题库").exists())

    def test_upload_rejects_unsupported_file_type_and_client_paths(self):
        unsupported = self.client.post(
            "/question-workspace/imports",
            files={"file": ("questions.exe", b"not allowed", "application/octet-stream")},
        )
        self.assertEqual(unsupported.status_code, 415)

        path_like = self.client.post(
            "/question-workspace/imports",
            files={"file": ("../../questions.md", STRUCTURED_MARKDOWN.encode("utf-8"), "text/markdown")},
        )
        self.assertEqual(path_like.status_code, 400)

    def test_owner_can_revise_missing_answer_then_confirm_and_deactivate(self):
        markdown = """## 题目 1\n- 题型：简答题\n- 题干：请说明阴阳的含义。\n- 答案：\n"""
        uploaded = self.client.post(
            "/question-workspace/imports",
            files={"file": ("needs-review.txt", markdown.encode("utf-8"), "text/plain")},
        )
        question_id = uploaded.json()["items"][0]["question_id"]

        revised = self.client.patch(
            f"/question-workspace/items/{question_id}",
            json={"answer": "阴阳是对立统一的两个方面。", "explanation": "人工补充答案。"},
        )

        self.assertEqual(revised.status_code, 200)
        self.assertEqual(revised.json()["status"], "preview_ready")
        restored = self.client.get(
            f"/question-workspace/imports/{uploaded.json()['job_id']}/items"
        ).json()["items"][0]
        self.assertEqual(restored["explanation"], "人工补充答案。")
        self.assertNotIn("analysis", restored)
        self.assertEqual(
            self.client.post(f"/question-workspace/items/{question_id}/confirm").status_code,
            200,
        )
        deactivated = self.client.post(
            f"/question-workspace/questions/{question_id}/deactivate"
        )
        self.assertEqual(deactivated.status_code, 200)
        self.assertEqual(deactivated.json()["status"], "inactive")
        self.assertEqual(self.client.get("/question-workspace/questions").json()["items"], [])

    def test_deactivate_rebuilds_personal_index_without_rolling_back_status(self):
        uploaded = self.upload_markdown()
        question_id = uploaded.json()["items"][0]["question_id"]
        self.client.post(f"/question-workspace/items/{question_id}/confirm")

        from APP.backend.routers import question_workspace_routes
        with patch.object(
            question_workspace_routes,
            "question_index_sync",
            return_value={"ok": True, "status": "empty", "count": 0},
        ) as sync:
            deactivated = self.client.post(
                f"/question-workspace/questions/{question_id}/deactivate"
            )

        self.assertEqual(deactivated.status_code, 200)
        self.assertEqual(deactivated.json()["status"], "inactive")
        self.assertEqual(deactivated.json()["vector_index"]["count"], 0)
        sync.assert_called_once()

    def test_other_user_cannot_revise_reject_or_deactivate_an_owned_question(self):
        uploaded = self.upload_markdown()
        question_id = uploaded.json()["items"][0]["question_id"]
        self.current_user_id = 2

        self.assertEqual(self.client.patch(
            f"/question-workspace/items/{question_id}",
            json={"answer": "越权答案"},
        ).status_code, 404)
        self.assertEqual(self.client.post(
            f"/question-workspace/items/{question_id}/reject"
        ).status_code, 404)
        self.assertEqual(self.client.post(
            f"/question-workspace/questions/{question_id}/deactivate"
        ).status_code, 404)

    def test_active_personal_question_is_retrievable_only_by_owner_scope(self):
        uploaded = self.upload_markdown()
        question_id = uploaded.json()["items"][0]["question_id"]
        self.client.post(f"/question-workspace/items/{question_id}/confirm")

        owner_result = self.client.get(
            "/training/practice/next",
            params={"kp_id": "KP_YINYANG", "scope": "user"},
        )
        self.assertEqual(owner_result.status_code, 200)
        self.assertTrue(owner_result.json()["available"])
        self.assertEqual(owner_result.json()["question"]["question_id"], question_id)
        self.assertEqual(owner_result.json()["question"]["source_scope"], "user")
        self.assertNotIn("answer", owner_result.json()["question"])

        self.current_user_id = 2
        other_result = self.client.get(
            "/training/practice/next",
            params={"kp_id": "KP_YINYANG", "scope": "user"},
        )
        self.assertEqual(other_result.status_code, 200)
        self.assertFalse(other_result.json()["available"])

    def test_owner_can_grade_an_issued_personal_question_without_answer_leakage(self):
        uploaded = self.upload_markdown()
        question_id = uploaded.json()["items"][0]["question_id"]
        self.client.post(f"/question-workspace/items/{question_id}/confirm")
        issued = self.client.get(
            "/training/practice/next",
            params={"kp_id": "KP_YINYANG", "scope": "user"},
        ).json()["question"]
        runner_payload = {
            "grading": {
                "score": 100,
                "is_correct": True,
                "error_type": "",
                "analysis": "回答正确。",
                "standard_answer": "权威答案",
            },
        }

        from APP.backend.routers import training_routes
        with patch.object(training_routes, "practice_grading_runner", return_value=runner_payload) as runner:
            response = self.client.post(
                "/training/practice/grade",
                json={
                    "question_id": question_id,
                    "stem": "客户端伪造题干",
                    "student_answer": "对立制约、互根互用、消长平衡、相互转化。",
                    "standard_answer": "客户端伪造答案",
                    "request_id": issued["request_id"],
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertNotIn("standard_answer", response.json()["grading"])
        runner.assert_called_once()
        repeated = self.client.post(
            "/training/practice/grade",
            json={
                "question_id": question_id,
                "stem": "重放",
                "student_answer": "重放",
                "request_id": issued["request_id"],
            },
        )
        self.assertEqual(repeated.status_code, 409)

        with patch.object(training_routes, "practice_grading_runner") as runner:
            missing_request = self.client.post(
                "/training/practice/grade",
                json={
                    "question_id": question_id,
                    "stem": "绕过凭证",
                    "student_answer": "任意答案",
                },
            )
        self.assertEqual(missing_request.status_code, 400)
        self.assertEqual(
            missing_request.json()["detail"],
            "controlled practice requires an issued request_id",
        )
        runner.assert_not_called()

    def test_wrong_personal_question_is_recorded_in_unified_mistake_history(self):
        uploaded = self.upload_markdown()
        question_id = uploaded.json()["items"][0]["question_id"]
        self.client.post(f"/question-workspace/items/{question_id}/confirm")
        issued = self.client.get(
            "/v1/workshop/practice/next",
            params={"kp_id": "KP_YINYANG", "scope": "user", "mode": "case"},
        ).json()["question"]
        runner_payload = {
            "grading": {
                "score": 20,
                "is_correct": False,
                "error_type": "知识点遗漏",
                "analysis": "阴阳关系回答不完整。",
                "standard_answer": "权威答案",
            },
        }

        from APP.backend.routers import training_routes
        with patch.object(training_routes, "practice_grading_runner", return_value=runner_payload):
            graded = self.client.post(
                "/v1/workshop/practice/grade",
                json={
                    "question_id": question_id,
                    "stem": "客户端伪造题干",
                    "student_answer": "阴阳相反。",
                    "request_id": issued["request_id"],
                },
            )

        self.assertEqual(graded.status_code, 200)
        self.assertEqual(len(graded.json()["writeback"]["mistake_ids"]), 1)
        history = self.client.get("/v1/workshop/practice/mistakes", params={"status": "all"})
        self.assertEqual(history.status_code, 200)
        self.assertEqual(history.json()["total"], 1)
        mistake = history.json()["items"][0]
        self.assertEqual(mistake["question_id"], question_id)
        self.assertEqual(mistake["student_answer"], "阴阳相反。")
        self.assertEqual(mistake["error_type"], "知识点遗漏")
        self.assertFalse(mistake["variation_available"])
        with self.Session() as db:
            self.assertEqual(db.query(database.QuestionAttempt).count(), 1)
            self.assertEqual(db.query(database.MistakeRecord).count(), 1)

    def test_owner_can_rebuild_personal_index_and_other_user_cannot_target_it(self):
        uploaded = self.upload_markdown()
        question_id = uploaded.json()["items"][0]["question_id"]
        self.client.post(f"/question-workspace/items/{question_id}/confirm")

        from APP.backend.routers import question_workspace_routes
        with patch.object(
            question_workspace_routes,
            "question_index_sync",
            return_value={"ok": True, "status": "rebuilt", "count": 1},
        ) as sync:
            rebuilt = self.client.post("/question-workspace/index/rebuild")

        self.assertEqual(rebuilt.status_code, 200)
        self.assertTrue(rebuilt.json()["vector_index"]["ok"])
        sync.assert_called_once()
        self.assertEqual(sync.call_args.kwargs["owner_user_id"], 1)

        self.current_user_id = 2
        with patch.object(
            question_workspace_routes,
            "question_index_sync",
            return_value={"ok": True, "status": "empty", "count": 0},
        ) as other_sync:
            other = self.client.post("/question-workspace/index/rebuild")
        self.assertEqual(other.status_code, 200)
        self.assertEqual(other.json()["vector_index"]["count"], 0)
        self.assertEqual(other_sync.call_args.kwargs["owner_user_id"], 2)

    def test_vector_index_failure_does_not_rollback_confirmed_personal_question(self):
        from APP.backend.routers import question_workspace_routes

        uploaded = self.upload_markdown()
        question_id = uploaded.json()["items"][0]["question_id"]
        with patch.object(
            question_workspace_routes,
            "question_index_sync",
            side_effect=RuntimeError("faiss unavailable"),
        ):
            confirmed = self.client.post(
                f"/question-workspace/items/{question_id}/confirm"
            )

        self.assertEqual(confirmed.status_code, 200)
        self.assertEqual(confirmed.json()["status"], "active")
        self.assertFalse(confirmed.json()["vector_index"]["ok"])
        self.assertTrue(confirmed.json()["vector_index"]["rebuild_required"])
        with self.Session() as db:
            item = db.query(database.UserQuestionItem).filter_by(
                question_id=question_id,
                owner_user_id=1,
            ).one()
            self.assertEqual(item.status, "active")
            self.assertEqual(db.query(database.QuestionBankItem).count(), 0)

    def test_other_user_cannot_grade_an_owners_personal_question_request(self):
        uploaded = self.upload_markdown()
        question_id = uploaded.json()["items"][0]["question_id"]
        self.client.post(f"/question-workspace/items/{question_id}/confirm")
        issued = self.client.get(
            "/training/practice/next",
            params={"kp_id": "KP_YINYANG", "scope": "user"},
        ).json()["question"]
        self.current_user_id = 2

        response = self.client.post(
            "/training/practice/grade",
            json={
                "question_id": question_id,
                "stem": "越权",
                "student_answer": "越权",
                "request_id": issued["request_id"],
            },
        )

        self.assertEqual(response.status_code, 400)

    def test_choice_question_without_options_requires_human_review(self):
        markdown = """## 题目 1
- 题型：单项选择题
- 题干：阴阳学说中属于阴的是哪一项？
- 答案：B
- 解析：阴具有内守、静止等属性。
"""
        response = self.client.post(
            "/question-workspace/imports",
            files={"file": ("choice.md", markdown.encode("utf-8"), "text/markdown")},
        )

        self.assertEqual(response.status_code, 201)
        item = response.json()["items"][0]
        self.assertEqual(item["status"], "needs_human_review")
        self.assertEqual(item["review_reason"], "选择题缺少选项，需要人工修订")
        self.assertEqual(
            self.client.post(
                f"/question-workspace/items/{item['question_id']}/confirm"
            ).status_code,
            409,
        )

    def test_missing_answer_stays_in_human_review_until_revised(self):
        markdown = """## 题目 1\n- 题型：简答题\n- 题干：请说明阴阳的含义。\n- 答案：\n"""
        response = self.client.post(
            "/question-workspace/imports",
            files={"file": ("needs-review.txt", markdown.encode("utf-8"), "text/plain")},
        )

        self.assertEqual(response.status_code, 201)
        body = response.json()
        self.assertEqual(body["status"], "needs_human_review")
        question_id = body["items"][0]["question_id"]
        rejected_confirmation = self.client.post(
            f"/question-workspace/items/{question_id}/confirm"
        )
        self.assertEqual(rejected_confirmation.status_code, 409)


if __name__ == "__main__":
    unittest.main()
