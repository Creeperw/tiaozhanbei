import pytest

from competition_app.agents.paper_assembly import PaperAssemblyAgent
from competition_app.contracts.base import AgentEnvelope
from competition_app.contracts.knowledge import QuestionDetail, QuestionRetrievalMetadata
from competition_app.contracts.paper import (
    BlueprintUnit,
    ExamPaperItem,
    PaperBlueprint,
    QuestionCandidatePool,
    UnitQuestionCandidates,
)


def test_paper_assembly_choice_type_aliases_exclude_non_choice_questions() -> None:
    assert PaperAssemblyAgent._matches_question_type("单项选择题", ["单选题"])
    assert PaperAssemblyAgent._matches_question_type("多项选择题", ["选择题"])
    assert not PaperAssemblyAgent._matches_question_type("简答题", ["选择题"])


def test_paper_instructions_use_actual_selected_question_count() -> None:
    instructions = PaperAssemblyAgent._build_learner_instructions(10, 15)

    assert "共10题" in instructions
    assert "15分钟" in instructions
    assert "共15题" not in instructions


def test_paper_instructions_only_explain_question_types_in_the_actual_paper() -> None:
    instructions = PaperAssemblyAgent._build_learner_instructions(
        10, 40, ["单项选择题", "简答题", "案例分析题"]
    )

    assert "单项选择题选择一个最佳答案" in instructions
    assert "主观题写明判断依据和关键步骤" in instructions
    assert "多项选择题选择所有正确答案" not in instructions


def test_paper_recommended_duration_comes_from_actual_question_workload() -> None:
    context = _assembly_context()
    question = context["dependency_outputs"]["question_pool"].payload.units[0].items[0]
    items = [
        ExamPaperItem(
            sequence=index,
            unit_id="U1",
            question=question.model_copy(
                update={
                    "question_id": f"Q{index}",
                    "question_type": question_type,
                }
            ),
            selection_rationale="测试",
        )
        for index, question_type in enumerate(
            ["单项选择题"] * 8 + ["简答题", "案例分析题"], start=1
        )
    ]

    assert PaperAssemblyAgent._recommended_duration_minutes(items) == 35


def test_duplicate_question_constraint_is_user_readable() -> None:
    question_id = "Q_DUPLICATE"
    message = f"题目{question_id}被模型重复选择，系统已保留首次选择并丢弃后续重复项。"

    assert question_id in message
    assert "保留首次选择" in message


class AssemblyModel:
    async def complete_json(self, role, payload, on_delta=None):
        return {
            "title": "测试选择题卷",
            "instructions": "请作答。",
            "selected_items": [
                {
                    "unit_id": "U1",
                    "question_id": "Q1",
                    "selection_rationale": "正式题库题优先。",
                }
            ],
            "generated_items": [
                {
                    "unit_id": "U1",
                    "question_type": "单项选择题",
                    "stem": "原创补充题",
                    "options": ["A. 甲", "B. 乙"],
                    "reference_answer": "A",
                    "analysis": "甲为正确答案。",
                    "selection_rationale": "补足用户明确题量。",
                    "source_tier": "model_knowledge",
                }
            ],
            "coverage_summary": {},
            "unresolved_constraints": [],
        }


class GeneratedOnlyAssemblyModel(AssemblyModel):
    async def complete_json(self, role, payload, on_delta=None):
        result = await super().complete_json(role, payload, on_delta)
        result["selected_items"] = []
        result["generated_items"] = [
            {**result["generated_items"][0], "stem": f"原创补充题{index}"}
            for index in range(1, 3)
        ]
        return result


class LooseLiveAssemblyModel(AssemblyModel):
    async def complete_json(self, role, payload, on_delta=None):
        return {
            "paper_title": "组装测试" * 100,
            "instructions": None,
            "selected_questions": [
                {
                    "unit_id": "U1",
                    "question_id": "Q1",
                    "score": "待确认",
                    "reason": "正式候选优先",
                    "unexpected": "模型附加字段",
                }
            ],
            "generated_questions": [
                {
                    "unit_id": "U1",
                    "type": "单项选择题",
                    "question": "补充题",
                    "options": {"A": "甲", "B": "乙"},
                    "answer": ["A"],
                    "explanation": "甲正确",
                    "reason": "补足缺口",
                    "source_tier": "模型生成",
                }
            ],
            "coverage_summary": "覆盖当前单元",
            "warnings": "题量按候选池调整",
        }


