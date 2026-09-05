import pytest
from pydantic import ValidationError

from competition_app.agents.paper_assembly_compiler import (
    PaperAssemblyCompilerAgent,
    PaperAssemblySystemBindingCompiler,
)
from competition_app.contracts.paper_assembly_compilation import (
    PaperAssemblyCandidateCatalogSnapshot,
)
from competition_app.llm.schemas import (
    PaperAssemblyCandidateChoiceModelOutput,
    PaperAssemblySelectionModelOutput,
    PaperGapGenerationModelOutput,
)


_CONTEXT = {
    "trace_id": "TRACE_ASSEMBLY_COMPILER",
    "request_id": "REQ_ASSEMBLY_COMPILER",
    "step_id": "paper_assembly",
    "learner_id": "LEARNER_ASSEMBLY_COMPILER",
}

_DOCUMENT = "【试卷标题】测试卷\n单元U1选用候选题Q1。"
_CATALOG = [
    {
        "unit_id": "U1",
        "items": [{"question_id": "Q1"}],
    }
]


def _system_catalog() -> PaperAssemblyCandidateCatalogSnapshot:
    return PaperAssemblyCandidateCatalogSnapshot(
        catalog_id="PCC_1234567890abcdef1234567890abcdef",
        execution_id="E1",
        candidate_pool_id="POOL1",
        catalog_digest="a" * 64,
        candidates=[
            {
                "candidate_no": 1,
                "binding_id": "PCB_1234567890abcdef1234567890abcdef",
                "unit_id": "U1",
                "question_id": "Q1",
                "question_type": "单项选择题",
                "content_digest": "b" * 64,
                "selectable": True,
            }
        ],
    )


def test_system_binding_compiler_resolves_number_and_discards_invalid_choices() -> None:
    choices = [
        PaperAssemblyCandidateChoiceModelOutput(
            candidate_no=1, rationale="覆盖核心概念。"
        ),
        PaperAssemblyCandidateChoiceModelOutput(
            candidate_no=1, rationale="重复选择。"
        ),
        PaperAssemblyCandidateChoiceModelOutput(
            candidate_no=99, rationale="不存在的序号。"
        ),
    ]

    result = PaperAssemblySystemBindingCompiler.compile(
        snapshot=_system_catalog(),
        selected_candidates=choices,
        expected_execution_id="E1",
    )

    assert [(item.unit_id, item.question_id) for item in result.selected_items] == [
        ("U1", "Q1")
    ]
    assert {issue.code for issue in result.issues} == {
        "candidate_no_duplicate",
        "candidate_no_unknown",
    }


def test_system_binding_compiler_rejects_stale_execution_catalog() -> None:
    result = PaperAssemblySystemBindingCompiler.compile(
        snapshot=_system_catalog(),
        selected_candidates=[
            PaperAssemblyCandidateChoiceModelOutput(
                candidate_no=1, rationale="选择候选。"
            )
        ],
        expected_execution_id="E2",
    )

    assert result.selected_items == []
    assert result.issues[0].code == "catalog_execution_mismatch"


def test_selection_schema_forbids_model_owned_identity_and_anchor_fields() -> None:
    with pytest.raises(ValidationError):
        PaperAssemblySelectionModelOutput.model_validate(
            {
                "selection_summary": "选择第一题。",
                "selected_candidates": [
                    {
                        "candidate_no": 1,
                        "rationale": "符合要求。",
                        "question_id": "Q_FORGED",
                        "source_anchor": "FORGED",
                    }
                ],
            }
        )


def test_gap_schema_forbids_model_owned_unit_source_and_question_id() -> None:
    with pytest.raises(ValidationError):
        PaperGapGenerationModelOutput.model_validate(
            {
                "generation_summary": "补充一道题。",
                "generated_items": [
                    {
                        "unit_id": "U_FORGED",
                        "question_id": "Q_FORGED",
                        "source_tier": "textbook",
                        "question_type": "单项选择题",
                        "stem": "测试题",
                        "options": ["A. 甲", "B. 乙"],
                        "reference_answer": "A",
                        "analysis": "甲正确。",
                        "rationale": "补足缺口。",
                        "evidence_nos": [],
                    }
                ],
            }
        )


