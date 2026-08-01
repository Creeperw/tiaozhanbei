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


class ModelWithoutSystemManagedText:
    async def complete_json(self, role, payload, on_delta=None):
        business_payload = payload["payload"]
        schema_text = str(business_payload["output_schema"])
        assert "short_term_plan_content" not in schema_text
        assert business_payload["system_inserted_fields"] == [
            "short_term_plan_content"
        ]
        return {
            "status": "compiled",
            "contract_version": "1.0",
            "contract": {
                "scope": "short_term",
                "duration_days": 14,
                "progression_nodes": ["先完成教材核对", "再完成闭卷比较"],
                "expected_output": "一张补益剂类方比较表",
                "completion_criteria": "能够闭卷比较代表方剂",
                "selected_stage_id": None,
                "selected_books": ["《方剂学》"],
                "field_anchors": {
                    "/duration_days": [
                        {"source_field": "duration_days", "source_quote": "14"}
                    ],
                    "/progression_nodes": [
                        {
                            "source_field": "progression_nodes",
                            "source_quote": "先完成教材核对",
                        },
                        {
                            "source_field": "progression_nodes",
                            "source_quote": "再完成闭卷比较",
                        },
                    ],
                    "/expected_output": [
                        {
                            "source_field": "expected_output",
                            "source_quote": "一张补益剂类方比较表",
                        }
                    ],
                    "/completion_criteria": [
                        {
                            "source_field": "completion_criteria",
                            "source_quote": "能够闭卷比较代表方剂",
                        }
                    ],
                    "/selected_books": [
                        {
                            "source_field": "selected_books",
                            "source_quote": "《方剂学》",
                        }
                    ],
                },
            },
        }


@pytest.mark.asyncio
async def test_system_injects_exact_short_term_content() -> None:
    diagnosis_output = {
        "short_term_plan_content": "未来14天使用《方剂学》完成补益剂学习，最后提交闭卷验收。",
        "duration_days": 14,
        "progression_nodes": ["先完成教材核对", "再完成闭卷比较"],
        "expected_output": "一张补益剂类方比较表",
        "completion_criteria": "能够闭卷比较代表方剂",
        "selected_books": ["《方剂学》"],
    }

    envelope = await PlanContractCompilerAgent(
        ModelWithoutSystemManagedText()
    ).compile(
        compiler_context(),
        plan_scope="short_term",
        diagnosis_output=diagnosis_output,
        trusted_route={},
        parent_plan_constraints={"current_stage_duration_days": 30},
    )

    assert envelope.result.status == "compiled"
    contract = envelope.result.contract
    assert contract.short_term_plan_content == diagnosis_output[
        "short_term_plan_content"
    ]
    assert contract.field_anchors["/short_term_plan_content"][0].source_quote == (
        diagnosis_output["short_term_plan_content"]
    )


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
async def test_compiler_prefers_valid_diagnosis_fields_over_model_invention() -> None:
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

    assert envelope.result.status == "compiled"
    assert envelope.result.contract.short_term_plan_content == "原始计划正文。"
    assert envelope.result.contract.duration_days == 7
    assert envelope.result.contract.selected_books == ["《方剂学》"]


class DocumentCompilerModel:
    def __init__(self) -> None:
        self.called = False

    async def complete_json(self, role, payload, on_delta=None):
        assert role == "plan_contract_compiler"
        self.called = True
        business_payload = payload["payload"]
        source = business_payload["diagnosis_output"]["plan_document"]
        props = business_payload["output_schema"]["$defs"]["CompiledShortTermContract"]["properties"]
        assert "short_term_plan_content" not in props
        return {
            "status": "compiled",
            "contract_version": "1.0",
            "contract": {
                "scope": "short_term",
                "short_term_plan_content": source,
                "duration_days": 14,
                "progression_nodes": ["先完成教材核对", "再完成闭卷比较"],
                "expected_output": "一张类方比较表",
                "completion_criteria": "能够闭卷比较代表方剂",
                "selected_stage_id": "stage-1",
                "selected_books": ["《方剂学》"],
                "field_anchors": {
                    "/short_term_plan_content": [{"source_field": "plan_document", "source_quote": source}],
                    "/duration_days": [{"source_field": "plan_document", "source_quote": "14天"}],
                    "/progression_nodes": [
                        {"source_field": "plan_document", "source_quote": "先完成教材核对"},
                        {"source_field": "plan_document", "source_quote": "再完成闭卷比较"},
                    ],
                    "/expected_output": [{"source_field": "plan_document", "source_quote": "一张类方比较表"}],
                    "/completion_criteria": [{"source_field": "plan_document", "source_quote": "能够闭卷比较代表方剂"}],
                    "/selected_books": [{"source_field": "plan_document", "source_quote": "《方剂学》"}],
                },
            },
        }