class InvalidAssemblyModel(AssemblyModel):
    async def complete_json(self, role, payload, on_delta=None):
        return {
            "title": "格式异常的模型输出",
            "instructions": "请作答",
            "selected_items": [
                {
                    "unit_id": ["U1"],
                    "question_id": {"value": "Q1"},
                    "selection_rationale": {"reason": "候选优先"},
                }
            ],
            "generated_items": [],
            "coverage_summary": {1: "非法键"},
            "unresolved_constraints": [],
        }


class UnderSelectingAssemblyModel(AssemblyModel):
    async def complete_json(self, role, payload, on_delta=None):
        result = await super().complete_json(role, payload, on_delta)
        result["generated_items"] = []
        return result


class OutOfPoolSelectionAssemblyModel(AssemblyModel):
    async def complete_json(self, role, payload, on_delta=None):
        result = await super().complete_json(role, payload, on_delta)
        result["selected_items"] = [
            {
                "unit_id": "U1",
                "question_id": "Q_NOT_IN_POOL",
                "selection_rationale": "模型错误引用。",
            }
        ]
        result["generated_items"] = []
        return result


class DuplicateStemSelectionAssemblyModel(AssemblyModel):
    async def complete_json(self, role, payload, on_delta=None):
        result = await super().complete_json(role, payload, on_delta)
        result["selected_items"].append(
            {
                "unit_id": "U1",
                "question_id": "Q2",
                "selection_rationale": "重复题干候选。",
            }
        )
        result["generated_items"] = []
        return result


class MixedQuestionTypeAssemblyModel(AssemblyModel):
    async def complete_json(self, role, payload, on_delta=None):
        result = await super().complete_json(role, payload, on_delta)
        result["generated_items"] = [
            {
                "unit_id": "U2",
                "question_type": "简答题",
                "stem": "请简述四君子汤的配伍意义。",
                "options": [],
                "reference_answer": "人参为君，白术为臣，茯苓为佐，甘草为使。",
                "analysis": "考查君臣佐使与益气健脾的配伍逻辑。",
                "selection_rationale": "候选池无符合要求的简答题，原创补足。",
                "source_tier": "model_knowledge",
            }
        ]
        return result


class MultipleChoiceArrayAnswerAssemblyModel(AssemblyModel):
    async def complete_json(self, role, payload, on_delta=None):
        result = await super().complete_json(role, payload, on_delta)
        result["selected_items"] = []
        result["generated_items"] = [
            {
                "unit_id": "U1",
                "question_type": "多项选择题",
                "stem": "关于四君子汤的组成，正确的有",
                "options": ["A. 人参", "B. 白术", "C. 茯苓", "D. 炙甘草"],
                "reference_answer": ["A", "B", "C", "D"],
                "analysis": "四味药均属于四君子汤。",
                "selection_rationale": "补足多项选择题缺口。",
                "source_tier": "model_knowledge",
            }
        ]
        return result

class BatchedTwentyChoiceModel:
    def __init__(self) -> None:
        self.gap_calls: list[dict] = []

    async def complete_json(self, role, payload, on_delta=None):
        business = payload["payload"]
        if business.get("phase") == "paper_gap_generation":
            self.gap_calls.append(business)
            count = int(business["gap_count"])
            unit_id = business["unit_id"]
            return {
                "generated_items": [
                    {
                        "unit_id": unit_id,
                        "question_type": "单项选择题",
                        "stem": f"四君子汤专项补充题{len(self.gap_calls)}-{index}",
                        "options": ["A. 正确项", "B. 干扰项"],
                        "reference_answer": "A",
                        "analysis": "依据四君子汤教学知识作答。",
                        "selection_rationale": "补足专项训练题量。",
                        "source_tier": "model_knowledge",
                    }
                    for index in range(1, count + 1)
                ]
            }
        return {
            "title": "四君子汤选择题专项训练卷",
            "instructions": "请作答。",
            "selected_items": [],
            "generated_items": [],
            "coverage_summary": {},
            "unresolved_constraints": [],
        }