class AssemblyCompilerModel:
    def __init__(self, *, unit_id: str = "U1", question_id: str = "Q1") -> None:
        self.unit_id = unit_id
        self.question_id = question_id

    async def complete_json(self, role, payload, on_delta=None):
        return {
            "status": "compiled",
            "contract_version": "1.0",
            "contract": {
                "title": "测试卷",
                "selected_items": [
                    {
                        "unit_id": self.unit_id,
                        "question_id": self.question_id,
                        "source_anchors": [
                            {
                                "source_field": "assembly_document",
                                "source_quote": "单元U1选用候选题Q1。",
                            }
                        ],
                    }
                ],
                "generated_items": [],
                "field_anchors": {
                    "/title": [
                        {"source_field": "assembly_document", "source_quote": "测试卷"}
                    ]
                },
            },
        }


@pytest.mark.asyncio
async def test_assembly_compiler_accepts_anchored_candidate_selection() -> None:
    envelope = await PaperAssemblyCompilerAgent(AssemblyCompilerModel()).compile(
        _CONTEXT,
        assembly_document=_DOCUMENT,
        candidate_catalog=_CATALOG,
    )

    assert envelope.result.status == "compiled"
    assert envelope.result.contract.selected_items[0].question_id == "Q1"


class AnchorIdCompilerModel:
    async def complete_json(self, role, payload, on_delta=None):
        anchor_catalog = payload["payload"]["anchor_catalog"]
        selection_anchor = next(
            item for item in anchor_catalog if item["anchor_id"].startswith("SELECTION_")
        )
        return {
            "status": "compiled",
            "contract_version": "1.0",
            "contract": {
                "title": "测试卷",
                "selected_items": [
                    {
                        "unit_id": "U1",
                        "question_id": "Q1",
                        "source_anchor_ids": [selection_anchor["anchor_id"]],
                    }
                ],
                "generated_items": [],
                "field_anchors": {
                    "/title": [
                        {
                            "source_field": "assembly_document",
                            "source_quote": "测试卷",
                        }
                    ]
                },
            },
        }


class FullyAnchoredCompilerModel:
    async def complete_json(self, role, payload, on_delta=None):
        anchor_catalog = payload["payload"]["anchor_catalog"]
        title_anchor = next(
            item for item in anchor_catalog if item["anchor_id"] == "DOC_TITLE"
        )
        selection_anchor = next(
            item for item in anchor_catalog if item["anchor_id"].startswith("SELECTION_")
        )
        return {
            "status": "compiled",
            "contract_version": "1.0",
            "contract": {
                "title": "测试卷",
                "selected_items": [
                    {
                        "unit_id": "U1",
                        "question_id": "Q1",
                        "source_anchor_ids": [selection_anchor["anchor_id"]],
                    }
                ],
                "generated_items": [],
                "field_anchors": {
                    "/title": [
                        {"anchor_id": title_anchor["anchor_id"]}
                    ]
                },
            },
        }


@pytest.mark.asyncio
async def test_anchor_id_materializes_exact_source_owned_quote() -> None:
    document = "【试卷标题】测试卷\n单元U1选用候选题Q1。"
    envelope = await PaperAssemblyCompilerAgent(AnchorIdCompilerModel()).compile(
        _CONTEXT,
        assembly_document=document,
        candidate_catalog=_CATALOG,
    )

    assert envelope.result.status == "compiled"
    quote = envelope.result.contract.selected_items[0].source_anchors[0].source_quote
    assert quote == "单元U1选用候选题Q1。"


@pytest.mark.asyncio
async def test_compiler_receives_source_owned_title_anchor() -> None:
    envelope = await PaperAssemblyCompilerAgent(
        FullyAnchoredCompilerModel()
    ).compile(
        _CONTEXT,
        assembly_document="测试卷\n单元U1选用候选题Q1。",
        candidate_catalog=_CATALOG,
    )

    assert envelope.result.status == "compiled"
    title_quote = envelope.result.contract.field_anchors["/title"][0].source_quote
    assert title_quote == "测试卷"


@pytest.mark.asyncio
async def test_assembly_compiler_rejects_unknown_candidate_id() -> None:
    envelope = await PaperAssemblyCompilerAgent(
        AssemblyCompilerModel(question_id="Q_UNKNOWN")
    ).compile(
        _CONTEXT,
        assembly_document=_DOCUMENT,
        candidate_catalog=_CATALOG,
    )

    assert envelope.result.status == "needs_revision"
    assert any(issue.code == "candidate_unknown" for issue in envelope.result.issues)


