import unittest


class AgentRuntimeTests(unittest.TestCase):
    def _valid_plan(self):
        from APP.backend.agent_contracts import AgentExecutionPlan

        return AgentExecutionPlan(
            task_type="resource_generation",
            need_cross_validation=True,
            risk_level="medium",
            objective="生成学习资源",
            steps=[
                {"id": "collect", "agent": "memory_agent", "action": "build_context", "tools": [], "depends_on": []},
                {"id": "retrieve", "agent": "knowledge_base_agent", "action": "build_evidence_pack", "tools": ["search_rag"], "depends_on": ["collect"]},
            ],
        )

    def test_validates_known_agents_tools_and_dependencies(self):
        from APP.backend.agent_runtime import validate_execution_plan

        result = validate_execution_plan(self._valid_plan(), available_tools=["search_rag"])

        self.assertEqual(result["status"], "valid")
        self.assertEqual(result["step_count"], 2)

    def test_rejects_unknown_agent(self):
        from APP.backend.agent_runtime import validate_execution_plan

        plan = self._valid_plan()
        plan.steps[0] = {**plan.steps[0], "agent": "unknown_agent"}

        with self.assertRaises(ValueError):
            validate_execution_plan(plan, available_tools=["search_rag"])

    def test_rejects_missing_dependency(self):
        from APP.backend.agent_runtime import validate_execution_plan

        plan = self._valid_plan()
        plan.steps[1] = {**plan.steps[1], "depends_on": ["missing"]}

        with self.assertRaises(ValueError):
            validate_execution_plan(plan, available_tools=["search_rag"])

    def test_rejects_unauthorized_tool(self):
        from APP.backend.agent_runtime import validate_execution_plan

        plan = self._valid_plan()
        plan.steps[1] = {**plan.steps[1], "tools": ["search_health_web"]}

        with self.assertRaises(ValueError):
            validate_execution_plan(plan, available_tools=["search_rag"])

    def test_builds_trace_without_executing_steps(self):
        from APP.backend.agent_runtime import build_runtime_trace

        trace = build_runtime_trace(self._valid_plan())

        self.assertEqual(trace[0]["agent"], "memory_agent")
        self.assertEqual(trace[0]["status"], "planned")
        self.assertIn("时珍智训", trace[0]["system_prompt"])
        self.assertEqual(trace[1]["depends_on"], ["collect"])
        self.assertIn("中医药人才个性化培养", trace[1]["system_prompt"])


if __name__ == "__main__":
    unittest.main()