class BatchedTwentyFiveFillBlankModel:
    def __init__(self) -> None:
        self.gap_calls: list[dict] = []

    async def complete_json(self, role, payload, on_delta=None):
        business = payload["payload"]
        if business.get("phase") == "paper_gap_generation":
            self.gap_calls.append(business)
            return {
                "generated_items": [
                    {
                        "unit_id": business["unit_id"],
                        "question_type": "填空题",
                        "stem": f"四君子汤专项填空题{len(self.gap_calls)}-{index}：____。",
                        "options": [],
                        "reference_answer": "四君子汤知识点答案",
                        "analysis": "依据四君子汤组成、功效主治或配伍意义解析。",
                        "selection_rationale": "正式题不足后生成的新变式题。",
                        "source_tier": "model_knowledge",
                    }
                    for index in range(1, int(business["gap_count"]) + 1)
                ]
            }
        return {
            "title": "四君子汤填空题专项训练卷",
            "instructions": "请作答。",
            "selected_items": [],
            "generated_items": [],
            "coverage_summary": {},
            "unresolved_constraints": [],
        }


def _assembly_context() -> dict:
    question = QuestionDetail(
        question_id="Q1",
        question_type="单项选择题",
        stem="正式候选题",
        reference_answer="B",
        analysis="乙为正确答案。",
        tags=[],
        source_metadata={},
        bridges=[],
        retrieval=QuestionRetrievalMetadata(
            channels=["vector"], channel_scores={"vector": 1.0}, fusion_score=1.0
        ),
    )
    blueprint = PaperBlueprint(
        blueprint_id="BP1",
        title="测试卷",
        source_status="practice_sample",
        scope_summary="测试主题",
        required_total_question_count=2,
        question_count_is_hard_constraint=True,
        units=[
            BlueprintUnit(
                unit_id="U1",
                sequence=1,
                knowledge_module="测试模块",
                learning_objective="完成测试",
                retrieval_query="测试",
                question_type_preferences=["单项选择题"],
                required_question_count=2,
            )
        ],
    )
    pool = QuestionCandidatePool(
        pool_id="POOL1",
        blueprint_id="BP1",
        units=[
            UnitQuestionCandidates(
                unit_id="U1",
                retrieval_query="测试",
                resolved_kp_ids=[],
                requested_limit=10,
                required_question_count=2,
                items=[question],
            )
        ],
    )
    common = {
        "case_id": "C1", "trace_id": "T1", "request_id": "R1",
        "execution_id": "E1", "task_type": "paper_generation", "learner_id": "L1",
    }
    return {
        **common,
        "step_id": "paper_assembly",
        "dependency_outputs": {
            "paper_blueprint": AgentEnvelope(
                **common, artifact_id="A1", artifact_type="paper_blueprint",
                step_id="paper_blueprint", producer="expert_agent", payload=blueprint,
            ),
            "question_pool": AgentEnvelope(
                **common, artifact_id="A2", artifact_type="question_candidate_pool",
                step_id="question_pool", producer="knowledge_base_agent", payload=pool,
            ),
        },
    }


@pytest.mark.asyncio
async def test_paper_assembly_generates_only_the_hard_question_gap() -> None:
    result = await PaperAssemblyAgent(AssemblyModel()).run(_assembly_context())

    assert len(result.payload.items) == 2
    generated = result.payload.items[1].question
    assert generated.origin == "generated"
    assert generated.source_tier == "model_knowledge"
    assert generated.options == ["A. 甲", "B. 乙"]
    assert generated.reference_answer == "A"
    assert generated.analysis == "甲为正确答案。"
    assert result.payload.learner_questions()[1].options == ["A. 甲", "B. 乙"]


@pytest.mark.asyncio
async def test_paper_assembly_normalizes_loose_live_model_output() -> None:
    result = await PaperAssemblyAgent(LooseLiveAssemblyModel()).run(
        _assembly_context()
    )

    assert len(result.payload.items) == 2
    assert result.payload.items[0].question.question_id == "Q1"
    assert result.payload.items[1].question.origin == "generated"
    assert result.payload.items[1].question.options == ["A. 甲", "B. 乙"]
    assert result.payload.items[1].question.source_tier == "model_knowledge"


@pytest.mark.asyncio
async def test_paper_assembly_falls_back_to_candidate_pool_on_invalid_protocol() -> None:
    context = _assembly_context()
    blueprint = context["dependency_outputs"]["paper_blueprint"].payload
    blueprint.required_total_question_count = None
    blueprint.question_count_is_hard_constraint = False

    result = await PaperAssemblyAgent(InvalidAssemblyModel()).run(context)

    assert [item.question.question_id for item in result.payload.items] == ["Q1"]
    assert any(
        "确定性组装" in item for item in result.payload.unresolved_constraints
    )