@pytest.mark.asyncio
async def test_document_compiler_is_called_for_complete_prose_document() -> None:
    model = DocumentCompilerModel()
    document = (
        "## 当前周期目标\n未来14天使用《方剂学》完成补益剂学习。"
        "推进节点：先完成教材核对；再完成闭卷比较。"
        "预期产出：一张类方比较表。"
        "完成标准：能够闭卷比较代表方剂。"
    )
    envelope = await PlanContractCompilerAgent(model).compile(
        compiler_context(),
        plan_scope="short_term",
        diagnosis_output={"plan_document": document},
        trusted_route={},
        parent_plan_constraints={"current_stage_duration_days": 30},
    )
    assert model.called is True
    assert envelope.result.status == "compiled"
    assert envelope.result.contract.short_term_plan_content == document


@pytest.mark.asyncio
async def test_document_compiler_does_not_invent_missing_semantics() -> None:
    envelope = await PlanContractCompilerAgent(StubChatModel()).compile(
        compiler_context(),
        plan_scope="short_term",
        diagnosis_output={"plan_document": "只说学习方剂，不给周期、节点、产出或完成标准。"},
        trusted_route={},
        parent_plan_constraints={},
    )
    assert envelope.result.status == "needs_revision"
    assert envelope.result.issues


class LongTermCompilerModel:
    def __init__(self, *, anchor_indexes: tuple[int, ...]) -> None:
        self.anchor_indexes = anchor_indexes

    async def complete_json(self, role, payload, on_delta=None):
        assert role == "plan_contract_compiler"
        anchors = {
            "/total_duration_days": [
                {"source_field": "plan_document", "source_quote": "共30天"}
            ]
        }
        for index in self.anchor_indexes:
            for field in (
                "stage",
                "stage_name",
                "books",
                "goal",
                "duration_days",
                "schedule_summary",
            ):
                anchors[f"/stages/{index}/{field}"] = [
                    {"source_field": "plan_document", "source_quote": "阶段"}
                ]
        return {
            "status": "compiled",
            "contract_version": "1.0",
            "contract": {
                "scope": "long_term",
                "total_duration_days": 30,
                "stages": [
                    {
                        "stage": 1,
                        "stage_name": "基础阶段",
                        "books": ["《中医基础理论》"],
                        "goal": "掌握基础理论",
                        "duration_days": 15,
                        "schedule_summary": "完成基础学习与验收",
                    },
                    {
                        "stage": 2,
                        "stage_name": "临床阶段",
                        "books": ["《中医内科学》"],
                        "goal": "建立辨证思路",
                        "duration_days": 15,
                        "schedule_summary": "完成临床学习与验收",
                    },
                ],
                "field_anchors": anchors,
            },
        }


@pytest.mark.asyncio
async def test_long_term_stage_anchor_indexes_must_match_contract_positions() -> None:
    document = "共30天。阶段一使用《中医基础理论》；阶段二使用《中医内科学》。"
    envelope = await PlanContractCompilerAgent(
        LongTermCompilerModel(anchor_indexes=(1, 2))
    ).compile(
        compiler_context(),
        plan_scope="long_term",
        diagnosis_output={"plan_document": document},
        trusted_route={},
        parent_plan_constraints={},
    )

    assert envelope.result.status == "needs_revision"
    assert "/stages" in {issue.field_path for issue in envelope.result.issues}


@pytest.mark.asyncio
async def test_long_term_stage_anchor_indexes_accept_exact_positions() -> None:
    document = "共30天。阶段一使用《中医基础理论》；阶段二使用《中医内科学》。"
    envelope = await PlanContractCompilerAgent(
        LongTermCompilerModel(anchor_indexes=(0, 1))
    ).compile(
        compiler_context(),
        plan_scope="long_term",
        diagnosis_output={"plan_document": document},
        trusted_route={},
        parent_plan_constraints={},
    )

    assert envelope.result.status == "compiled"


class RejectingCompilerModel:
    async def complete_json(self, role, payload, on_delta=None):
        return {
            "status": "needs_revision",
            "contract_version": "1.0",
            "issues": [
                {
                    "code": "missing_required_field",
                    "category": "missing",
                    "field_path": "/stages",
                }
            ],
        }


@pytest.mark.asyncio
async def test_long_term_document_fallback_never_invents_stage_fields() -> None:
    document = (
        "total_duration_days：30\n"
        "stages：\n"
        "- 阶段1使用《中医基础理论》\n"
        "- 阶段2使用《中医内科学》"
    )
    envelope = await PlanContractCompilerAgent(RejectingCompilerModel()).compile(
        compiler_context(),
        plan_scope="long_term",
        diagnosis_output={"plan_document": document},
        trusted_route={},
        parent_plan_constraints={},
    )

    assert envelope.result.status == "needs_revision"
    assert envelope.result.issues[0].field_path == "/stages"
