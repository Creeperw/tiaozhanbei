import unittest
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from APP.backend import database
from APP.backend.agent_contracts import LearnerContextBrief


class KnowledgeAgentServiceTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        database.Base.metadata.create_all(bind=self.engine)
        self.Session = sessionmaker(bind=self.engine)

    def _brief(self):
        return LearnerContextBrief(
            learner_id="1",
            learner_group="跨专业进阶群体",
            goal="掌握脾胃气虚证与四君子汤辨析",
            source_scope="test",
            source_id="brief-1",
            kp_ids=["KP_FJ_001"],
            confidence=0.9,
        )

    def _seed(self, db):
        db.add(database.UserModel(id=1, username="learner", email="learner@example.com", hashed_password="x"))
        db.add_all(
            [
                database.KnowledgePoint(
                    kp_id="KP_FJ_001",
                    name="四君子汤",
                    aliases_json='["四君子", "四君子方"]',
                    description="补气健脾基础方",
                ),
                database.KnowledgePoint(
                    kp_id="KP_ZD_021",
                    name="脾胃气虚证",
                    aliases_json='["脾气虚", "中焦气虚"]',
                    description="中医证候辨析知识点",
                ),
                database.QuestionBankItem(
                    question_id="Q_FJ_001",
                    stem="四君子汤主治哪类证候？",
                    answer="脾胃气虚证",
                    analysis="围绕补气健脾的方证对应关系。",
                    kp_ids_json='["KP_FJ_001", "KP_ZD_021"]',
                    difficulty=2.0,
                    quality_score=0.9,
                ),
                database.TeachingResource(
                    resource_id="RES_FJ_001",
                    title="四君子汤知识卡",
                    resource_type="knowledge_card",
                    summary="用于复习方剂组成和主治。",
                    kp_ids_json='["KP_FJ_001"]',
                    quality_score=0.88,
                ),
                database.MistakeRecord(
                    user_id=1,
                    question_id="Q_FJ_001",
                    kp_ids_json='["KP_FJ_001"]',
                    error_type="证型-方剂匹配错误",
                    summary="将四君子汤误判为温里方。",
                ),
            ]
        )
        db.commit()

    def test_aligns_exact_terms_to_existing_knowledge_points(self):
        from APP.backend.knowledge_agent_service import align_knowledge_points

        db = self.Session()
        try:
            self._seed(db)

            result = align_knowledge_points(db, "请讲解四君子汤和脾胃气虚证")

            self.assertEqual(result["label_status"], "matched")
            self.assertEqual(result["resolved_kp_ids"], ["KP_FJ_001", "KP_ZD_021"])
            self.assertEqual(result["candidate_kp_ids"], [])
        finally:
            db.close()

    def test_creates_candidate_when_no_knowledge_point_matches(self):
        from APP.backend.knowledge_agent_service import align_knowledge_points

        db = self.Session()
        try:
            self._seed(db)

            result = align_knowledge_points(db, "请补充一个尚未建库的舌诊训练专题", user_id=1)

            self.assertEqual(result["label_status"], "pending_review")
            self.assertEqual(len(result["candidate_kp_ids"]), 1)
            candidate = db.query(database.CandidateKnowledgePoint).filter_by(candidate_id=result["candidate_kp_ids"][0]).one()
            self.assertEqual(candidate.status, "pending")
            self.assertEqual(candidate.created_by_user_id, 1)
        finally:
            db.close()

    def test_builds_source_layered_evidence_pack_with_personal_public_questions_and_resources(self):
        from APP.backend.knowledge_agent_service import build_evidence_pack

        def fake_rag_search(query, top_k=5, user_id=None, include_public=True, include_personal=True):
            return [
                {"scope": "public", "source": "public.md", "content": "四君子汤由人参、白术、茯苓、甘草组成。", "score": 0.81, "type": "text"},
                {"scope": "personal", "source": "personal.md", "content": "用户最近错在四君子汤与理中丸区分。", "score": 0.92, "type": "text"},
            ]

        db = self.Session()
        try:
            self._seed(db)

            pack = build_evidence_pack(db, query="四君子汤怎么复习", learner_context=self._brief(), rag_search=fake_rag_search)
            payload = pack.model_dump()

            self.assertEqual(payload["resolved_kp_ids"], ["KP_FJ_001"])
            self.assertEqual(payload["personal_evidence"][0]["source"], "personal.md")
            self.assertEqual(payload["public_evidence"][0]["source"], "public.md")
            self.assertEqual(payload["question_evidence"][0]["question_id"], "Q_FJ_001")
            self.assertEqual(payload["resource_evidence"][0]["resource_id"], "RES_FJ_001")
            self.assertEqual(payload["items"][0]["source_scope"], "personal")
            self.assertEqual(payload["kp_ids"], ["KP_FJ_001"])
        finally:
            db.close()

    def test_does_not_use_unrelated_profile_kp_ids_as_current_query_evidence(self):
        from APP.backend.knowledge_agent_service import build_evidence_pack

        db = self.Session()
        try:
            self._seed(db)
            db.add(
                database.KnowledgePoint(
                    kp_id="KP_UNRELATED",
                    name="经络腧穴",
                    aliases_json='["腧穴"]',
                    description="无关知识点",
                )
            )
            db.add(
                database.QuestionBankItem(
                    question_id="Q_UNRELATED",
                    stem="足三里定位在哪？",
                    answer="犊鼻下 3 寸",
                    kp_ids_json='["KP_UNRELATED"]',
                    difficulty=2.0,
                    quality_score=0.95,
                )
            )
            db.commit()
            brief = LearnerContextBrief(
                learner_id="1",
                learner_group="跨专业进阶群体",
                goal="掌握脾胃气虚证与四君子汤辨析",
                source_scope="test",
                source_id="brief-1",
                kp_ids=["KP_UNRELATED"],
                confidence=0.9,
            )

            pack = build_evidence_pack(db, query="四君子汤怎么复习", learner_context=brief, rag_search=lambda *args, **kwargs: [])
            question_ids = {item["question_id"] for item in pack.model_dump()["question_evidence"]}

            self.assertIn("Q_FJ_001", question_ids)
            self.assertNotIn("Q_UNRELATED", question_ids)
            self.assertEqual(pack.model_dump()["kp_ids"], ["KP_FJ_001", "KP_UNRELATED"])
        finally:
            db.close()

    def test_detects_conflicting_rag_evidence(self):
        from APP.backend.knowledge_agent_service import build_evidence_pack

        def conflicting_rag_search(query, top_k=5, user_id=None, include_public=True, include_personal=True):
            return [
                {"scope": "public", "source": "a.md", "content": "四君子汤主治脾胃气虚证。", "score": 0.9, "type": "text"},
                {"scope": "public", "source": "b.md", "content": "四君子汤不适用于脾胃气虚证。", "score": 0.88, "type": "text"},
            ]

        db = self.Session()
        try:
            self._seed(db)

            pack = build_evidence_pack(db, query="四君子汤主治脾胃气虚证吗", learner_context=self._brief(), rag_search=conflicting_rag_search)
            payload = pack.model_dump()

            self.assertEqual(payload["risk_notes"], ["存在可能冲突的知识库证据，需审核智能体复核"])
            self.assertEqual(payload["conflict_evidence"][0]["type"], "possible_negation_conflict")
        finally:
            db.close()

    def test_question_evidence_uses_atlas_reverse_index_without_scanning_orm_rows(self):
        from APP.backend import knowledge_agent_service

        atlas_question = {
            "question_id": "atlas-q",
            "stem": "折返形成的条件？",
            "options": [{"option_id": "A", "content": "单向传导阻滞"}],
            "answer": ["A"],
            "explanation": "折返需要单向传导阻滞。",
            "kp_ids": ["062438"],
            "difficulty": "",
            "score": 1.0,
            "channels": ["atlas_question_bank", "kp_reverse_index"],
        }
        with patch.object(
            knowledge_agent_service.atlas_service,
            "questions_for_kps",
            return_value=[atlas_question],
        ):
            evidence = knowledge_agent_service._question_evidence(
                object(), ["062438"], limit=5
            )

        self.assertEqual(evidence[0]["question_id"], "atlas-q")
        self.assertEqual(evidence[0]["options"], atlas_question["options"])
        self.assertEqual(evidence[0]["analysis"], atlas_question["explanation"])
        self.assertIn("kp_reverse_index", evidence[0]["channels"])

    def test_evidence_pack_merges_document_chunks_and_semantic_question_contracts(self):
        from APP.backend import knowledge_agent_service

        semantic_question = {
            "question_id": "Q_SEMANTIC",
            "stem": "四君子汤如何配伍？",
            "options": [],
            "answer": "人参、白术、茯苓、甘草",
            "explanation": "补气健脾配伍。",
            "kp_ids": ["KP_FJ_001"],
            "difficulty": 2.0,
            "score": 0.96,
            "channels": ["question_index_v2", "semantic_search"],
        }
        document_chunks = [
            {
                "scope": "public",
                "source": "full.md",
                "content": "四君子汤是补气健脾基础方。",
                "score": 0.88,
                "type": "text",
            }
        ]
        db = self.Session()
        try:
            self._seed(db)
            with patch.object(
                knowledge_agent_service.rag_service,
                "search",
                return_value=document_chunks,
            ), patch.object(
                knowledge_agent_service.question_index_search_service,
                "search",
                return_value=[semantic_question],
            ):
                payload = knowledge_agent_service.build_evidence_pack(
                    db,
                    query="四君子汤怎么复习",
                    learner_context=self._brief(),
                ).model_dump()

            self.assertEqual(payload["public_evidence"][0]["source"], "full.md")
            questions = {item["question_id"]: item for item in payload["question_evidence"]}
            self.assertIn("Q_SEMANTIC", questions)
            self.assertIn("Q_FJ_001", questions)
            self.assertEqual(questions["Q_SEMANTIC"]["analysis"], "补气健脾配伍。")
            self.assertIn("semantic_search", questions["Q_SEMANTIC"]["channels"])
        finally:
            db.close()

    def test_evidence_pack_explicitly_degrades_when_embedding_runtime_is_unavailable(self):
        from APP.backend import knowledge_agent_service
        from APP.backend.rag_core import RAGUnavailableError

        failure = RAGUnavailableError(
            state="disabled",
            message="embedding retrieval is disabled by EMBEDDING_MODE",
        )
        db = self.Session()
        try:
            self._seed(db)
            with patch.object(
                knowledge_agent_service.rag_service,
                "search",
                side_effect=failure,
            ), patch.object(
                knowledge_agent_service.question_index_search_service,
                "search",
                side_effect=failure,
            ):
                payload = knowledge_agent_service.build_evidence_pack(
                    db,
                    query="四君子汤怎么复习",
                    learner_context=self._brief(),
                ).model_dump()

            self.assertEqual(payload["public_evidence"], [])
            self.assertEqual(payload["personal_evidence"], [])
            self.assertEqual(payload["question_evidence"][0]["question_id"], "Q_FJ_001")
            self.assertTrue(any("document_rag=disabled" in note for note in payload["risk_notes"]))
            self.assertTrue(any("question_index=disabled" in note for note in payload["risk_notes"]))
            self.assertLess(payload["confidence"], 0.82)
        finally:
            db.close()


if __name__ == "__main__":
    unittest.main()