@pytest.mark.asyncio
async def test_assembly_compiler_rejects_candidate_from_wrong_unit() -> None:
    envelope = await PaperAssemblyCompilerAgent(
        AssemblyCompilerModel(unit_id="U2")
    ).compile(
        _CONTEXT,
        assembly_document=_DOCUMENT,
        candidate_catalog=_CATALOG,
    )

    assert envelope.result.status == "needs_revision"
    assert any(
        issue.code == "candidate_unit_mismatch" for issue in envelope.result.issues
    )


class MissingChoiceOptionsCompilerModel:
    async def complete_json(self, role, payload, on_delta=None):
        return {
            "status": "compiled",
            "contract_version": "1.0",
            "contract": {
                "title": "测试卷",
                "selected_items": [],
                "generated_items": [
                    {
                        "unit_id": "U1",
                        "question_type": "单项选择题",
                        "stem": "原创题",
                        "options": [],
                        "reference_answer": "A",
                        "explanation": "解析",
                        "source_anchors": [
                            {
                                "source_field": "assembly_document",
                                "source_quote": "单元U1原创单项选择题：题干：原创题；参考答案：A；解析：解析。",
                            }
                        ],
                    }
                ],
                "field_anchors": {
                    "/title": [
                        {"source_field": "assembly_document", "source_quote": "测试卷"}
                    ]
                },
            },
        }


@pytest.mark.asyncio
async def test_assembly_compiler_rejects_generated_choice_without_options() -> None:
    envelope = await PaperAssemblyCompilerAgent(
        MissingChoiceOptionsCompilerModel()
    ).compile(
        _CONTEXT,
        assembly_document=(
            "【试卷标题】测试卷\n"
            "单元U1原创单项选择题：题干：原创题；参考答案：A；解析：解析。"
        ),
        candidate_catalog=_CATALOG,
    )

    assert envelope.result.status == "needs_revision"
    assert envelope.result.issues[0].code == "schema_invalid"


class SystemBoundGapCompilerModel:
    async def complete_json(self, role, payload, on_delta=None):
        return {
            "status": "compiled",
            "contract_version": "1.0",
            "contract": {
                "title": "测试卷",
                "selected_items": [],
                "generated_items": [
                    {
                        # Simulate a model copying the learner-facing chapter
                        # label instead of the internal unit ID.  The scoped
                        # gap call must replace it with the system binding.
                        "unit_id": "《中医学基础》阴阳学说",
                        "question_type": "单项选择题",
                        "stem": "阴阳互根互用体现在哪一项？",
                        "options": ["A. 相互依存", "B. 毫无关联"],
                        "reference_answer": "A",
                        "explanation": "阴阳双方相互依存。",
                        "source_anchors": [
                            {
                                "source_field": "assembly_document",
                                "source_quote": (
                                    "所属单元：《中医学基础》阴阳学说\n"
                                    "题型：单项选择题\n"
                                    "题干：阴阳互根互用体现在哪一项？\n"
                                    "选项：A. 相互依存 B. 毫无关联\n"
                                    "参考答案：A\n"
                                    "解析：阴阳双方相互依存。"
                                ),
                            }
                        ],
                    }
                ],
                "field_anchors": {
                    "/title": [
                        {"source_field": "assembly_document", "source_quote": "测试卷"}
                    ]
                },
            },
        }


@pytest.mark.asyncio
async def test_scoped_gap_generation_uses_system_owned_unit_binding() -> None:
    document = (
        "测试卷\n"
        "所属单元：《中医学基础》阴阳学说\n"
        "题型：单项选择题\n"
        "题干：阴阳互根互用体现在哪一项？\n"
        "选项：A. 相互依存 B. 毫无关联\n"
        "参考答案：A\n"
        "解析：阴阳双方相互依存。"
    )
    envelope = await PaperAssemblyCompilerAgent(
        SystemBoundGapCompilerModel()
    ).compile(
        _CONTEXT,
        assembly_document=document,
        candidate_catalog=_CATALOG,
        generated_unit_binding="U1",
    )

    assert envelope.result.status == "compiled"
    assert envelope.result.contract.generated_items[0].unit_id == "U1"


