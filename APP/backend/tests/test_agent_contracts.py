import importlib
import unittest

from pydantic import ValidationError

from APP.backend import deep_training_service, training_service


class AgentContractsTests(unittest.TestCase):
    def _contracts(self):
        try:
            return importlib.import_module("APP.backend.agent_contracts")
        except ModuleNotFoundError as exc:
            self.fail(f"agent_contracts module is missing: {exc}")

    def test_builds_learner_context_brief_with_reusable_metadata(self):
        contracts = self._contracts()

        payload = contracts.LearnerContextBrief(
            learner_id="learner-001",
            learner_group="跨专业进阶群体",
            goal="6 个月内完成方剂学补弱",
            source_scope="profile",
            source_id="profile-001",
            kp_ids=["KP_FJ_001", "KP_ZD_021"],
            risk_notes=["健康字段仍需通过 facade 映射为培训语义"],
            confidence=0.91,
        )

        self.assertEqual(payload.model_dump()["learner_group"], "跨专业进阶群体")
        self.assertEqual(payload.model_dump()["source_scope"], "profile")
        self.assertEqual(payload.model_dump()["kp_ids"], ["KP_FJ_001", "KP_ZD_021"])

    def test_builds_agent_execution_plan_and_evidence_pack(self):
        contracts = self._contracts()

        evidence_item = contracts.EvidenceItem(
            source_scope="knowledge_base",
            source_id="SRC_FJ_001",
            summary="四君子汤主治脾胃气虚证",
            kp_ids=["KP_FJ_001", "KP_ZD_021"],
            confidence=0.97,
        )
        evidence_pack = contracts.EvidencePack(
            source_scope="knowledge_base",
            source_id="PACK_FJ_001",
            items=[evidence_item],
            kp_ids=["KP_FJ_001", "KP_ZD_021"],
            confidence=0.97,
            risk_notes=["公共知识库证据已对齐"],
        )
        plan = contracts.AgentExecutionPlan(
            plan_id="plan-001",
            objective="完成错题复盘与诊断",
            assigned_agents=["diagnosis_agent", "audit_agent"],
            steps=["收集上下文", "交叉检验输出"],
            source_scope="training_flow",
            source_id="flow-001",
            kp_ids=["KP_FJ_001"],
            confidence=0.89,
            agent_trace=[{"agent": "planner_agent", "status": "success"}],
        )

        self.assertEqual(evidence_pack.model_dump()["items"][0]["summary"], "四君子汤主治脾胃气虚证")
        self.assertEqual(plan.model_dump()["assigned_agents"], ["diagnosis_agent", "audit_agent"])
        self.assertEqual(plan.model_dump()["agent_trace"][0]["agent"], "planner_agent")

    def test_builds_expert_artifact_and_review_decision(self):
        contracts = self._contracts()

        artifact = contracts.ExpertArtifact(
            artifact_type="grading_result",
            title="四君子汤批改结果",
            content={"score": 40, "error_type": "证型-方剂匹配错误"},
            source_scope="practice_grading",
            source_id="q_sijunzi_001",
            kp_ids=["KP_FJ_001", "KP_ZD_021"],
            risk_notes=["需要补充变式题复盘"],
            confidence=0.86,
            agent_trace=[{"agent": "expert_agent", "status": "success"}],
        )
        review = contracts.ReviewDecision(
            decision="revise",
            reviewer="audit_agent",
            reason="事实一致性或知识点覆盖不足",
            source_scope="cross_validation",
            source_id="review-001",
            kp_ids=["KP_FJ_001"],
            risk_notes=["需人工抽检 1 条证据"],
            confidence=0.84,
            agent_trace=[{"agent": "audit_agent", "status": "success"}],
        )

        self.assertEqual(artifact.model_dump()["artifact_type"], "grading_result")
        self.assertEqual(review.model_dump()["decision"], "revise")
        self.assertEqual(review.model_dump()["risk_notes"], ["需人工抽检 1 条证据"])

    def test_builds_diagnosis_report_with_context_and_evidence(self):
        contracts = self._contracts()

        evidence_pack = contracts.EvidencePack(
            source_scope="behavior_window",
            source_id="behavior-001",
            items=[
                contracts.EvidenceItem(
                    source_scope="behavior_window",
                    source_id="weekly-login",
                    summary="登录频率周环比下降 50%",
                    kp_ids=[],
                    confidence=0.93,
                )
            ],
            kp_ids=[],
            confidence=0.93,
        )
        report = contracts.DiagnosisReport(
            diagnosis_id="diag-001",
            stage_id="T2",
            stage_name="行为怠惰",
            summary="近期学习节奏明显下降",
            source_scope="diagnosis",
            source_id="diag-source-001",
            evidence_pack=evidence_pack,
            interventions=["reduce_daily_tasks_and_send_popup"],
            risk_notes=["需要结合最近错题量继续观察"],
            confidence=0.9,
            agent_trace=[{"agent": "diagnosis_agent", "status": "success"}],
        )

        self.assertEqual(report.model_dump()["stage_id"], "T2")
        self.assertEqual(report.model_dump()["evidence_pack"]["items"][0]["summary"], "登录频率周环比下降 50%")
        self.assertEqual(report.model_dump()["interventions"], ["reduce_daily_tasks_and_send_popup"])

    def test_agent_execution_plan_accepts_current_learning_plan_payload(self):
        contracts = self._contracts()
        payload = training_service.build_learning_plan_summary(
            profile={
                "constitution": "学历教育群体",
                "health_goals": "4 周内完成方剂学期末复习",
                "diet_restrictions": "每天 45 分钟",
                "exercise_preferences": "题目、案例辨证和知识热力图",
                "medical_history": "病机到治法的推理链薄弱",
            },
            memories=[
                {"category": "mistake", "title": "错题：四君子汤", "content": "错因：证型-方剂匹配错误；知识点：四君子汤、脾胃气虚证"},
                {"category": "preference", "title": "资源偏好", "content": "偏好案例辨证"},
            ],
            events=[{"agent_name": "diagnosis_agent", "output_summary": "建议先补证型与方剂匹配"}],
        )

        plan = contracts.AgentExecutionPlan(**payload)

        self.assertEqual(plan.model_dump()["plan_summary"]["goal"], "4 周内完成方剂学期末复习")
        self.assertEqual(plan.model_dump()["weekly_plan"]["acceptance"], "完成短练并达到 80% 正确率；错题需完成 1 次复盘。")
        self.assertTrue(any(task["type"] == "mistake_review" for task in plan.model_dump()["daily_tasks"]))
        self.assertEqual(plan.model_dump()["constraints"]["daily_available_minutes"], 45)
        self.assertEqual(plan.model_dump()["agent_trace"][0]["agent"], "diagnosis_agent")

    def test_review_decision_accepts_current_cross_validation_payload(self):
        contracts = self._contracts()
        payload = deep_training_service.cross_validate_output(
            generated={
                "claims": ["四君子汤主治脾胃气虚证", "四君子汤由人参、白术、茯苓、甘草组成"],
                "kp_ids": ["KP_FJ_001", "KP_ZD_021"],
                "difficulty": 2,
                "safety_risk": "low",
            },
            evidence={
                "source_ids": ["SRC_FJ_001"],
                "supported_claims": ["四君子汤主治脾胃气虚证", "四君子汤由人参、白术、茯苓、甘草组成"],
                "required_kp_ids": ["KP_FJ_001", "KP_ZD_021"],
                "expected_difficulty": 2,
            },
        )

        review = contracts.ReviewDecision(**payload)

        self.assertEqual(review.model_dump()["decision"], "pass")
        self.assertGreaterEqual(review.model_dump()["fact_consistency"], 0.95)
        self.assertGreaterEqual(review.model_dump()["knowledge_coverage"], 0.9)
        self.assertEqual(review.model_dump()["conflicts"], [])

    def test_diagnosis_report_accepts_current_learning_state_payload(self):
        contracts = self._contracts()
        payload = deep_training_service.diagnose_learning_state(
            l0_baseline={"daily_available_minutes": 45, "default_daily_tasks": 3, "preferred_time_slot": "20:00-21:00"},
            l3_behavior={"login_weekly_change": -0.5, "focus_time_change": -0.45, "task_completion_rate": 0.42, "retry_count": 1},
            mistakes=[],
        )

        report = contracts.DiagnosisReport(**payload)

        self.assertEqual(report.model_dump()["t_stage"]["stage_id"], "T2")
        self.assertEqual(report.model_dump()["l0_baseline"]["daily_available_minutes"], 45)
        self.assertEqual(report.model_dump()["l3_window"]["task_completion_rate"], 0.42)
        self.assertEqual(report.model_dump()["attribution"]["primary"], "节奏下降")

    def test_defaults_collection_fields_and_validates_confidence_range(self):
        contracts = self._contracts()

        artifact = contracts.ExpertArtifact(
            artifact_type="study_plan",
            title="最小专家产物",
            content={"goal": "完成今日学习"},
            source_scope="learning_plan",
            source_id="artifact-001",
            confidence=0.5,
        )

        self.assertEqual(artifact.model_dump()["kp_ids"], [])
        self.assertEqual(artifact.model_dump()["risk_notes"], [])
        self.assertEqual(artifact.model_dump()["agent_trace"], [])

        with self.assertRaises(ValidationError):
            contracts.EvidenceItem(
                source_scope="knowledge_base",
                source_id="SRC_BAD",
                summary="bad confidence",
                confidence=1.2,
            )

        with self.assertRaises(ValidationError):
            contracts.EvidenceItem(summary="missing source metadata")

    def test_plan_and_diagnosis_reject_empty_payloads(self):
        contracts = self._contracts()

        with self.assertRaises(ValidationError):
            contracts.AgentExecutionPlan()

        with self.assertRaises(ValidationError):
            contracts.AgentExecutionPlan(plan_summary={}, weekly_plan={}, daily_tasks=[{}])

        with self.assertRaises(ValidationError):
            contracts.AgentExecutionPlan(
                plan_summary={"goal": ""},
                weekly_plan={"acceptance": ""},
                constraints={"daily_available_minutes": ""},
                daily_tasks=[{"type": "", "title": ""}],
            )

        with self.assertRaises(ValidationError):
            contracts.DiagnosisReport()

        with self.assertRaises(ValidationError):
            contracts.DiagnosisReport(t_stage={}, l0_baseline={}, l3_window={}, attribution={})

        with self.assertRaises(ValidationError):
            contracts.DiagnosisReport(
                t_stage={"stage_id": "", "stage_name": ""},
                l0_baseline={"daily_available_minutes": ""},
                l3_window={"task_completion_rate": ""},
                attribution={"primary": ""},
            )


if __name__ == "__main__":
    unittest.main()
