import pytest

from competition_app.agents.plan_contract_compiler import PlanContractCompilerAgent
from competition_app.llm.stub import StubChatModel


def compiler_context() -> dict[str, str]:
    return {
        "trace_id": "TRACE_COMPILER_TEST",
        "request_id": "REQ_COMPILER_TEST",
        "learner_id": "LEARNER_COMPILER_TEST",
        "step_id": "diagnosis_agent",
    }


@pytest.mark.asyncio
async def test_stub_compiler_reports_missing_short_term_fields() -> None:
    envelope = await PlanContractCompilerAgent(StubChatModel()).compile(
        compiler_context(),
        plan_scope="short_term",
        diagnosis_output={
            "short_term_plan_content": "使用《方剂学》完成补益剂学习。",
            "expected_output": "一份类方比较表。",
            "completion_criteria": "能够闭卷比较代表方剂。",
            "selected_books": ["《方剂学》"],
        },
        trusted_route={},
        parent_plan_constraints={"current_stage_duration_days": 30},
    )

    assert envelope.result.status == "needs_revision"
    assert {issue.field_path for issue in envelope.result.issues} == {
        "/duration_days",
        "/progression_nodes",
    }


@pytest.mark.asyncio
async def test_stub_compiler_copies_short_term_values_without_defaults() -> None:
    diagnosis_output = {
        "short_term_plan_content": (
            "未来30天使用《方剂学》学习补益剂，先完成教材核对，"
            "再完成类方比较并提交闭卷验收。"
        ),
        "duration_days": 30,
        "progression_nodes": ["完成教材核对。", "完成闭卷比较验收。"],
        "expected_output": "一份类方比较表。",
        "completion_criteria": "能够闭卷比较代表方剂。",
        "selected_stage_id": "stage-1",
        "selected_books": ["《方剂学》"],
    }

    envelope = await PlanContractCompilerAgent(StubChatModel()).compile(
        compiler_context(),
        plan_scope="short_term",
        diagnosis_output=diagnosis_output,
        trusted_route={},
        parent_plan_constraints={"current_stage_duration_days": 30},
    )

    assert envelope.result.status == "compiled"
    contract = envelope.result.contract
    assert contract.duration_days == diagnosis_output["duration_days"]
    assert contract.progression_nodes == diagnosis_output["progression_nodes"]
    assert contract.selected_books == diagnosis_output["selected_books"]


class InventingCompilerModel:
    async def complete_json(self, role, payload, on_delta=None):
        return {
            "status": "compiled",
            "contract_version": "1.0",
            "contract": {
                "scope": "short_term",
                "short_term_plan_content": "模型擅自改写的计划。",
                "duration_days": 30,
                "progression_nodes": ["节点一", "节点二"],
                "expected_output": "产出",
                "completion_criteria": "标准",
                "selected_stage_id": "stage-1",
                "selected_books": ["《模型虚构教材》"],
                "field_anchors": {
                    "/short_term_plan_content": [
                        {
                            "source_field": "short_term_plan_content",
                            "source_quote": "模型擅自改写的计划。",
                        }
                    ],
                    "/duration_days": [
                        {"source_field": "duration_days", "source_quote": "30"}
                    ],
                    "/progression_nodes": [
                        {
                            "source_field": "progression_nodes",
                            "source_quote": '["节点一", "节点二"]',
                        }
                    ],
                    "/selected_books": [
                        {
                            "source_field": "selected_books",
                            "source_quote": '["《模型虚构教材》"]',
                        }
                    ],
                },
            },
        }


@pytest.mark.asyncio
async def test_compiler_rejects_values_not_anchored_in_diagnosis_output() -> None:
    envelope = await PlanContractCompilerAgent(InventingCompilerModel()).compile(
        compiler_context(),
        plan_scope="short_term",
        diagnosis_output={
            "short_term_plan_content": "原始计划正文。",
            "duration_days": 7,
            "progression_nodes": ["原始节点一", "原始节点二"],
            "expected_output": "原始产出",
            "completion_criteria": "原始标准",
            "selected_books": ["《方剂学》"],
        },
        trusted_route={},
        parent_plan_constraints={},
    )

    assert envelope.result.status == "needs_revision"
    assert any(
        issue.code == "source_anchor_invalid"
        for issue in envelope.result.issues
    )
