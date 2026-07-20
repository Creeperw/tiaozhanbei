import copy
import json
import unittest

from unittest.mock import Mock

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from APP.backend import database
from APP.backend.training_orchestration_adapter import (
    TrainingOrchestrationInput,
    _normalize_orchestration_payload,
    build_orchestration_request,
    execute_training_orchestration,
)


class TrainingOrchestrationAdapterTests(unittest.TestCase):
    def make_input(self, task_type="handout_generation"):
        return TrainingOrchestrationInput(
            task_id="TT_test_001",
            user_id=7,
            task_type=task_type,
            title="四君子汤训练资料",
            query="学习四君子汤",
            inputs={
                "kp_ids": ["KP_FJ_001"],
                "difficulty": 3,
                "duration_minutes": 20,
            },
            options={},
        )

    def test_builds_handout_request_without_query_concatenation(self):
        request = build_orchestration_request(self.make_input())

        self.assertEqual(request.query, "学习四君子汤")
        self.assertEqual(request.task_type, "handout_generation")
        self.assertEqual(request.requested_outputs, ["handout"])
        self.assertEqual(request.task_context.correlation_id, "TT_test_001")
        self.assertEqual(request.task_context.kp_ids, ["KP_FJ_001"])
        self.assertEqual(request.task_context.difficulty, 3)
        self.assertEqual(request.task_context.expected_duration_min, 20)

    def test_builds_knowledge_card_request(self):
        request = build_orchestration_request(self.make_input("knowledge_card_generation"))
        self.assertEqual(request.requested_outputs, ["knowledge_card"])

    def test_rejects_unsupported_task_type(self):
        with self.assertRaises(ValueError):
            build_orchestration_request(self.make_input("unsupported_generation"))

    def test_maps_successful_reviewed_artifact(self):
        def runner(**kwargs):
            self.assertEqual(kwargs["request"].requested_outputs, ["handout"])
            return {
                "run_id": "run_001",
                "status": "success",
                "task_type": "resource_generation",
                "execution_plan": {
                    "objective": "生成讲义",
                    "assigned_agents": ["memory_agent", "knowledge_base_agent", "expert_handout", "audit_agent"],
                    "status": "ready",
                },
                "steps": [
                    {
                        "step_id": "artifact_handout",
                        "agent_name": "expert_handout",
                        "action": "generate_handout",
                        "status": "success",
                        "output_summary": "handout generated",
                        "error": None,
                    }
                ],
                "final": {
                    "artifact": {
                        "artifact_type": "handout",
                        "title": "四君子汤讲义",
                        "content": {"sections": [{"title": "组成", "body": "人参、白术、茯苓、甘草"}]},
                        "source_id": "ART_1",
                    },
                    "artifacts": [],
                    "evidence_pack": {
                        "pack_id": "EP_1",
                        "source_scope": "public",
                        "source_id": "SRC_1",
                        "items": [{"source_id": "SRC_1", "summary": "教材证据"}],
                        "resolved_kp_ids": ["KP_FJ_001"],
                    },
                    "audit": {
                        "decision": "pass",
                        "reason": "审核通过",
                        "source_scope": "audit_agent",
                        "source_id": "ART_1",
                        "source_ids": ["ART_1"],
                    },
                },
            }

        result = execute_training_orchestration(
            db=object(),
            value=self.make_input(),
            runner=runner,
        )

        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["artifact"]["content"]["sections"][0]["title"], "组成")
        self.assertEqual(result["orchestration_run_id"], "run_001")
        self.assertEqual(result["trace"][0]["step_id"], "orchestration")
        self.assertEqual(result["trace"][0]["run_id"], "run_001")
        self.assertEqual(result["trace"][-1]["action"], "publication_gate")
        self.assertTrue(result["trace"][-1]["published"])

    def test_executes_completed_handout_with_safe_public_projection(self):
        artifact_content = {"sections": [{"title": "组成", "body": "人参、白术"}]}
        payload = {
            "run_id": "run_sensitive",
            "status": "success",
            "execution_plan": {
                "objective": "生成讲义",
                "status": "ready",
                "assigned_agents": ["memory_agent", "audit_agent"],
            },
            "steps": [],
            "final": {
                "artifact": {
                    "artifact_type": "handout",
                    "title": "四君子汤讲义",
                    "content": artifact_content,
                    "source_id": "ART_1",
                },
                "evidence_pack": {
                    "pack_id": "EP_1",
                    "source_scope": "public",
                    "source_id": "SRC_1",
                    "items": [
                        {
                            "source_id": "SRC_1",
                            "source_scope": "public",
                            "summary": "教材证据",
                        }
                    ],
                    "resolved_kp_ids": ["KP_FJ_001"],
                },
                "audit": {
                    "decision": "pass",
                    "reason": "审核通过",
                    "source_scope": "audit_agent",
                    "source_id": "ART_1",
                    "source_ids": ["ART_1"],
                },
            },
        }

        result = execute_training_orchestration(
            db=object(),
            value=self.make_input(),
            runner=lambda **kwargs: payload,
        )

        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["artifact"]["content"], artifact_content)
        self.assertEqual(result["evidence_pack"]["source_id"], "SRC_1")
        self.assertEqual(result["evidence_pack"]["items"][0]["summary"], "教材证据")
        self.assertEqual(result["trace"][0]["assigned_agents"], ["memory_agent", "audit_agent"])
        self.assertEqual(result["orchestration_run_id"], "run_sensitive")

    def successful_payload(self):
        return {
            "run_id": "run_sensitive",
            "status": "success",
            "execution_plan": {"objective": "生成讲义", "assigned_agents": ["expert_handout"]},
            "steps": [],
            "final": {
                "artifact": {
                    "artifact_type": "handout",
                    "title": "敏感草稿",
                    "content": {"body": "UNREVIEWED_EXPERT_BODY"},
                    "source_id": "ART_sensitive",
                },
                "evidence_pack": {
                    "pack_id": "EP_1",
                    "source_scope": "public",
                    "source_id": "SRC_1",
                    "items": [{"source_id": "SRC_1", "summary": "证据"}],
                    "resolved_kp_ids": ["KP_FJ_001"],
                },
                "audit": {
                    "decision": "pass",
                    "reason": "通过",
                    "source_id": "ART_sensitive",
                    "source_ids": ["ART_sensitive"],
                },
            },
        }

    def test_normalization_projects_only_safe_final_allowlists(self):
        marker = "UNREVIEWED_EXPERT_BODY"
        normalized = _normalize_orchestration_payload(
            {
                "status": "success",
                "final": {
                    "artifact": {
                        "artifact_type": "handout",
                        "title": {"raw": marker},
                        "content": {"body": "safe content"},
                        "source_id": {"raw": marker},
                        "raw": {"nested": marker},
                    },
                    "evidence_pack": {
                        "pack_id": "EP_1",
                        "source_scope": "public",
                        "source_id": "SRC_1",
                        "resolved_kp_ids": ["KP_FJ_001", {"raw": marker}],
                        "items": [{"source_id": "SRC_1", "source_scope": "public", "summary": "教材证据", "kp_ids": ["KP_FJ_001", {"raw": marker}], "confidence": 0.8, "raw": {"nested": marker}}],
                        "raw": {"nested": marker},
                    },
                    "audit": {
                        "decision": "pass",
                        "reason": {"raw": marker},
                        "source_scope": "audit_agent",
                        "source_id": "ART_1",
                        "source_ids": ["ART_1", {"raw": marker}],
                        "raw": {"nested": marker},
                    },
                    "claims": {"raw": marker},
                    "tool_parameters": {"raw": marker},
                },
            }
        )

        self.assertEqual(set(normalized["final"]), {"artifact", "evidence_pack", "audit"})
        self.assertEqual(normalized["final"]["artifact"]["title"], "")
        self.assertEqual(normalized["final"]["artifact"]["source_id"], "")
        self.assertEqual(
            normalized["final"]["evidence_pack"],
            {
                "pack_id": "EP_1",
                "source_scope": "public",
                "source_id": "SRC_1",
                "resolved_kp_ids": ["KP_FJ_001"],
                "items": [{"source_id": "SRC_1", "source_scope": "public", "summary": "教材证据", "kp_ids": ["KP_FJ_001"], "confidence": 0.8}],
            },
        )
        self.assertEqual(
            normalized["final"]["audit"],
            {
                "decision": "pass",
                "reason": "",
                "source_scope": "audit_agent",
                "source_id": "ART_1",
                "source_ids": ["ART_1"],
                "audit_id": "",
            },
        )
        self.assertNotIn(marker, json.dumps(normalized, ensure_ascii=False))

    def test_malicious_nested_final_values_fail_closed_without_crossing_projection_boundary(self):
        marker = "UNREVIEWED_EXPERT_BODY"
        cases = {
            "malformed_final": {"final": {"nested": marker}},
            "malformed_artifact": {
                "final": {"artifact": {"artifact_type": "handout", "title": {"raw": marker}, "content": marker}}
            },
            "malformed_evidence": {
                "final": {
                    "evidence_pack": {
                        "pack_id": {"raw": marker},
                        "source_scope": [marker],
                        "source_id": {"raw": marker},
                        "items": [{"source_id": {"raw": marker}, "summary": [marker]}],
                        "resolved_kp_ids": [{"raw": marker}],
                    }
                }
            },
            "malformed_audit": {
                "final": {
                    "audit": {
                        "decision": {"raw": marker},
                        "reason": [marker],
                        "source_scope": {"raw": marker},
                        "source_id": [marker],
                        "source_ids": [{"raw": marker}],
                    }
                }
            },
        }

        for name, mutation in cases.items():
            with self.subTest(name=name):
                payload = copy.deepcopy(self.successful_payload())
                payload.update(mutation)
                result = execute_training_orchestration(
                    db=object(),
                    value=self.make_input(),
                    runner=lambda **kwargs: payload,
                )

                self.assertNotEqual(result["status"], "completed")
                self.assertEqual(result["artifact"]["content"], {})
                self.assertEqual(
                    result["evidence_pack"],
                    {"pack_id": "", "source_scope": "", "source_id": "", "resolved_kp_ids": [], "items": []},
                )
                self.assertFalse(result["trace"][-1]["published"])
                self.assertNotIn(marker, json.dumps(result, ensure_ascii=False))

    def test_completed_result_only_exposes_evidence_and_audit_allowlists(self):
        marker = "UNREVIEWED_EXPERT_BODY"
        payload = copy.deepcopy(self.successful_payload())
        payload["final"]["artifact"]["content"] = {"body": "审核后的讲义正文"}
        payload["final"]["audit"]["source_scope"] = "audit_agent"
        payload["final"]["artifacts"] = [{"raw": marker}]
        payload["final"]["claims"] = {"raw": marker}
        payload["final"]["tool_parameters"] = {"raw": marker}
        payload["final"]["unknown"] = {"raw": marker}
        payload["final"]["evidence_pack"].update({"raw": {"nested": marker}, "items": [{"source_id": "SRC_1", "source_scope": "public", "summary": "教材证据", "kp_ids": ["KP_FJ_001"], "confidence": 0.8, "raw": {"nested": marker}}]})
        payload["final"]["audit"].update({"raw": {"nested": marker}, "reviewer": {"raw": marker}})

        result = execute_training_orchestration(
            db=object(),
            value=self.make_input(),
            runner=lambda **kwargs: payload,
        )

        self.assertEqual(result["status"], "completed")
        self.assertEqual(set(result["evidence_pack"]), {"pack_id", "source_scope", "source_id", "resolved_kp_ids", "items"})
        self.assertEqual(set(result["evidence_pack"]["items"][0]), {"source_id", "source_scope", "summary", "kp_ids", "confidence"})
        self.assertEqual(
            set(result["audit"]),
            {"decision", "source_scope", "source_id", "source_ids", "reason", "audit_id", "status"},
        )
        self.assertNotIn(marker, json.dumps(result, ensure_ascii=False))

    def test_unhashable_artifact_source_id_fails_closed_without_leaking_marker(self):
        payload = copy.deepcopy(self.successful_payload())
        payload["final"]["artifact"]["source_id"] = ["UNREVIEWED_EXPERT_BODY"]

        result = execute_training_orchestration(
            db=object(),
            value=self.make_input(),
            runner=lambda **kwargs: payload,
        )

        serialized = json.dumps(result, ensure_ascii=False)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["artifact"]["content"], {})
        self.assertEqual(result["evidence_pack"], {
            "pack_id": "",
            "source_scope": "",
            "source_id": "",
            "resolved_kp_ids": [],
            "items": [],
        })
        self.assertFalse(result["trace"][-1]["published"])
        self.assertNotIn("UNREVIEWED_EXPERT_BODY", serialized)

    def test_unpublished_audit_identity_fields_are_cleared(self):
        payload = copy.deepcopy(self.successful_payload())
        payload["status"] = "rejected"
        payload["final"]["audit"].update({
            "decision": "reject",
            "source_scope": "UNREVIEWED_SCOPE",
            "source_id": "UNREVIEWED_SOURCE",
            "source_ids": ["UNREVIEWED_SOURCE"],
            "audit_id": "UNREVIEWED_AUDIT",
        })

        result = execute_training_orchestration(
            db=object(),
            value=self.make_input(),
            runner=lambda **kwargs: payload,
        )

        self.assertEqual(set(result["audit"]), {"decision", "reason", "status"})
        self.assertNotIn("UNREVIEWED", json.dumps(result, ensure_ascii=False))

    def test_unpublished_evidence_identity_fields_are_cleared(self):
        for field in ("pack_id", "source_scope", "source_id", "resolved_kp_ids"):
            with self.subTest(field=field):
                payload = copy.deepcopy(self.successful_payload())
                payload["status"] = "rejected"
                payload["final"]["audit"]["decision"] = "reject"
                payload["final"]["evidence_pack"][field] = (
                    ["UNREVIEWED_EXPERT_BODY"]
                    if field == "resolved_kp_ids"
                    else "UNREVIEWED_EXPERT_BODY"
                )

                result = execute_training_orchestration(
                    db=object(),
                    value=self.make_input(),
                    runner=lambda **kwargs: payload,
                )

                self.assertEqual(result["status"], "failed")
                self.assertEqual(result["artifact"]["content"], {})
                self.assertNotIn("UNREVIEWED_EXPERT_BODY", json.dumps(result, ensure_ascii=False))
                self.assertEqual(
                    result["evidence_pack"],
                    {
                        "pack_id": "",
                        "source_scope": "",
                        "source_id": "",
                        "resolved_kp_ids": [],
                        "items": [],
                    },
                )
                self.assertFalse(result["trace"][-1]["published"])

    def test_rejects_empty_evidence_item_without_resolved_knowledge_point(self):
        payload = copy.deepcopy(self.successful_payload())
        payload["final"]["audit"]["source_scope"] = "audit_agent"
        payload["final"]["evidence_pack"] = {
            "pack_id": "EP_1",
            "source_scope": "public",
            "source_id": "SRC_1",
            "items": [{}],
            "resolved_kp_ids": [],
        }

        result = execute_training_orchestration(
            db=object(),
            value=self.make_input(),
            runner=lambda **kwargs: payload,
        )

        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["artifact"]["content"], {})
        self.assertNotIn("UNREVIEWED_EXPERT_BODY", json.dumps(result, ensure_ascii=False))
        self.assertFalse(result["trace"][-1]["published"])

    def test_completed_result_projects_safe_public_evidence_items(self):
        payload = copy.deepcopy(self.successful_payload())
        payload["final"]["artifact"]["content"] = {"body": "审核后的讲义正文"}
        payload["final"]["audit"]["source_scope"] = "audit_agent"
        payload["final"]["evidence_pack"]["items"] = [
            {
                "source_id": "SRC_1",
                "source_scope": "public",
                "summary": "教材证据",
                "kp_ids": ["KP_FJ_001"],
                "confidence": {"tool_parameters": {"raw_payload": "UNREVIEWED_EXPERT_BODY"}},
                "tool_parameters": {"secret": "UNREVIEWED_EXPERT_BODY"},
            }
        ]

        result = execute_training_orchestration(
            db=object(),
            value=self.make_input(),
            runner=lambda **kwargs: payload,
        )

        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["evidence_pack"]["pack_id"], "EP_1")
        self.assertEqual(result["evidence_pack"]["source_scope"], "public")
        self.assertEqual(result["evidence_pack"]["source_id"], "SRC_1")
        self.assertEqual(
            result["evidence_pack"]["items"],
            [
                {
                    "source_id": "SRC_1",
                    "source_scope": "public",
                    "summary": "教材证据",
                    "kp_ids": ["KP_FJ_001"],
                }
            ],
        )
        self.assertEqual(result["evidence_pack"]["resolved_kp_ids"], ["KP_FJ_001"])
        self.assertNotIn("confidence", json.dumps(result["evidence_pack"]["items"], ensure_ascii=False))
        self.assertNotIn("UNREVIEWED_EXPERT_BODY", json.dumps(result, ensure_ascii=False))

    def test_rejects_blank_evidence_identity_and_resolved_knowledge_point(self):
        payload = copy.deepcopy(self.successful_payload())
        payload["final"]["audit"]["source_scope"] = "audit_agent"
        payload["final"]["evidence_pack"] = {
            "pack_id": "EP_1",
            "source_scope": "public",
            "source_id": "SRC_1",
            "items": [{"source_id": " ", "source_scope": "public", "summary": "教材证据"}],
            "resolved_kp_ids": [" "],
        }

        result = execute_training_orchestration(
            db=object(),
            value=self.make_input(),
            runner=lambda **kwargs: payload,
        )

        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["artifact"]["content"], {})
        self.assertFalse(result["trace"][-1]["published"])

    def test_failure_cases_never_publish_expert_body(self):
        cases = {}

        rejected = self.successful_payload()
        rejected["status"] = "rejected"
        rejected["final"]["audit"]["decision"] = "reject"
        cases["reject"] = (rejected, "failed")

        review = self.successful_payload()
        review["status"] = "human_review"
        review["final"]["audit"]["decision"] = "needs_human_review"
        cases["human_review"] = (review, "needs_human_review")

        wrong_type = self.successful_payload()
        wrong_type["final"]["artifact"]["artifact_type"] = "knowledge_card"
        cases["wrong_type"] = (wrong_type, "failed")

        missing_evidence = self.successful_payload()
        missing_evidence["final"]["evidence_pack"] = {}
        cases["missing_evidence"] = (missing_evidence, "failed")

        missing_audit = self.successful_payload()
        missing_audit["final"]["audit"] = None
        cases["missing_audit"] = (missing_audit, "failed")

        unrelated_audit = self.successful_payload()
        unrelated_audit["final"]["audit"]["source_id"] = "OTHER_ART"
        unrelated_audit["final"]["audit"]["source_ids"] = ["OTHER_ART"]
        cases["unrelated_audit"] = (unrelated_audit, "failed")

        failed_step = self.successful_payload()
        failed_step["steps"] = [{"step_id": "evidence", "status": "failed", "error": "safe_code"}]
        cases["failed_step"] = (failed_step, "failed")

        missing_audit_source_scope = self.successful_payload()
        cases["missing_audit_source_scope"] = (missing_audit_source_scope, "failed")

        for name, (payload, expected_status) in cases.items():
            with self.subTest(name=name):
                artifact = payload["final"].get("artifact")
                if isinstance(artifact, dict):
                    artifact["content"] = {"body": "UNREVIEWED_EXPERT_BODY"}
                evidence = payload["final"].get("evidence_pack")
                if isinstance(evidence, dict):
                    evidence["items"] = [{"source_id": "SRC_1", "summary": "UNREVIEWED_EXPERT_BODY"}]
                audit = payload["final"].get("audit")
                if isinstance(audit, dict):
                    audit["reason"] = "UNREVIEWED_EXPERT_BODY"
                result = execute_training_orchestration(
                    db=object(),
                    value=self.make_input(),
                    runner=lambda **kwargs: payload,
                )
                serialized = json.dumps(result, ensure_ascii=False)
                self.assertEqual(result["status"], expected_status)
                self.assertEqual(result["artifact"]["artifact_type"], "handout")
                self.assertEqual(result["artifact"]["content"], {})
                self.assertNotIn("UNREVIEWED_EXPERT_BODY", serialized)
                self.assertFalse(result["trace"][-1]["published"])

    def test_trace_omits_runner_step_text(self):
        payload = self.successful_payload()
        payload["final"]["artifact"]["content"] = {"body": "审核后的内容"}
        payload["final"]["audit"]["source_scope"] = "audit_agent"
        payload["execution_plan"]["objective"] = "UNREVIEWED_EXPERT_BODY"
        payload["steps"] = [
            {
                "step_id": "artifact_handout",
                "agent_name": "expert_handout",
                "action": "generate_handout",
                "status": "success",
                "output_summary": "UNREVIEWED_EXPERT_BODY",
                "error": "UNREVIEWED_EXPERT_BODY",
            }
        ]

        result = execute_training_orchestration(
            db=object(),
            value=self.make_input(),
            runner=lambda **kwargs: payload,
        )

        self.assertEqual(result["status"], "completed")
        self.assertNotIn("UNREVIEWED_EXPERT_BODY", json.dumps(result, ensure_ascii=False))

    def test_missing_evidence_rejection_returns_actionable_safe_reason(self):
        payload = self.successful_payload()
        payload["status"] = "rejected"
        payload["final"]["artifact"] = None
        payload["final"]["audit"] = {
            "decision": "reject",
            "reason": "missing_evidence_ids:内部证据摘要",
            "source_scope": "audit_agent",
            "source_id": "ART_sensitive",
            "source_ids": ["ART_sensitive"],
        }

        result = execute_training_orchestration(
            db=object(),
            value=self.make_input(),
            runner=lambda **kwargs: payload,
        )

        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["audit"]["reason"], "缺少可引用的正式训练证据，请先导入知识点和教学资源。")
        self.assertEqual(result["artifact"]["content"], {})

    def test_malformed_audit_source_ids_fails_closed(self):
        malformed_values = [1, True, "ART_sensitive", {"id": "ART_sensitive"}, ["ART_sensitive", 1, " "]]

        for source_ids in malformed_values:
            with self.subTest(source_ids=source_ids):
                payload = copy.deepcopy(self.successful_payload())
                payload["final"]["audit"]["source_id"] = ""
                payload["final"]["audit"]["source_ids"] = source_ids

                result = execute_training_orchestration(
                    db=object(),
                    value=self.make_input(),
                    runner=lambda **kwargs: payload,
                )

                serialized = json.dumps(result, ensure_ascii=False)
                self.assertEqual(result["status"], "failed")
                self.assertEqual(result["artifact"]["content"], {})
                self.assertNotIn("UNREVIEWED_EXPERT_BODY", serialized)
                self.assertFalse(result["trace"][-1]["published"])

    def test_invalid_runner_top_level_collections_fail_closed(self):
        cases = [
            {"status": "failed", "final": {}, "steps": 1},
            {"status": "failed", "final": {}, "execution_plan": 1},
        ]

        for payload in cases:
            with self.subTest(payload=payload):
                result = execute_training_orchestration(
                    db=object(),
                    value=self.make_input(),
                    runner=lambda **kwargs: payload,
                )
                serialized = json.dumps(result, ensure_ascii=False)
                self.assertEqual(result["status"], "failed")
                self.assertEqual(result["artifact"]["content"], {})
                self.assertEqual(
                    result["evidence_pack"],
                    {
                        "pack_id": "",
                        "source_scope": "",
                        "source_id": "",
                        "resolved_kp_ids": [],
                        "items": [],
                    },
                )
                self.assertEqual(result["trace"][0]["step_id"], "orchestration")
                self.assertEqual(result["trace"][0]["assigned_agents"], [])
                self.assertFalse(result["trace"][-1]["published"])
                self.assertNotIn("UNREVIEWED_EXPERT_BODY", serialized)

    def test_runner_failure_and_malformed_response_fail_closed(self):
        cases = [
            lambda **kwargs: None,
            lambda **kwargs: [],
            lambda **kwargs: (_ for _ in ()).throw(RuntimeError("UNREVIEWED_EXPERT_BODY")),
        ]

        for runner in cases:
            with self.subTest(runner=runner):
                result = execute_training_orchestration(
                    db=object(),
                    value=self.make_input(),
                    runner=runner,
                )
                serialized = json.dumps(result, ensure_ascii=False)
                self.assertEqual(result["status"], "failed")
                self.assertEqual(result["artifact"]["content"], {})
                self.assertNotIn("UNREVIEWED_EXPERT_BODY", serialized)
                self.assertFalse(result["trace"][-1]["published"])

    def test_trace_sanitizes_untrusted_assigned_agents_for_success_and_failure(self):
        assigned_agents_cases = [
            (1, []),
            (True, []),
            ("UNREVIEWED_EXPERT_BODY", []),
            ({"agent": "x"}, []),
            (["memory_agent", 1, " ", {"raw": "UNREVIEWED_EXPERT_BODY"}], ["memory_agent"]),
        ]

        for status, decision in (("success", "pass"), ("rejected", "reject")):
            for assigned_agents, expected_agents in assigned_agents_cases:
                with self.subTest(status=status, assigned_agents=assigned_agents):
                    payload = copy.deepcopy(self.successful_payload())
                    payload["status"] = status
                    payload["execution_plan"]["assigned_agents"] = assigned_agents
                    payload["final"]["audit"]["decision"] = decision
                    if status == "rejected":
                        payload["final"]["artifact"]["content"] = {
                            "body": "UNREVIEWED_EXPERT_BODY"
                        }

                    result = execute_training_orchestration(
                        db=object(),
                        value=self.make_input(),
                        runner=lambda **kwargs: payload,
                    )

                    serialized = json.dumps(result, ensure_ascii=False)
                    self.assertEqual(result["trace"][0]["assigned_agents"], expected_agents)
                    self.assertNotIn("UNREVIEWED_EXPERT_BODY", serialized)
                    if status == "rejected":
                        self.assertEqual(result["status"], "failed")
                        self.assertEqual(result["artifact"]["content"], {})
                        self.assertFalse(result["trace"][-1]["published"])


