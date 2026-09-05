import unittest

from APP.backend.agent_contracts import LearnerContextBrief


class PlannerAgentServiceTests(unittest.TestCase):
    def _context(self):
        return LearnerContextBrief(
            learner_id="1",
            learner_group="跨专业进阶群体",
            goal="掌握脾胃气虚证与四君子汤",
            source_scope="memory_agent",
            source_id="learner:1",
            confidence=0.8,
            profile={"learning_goal": "掌握脾胃气虚证与四君子汤"},
            learning_state={"weak_kp_ids": ["KP_FJ_001"]},
        )

    def test_generates_learning_path_plan(self):
        from APP.backend.planner_agent_service import generate_agent_execution_plan

        plan = generate_agent_execution_plan(
            learner_context=self._context(),
            user_request="帮我制定四君子汤学习路径",
            available_tools=["search_rag"],
        )

        self.assertEqual(plan.task_type, "learning_path_planning")
        self.assertEqual(plan.steps[0]["agent"], "memory_agent")
        self.assertTrue(any(step["agent"] == "diagnosis_agent" for step in plan.steps))
        self.assertEqual(plan.risk_level, "low")

    def test_generates_resource_generation_plan_with_cross_validation(self):
        from APP.backend.planner_agent_service import generate_agent_execution_plan

        plan = generate_agent_execution_plan(
            learner_context=self._context(),
            user_request="生成一张四君子汤知识卡和讲义",
            available_tools=["search_rag", "search_health_web"],
        )

        self.assertEqual(plan.task_type, "resource_generation")
        self.assertTrue(plan.need_cross_validation)
        self.assertTrue(any(step["agent"] == "knowledge_base_agent" for step in plan.steps))
        self.assertTrue(any(step["agent"] == "expert_handout" for step in plan.steps))
        self.assertTrue(any(step["agent"] == "expert_knowledge_card" for step in plan.steps))
        audit_step = next(step for step in plan.steps if step["id"] == "audit")
        self.assertIn("artifact_handout", audit_step["depends_on"])
        self.assertIn("artifact_knowledge_card", audit_step["depends_on"])
        self.assertEqual(plan.plan_summary["review_decision"]["decision"], "pass")

    def test_routes_resource_generation_to_matching_expert_type(self):
        from APP.backend.planner_agent_service import generate_agent_execution_plan

        cases = [
            ("生成一套四君子汤试卷", "expert_paper", "generate_paper"),
            ("生成一个脾胃气虚证案例训练", "expert_case_training", "generate_case_training"),
            ("生成四君子汤知识卡", "expert_knowledge_card", "generate_knowledge_card"),
        ]

        for request, expected_agent, expected_action in cases:
            with self.subTest(request=request):
                plan = generate_agent_execution_plan(
                    learner_context=self._context(),
                    user_request=request,
                    available_tools=["search_rag"],
                )
                artifact_step = next(step for step in plan.steps if step["agent"] == expected_agent)
                self.assertEqual(artifact_step["action"], expected_action)

    def test_generates_grading_remediation_plan(self):
        from APP.backend.planner_agent_service import generate_agent_execution_plan

        plan = generate_agent_execution_plan(
            learner_context=self._context(),
            user_request="批改我的四君子汤作业并给出补救练习",
            available_tools=["search_rag"],
        )

        self.assertEqual(plan.task_type, "grading_remediation")
        self.assertTrue(any(step["agent"] == "expert_grading" for step in plan.steps))
        self.assertTrue(plan.need_cross_validation)

    def test_generates_document_ingestion_plan(self):
        from APP.backend.planner_agent_service import generate_agent_execution_plan

        plan = generate_agent_execution_plan(
            learner_context=self._context(),
            user_request="上传 PDF 考纲后加入知识库",
            available_tools=["search_rag"],
        )

        self.assertEqual(plan.task_type, "document_ingestion")
        self.assertEqual(plan.steps[0]["agent"], "audit_agent")
        self.assertTrue(any(step["action"] == "extract_with_markitdown" for step in plan.steps))

    def test_uploading_paper_document_still_routes_to_ingestion(self):
        from APP.backend.planner_agent_service import generate_agent_execution_plan

        cases = [
            "上传一份试卷并加入知识库",
            "上传讲义并加入知识库",
            "上传作业答案 PDF 入库",
        ]

        for request in cases:
            with self.subTest(request=request):
                plan = generate_agent_execution_plan(
                    learner_context=self._context(),
                    user_request=request,
                    available_tools=["search_rag"],
                )

                self.assertEqual(plan.task_type, "document_ingestion")
                self.assertEqual(plan.steps[0]["agent"], "audit_agent")


if __name__ == "__main__":
    unittest.main()
