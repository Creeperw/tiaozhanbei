import importlib
import unittest

from APP.backend.agent_contracts import DiagnosisReport, EvidenceItem, EvidencePack, LearnerContextBrief


class ExpertAgentServiceTests(unittest.TestCase):
    def _service(self):
        try:
            return importlib.import_module("APP.backend.expert_agent_service")
        except ModuleNotFoundError as exc:
            self.fail(f"expert_agent_service module is missing: {exc}")

    def _learner_context(self):
        return LearnerContextBrief(
            learner_id="learner-007",
            learner_group="跨专业进阶群体",
            goal="掌握脾胃气虚证与四君子汤的辨证应用",
            source_scope="learner_profile",
            source_id="profile-007",
            kp_ids=["KP_ZH_001", "KP_FJ_001"],
            confidence=0.92,
            short_term_memory={"recent_mistake": "将四君子汤误判为中焦虚寒证"},
            learning_state={"target_difficulty": 3, "available_minutes": 35},
        )

    def _evidence_pack(self):
        items = [
            EvidenceItem(
                source_scope="knowledge_point",
                source_id="SRC_FJ_001",
                summary="四君子汤主治脾胃气虚证，治法为益气健脾。",
                kp_ids=["KP_ZH_001", "KP_FJ_001"],
                confidence=0.97,
            ),
            EvidenceItem(
                source_scope="teaching_resource",
                source_id="RES_COMPARE_001",
                summary="四君子汤与理中丸的核心区别在于前者补气健脾，后者温中祛寒。",
                kp_ids=["KP_ZH_001", "KP_FJ_001"],
                confidence=0.95,
            ),
        ]
        return EvidencePack(
            source_scope="knowledge_base_agent",
            source_id="PACK_SIJUNZI_001",
            items=items,
            kp_ids=["KP_ZH_001", "KP_FJ_001"],
            resolved_kp_ids=["KP_ZH_001", "KP_FJ_001"],
            resource_evidence=[
                {"resource_id": "RES_COMPARE_001", "title": "四君子汤对比卡", "resource_type": "knowledge_card"}
            ],
            confidence=0.96,
        )

    def _diagnosis_report(self):
        return DiagnosisReport(
            diagnosis_id="diag-007",
            stage_id="T4",
            stage_name="知识点未掌握",
            summary="学习者对脾胃气虚证与方剂匹配存在稳定性错误，需要短时高频复盘。",
            source_scope="diagnosis_agent",
            source_id="diag-source-007",
            kp_ids=["KP_ZH_001", "KP_FJ_001"],
            interventions=["对比复盘", "案例辨证短练"],
            confidence=0.88,
        )

    def test_generate_handout_returns_handout_artifact_with_learning_metadata(self):
        service = self._service()

        artifact = service.generate_handout(
            learner_context=self._learner_context(),
            evidence_pack=self._evidence_pack(),
            diagnosis_report=self._diagnosis_report(),
            request={
                "topic": "脾胃气虚证 + 四君子汤",
                "difficulty": 3,
                "expected_duration_min": 18,
            },
        )

        self.assertEqual(artifact.artifact_type, "handout")
        self.assertEqual(artifact.content["source_ids"], ["SRC_FJ_001", "RES_COMPARE_001"])
        self.assertEqual(artifact.content["kp_ids"], ["KP_ZH_001", "KP_FJ_001"])
        self.assertEqual(artifact.content["difficulty"], 3)
        self.assertEqual(artifact.content["expected_duration_min"], 18)
        self.assertTrue(artifact.content["sections"])
        self.assertEqual(artifact.content["sections"][0]["title"], "正式证据要点")
        self.assertEqual(artifact.content["remediation_suggestions"], ["以正式证据要点复习。"])
        self.assertEqual(artifact.content["schema_version"], "v1")
        self.assertTrue(artifact.content["claims"])
        self.assertEqual(artifact.content["review_decision"]["decision"], "pass")

    def test_generation_uses_only_formal_evidence_summaries_for_body_and_claims(self):
        service = self._service()
        evidence_pack = EvidencePack(
            source_scope="knowledge_base_agent",
            source_id="PACK_MIXED_SOURCES",
            items=[
                EvidenceItem(
                    source_scope="knowledge_point",
                    source_id="KP_FJ_001",
                    summary="四君子汤主治脾胃气虚证，核心治法是益气健脾。",
                    kp_ids=["KP_FJ_001"],
                    confidence=0.95,
                ),
                *[
                    EvidenceItem(
                        source_scope=source_scope,
                        source_id=f"{source_scope}:COMPARE",
                        summary="理中丸偏于温中祛寒，适用于中焦虚寒证。",
                        kp_ids=["KP_FJ_018"],
                        confidence=0.9,
                    )
                    for source_scope in ("personal", "mistake_record", "question_bank", "submission")
                ],
            ],
            kp_ids=["KP_FJ_001"],
            resolved_kp_ids=["KP_FJ_001"],
            confidence=0.95,
        )

        for generator in (service.generate_handout, service.generate_knowledge_card):
            with self.subTest(generator=generator.__name__):
                artifact = generator(
                    learner_context=self._learner_context(),
                    evidence_pack=evidence_pack,
                    diagnosis_report=self._diagnosis_report(),
                    request={"topic": "四君子汤", "difficulty": 2, "expected_duration_min": 15},
                )
                claims = artifact.content["claims"]
                body = "\n".join(
                    bullet
                    for section in artifact.content.get("sections", [])
                    for bullet in section.get("bullets", [])
                ) or artifact.content.get("back", "")

                self.assertEqual(body, "四君子汤主治脾胃气虚证，核心治法是益气健脾。")
                self.assertEqual(claims, [{
                    "text": "四君子汤主治脾胃气虚证，核心治法是益气健脾。",
                    "evidence_ids": ["KP_FJ_001"],
                }])
                self.assertNotIn("理中丸", body)
                self.assertNotIn("理中丸", "\n".join(claim["text"] for claim in claims))
                self.assertNotIn("理中丸", "\n".join(artifact.content["remediation_suggestions"]))
                self.assertEqual(artifact.content["review_decision"]["decision"], "pass")

    def test_generation_without_formal_evidence_is_rejected_by_audit(self):
        service = self._service()
        evidence_pack = EvidencePack(
            source_scope="knowledge_base_agent",
            source_id="PACK_UNTRUSTED_ONLY",
            items=[
                EvidenceItem(
                    source_scope="personal",
                    source_id="personal:claim",
                    summary="四君子汤主治脾胃气虚证，核心治法是益气健脾。",
                    kp_ids=["KP_FJ_001"],
                    confidence=0.9,
                )
            ],
            kp_ids=["KP_FJ_001"],
            resolved_kp_ids=["KP_FJ_001"],
            confidence=0.9,
        )

        for generator in (service.generate_handout, service.generate_knowledge_card):
            with self.subTest(generator=generator.__name__):
                artifact = generator(
                    learner_context=self._learner_context(),
                    evidence_pack=evidence_pack,
                    diagnosis_report=self._diagnosis_report(),
                    request={"topic": "四君子汤", "difficulty": 2, "expected_duration_min": 15},
                )

                self.assertEqual(artifact.content["claims"][0]["evidence_ids"], [])
                self.assertEqual(artifact.content["review_decision"]["decision"], "reject")
                self.assertTrue(any(
                    conflict.startswith("missing_evidence_ids:")
                    for conflict in artifact.content["review_decision"]["conflicts"]
                ))

    def test_generation_merges_public_chunks_for_one_source_into_one_claim(self):
        service = self._service()
        evidence_pack = EvidencePack(
            source_scope="knowledge_base_agent",
            source_id="PACK_PUBLIC_CHUNKS",
            items=[
                EvidenceItem(
                    source_scope="public",
                    source_id="public:fangji.md",
                    summary="第一段：四君子汤主治脾胃气虚证。",
                    kp_ids=["KP_FJ_001"],
                    confidence=0.9,
                ),
                EvidenceItem(
                    source_scope="public",
                    source_id="public:fangji.md",
                    summary="第一段：四君子汤主治脾胃气虚证。",
                    kp_ids=["KP_FJ_001"],
                    confidence=0.9,
                ),
                EvidenceItem(
                    source_scope="public",
                    source_id="public:fangji.md",
                    summary="第二段：治法为益气健脾。",
                    kp_ids=["KP_FJ_001"],
                    confidence=0.9,
                ),
            ],
            kp_ids=["KP_FJ_001"],
            resolved_kp_ids=["KP_FJ_001"],
            confidence=0.9,
        )
        expected_summary = "第一段：四君子汤主治脾胃气虚证。\n第二段：治法为益气健脾。"

        for generator in (service.generate_handout, service.generate_knowledge_card):
            with self.subTest(generator=generator.__name__):
                artifact = generator(
                    learner_context=self._learner_context(),
                    evidence_pack=evidence_pack,
                    diagnosis_report=self._diagnosis_report(),
                    request={"topic": "四君子汤", "difficulty": 2, "expected_duration_min": 15},
                )

                self.assertEqual(artifact.content["source_ids"], ["public:fangji.md"])
                self.assertEqual(artifact.content["claims"], [{
                    "text": expected_summary,
                    "evidence_ids": ["public:fangji.md"],
                }])
                self.assertEqual(artifact.content.get("back") or artifact.content["sections"][0]["bullets"][0], expected_summary)
                self.assertEqual(artifact.content["review_decision"]["decision"], "pass")

    def test_generation_excludes_cross_scope_source_id_collisions(self):
        service = self._service()
        items = [
            EvidenceItem(
                source_scope="public",
                source_id="shared:source",
                summary="公共来源结论。",
                kp_ids=["KP_CONFLICT"],
                confidence=0.9,
            ),
            EvidenceItem(
                source_scope="teaching_resource",
                source_id="shared:source",
                summary="教学资源中的不同结论。",
                kp_ids=["KP_CONFLICT"],
                confidence=0.9,
            ),
            EvidenceItem(
                source_scope="knowledge_point",
                source_id="KP_SAFE_001",
                summary="正式知识点结论。",
                kp_ids=["KP_SAFE_001"],
                confidence=0.9,
            ),
        ]
        evidence_pack = EvidencePack(
            source_scope="knowledge_base_agent",
            source_id="PACK_COLLIDING_SOURCES",
            items=items,
            kp_ids=["KP_SAFE_001"],
            resolved_kp_ids=["KP_SAFE_001"],
            confidence=0.9,
        )
        conflict_only_pack = evidence_pack.model_copy(update={"items": items[:2]})

        for generator in (service.generate_handout, service.generate_knowledge_card):
            with self.subTest(generator=generator.__name__, mode="with-safe-source"):
                artifact = generator(
                    learner_context=self._learner_context(),
                    evidence_pack=evidence_pack,
                    diagnosis_report=self._diagnosis_report(),
                    request={"topic": "四君子汤", "difficulty": 2, "expected_duration_min": 15},
                )
                self.assertEqual(artifact.content["source_ids"], ["KP_SAFE_001"])
                self.assertEqual(artifact.content["claims"], [{
                    "text": "正式知识点结论。",
                    "evidence_ids": ["KP_SAFE_001"],
                }])
                self.assertEqual(artifact.content["review_decision"]["decision"], "pass")

            with self.subTest(generator=generator.__name__, mode="conflict-only"):
                artifact = generator(
                    learner_context=self._learner_context(),
                    evidence_pack=conflict_only_pack,
                    diagnosis_report=self._diagnosis_report(),
                    request={"topic": "四君子汤", "difficulty": 2, "expected_duration_min": 15},
                )
                self.assertEqual(artifact.content["source_ids"], [])
                self.assertEqual(artifact.content["claims"][0]["evidence_ids"], [])
                self.assertEqual(artifact.content["review_decision"]["decision"], "reject")

    def test_generation_excludes_source_ids_shared_with_untrusted_scopes(self):
        service = self._service()
        for untrusted_scope in ("personal", "question_bank", "mistake_record", "submission"):
            with self.subTest(untrusted_scope=untrusted_scope):
                shared_items = [
                    EvidenceItem(
                        source_scope="public",
                        source_id="shared:public-source",
                        summary="公共来源结论。",
                        kp_ids=["KP_CONFLICT"],
                        confidence=0.9,
                    ),
                    EvidenceItem(
                        source_scope=untrusted_scope,
                        source_id="shared:public-source",
                        summary="非正式来源结论。",
                        kp_ids=["KP_CONFLICT"],
                        confidence=0.9,
                    ),
                ]
                conflict_only_pack = EvidencePack(
                    source_scope="knowledge_base_agent",
                    source_id=f"PACK_{untrusted_scope.upper()}_CONFLICT",
                    items=shared_items,
                    kp_ids=["KP_CONFLICT"],
                    resolved_kp_ids=["KP_CONFLICT"],
                    confidence=0.9,
                )
                safe_pack = conflict_only_pack.model_copy(update={"items": [
                    *shared_items,
                    EvidenceItem(
                        source_scope="knowledge_point",
                        source_id="KP_SAFE_001",
                        summary="独立正式知识点结论。",
                        kp_ids=["KP_SAFE_001"],
                        confidence=0.9,
                    ),
                ]})

                for generator in (service.generate_handout, service.generate_knowledge_card):
                    with self.subTest(generator=generator.__name__, mode="conflict-only"):
                        artifact = generator(
                            learner_context=self._learner_context(),
                            evidence_pack=conflict_only_pack,
                            diagnosis_report=self._diagnosis_report(),
                            request={"topic": "四君子汤", "difficulty": 2, "expected_duration_min": 15},
                        )
                        self.assertEqual(artifact.content["source_ids"], [])
                        self.assertEqual(artifact.content["claims"][0]["evidence_ids"], [])
                        self.assertEqual(artifact.content["review_decision"]["decision"], "reject")

                    with self.subTest(generator=generator.__name__, mode="with-safe-source"):
                        artifact = generator(
                            learner_context=self._learner_context(),
                            evidence_pack=safe_pack,
                            diagnosis_report=self._diagnosis_report(),
                            request={"topic": "四君子汤", "difficulty": 2, "expected_duration_min": 15},
                        )
                        self.assertEqual(artifact.content["source_ids"], ["KP_SAFE_001"])
                        self.assertEqual(artifact.content["claims"], [{
                            "text": "独立正式知识点结论。",
                            "evidence_ids": ["KP_SAFE_001"],
                        }])
                        self.assertEqual(artifact.content["review_decision"]["decision"], "pass")

    def test_generate_knowledge_card_returns_card_artifact_with_fixed_topic(self):
        service = self._service()

        artifact = service.generate_knowledge_card(
            learner_context=self._learner_context(),
            evidence_pack=self._evidence_pack(),
            diagnosis_report=self._diagnosis_report(),
            request={
                "topic": "脾胃气虚证 + 四君子汤",
                "difficulty": 2,
                "expected_duration_min": 8,
            },
        )

        self.assertEqual(artifact.artifact_type, "knowledge_card")
        self.assertEqual(artifact.content["difficulty"], 2)
        self.assertEqual(artifact.content["expected_duration_min"], 8)
        self.assertEqual(artifact.content["front"], "正式证据要点是什么？")
        self.assertIn("脾胃气虚证", artifact.content["back"])
        self.assertEqual(artifact.content["remediation_suggestions"], ["以正式证据要点复习。"])
        self.assertEqual(artifact.content["review_decision"]["decision"], "pass")

    def test_generate_paper_returns_a_blueprint_without_authoritative_questions(self):
        service = self._service()

        artifact = service.generate_paper(
            learner_context=self._learner_context(),
            evidence_pack=self._evidence_pack(),
            diagnosis_report=self._diagnosis_report(),
            request={
                "topic": "脾胃气虚证 + 四君子汤",
                "difficulty": 3,
                "expected_duration_min": 20,
                "question_count": 4,
                "kp_ids": ["KP_ZH_001", "KP_FJ_001"],
                "types": ["single_choice", "short_answer"],
                "distribution": {"single_choice": 3, "short_answer": 1},
            },
        )

        self.assertEqual(artifact.artifact_type, "paper")
        self.assertEqual(artifact.content["difficulty"], 3)
        self.assertEqual(artifact.content["expected_duration_min"], 20)
        self.assertNotIn("questions", artifact.content)
        self.assertNotIn("standard_answer", str(artifact.content))
        self.assertEqual(
            artifact.content["paper_blueprint"],
            {
                "question_count": 4,
                "kp_ids": ["KP_ZH_001", "KP_FJ_001"],
                "types": ["single_choice", "short_answer"],
                "distribution": {"single_choice": 3, "short_answer": 1},
                "difficulty": 3,
                "exclusion_criteria": ["不生成试题正文或标准答案", "仅使用已解析知识点"],
            },
        )
        self.assertEqual(artifact.content["review_decision"]["decision"], "pass")

    def test_generate_paper_defaults_to_evidence_resolved_knowledge_points(self):
        service = self._service()
        evidence_pack = self._evidence_pack().model_copy(update={"resolved_kp_ids": ["KP_FJ_001"]})

        artifact = service.generate_paper(
            learner_context=self._learner_context(),
            evidence_pack=evidence_pack,
            diagnosis_report=self._diagnosis_report(),
            request={
                "topic": "四君子汤",
                "question_count": 1,
                "types": ["short_answer"],
                "distribution": {"short_answer": 1},
            },
        )

        self.assertEqual(artifact.content["paper_blueprint"]["kp_ids"], ["KP_FJ_001"])

    def test_generate_paper_rejects_invalid_blueprint_constraints(self):
        service = self._service()
        base_request = {"topic": "四君子汤", "kp_ids": ["KP_FJ_001"]}

        for update in (
            {"question_count": 0},
            {"question_count": 51},
            {"kp_ids": [" "]},
            {"types": ["unsupported"]},
            {"types": ["single_choice"], "distribution": {"short_answer": 1}},
            {"types": ["single_choice"], "distribution": {"single_choice": 2}, "question_count": 1},
        ):
            with self.subTest(update=update):
                with self.assertRaises(ValueError):
                    service.generate_paper(
                        learner_context=self._learner_context(),
                        evidence_pack=self._evidence_pack(),
                        diagnosis_report=self._diagnosis_report(),
                        request={**base_request, **update},
                    )

    def test_grade_submission_returns_grading_artifact_and_preserves_existing_behavior(self):
        service = self._service()

        artifact = service.grade_submission(
            learner_context=self._learner_context(),
            evidence_pack=self._evidence_pack(),
            diagnosis_report=self._diagnosis_report(),
            submission={
                "question_id": "q_sijunzi_001",
                "question_type": "single_choice",
                "stem": "四君子汤主治的核心证型是？",
                "student_answer": "中焦虚寒证",
                "standard_answer": "脾胃气虚证",
                "rubric": "答出脾胃气虚证得满分，混为中焦虚寒证需回看四君子汤与理中丸对比。",
                "knowledge_points": ["四君子汤", "脾胃气虚证"],
                "difficulty": 2,
            },
        )

        self.assertEqual(artifact.artifact_type, "grading")
        self.assertEqual(artifact.content["source_ids"], ["SRC_FJ_001", "RES_COMPARE_001"])
        self.assertEqual(artifact.content["kp_ids"], ["KP_ZH_001", "KP_FJ_001"])
        self.assertEqual(artifact.content["difficulty"], 2)
        self.assertEqual(artifact.content["expected_duration_min"], 12)
        self.assertEqual(artifact.content["grading"]["is_correct"], False)
        self.assertLess(artifact.content["grading"]["score"], 100)
        self.assertEqual(artifact.content["grading"]["error_type"], "证型-方剂匹配错误")
        self.assertEqual(artifact.content["mistake_record"]["source"], "practice_grading")
        self.assertIn("脾胃气虚证", artifact.content["remediation"]["review_card"]["content"])
        self.assertGreaterEqual(len(artifact.content["remediation"]["variant_questions"]), 2)
        self.assertNotIn("decision", artifact.content)
        self.assertEqual(artifact.content["review_decision"]["decision"], "pass")

    def test_generate_case_training_returns_case_training_artifact(self):
        service = self._service()

        artifact = service.generate_case_training(
            learner_context=self._learner_context(),
            evidence_pack=self._evidence_pack(),
            diagnosis_report=self._diagnosis_report(),
            request={
                "topic": "脾胃气虚证 + 四君子汤",
                "difficulty": 3,
                "expected_duration_min": 15,
            },
        )

        self.assertEqual(artifact.artifact_type, "case_training")
        self.assertEqual(artifact.content["difficulty"], 3)
        self.assertEqual(artifact.content["expected_duration_min"], 15)
        self.assertIn("脾胃气虚证", artifact.content["case_summary"])
        self.assertTrue(artifact.content["checkpoints"])
        self.assertIn("四君子汤", artifact.content["reference_answer"])
        self.assertIn("理中丸", "\n".join(artifact.content["remediation_suggestions"]))
        self.assertEqual(artifact.content["review_decision"]["decision"], "pass")


if __name__ == "__main__":
    unittest.main()