class TrainingVariationAdapterTests(unittest.TestCase):
    def _value(self):
        return TrainingOrchestrationInput(
            task_id="T1",
            user_id=7,
            task_type="mistake_variation",
            title="错题变式",
            query="生成变式",
            inputs={"mistake_id": 9, "attempt_item_id": "ITEM_1", "source_question_version_id": "QV_1", "audit_id": "AUD_1"},
            options={},
        )

    def test_passed_variation_is_published_only_through_service(self):
        publisher = Mock(return_value={"scope": "user", "status": "published", "question_version_id": "QV_PUBLISHED"})
        payload = {
            "run_id": "run_1",
            "status": "success",
            "steps": [],
            "final": {
                "artifact": {
                    "artifact_type": "question_variation",
                    "title": "变式",
                    "source_id": "VAR_1",
                    "content": {
                        "stem": "换一种情境：四君子汤对应何证？",
                        "question_type": "single_choice",
                        "difficulty": 2,
                        "kp_ids": ["KP_FJ_001"],
                        "source_mistake_id": 9,
                        "source_question_version_id": "QV_1",
                        "answer": "脾胃气虚证",
                        "analysis": "四君子汤用于脾胃气虚证。",
                    },
                },
                "evidence_pack": {
                    "pack_id": "EP_1",
                    "source_scope": "mistake_variation",
                    "source_id": "QV_1",
                    "resolved_kp_ids": ["KP_FJ_001"],
                    "items": [{
                        "source_id": "QV_1",
                        "source_scope": "question",
                        "summary": "原题",
                        "kp_ids": ["KP_FJ_001"],
                    }],
                },
                "audit": {
                    "decision": "pass",
                    "source_scope": "audit_agent",
                    "source_id": "VAR_1",
                    "source_ids": ["VAR_1"],
                    "audit_id": "AUD_1",
                },
            },
        }
        engine = create_engine("sqlite://")
        database.Base.metadata.create_all(engine)
        Session = sessionmaker(bind=engine)
        with Session() as db:
            db.add(database.LearningAttemptRecord(attempt_id="ATTEMPT_1", learner_id=7, attempt_type="practice"))
            db.add(database.LearningAttemptItemRecord(attempt_item_id="ITEM_1", attempt_id="ATTEMPT_1", question_version_id="QV_1"))
            db.flush()
            result = execute_training_orchestration(
                db=db,
                value=self._value(),
                runner=lambda **_: payload,
                variation_publisher=publisher,
            )
        engine.dispose()
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["audit"]["status"], "completed")
        self.assertTrue(result["audit"]["audit_id"].startswith("AUD_"))
        self.assertEqual(result["audit"]["source_id"], "VAR_1")
        self.assertEqual(
            result["artifact"]["content"]["audit_id"],
            result["audit"]["audit_id"],
        )
        publisher.assert_called_once()
        kwargs = publisher.call_args.kwargs
        self.assertEqual(kwargs["owner_user_id"], 7)
        self.assertEqual(kwargs["standard_answer"], "脾胃气虚证")
        self.assertNotIn("answer", result["artifact"]["content"])

    def test_passed_variation_without_authoritative_answer_is_not_published(self):
        publisher = Mock()
        payload = {
            "status": "success", "run_id": "run-missing-answer", "steps": [],
            "final": {
                "artifact": {"artifact_type": "question_variation", "title": "变式", "source_id": "VAR_1", "content": {
                    "stem": "题干", "question_type": "short_answer", "difficulty": 2,
                    "kp_ids": ["KP_1"], "source_mistake_id": 9,
                    "source_question_version_id": "QV_1",
                }},
                "evidence_pack": {"pack_id": "EP", "source_scope": "mistake_variation", "source_id": "QV_1", "resolved_kp_ids": ["KP_1"], "items": []},
                "audit": {"decision": "pass", "source_scope": "audit_agent", "source_id": "VAR_1"},
            },
        }
        result = execute_training_orchestration(
            db=Mock(), value=self._value(), runner=lambda **_: payload,
            variation_publisher=publisher,
        )
        self.assertEqual(result["status"], "failed")
        publisher.assert_not_called()

    def test_rejected_variation_is_noop(self):
        publisher = Mock()
        result = execute_training_orchestration(
            db=object(),
            value=self._value(),
            runner=lambda **_: {
                "status": "rejected",
                "final": {"audit": {"decision": "reject"}},
            },
            variation_publisher=publisher,
        )
        self.assertEqual(result["status"], "failed")
        publisher.assert_not_called()

    def test_invalid_private_rubric_is_rejected_without_publishing_records(self):
        engine = create_engine("sqlite://")
        database.Base.metadata.create_all(engine)
        Session = sessionmaker(bind=engine)
        publisher = Mock(return_value={"question_version_id": "QV_SHOULD_NOT_PUBLISH"})
        payload = {
            "status": "success", "run_id": "run-invalid-rubric", "steps": [],
            "final": {
                "artifact": {"artifact_type": "question_variation", "title": "变式", "source_id": "VAR_INVALID", "content": {
                    "stem": "题干", "question_type": "short_answer", "difficulty": 2,
                    "kp_ids": ["KP_1"], "source_mistake_id": 9,
                    "source_question_version_id": "QV_1", "answer": "答案",
                    "analysis": "",
                }},
                "evidence_pack": {"pack_id": "EP", "source_scope": "mistake_variation", "source_id": "QV_1", "resolved_kp_ids": ["KP_1"], "items": []},
                "audit": {"decision": "pass", "source_scope": "audit_agent", "source_id": "VAR_INVALID"},
            },
        }
        try:
            with Session() as db:
                result = execute_training_orchestration(
                    db=db, value=self._value(), runner=lambda **_: payload,
                    variation_publisher=publisher,
                )
                self.assertEqual(result["status"], "failed")
                publisher.assert_not_called()
                self.assertEqual(db.query(database.GradingResultRecord).count(), 0)
                self.assertEqual(db.query(database.AuditResultRecord).count(), 0)
                self.assertEqual(db.query(database.VariationRubricRecord).count(), 0)
                self.assertEqual(db.query(database.VariationSetRecord).count(), 0)
        finally:
            engine.dispose()

    def test_pass_uses_persisted_current_audit_and_allowlisted_candidate(self):
        engine = create_engine("sqlite://")
        database.Base.metadata.create_all(engine)
        Session = sessionmaker(bind=engine)
        payload = {
            "status": "success", "run_id": "run-current", "steps": [],
            "final": {
                "artifact": {"artifact_type": "question_variation", "title": "变式", "source_id": "VAR_CURRENT", "content": {
                    "stem": "安全题干", "question_type": "single_choice", "difficulty": 2,
                    "kp_ids": ["KP_1"], "source_mistake_id": 9,
                    "source_question_version_id": "QV_1", "answer": "SENTINEL_ANSWER",
                    "analysis": "SENTINEL_ANALYSIS",
                }},
                "evidence_pack": {"pack_id": "EP", "source_scope": "mistake_variation", "source_id": "QV_1", "resolved_kp_ids": ["KP_1"], "items": []},
                "audit": {"decision": "pass", "source_scope": "audit_agent", "source_id": "VAR_CURRENT", "audit_id": "UNTRUSTED"},
            },
        }
        publisher = Mock(return_value={"question_version_id": "QV_AUTHORITATIVE"})
        try:
            with Session() as db:
                db.add(database.LearningAttemptRecord(attempt_id="ATTEMPT_1", learner_id=7, attempt_type="practice"))
                db.add(database.LearningAttemptItemRecord(attempt_item_id="ITEM_1", attempt_id="ATTEMPT_1", question_version_id="QV_1"))
                db.flush()
                result = execute_training_orchestration(db=db, value=self._value(), runner=lambda **_: payload, variation_publisher=publisher)
                current = db.query(database.AuditResultRecord).one()
                audited_candidate = db.query(database.GradingResultRecord).one()
                self.assertIn("SENTINEL_ANSWER", audited_candidate.payload_json)
                self.assertIn("SENTINEL_ANALYSIS", audited_candidate.payload_json)
                self.assertNotEqual(current.audit_id, "AUD_1")
                self.assertEqual(publisher.call_args.kwargs["audit_id"], current.audit_id)
                serialized = str(result)
                self.assertNotIn("SENTINEL_ANSWER", serialized)
                self.assertNotIn("SENTINEL_ANALYSIS", serialized)
                self.assertEqual(
                    publisher.call_args.kwargs["standard_answer"], "SENTINEL_ANSWER"
                )
        finally:
            engine.dispose()


if __name__ == "__main__":
    unittest.main()
