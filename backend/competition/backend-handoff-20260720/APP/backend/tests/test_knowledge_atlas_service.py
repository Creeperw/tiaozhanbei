import json
import tempfile
import time
import unittest
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from APP.backend.database import Base, QuestionBankItem


def write_atlas_fixture(root: Path, video_root: Path) -> None:
    (root / "01_question_bank").mkdir(parents=True)
    (root / "03_pipeline_chunks").mkdir(parents=True)
    (root / "04_knowledge_points" / "images").mkdir(parents=True)
    (root / "07_exam_bridge").mkdir(parents=True)
    (root / "04_knowledge_points" / "final_knowledge_points.json").write_text(
        json.dumps([
            {"kp": {
                "kp_id": "kp-reentry",
                "kp_lv1": "药理学",
                "kp_lv2": "第一节 心律失常的电生理学基础",
                "kp_lv3": "折返",
                "other_name": "re-entry",
                "order": "1",
                "raw_content": ["chunk-1"],
            }},
            {"kp": {
                "kp_id": "kp-question-only",
                "kp_lv1": "药理学",
                "kp_lv2": "第一节 心律失常的电生理学基础",
                "kp_lv3": "有效不应期",
                "raw_content": [],
            }},
            {"kp": {
                "kp_id": "kp-empty",
                "kp_lv1": "药理学",
                "kp_lv2": "第一节 心律失常的电生理学基础",
                "kp_lv3": "动作电位",
                "raw_content": [],
            }},
            {"kp": {
                "kp_id": "kp-video-only",
                "kp_lv1": "药理学",
                "kp_lv2": "第一节 心律失常的电生理学基础",
                "kp_lv3": "传导速度",
                "raw_content": [],
            }},
        ], ensure_ascii=False),
        encoding="utf-8",
    )
    (root / "01_question_bank" / "formatted_questions.json").write_text(
        json.dumps([
            {
                "question_id": "q-linked",
                "question_type": "单项选择题",
                "question_content": "折返形成需要什么条件？",
                "options": ["单向传导阻滞", "完全静止"],
                "answer": ["单向传导阻滞"],
                "explanation": "折返需要单向传导阻滞。",
                "kp_ids": ["kp-reentry"],
            },
            {
                "question_id": "q-question-only",
                "question_content": "有效不应期的意义是什么？",
                "options": [],
                "answer": "避免强直收缩",
                "kp_ids": ["kp-question-only"],
            },
            {
                "question_id": "q-pending",
                "question_content": "尚未关联的题目",
                "options": [],
                "answer": "待定",
                "kp_ids": [],
            },
        ], ensure_ascii=False),
        encoding="utf-8",
    )
    (root / "03_pipeline_chunks" / "source_chunks.jsonl").write_text(
        json.dumps({
            "chunk_uid": "chunk-1",
            "book": "药理学",
            "retrieval_text": "折返是心律失常的重要机制。",
            "retrieval_char_count": 15,
            "char_count": 12,
            "metadata": {"heading_path": "药理学 > 心律失常"},
        }, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    (root / "04_knowledge_points" / "images" / "reentry.png").write_bytes(b"PNG")
    video_root.mkdir(parents=True)
    (video_root / "catalog.json").write_text('{"version":1}', encoding="utf-8")
    result_root = video_root / "BVfixture"
    result_root.mkdir()
    (result_root / "classification_result.json").write_text(
        json.dumps({
            "bvid": "BVfixture",
            "video_title": "心律失常",
            "pages": [{
                "page": 1,
                "cid": 123,
                "original_part_title": "折返",
                "segments": [{
                    "start_seconds": 12,
                    "end_seconds": 24,
                    "topic": "折返机制",
                    "transcript": "折返机制讲解",
                    "kp_matches": [{"kp_id": "kp-reentry"}],
                }],
            }],
        }, ensure_ascii=False),
        encoding="utf-8",
    )
    second_result = video_root / "BVvideoonly"
    second_result.mkdir()
    (second_result / "classification_result.json").write_text(
        json.dumps({
            "bvid": "BVvideoonly",
            "pages": [{"page": 1, "segments": [{
                "start_seconds": 1,
                "end_seconds": 2,
                "kp_matches": [{"kp_id": "kp-video-only"}],
            }]}],
        }, ensure_ascii=False),
        encoding="utf-8",
    )


class FakeExamRepository:
    def get_membership(self, track_id, membership_id):
        if (track_id, membership_id) != ("track-a", "membership-a"):
            raise KeyError("unknown membership")
        return {
            "membership": {"node_id": "node-a"},
            "node": {"title_normalized": "折返"},
            "breadcrumb": [{"title": "药理学"}, {"title": "折返"}],
        }

    def get_catalog_subtree_knowledge_points(self, membership_id, accepted_only=True):
        return {"knowledge_points": [{"kp_id": "kp-reentry"}]}


class KnowledgeAtlasServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.data_root = root / "backend_delivery"
        self.video_root = root / "videos"
        write_atlas_fixture(self.data_root, self.video_root)

    def tearDown(self):
        self.temp.cleanup()

    def make_store(self):
        from APP.backend.knowledge_atlas_service import KnowledgeAtlasStore

        return KnowledgeAtlasStore(
            self.data_root,
            video_root=self.video_root,
            asset_version="2026-07-18",
        )

    def test_missing_assets_report_local_unavailability_without_raising(self):
        from APP.backend.knowledge_atlas_service import KnowledgeAtlasStore

        missing = KnowledgeAtlasStore(Path(self.temp.name) / "missing", video_root=self.video_root)
        status = missing.status()

        self.assertFalse(status["available"])
        self.assertFalse(status["warmed"])
        self.assertIn("final_knowledge_points.json", " ".join(status["errors"]))

    def test_active_import_staging_without_ready_receipt_keeps_atlas_unavailable(self):
        staging = self.data_root.parent / ".backend_delivery.importing-deadbeef"
        staging.mkdir()
        store = self.make_store()

        status = store.status()

        self.assertFalse(status["available"])
        self.assertIn("import is not ready", " ".join(status["errors"]))

        (self.data_root.parent / ".backend_delivery.ready.json").write_text(
            json.dumps({"component": "backend_delivery", "target": "backend_delivery"}),
            encoding="utf-8",
        )
        self.assertTrue(store.status()["available"])

    def test_routes_nodes_detail_and_all_resource_node_styles_match_handoff_contract(self):
        store = self.make_store()

        routes = store.routes()
        self.assertEqual([route["id"] for route in routes], ["textbook_14_5", "tcm_assistant", "postgraduate"])
        level_one = store.nodes(1, route_id="textbook_14_5")
        self.assertEqual(level_one["nodes"][0]["name"], "药理学")
        level_three = store.nodes(
            3,
            lv1="药理学",
            lv2="第一节 心律失常的电生理学基础",
            route_id="textbook_14_5",
        )
        styles = {node["id"]: node["node_style"] for node in level_three["nodes"]}
        self.assertEqual(styles["kp-reentry"], "solid")
        self.assertEqual(styles["kp-question-only"], "ring")
        self.assertEqual(styles["kp-video-only"], "video")
        self.assertEqual(styles["kp-empty"], "dashed")

        detail = store.detail("kp-reentry", question_limit=1)
        self.assertEqual(detail["kp"]["lv3"], "折返")
        self.assertEqual(detail["chunks"][0]["uid"], "chunk-1")
        self.assertEqual(detail["questions"][0]["question_id"], "q-linked")
        self.assertEqual(detail["questions"][0]["stem"], "折返形成需要什么条件？")
        self.assertEqual(detail["videos"][0]["bvid"], "BVfixture")

    def test_video_catalog_signature_causes_hot_reload(self):
        store = self.make_store()
        self.assertEqual(store.detail("kp-reentry")["videos"][0]["start_seconds"], 12)

        result_path = self.video_root / "BVfixture" / "classification_result.json"
        payload = json.loads(result_path.read_text(encoding="utf-8"))
        payload["pages"][0]["segments"][0]["start_seconds"] = 36
        result_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        time.sleep(0.001)
        (self.video_root / "catalog.json").write_text('{"version":2,"changed":true}', encoding="utf-8")

        self.assertEqual(store.detail("kp-reentry")["videos"][0]["start_seconds"], 36)

    def test_context_resolver_prefers_exam_knowledge_point_match(self):
        store = self.make_store()

        result = store.resolve_context(
            track_id="track-a",
            membership_id="membership-a",
            exam_repository=FakeExamRepository(),
        )

        self.assertTrue(result["resolved"])
        self.assertEqual(result["match_level"], "kp")
        self.assertEqual(result["route"], "textbook_14_5")
        self.assertEqual(result["kp_id"], "kp-reentry")
        self.assertEqual(result["lv1"], "药理学")

    def test_context_resolver_treats_track_only_navigation_as_route_level_success(self):
        store = self.make_store()

        result = store.resolve_context(
            track_id="EXAM_2025_TCM_PHYSICIAN",
            membership_id="",
            exam_repository=None,
        )

        self.assertTrue(result["resolved"])
        self.assertEqual(result["match_level"], "track")
        self.assertNotIn("notice", result)

    def test_context_resolver_never_returns_a_book_outside_the_selected_route(self):
        store = self.make_store()
        store.ensure_hierarchy()
        store.kps["kp-off-route"] = {
            "kp_id": "kp-off-route",
            "kp_lv1": "中医学基础",
            "kp_lv2": "绪论",
            "kp_lv3": "整体观念",
        }
        store.tree["中医学基础"] = {"绪论": [store.kps["kp-off-route"]]}

        class OffRouteRepository:
            def get_membership(self, track_id, membership_id):
                del track_id, membership_id
                return {"node": {"title_normalized": "整体观念"}, "breadcrumb": []}

            def get_catalog_subtree_knowledge_points(self, membership_id, accepted_only=True):
                del membership_id, accepted_only
                return {"knowledge_points": [{"kp_id": "kp-off-route"}]}

        result = store.resolve_context(
            track_id="INTEGRATED_ASSISTANT",
            membership_id="membership-off-route",
            exam_repository=OffRouteRepository(),
        )

        self.assertEqual(result["lv1"], "中医学基础")
        self.assertIn(result["lv1"], store.route_books(result["route"]))
        self.assertEqual(result["route"], "textbook_14_5")

    def test_question_search_returns_full_question_contract(self):
        store = self.make_store()

        results = store.search_questions("单向传导阻滞", kp_ids=["kp-reentry"], limit=5)

        self.assertEqual(len(results), 1)
        question = results[0]
        self.assertEqual(question["question_id"], "q-linked")
        self.assertEqual(question["stem"], "折返形成需要什么条件？")
        self.assertEqual(question["options"], ["单向传导阻滞", "完全静止"])
        self.assertEqual(question["answer"], ["单向传导阻滞"])
        self.assertEqual(question["explanation"], "折返需要单向传导阻滞。")
        self.assertEqual(question["kp_ids"], ["kp-reentry"])
        self.assertGreater(question["score"], 0)
        self.assertIn("atlas_question_bank", question["channels"])

    def test_questions_for_kps_uses_prebuilt_reverse_index_and_returns_full_contract(self):
        store = self.make_store()

        questions = store.questions_for_kps(["kp-reentry"], limit=5)

        self.assertEqual([item["question_id"] for item in questions], ["q-linked"])
        self.assertEqual(questions[0]["options"], ["单向传导阻滞", "完全静止"])
        self.assertEqual(questions[0]["explanation"], "折返需要单向传导阻滞。")
        self.assertEqual(questions[0]["channels"], ["atlas_question_bank", "kp_reverse_index"])

    def test_catalog_datasets_expose_atlas_resources_instead_of_markdown_file_count(self):
        store = self.make_store()
        store.warm()

        datasets = {item["id"]: item for item in store.catalog_datasets()}

        self.assertEqual(set(datasets), {
            "atlas_knowledge_points",
            "atlas_question_bank",
            "atlas_chunks",
            "atlas_images",
            "atlas_exam_bridge",
            "atlas_videos",
        })
        self.assertEqual(datasets["atlas_knowledge_points"]["count"], 4)
        self.assertEqual(datasets["atlas_question_bank"]["count"], 3)
        self.assertEqual(datasets["atlas_question_bank"]["linked_count"], 2)
        self.assertEqual(datasets["atlas_question_bank"]["pending_link_count"], 1)
        self.assertEqual(datasets["atlas_chunks"]["count"], 1)
        self.assertEqual(datasets["atlas_images"]["count"], 1)
        self.assertTrue(datasets["atlas_videos"]["available"])

    def test_question_reconciliation_is_dry_run_by_default_and_never_deletes_db_only_rows(self):
        store = self.make_store()
        engine = create_engine("sqlite://")
        Base.metadata.create_all(engine)
        Session = sessionmaker(bind=engine)
        with Session() as db:
            db.add_all([
                QuestionBankItem(
                    question_id="q-linked",
                    stem="old stem\nA. preserved option",
                    answer="legacy answer",
                    analysis="legacy analysis",
                    source="legacy-index",
                    kp_ids_json="[]",
                    status="pending_link",
                ),
                QuestionBankItem(question_id="q-pending", stem="pending", kp_ids_json="[]", status="pending_link"),
                QuestionBankItem(question_id="db-only", stem="seed", kp_ids_json='["seed-kp"]', status="active"),
            ])
            db.commit()
            report = store.reconcile_questions(db, apply=False)
            self.assertEqual(report["atlas_total"], 3)
            self.assertEqual(report["atlas_linked"], 2)
            self.assertEqual(report["atlas_pending_link"], 1)
            self.assertEqual(report["matched"], 2)
            self.assertEqual(report["matched_linked"], 1)
            self.assertEqual(report["matched_pending_link"], 1)
            self.assertEqual(report["db_only"], 1)
            self.assertEqual(report["db_only_by_status"], {"active": 1})
            self.assertFalse(report["applied"])
            self.assertEqual(db.query(QuestionBankItem).filter_by(question_id="q-linked").one().kp_ids_json, "[]")

            applied = store.reconcile_questions(db, apply=True)
            self.assertTrue(applied["applied"])
            self.assertEqual(json.loads(db.query(QuestionBankItem).filter_by(question_id="q-linked").one().kp_ids_json), ["kp-reentry"])
            linked = db.query(QuestionBankItem).filter_by(question_id="q-linked").one()
            self.assertEqual(linked.status, "active")
            self.assertEqual(linked.stem, "old stem\nA. preserved option")
            self.assertEqual(linked.answer, "legacy answer")
            self.assertEqual(linked.analysis, "legacy analysis")
            self.assertEqual(linked.source, "legacy-index")
            self.assertEqual(db.query(QuestionBankItem).filter_by(question_id="q-pending").one().status, "pending_link")
            self.assertIsNotNone(db.query(QuestionBankItem).filter_by(question_id="db-only").one_or_none())
        engine.dispose()


if __name__ == "__main__":
    unittest.main()