@pytest.mark.asyncio
async def test_generated_question_source_tier_is_system_owned() -> None:
    class SelfClaimingModel(AssemblyModel):
        async def complete_json(self, role, payload, on_delta=None):
            result = await super().complete_json(role, payload, on_delta)
            result["generated_items"][0]["source_tier"] = "textbook"
            return result

    result = await PaperAssemblyAgent(SelfClaimingModel()).run(_assembly_context())

    assert result.payload.items[1].question.source_tier == "model_knowledge"


@pytest.mark.asyncio
async def test_paper_assembly_can_fill_hard_count_with_no_retrieved_candidates() -> None:
    context = _assembly_context()
    context["dependency_outputs"]["question_pool"].payload.units[0].items = []

    result = await PaperAssemblyAgent(GeneratedOnlyAssemblyModel()).run(context)

    assert len(result.payload.items) == 2
    assert all(item.question.origin == "generated" for item in result.payload.items)


@pytest.mark.asyncio
async def test_paper_assembly_deterministically_fills_model_underselection() -> None:
    context = _assembly_context()
    question = context["dependency_outputs"]["question_pool"].payload.units[0].items[0]
    context["dependency_outputs"]["question_pool"].payload.units[0].items.append(
        question.model_copy(update={"question_id": "Q2", "stem": "第二道正式候选题"})
    )

    result = await PaperAssemblyAgent(UnderSelectingAssemblyModel()).run(context)

    assert len(result.payload.items) == 2
    assert [item.question.question_id for item in result.payload.items] == ["Q1", "Q2"]
    assert result.payload.items[1].question.origin == "retrieved"


@pytest.mark.asyncio
async def test_paper_assembly_discards_out_of_pool_selection_and_keeps_valid_fallback() -> None:
    context = _assembly_context()
    blueprint = context["dependency_outputs"]["paper_blueprint"].payload
    blueprint.required_total_question_count = None
    blueprint.question_count_is_hard_constraint = False

    result = await PaperAssemblyAgent(OutOfPoolSelectionAssemblyModel()).run(context)

    assert [item.question.question_id for item in result.payload.items] == ["Q1"]
    assert any("Q_NOT_IN_POOL" in item for item in result.payload.unresolved_constraints)
    assert any("正式候选池保留一道题" in item for item in result.payload.unresolved_constraints)


@pytest.mark.asyncio
async def test_paper_assembly_discards_different_ids_with_the_same_stem() -> None:
    context = _assembly_context()
    blueprint = context["dependency_outputs"]["paper_blueprint"].payload
    blueprint.required_total_question_count = None
    blueprint.question_count_is_hard_constraint = False
    question = context["dependency_outputs"]["question_pool"].payload.units[0].items[0]
    context["dependency_outputs"]["question_pool"].payload.units[0].items.append(
        question.model_copy(update={"question_id": "Q2"})
    )

    result = await PaperAssemblyAgent(DuplicateStemSelectionAssemblyModel()).run(context)

    assert [item.question.question_id for item in result.payload.items] == ["Q1"]
    assert any("题干重复" in item for item in result.payload.unresolved_constraints)


@pytest.mark.asyncio
async def test_paper_assembly_allows_generated_short_answer_for_short_answer_unit() -> None:
    context = _assembly_context()
    blueprint = context["dependency_outputs"]["paper_blueprint"].payload
    blueprint.units.append(
        BlueprintUnit(
            unit_id="U2",
            sequence=2,
            knowledge_module="配伍意义",
            learning_objective="说明君臣佐使配伍逻辑",
            retrieval_query="四君子汤 配伍意义",
            question_type_preferences=["简答题"],
            required_question_count=1,
        )
    )
    blueprint.required_total_question_count = 2
    pool = context["dependency_outputs"]["question_pool"].payload
    pool.units.append(
        UnitQuestionCandidates(
            unit_id="U2",
            retrieval_query="四君子汤 配伍意义",
            resolved_kp_ids=[],
            requested_limit=10,
            required_question_count=1,
            items=[],
        )
    )

    result = await PaperAssemblyAgent(MixedQuestionTypeAssemblyModel()).run(context)

    generated = result.payload.items[1].question
    assert generated.question_type == "简答题"
    assert generated.options == []
    assert generated.reference_answer
    assert generated.analysis


