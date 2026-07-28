import pytest

from competition_app.agents.paper_blueprint_compiler import (
    PaperBlueprintCompilerAgent,
)


_CONTEXT = {
    "trace_id": "TRACE_BLUEPRINT_COMPILER",
    "request_id": "REQ_BLUEPRINT_COMPILER",
    "step_id": "paper_blueprint",
    "learner_id": "LEARNER_BLUEPRINT_COMPILER",
}


_DOCUMENT = (
    "【标题】四君子汤练习卷\n"
    "【范围】四君子汤组成与功效。\n"
    "【单元一：组成】学习目标：掌握组成。检索表达：四君子汤 组成。目标题数：2题。"
)


class AnchoredBlueprintCompilerModel:
    async def complete_json(self, role, payload, on_delta=None):
        return {
            "status": "compiled",
            "contract_version": "1.0",
            "contract": {
                "title": "四君子汤练习卷",
                "scope_summary": "四君子汤组成与功效。",
                "units": [
                    {
                        "unit_key": "组成",
                        "knowledge_module": "组成",
                        "learning_objective": "掌握组成。",
                        "retrieval_query": "四君子汤 组成",
                        "required_question_count": 2,
                    }
                ],
                "field_anchors": {
                    "/title": [
                        {"source_field": "blueprint_document", "source_quote": "四君子汤练习卷"}
                    ],
                    "/scope_summary": [
                        {"source_field": "blueprint_document", "source_quote": "四君子汤组成与功效。"}
                    ],
                    "/units": [
                        {"source_field": "blueprint_document", "source_quote": "【单元一：组成】"}
                    ],
                },
            },
        }


class InventingBlueprintCompilerModel(AnchoredBlueprintCompilerModel):
    async def complete_json(self, role, payload, on_delta=None):
        value = await super().complete_json(role, payload, on_delta)
        value["contract"]["field_anchors"]["/units"] = [
            {"source_field": "blueprint_document", "source_quote": "【单元二：辨析】"}
        ]
        return value


@pytest.mark.asyncio
async def test_blueprint_compiler_accepts_anchored_contract() -> None:
    envelope = await PaperBlueprintCompilerAgent(
        AnchoredBlueprintCompilerModel()
    ).compile(_CONTEXT, blueprint_document=_DOCUMENT)

    assert envelope.result.status == "compiled"
    assert envelope.result.contract.units[0].required_question_count == 2
    assert len(envelope.source_digest) == 64


@pytest.mark.asyncio
async def test_blueprint_compiler_rejects_invented_anchor() -> None:
    envelope = await PaperBlueprintCompilerAgent(
        InventingBlueprintCompilerModel()
    ).compile(_CONTEXT, blueprint_document=_DOCUMENT)

    assert envelope.result.status == "needs_revision"
    assert any(
        issue.code == "source_anchor_invalid" for issue in envelope.result.issues
    )


class InventingBlueprintValueModel(AnchoredBlueprintCompilerModel):
    async def complete_json(self, role, payload, on_delta=None):
        value = await super().complete_json(role, payload, on_delta)
        value["contract"]["units"][0]["retrieval_query"] = "四君子汤 配伍意义"
        return value


@pytest.mark.asyncio
async def test_blueprint_compiler_rejects_value_missing_from_document() -> None:
    envelope = await PaperBlueprintCompilerAgent(
        InventingBlueprintValueModel()
    ).compile(_CONTEXT, blueprint_document=_DOCUMENT)

    assert envelope.result.status == "needs_revision"
    assert any(
        issue.field_path == "/units/0/retrieval_query"
        for issue in envelope.result.issues
    )
