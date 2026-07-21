import unittest

from APP.backend.tool_runtime import ToolDefinition, ToolRuntime, build_default_tool_runtime


class ToolRuntimeTests(unittest.TestCase):
    def test_execute_registered_tool_returns_structured_success(self):
        runtime = ToolRuntime()
        runtime.register(
            ToolDefinition(
                name="echo_context",
                allowed_agents=frozenset({"memory_agent"}),
                handler=lambda **kwargs: {"value": kwargs["value"]},
                summarize_input=lambda kwargs: f"value={kwargs.get('value')}",
                summarize_output=lambda result: f"keys={sorted(result)}",
            )
        )

        result = runtime.execute("echo_context", "memory_agent", value="四君子汤")

        self.assertEqual(result.status, "success")
        self.assertEqual(result.tool_name, "echo_context")
        self.assertEqual(result.agent_name, "memory_agent")
        self.assertEqual(result.result, {"value": "四君子汤"})
        self.assertEqual(result.input_summary, "value=四君子汤")
        self.assertEqual(result.output_summary, "keys=['value']")
        self.assertIsNone(result.error)

    def test_execute_rejects_unknown_tool(self):
        runtime = ToolRuntime()

        result = runtime.execute("missing_tool", "memory_agent")

        self.assertEqual(result.status, "failed")
        self.assertEqual(result.error, "unknown_tool:missing_tool")
        self.assertEqual(result.result, None)

    def test_execute_rejects_unauthorized_agent(self):
        runtime = ToolRuntime()
        runtime.register(
            ToolDefinition(
                name="build_evidence_pack",
                allowed_agents=frozenset({"knowledge_base_agent"}),
                handler=lambda **kwargs: {"ok": True},
            )
        )

        result = runtime.execute("build_evidence_pack", "expert_knowledge_card")

        self.assertEqual(result.status, "failed")
        self.assertEqual(result.error, "unauthorized_tool:build_evidence_pack:expert_knowledge_card")
        self.assertIsNone(result.result)

    def test_execute_shapes_handler_exceptions_without_leaking_type_details(self):
        runtime = ToolRuntime()

        def broken(**kwargs):
            raise RuntimeError("secret stack detail")

        runtime.register(
            ToolDefinition(
                name="broken_tool",
                allowed_agents=frozenset({"audit_agent"}),
                handler=broken,
            )
        )

        result = runtime.execute("broken_tool", "audit_agent")

        self.assertEqual(result.status, "failed")
        self.assertEqual(result.error, "tool_execution_failed:broken_tool")
        self.assertNotIn("secret stack detail", result.output_summary)

    def test_execute_shapes_input_summary_failures_without_leaking_details(self):
        runtime = ToolRuntime()

        def explode_input(kwargs):
            raise RuntimeError("secret input summary detail")

        runtime.register(
            ToolDefinition(
                name="summary_broken_tool",
                allowed_agents=frozenset({"memory_agent"}),
                handler=lambda **kwargs: {"ok": True},
                summarize_input=explode_input,
            )
        )

        result = runtime.execute("summary_broken_tool", "memory_agent", value="四君子汤")

        self.assertEqual(result.status, "failed")
        self.assertEqual(result.error, "tool_summary_failed:summary_broken_tool")
        self.assertNotIn("secret input summary detail", result.output_summary)

    def test_execute_shapes_output_summary_failures_without_leaking_details(self):
        runtime = ToolRuntime()

        def explode_output(result):
            raise RuntimeError("secret output summary detail")

        runtime.register(
            ToolDefinition(
                name="output_summary_broken_tool",
                allowed_agents=frozenset({"memory_agent"}),
                handler=lambda **kwargs: {"value": kwargs["value"]},
                summarize_output=explode_output,
            )
        )

        result = runtime.execute("output_summary_broken_tool", "memory_agent", value="四君子汤")

        self.assertEqual(result.status, "failed")
        self.assertEqual(result.error, "tool_summary_failed:output_summary_broken_tool")
        self.assertNotIn("secret output summary detail", result.output_summary)

    def test_patient_audit_tool_rejects_hidden_case_material(self):
        runtime = build_default_tool_runtime()

        result = runtime.execute(
            "audit_simulated_patient_reply",
            "patient_audit_agent",
            draft={"reply": "诊断为HIDDEN_GOLD_STANDARD"},
            learner_message="您哪里不舒服？",
            conversation=(),
            patient_context={"golden_standard": "HIDDEN_GOLD_STANDARD"},
        )

        self.assertEqual(result.status, "success")
        self.assertEqual(result.result["decision"], "reject")

    def test_default_runtime_contains_first_orchestration_tools(self):
        runtime = build_default_tool_runtime()
        tool_names = runtime.tool_names()

        self.assertIn("build_learner_context_brief", tool_names)
        self.assertIn("build_diagnosis_snapshot", tool_names)
        self.assertIn("build_evidence_pack", tool_names)
        self.assertIn("generate_handout", tool_names)
        self.assertIn("generate_knowledge_card", tool_names)
        self.assertIn("generate_paper", tool_names)
        self.assertIn("grade_submission", tool_names)
        self.assertIn("generate_case_training", tool_names)
        self.assertIn("generate_simulated_patient_reply", tool_names)
        self.assertIn("audit_simulated_patient_reply", tool_names)
        self.assertIn("generate_question_variation", tool_names)
        self.assertIn("audit_artifact", tool_names)


if __name__ == "__main__":
    unittest.main()
