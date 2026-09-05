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


class MarkdownNormalizedBlueprintCompilerModel(AnchoredBlueprintCompilerModel):
    async def complete_json(self, role, payload, on_delta=None):
        value = await super().complete_json(role, payload, on_delta)
        value["contract"]["field_anchors"].pop("/units")
        value["contract"]["field_anchors"]["/title"] = [
            {
                "source_field": "blueprint_document",
                "source_quote": "标题：四君子汤练习卷",
            }
        ]
        value["contract"]["field_anchors"]["/units/0/learning_objective"] = [
            {
                "source_field": "blueprint_document",
                "source_quote": "学习目标：掌握组成。",
            }
        ]
        return value


@pytest.mark.asyncio
async def test_blueprint_compiler_accepts_formatting_only_anchor_differences() -> None:
    document = _DOCUMENT.replace("【标题】", "**标题**：")
    envelope = await PaperBlueprintCompilerAgent(
        MarkdownNormalizedBlueprintCompilerModel()
    ).compile(_CONTEXT, blueprint_document=document)

    assert envelope.result.status == "compiled"


class DifficultyExtractingBlueprintCompilerModel(AnchoredBlueprintCompilerModel):
    async def complete_json(self, role, payload, on_delta=None):
        value = await super().complete_json(role, payload, on_delta)
        value["contract"]["units"][0]["target_difficulty"] = 3
        value["contract"]["units"][0]["difficulty_is_hard_constraint"] = True
        value["contract"]["field_anchors"]["/units/0/target_difficulty"] = [
            {"source_field": "blueprint_document", "source_quote": "难度3"}
        ]
        return value


@pytest.mark.asyncio
async def test_blueprint_compiler_accepts_verbatim_difficulty_anchor() -> None:
    document = _DOCUMENT + "【单元一：组成】难度3。"
    envelope = await PaperBlueprintCompilerAgent(
        DifficultyExtractingBlueprintCompilerModel()
    ).compile(_CONTEXT, blueprint_document=document)

    assert envelope.result.status == "compiled"
    assert envelope.result.contract.units[0].target_difficulty == 3
    assert envelope.result.contract.units[0].difficulty_is_hard_constraint is True


class InventingDifficultyCompilerModel(DifficultyExtractingBlueprintCompilerModel):
    async def complete_json(self, role, payload, on_delta=None):
        value = await super().complete_json(role, payload, on_delta)
        # 难度数字不在原稿中：必须触发 source_anchor_invalid。
        value["contract"]["field_anchors"]["/units/0/target_difficulty"] = [
            {"source_field": "blueprint_document", "source_quote": "难度5"}
        ]
        return value


@pytest.mark.asyncio
async def test_blueprint_compiler_rejects_difficulty_without_document_anchor() -> None:
    envelope = await PaperBlueprintCompilerAgent(
        InventingDifficultyCompilerModel()
    ).compile(_CONTEXT, blueprint_document=_DOCUMENT)

    assert envelope.result.status == "needs_revision"
    assert any(
        issue.field_path == "/units/0/target_difficulty"
        and issue.code == "source_anchor_invalid"
        for issue in envelope.result.issues
    )


def test_blueprint_coerce_normalizes_out_of_range_difficulty() -> None:
    from competition_app.agents.paper_blueprint_compiler import _coerce_blueprint

    coerced = _coerce_blueprint(
        {
            "contract": {
                "title": "x",
                "scope_summary": "y",
                "units": [
                    {
                        "unit_key": "u",
                        "knowledge_module": "m",
                        "learning_objective": "o",
                        "retrieval_query": "q",
                        "required_question_count": 2,
                        "target_difficulty": "难度7",
                    }
                ],
            }
        }
    )

    unit = coerced["contract"]["units"][0]
    assert unit["target_difficulty"] is None
    assert unit.get("difficulty_is_hard_constraint", False) is False
