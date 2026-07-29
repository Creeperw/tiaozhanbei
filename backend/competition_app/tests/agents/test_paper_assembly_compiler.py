import pytest

from competition_app.agents.paper_assembly_compiler import PaperAssemblyCompilerAgent


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
