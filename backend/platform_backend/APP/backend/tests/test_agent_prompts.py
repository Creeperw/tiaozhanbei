import importlib
import unittest
import warnings
from unittest.mock import patch


class AgentPromptsTests(unittest.TestCase):
    def _exported_prompt_names(self, prompts):
        return [name for name in prompts.__all__ if name.endswith("_PROMPT")]

    def test_all_exported_prompts_use_training_identity(self):
        prompts = importlib.import_module("APP.backend.agent_prompts")

        for name in self._exported_prompt_names(prompts):
            prompt = getattr(prompts, name)
            with self.subTest(prompt=name):
                self.assertTrue(prompt.strip())
                self.assertTrue(
                    "时珍智训" in prompt or "中医药人才个性化培养" in prompt,
                    msg=f"{name} should use the training assistant identity",
                )
                self.assertNotIn("你是健康管理助手", prompt)
                self.assertNotIn("你是健康管理系统", prompt)
                self.assertNotIn("司宁健康管理助手", prompt)

    def test_agent_registry_expert_types_have_specific_prompts(self):
        prompts = importlib.import_module("APP.backend.agent_prompts")
        registry = importlib.import_module("APP.backend.agent_registry")

        expected_prompt_by_agent = {
            "expert_handout": ("EXPERT_HANDOUT_PROMPT", "讲义"),
            "expert_knowledge_card": ("EXPERT_KNOWLEDGE_CARD_PROMPT", "知识卡"),
            "expert_paper": ("EXPERT_PAPER_PROMPT", "试题"),
            "expert_grading": ("EXPERT_GRADING_PROMPT", "批改"),
            "expert_case_training": ("EXPERT_CASE_TRAINING_PROMPT", "案例训练"),
        }

        for agent_name, (prompt_name, keyword) in expected_prompt_by_agent.items():
            with self.subTest(agent=agent_name):
                self.assertIn(agent_name, registry.AGENT_REGISTRY)
                self.assertIn(prompt_name, prompts.__all__)
                prompt = getattr(prompts, prompt_name)
                self.assertIn(keyword, prompt)
                self.assertEqual(registry.AGENT_REGISTRY[agent_name].system_prompt, prompt)

        self.assertIn("EXPERT_TYPE_PROMPTS", prompts.__all__)
        for agent_name, (prompt_name, _) in expected_prompt_by_agent.items():
            with self.subTest(expert_prompt_map=agent_name):
                self.assertEqual(
                    prompts.EXPERT_TYPE_PROMPTS[agent_name],
                    getattr(prompts, prompt_name),
                )

    def test_agent_registry_uses_centralized_system_prompts(self):
        prompts = importlib.import_module("APP.backend.agent_prompts")
        registry = importlib.import_module("APP.backend.agent_registry")

        expected_prompt_by_agent = {
            "memory_agent": prompts.MEMORY_PROMPT,
            "planner_agent": prompts.PLANNER_PROMPT,
            "knowledge_base_agent": prompts.KNOWLEDGE_PROMPT,
            "diagnosis_agent": prompts.DIAGNOSIS_PROMPT,
            "audit_agent": prompts.AUDIT_PROMPT,
        }

        for agent_name, prompt in expected_prompt_by_agent.items():
            with self.subTest(agent=agent_name):
                self.assertEqual(registry.AGENT_REGISTRY[agent_name].system_prompt, prompt)

    def test_expert_and_audit_prompts_keep_teaching_and_medical_safety_boundaries(self):
        prompts = importlib.import_module("APP.backend.agent_prompts")

        guarded_prompt_names = [
            "DIAGNOSIS_PROMPT",
            "EXPERT_PROMPT",
            "EXPERT_HANDOUT_PROMPT",
            "EXPERT_KNOWLEDGE_CARD_PROMPT",
            "EXPERT_PAPER_PROMPT",
            "EXPERT_GRADING_PROMPT",
            "EXPERT_CASE_TRAINING_PROMPT",
            "AUDIT_PROMPT",
        ]

        for name in guarded_prompt_names:
            prompt = getattr(prompts, name)
            with self.subTest(prompt=name):
                self.assertTrue("教学" in prompt or "培训" in prompt)
                self.assertIn("不能替代真实诊断", prompt)
                self.assertIn("高风险医学内容", prompt)
                self.assertTrue("需要审核" in prompt or "人工复核" in prompt)

    def test_memory_prompt_preserves_medical_safety_constraint_extraction(self):
        prompts = importlib.import_module("APP.backend.agent_prompts")

        prompt = prompts.MEMORY_PROMPT

        for keyword in ("过敏史", "正在用药", "基础疾病", "检查异常"):
            with self.subTest(keyword=keyword):
                self.assertIn(keyword, prompt)
        self.assertIn("只作为教学安全边界", prompt)

    def test_intent_reply_templates_use_training_assistant_identity(self):
        templates = importlib.import_module("APP.intent_reply_template")

        for intent, template in templates.INTENT_TEMPLATES.items():
            with self.subTest(intent=intent):
                self.assertIn("时珍智训", template)
                self.assertIn("中医药学习", template)
                self.assertNotIn("作为一名专业的养生助手", template)
                self.assertNotIn("健康管理解答", template)

    def test_streaming_workflow_attaches_global_plan_prompt_trace(self):
        with warnings.catch_warnings():
            warnings.filterwarnings("ignore", category=DeprecationWarning)
            warnings.filterwarnings("ignore", category=ResourceWarning)
            workflow = importlib.import_module("APP.backend.health_workflow")

            def fake_load_context(state):
                return {**state, "user_context": "无", "compressed_context": "无", "history_text": "无"}

            def fake_context_manager(state):
                return {**state, "extracted_memories": {}}

            with patch.object(workflow, "load_context", side_effect=fake_load_context), \
                 patch.object(workflow, "context_manager", side_effect=fake_context_manager), \
                 patch.object(workflow, "build_learner_context_brief") as build_context, \
                 patch.object(workflow.planner_client, "chat_message") as chat_message:
                from APP.backend.agent_contracts import LearnerContextBrief

                build_context.return_value = LearnerContextBrief(
                    learner_id="1",
                    learner_group="跨专业进阶群体",
                    goal="掌握四君子汤",
                    source_scope="test",
                    source_id="test",
                    confidence=0.8,
                )
                chat_message.return_value = {"content": '{"action":"planning_finish","intent":"其他","finish_reason":"done"}'}

                events = workflow.stream_health_workflow_events(
                    db=object(),
                    user_id=1,
                    session_id="s1",
                    user_question="生成四君子汤知识卡",
                    stop_before_execution=True,
                )

                for event, state in events:
                    if event["type"] == "planning_delta" and event["title"] == "全局执行计划":
                        plan = state["global_execution_plan"]
                        self.assertIn("agent_trace", plan)
                        self.assertIn("时珍智训", plan["agent_trace"][0]["system_prompt"])
                        break
                else:
                    self.fail("streaming workflow did not emit the global execution plan event")

    def test_health_prompts_reexport_agent_prompts_and_support_star_import(self):
        prompts = importlib.import_module("APP.backend.agent_prompts")
        health_prompts = importlib.import_module("APP.backend.health_prompts")

        self.assertEqual(health_prompts.__all__, prompts.__all__)

        namespace = {}
        exec("from APP.backend.health_prompts import *", namespace)

        for name in prompts.__all__:
            with self.subTest(reexport=name):
                self.assertIn(name, namespace)
                self.assertEqual(namespace[name], getattr(prompts, name))


if __name__ == "__main__":
    unittest.main()