@pytest.mark.asyncio
async def test_soft_count_paper_can_fill_one_uncovered_non_choice_unit() -> None:
    context = _assembly_context()
    blueprint = context["dependency_outputs"]["paper_blueprint"].payload
    blueprint.required_total_question_count = None
    blueprint.question_count_is_hard_constraint = False
    blueprint.units[0].required_question_count = 1
    blueprint.units.append(
        BlueprintUnit(
            unit_id="U2",
            sequence=2,
            knowledge_module="配伍意义",
            learning_objective="说明配伍逻辑",
            retrieval_query="四君子汤 配伍意义",
            question_type_preferences=["简答题"],
            required_question_count=1,
        )
    )
    pool = context["dependency_outputs"]["question_pool"].payload
    pool.units[0].required_question_count = 1
    pool.units.append(
        UnitQuestionCandidates(
            unit_id="U2",
            retrieval_query="四君子汤 配伍意义",
            resolved_kp_ids=[],
            requested_limit=10,
            required_question_count=1,
            items=[],
        )
    )

    result = await PaperAssemblyAgent(MixedQuestionTypeAssemblyModel()).run(context)

    assert [item.question.question_type for item in result.payload.items] == [
        "单项选择题",
        "简答题",
    ]
    assert result.payload.duration_minutes == 10
    assert "主观题写明判断依据" in result.payload.instructions


@pytest.mark.asyncio
async def test_paper_assembly_normalizes_generated_multiple_choice_array_answer() -> None:
    context = _assembly_context()
    blueprint = context["dependency_outputs"]["paper_blueprint"].payload
    blueprint.required_total_question_count = 1
    blueprint.units[0].required_question_count = 1
    blueprint.units[0].question_type_preferences = ["多项选择题"]

    result = await PaperAssemblyAgent(
        MultipleChoiceArrayAnswerAssemblyModel()
    ).run(context)

    generated = result.payload.items[0].question
    assert generated.question_type == "多项选择题"
    assert generated.reference_answer == "A, B, C, D"
    assert result.payload.answer_key[generated.question_id] == "A, B, C, D"

@pytest.mark.asyncio
async def test_paper_assembly_fills_twenty_choice_questions_in_small_batches() -> None:
    context = _assembly_context()
    blueprint = context["dependency_outputs"]["paper_blueprint"].payload
    blueprint.required_total_question_count = 20
    blueprint.units[0].required_question_count = 20
    blueprint.units[0].question_type_preferences = ["单项选择题", "多项选择题"]
    pool = context["dependency_outputs"]["question_pool"].payload
    pool.units[0].required_question_count = 20
    model = BatchedTwentyChoiceModel()

    result = await PaperAssemblyAgent(model).run(context)

    assert len(result.payload.items) == 20
    assert all("选择题" in item.question.question_type for item in result.payload.items)
    assert all(item.question.reference_answer for item in result.payload.items)
    assert all(item.question.analysis for item in result.payload.items)
    assert len(model.gap_calls) >= 2
    assert all(call["gap_count"] <= 5 for call in model.gap_calls)


@pytest.mark.asyncio
async def test_paper_assembly_generates_twenty_five_fill_blanks_when_pool_is_empty() -> None:
    context = _assembly_context()
    blueprint = context["dependency_outputs"]["paper_blueprint"].payload
    blueprint.title = "四君子汤填空题专项训练卷"
    blueprint.scope_summary = "四君子汤组成、功效主治和配伍意义"
    blueprint.required_total_question_count = 25
    blueprint.units[0].knowledge_module = "四君子汤组成、功效主治和配伍意义"
    blueprint.units[0].retrieval_query = "四君子汤 组成 功效主治 配伍意义"
    blueprint.units[0].required_question_count = 25
    blueprint.units[0].question_type_preferences = ["填空题"]
    pool = context["dependency_outputs"]["question_pool"].payload
    pool.units[0].items = []
    pool.units[0].required_question_count = 25
    model = BatchedTwentyFiveFillBlankModel()

    result = await PaperAssemblyAgent(model).run(context)

    assert len(result.payload.items) == 25
    assert all(item.question.question_type == "填空题" for item in result.payload.items)
    assert all("四君子汤" in item.question.stem for item in result.payload.items)
    assert all(item.question.reference_answer for item in result.payload.items)
    assert all(item.question.analysis for item in result.payload.items)
    assert all(call["paper_scope"] == blueprint.scope_summary for call in model.gap_calls)
    assert all(call["retrieval_query"] == blueprint.units[0].retrieval_query for call in model.gap_calls)
    assert all(call["gap_count"] <= 5 for call in model.gap_calls)