class MarkdownGapCompilerModel:
    async def complete_json(self, role, payload, on_delta=None):
        anchor_catalog = payload["payload"]["anchor_catalog"]
        title_anchor = next(
            item for item in anchor_catalog if item["anchor_id"] == "DOC_TITLE"
        )
        question_anchor = next(
            item
            for item in anchor_catalog
            if item["anchor_id"].startswith("DOC_BLOCK_")
        )
        return {
            "status": "compiled",
            "contract_version": "1.0",
            "contract": {
                "title": "《中医学基础》阴阳学说单选题练习",
                "selected_items": [],
                "generated_items": [
                    {
                        "unit_id": "正文中的可读章节名",
                        "question_type": "单项选择题",
                        "stem": "阴阳互根互用体现在哪一项？",
                        "options": ["A．相互依存", "B．毫无关联"],
                        "reference_answer": "A",
                        "explanation": "阴阳双方相互依存。",
                        "source_anchor_ids": [question_anchor["anchor_id"]],
                    }
                ],
                "field_anchors": {
                    "/title": [{"anchor_id": title_anchor["anchor_id"]}]
                },
            },
        }


@pytest.mark.asyncio
async def test_markdown_gap_document_builds_real_title_and_question_anchors() -> None:
    document = (
        "---\n\n"
        "试卷标题：《中医学基础》阴阳学说单选题练习\n\n"
        "**所属单元**：《中医学基础》第一章 阴阳学说\n"
        "**题型**：单项选择题\n"
        "**题干**：阴阳互根互用体现在哪一项？\n"
        "**选项**：\nA．相互依存\nB．毫无关联\n"
        "**参考答案**：A\n"
        "**解析**：阴阳双方相互依存。\n\n---\n"
    )

    anchors = PaperAssemblyCompilerAgent._build_anchor_catalog(document)
    assert next(item for item in anchors if item["anchor_id"] == "DOC_TITLE")[
        "content"
    ].startswith("试卷标题：")
    assert any(item["anchor_id"] == "DOC_BLOCK_001" for item in anchors)

    envelope = await PaperAssemblyCompilerAgent(MarkdownGapCompilerModel()).compile(
        _CONTEXT,
        assembly_document=document,
        candidate_catalog=_CATALOG,
        generated_unit_binding="U1",
    )

    assert envelope.result.status == "compiled"
    assert envelope.result.contract.generated_items[0].unit_id == "U1"


class FormattingTolerantAssemblyCompilerModel(AssemblyCompilerModel):
    async def complete_json(self, role, payload, on_delta=None):
        value = await super().complete_json(role, payload, on_delta)
        value["contract"]["field_anchors"]["title"] = (
            value["contract"]["field_anchors"].pop("/title")
        )
        value["contract"]["field_anchors"]["title"][0]["source_quote"] = (
            "试卷标题：测试卷"
        )
        return value


@pytest.mark.asyncio
async def test_assembly_compiler_accepts_title_alias_and_markdown_formatting() -> None:
    envelope = await PaperAssemblyCompilerAgent(
        FormattingTolerantAssemblyCompilerModel()
    ).compile(
        _CONTEXT,
        assembly_document="**试卷标题**：测试卷\n单元U1选用候选题Q1。",
        candidate_catalog=_CATALOG,
    )

    assert envelope.result.status == "compiled"


class NarrowCandidateAnchorCompilerModel(AssemblyCompilerModel):
    async def complete_json(self, role, payload, on_delta=None):
        value = await super().complete_json(role, payload, on_delta)
        value["contract"]["selected_items"][0]["source_anchors"][0][
            "source_quote"
        ] = "选用候选题Q1"
        return value


@pytest.mark.asyncio
async def test_assembly_compiler_accepts_unit_from_surrounding_source_block() -> None:
    envelope = await PaperAssemblyCompilerAgent(
        NarrowCandidateAnchorCompilerModel()
    ).compile(
        _CONTEXT,
        assembly_document="【试卷标题】测试卷\n单元U1：选用候选题Q1。",
        candidate_catalog=_CATALOG,
    )

    assert envelope.result.status == "compiled"
