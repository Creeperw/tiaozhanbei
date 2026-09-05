import pytest

from competition_app.agents.audit import AuditAgent
from competition_app.contracts.base import AgentEnvelope
from competition_app.contracts.knowledge import QuestionDetail, QuestionRetrievalMetadata
from competition_app.contracts.paper import (
    BlueprintUnit,
    ExamPaperDraft,
    ExamPaperItem,
    PaperBlueprint,
    PaperDifficultySourceSummary,
    QuestionCandidatePool,
    UnitQuestionCandidates,
)
from competition_app.llm.openai_compatible import ModelResponseError


class PassingAuditModel:
    async def complete_json(self, role, payload, on_delta=None):
        if role == "paper_audit_findings_compiler":
            return {"status": "compiled", "contract_version": "1.0", "issues": []}
        return {
            "decision": "pass",
            "findings": [],
            "audit_report": "试卷蓝图、题目、答案与解析均已核验通过。",
        }


class RevisingAuditModel:
    async def complete_json(self, role, payload, on_delta=None):
        if role == "paper_audit_findings_compiler":
            return {
                "status": "compiled",
                "contract_version": "1.0",
                "issues": [
                    {
                        "issue_type": "content_quality",
                        "message": "存在可进一步优化的知识覆盖表达。",
                        "blocking": False,
                        "source_anchors": [
                            {
                                "source_field": "findings",
                                "source_quote": "存在可进一步优化的知识覆盖表达。",
                            }
                        ],
                    }
                ],
            }
        return {
            "decision": "revise",
            "findings": ["存在可进一步优化的知识覆盖表达。"],
            "audit_report": "试卷可发布，但知识覆盖表达仍可优化。",
        }


class PaperCompilerTransportFailureModel:
    async def complete_json(self, role, payload, on_delta=None):
        if role == "paper_audit_findings_compiler":
            raise ModelResponseError(
                "paper compiler transport failed",
                reason="transport_error",
                failover_eligible=True,
            )
        return {
            "decision": "revise",
            "findings": ["题1与题2考查内容重复，需要重新定位后返修。"],
            "audit_report": "试卷存在重复题，需要修订。",
        }


class PaperCompilerFormatDriftModel:
    async def complete_json(self, role, payload, on_delta=None):
        if role == "paper_audit_findings_compiler":
            return {
                "status": "needs_revision",
                "contract_version": "1.0",
                "issues": [
                    {
                        "code": "source_anchor_missing",
                        "field_path": "/issues/0",
                    }
                ],
            }
        return {
            "decision": "revise",
            "findings": ["题干直接泄露答案，需要改写后重新审核。"],
            "audit_report": "题干存在可局部修订的质量问题。",
        }


class InvalidAuditModel:
    async def complete_json(self, role, payload, on_delta=None):
        if role == "paper_audit_findings_compiler":
            return {"status": "compiled", "contract_version": "1.0", "issues": []}
        return {"result": "试卷整体可用"}


class ContradictoryPassingAuditModel:
    async def complete_json(self, role, payload, on_delta=None):
        if role == "paper_audit_findings_compiler":
            return {
                "status": "compiled",
                "contract_version": "1.0",
                "issues": [
                    {
                        "issue_type": "content_quality",
                        "message": "题1与题2考查内容重复，违反去重约束。",
                        "blocking": True,
                        "source_anchors": [
                            {
                                "source_field": "findings",
                                "source_quote": "题1与题2考查内容重复，违反去重约束。",
                            }
                        ],
                    }
                ],
            }
        return {
            "decision": "pass",
            "findings": [
                "题1与题2考查内容重复，违反去重约束。",
                "修改要求：请替换题2后重新审核。",
            ],
            "audit_report": "试卷存在重复题，必须修订后重新审核。",
        }


class NativeOffTopicAuditModel:
    async def complete_json(self, role, payload, on_delta=None):
        return {
            # Even a contradictory top-level pass cannot override a concrete,
            # system-located semantic fault.
            "decision": "pass",
            "findings": ["试卷题目Q1偏离当前蓝图单元，应定点替换后复审。"],
            "structured_findings": [
                {
                    "issue_type": "paper_blueprint_mismatch",
                    "message": "试卷题目Q1偏离当前蓝图单元，应定点替换后复审。",
                    "blocking": False,
                    "location_keys": ["paper:question:Q1"],
                }
            ],
            "audit_report": "题目Q1考查范围与当前蓝图单元不一致。",
        }


class CapturingAuditModel(PassingAuditModel):
    def __init__(self):
        self.audit_payload = None

    async def complete_json(self, role, payload, on_delta=None):
        if role == "audit_agent":
            self.audit_payload = payload
        return await super().complete_json(role, payload, on_delta=on_delta)


def test_paper_audit_uses_the_same_subjective_question_aliases_as_assembly() -> None:
    assert AuditAgent._matches_question_type("临床案例问答", ["简答题"])
    assert AuditAgent._matches_question_type("病例分析_实践技能", ["病例分析题"])


def _question(question_id: str) -> QuestionDetail:
    return QuestionDetail(
        question_id=question_id,
        question_type="单项选择题",
        stem=f"题干{question_id}",
        reference_answer="A",
        analysis="解析",
        options=["A. 正确", "B. 错误"],
        tags=[],
        source_metadata={},
        bridges=[],
        retrieval=QuestionRetrievalMetadata(
            channels=["vector"], channel_scores={"vector": 1.0}, fusion_score=1.0
        ),
    )


def _envelope(step_id: str, artifact_type: str, payload):
    return AgentEnvelope(
        artifact_id=f"A_{step_id}", artifact_type=artifact_type, case_id="C1",
        trace_id="T1", request_id="R1", execution_id="E1", step_id=step_id,
        producer="test", task_type="paper_generation", learner_id="L1", payload=payload,
    )


def _audit_context(actual_count: int, required_count: int = 20) -> dict:
    questions = [_question(f"Q{index}") for index in range(1, actual_count + 1)]
    blueprint = PaperBlueprint(
        blueprint_id="BP1", title="测试卷", source_status="practice_sample",
        scope_summary="测试", required_total_question_count=required_count,
        question_count_is_hard_constraint=True,
        units=[
            BlueprintUnit(
                unit_id="U1", sequence=1, knowledge_module="测试",
                learning_objective="测试", retrieval_query="测试",
                question_type_preferences=["单项选择题"],
                required_question_count=required_count,
            )
        ],
    )
    pool = QuestionCandidatePool(
        pool_id="P1", blueprint_id="BP1",
        units=[
            UnitQuestionCandidates(
                unit_id="U1", retrieval_query="测试", resolved_kp_ids=[],
                requested_limit=max(1, actual_count), required_question_count=required_count,
                items=questions,
            )
        ],
    )
    items = [
        ExamPaperItem(
            sequence=index, unit_id="U1", question=question,
            selection_rationale="测试",
        )
        for index, question in enumerate(questions, start=1)
    ]
    paper = ExamPaperDraft(
        paper_draft_id="D1", blueprint_id="BP1", candidate_pool_id="P1",
        title="测试卷", instructions="请作答。", items=items,
        answer_key={question.question_id: "A" for question in questions},
        explanations={question.question_id: "解析" for question in questions},
    )
    return {
        "case_id": "C1", "trace_id": "T1", "request_id": "R1",
        "execution_id": "E1", "step_id": "audit", "learner_id": "L1",
        "task_type": "paper_generation",
        "dependency_outputs": {
            "paper_blueprint": _envelope("paper_blueprint", "paper_blueprint", blueprint),
            "question_pool": _envelope("question_pool", "question_candidate_pool", pool),
            "paper_assembly": _envelope("paper_assembly", "exam_paper_draft", paper),
        },
    }


@pytest.mark.asyncio
async def test_paper_audit_revises_when_hard_question_count_is_short() -> None:
    result = await AuditAgent(PassingAuditModel()).run(_audit_context(19))

    assert result.payload.decision == "revise"
    assert any("20题" in finding and "19题" in finding for finding in result.payload.findings)
    issue = result.payload.structured_findings[0]
    assert issue.issue_type == "paper_item_invalid"
    assert issue.owner_step_id == "paper_assembly"
    assert issue.affected_step_ids == ["paper_assembly"]
    assert issue.locations[0].location_key == "paper:whole"


@pytest.mark.asyncio
async def test_paper_audit_passes_when_hard_question_count_is_met() -> None:
    result = await AuditAgent(PassingAuditModel()).run(_audit_context(20))

    assert result.payload.decision == "pass"


@pytest.mark.asyncio
async def test_paper_audit_passes_empty_state_paper_without_model_call() -> None:
    """空态占位卷直接确定性放行，不进入模型审核，也不当审核故障处理。"""
    context = _audit_context(0, required_count=2)
    paper = context["dependency_outputs"]["paper_assembly"].payload
    paper.empty_reason = "暂未找到与当前学习范围匹配的题目；请换个知识点或稍后再试。"

    result = await AuditAgent(PassingAuditModel()).run(context)

    assert result.payload.decision == "pass"
    assert "空态" in result.payload.audit_report
    assert "暂未找到与当前学习范围匹配的题目" in result.payload.audit_report


@pytest.mark.asyncio
async def test_paper_audit_excludes_large_retrieval_metadata_from_model_context() -> None:
    context = _audit_context(1, required_count=1)
    paper = context["dependency_outputs"]["paper_assembly"].payload
    paper.items[0].question.source_metadata = {
        "raw_retrieval_payload": "x" * 200_000,
    }
    model = CapturingAuditModel()

    result = await AuditAgent(model).run(context)

    assert result.payload.decision == "pass"
    serialized = str(model.audit_payload)
    assert "raw_retrieval_payload" not in serialized
    assert "题干Q1" in serialized
    assert len(serialized) < 25_000


@pytest.mark.asyncio
async def test_paper_audit_does_not_release_contradictory_blocking_findings() -> None:
    """模型顶层声明 pass 不能覆盖其明确给出的阻断问题。"""
    result = await AuditAgent(ContradictoryPassingAuditModel()).run(
        _audit_context(2, required_count=2)
    )

    assert result.payload.decision == "revise"
    assert any("违反去重约束" in finding for finding in result.payload.findings)
    assert result.payload.structured_findings


@pytest.mark.asyncio
async def test_paper_audit_blocks_located_off_topic_question_for_local_repair() -> None:
    result = await AuditAgent(NativeOffTopicAuditModel()).run(
        _audit_context(2, required_count=2)
    )

    assert result.payload.decision == "revise"
    assert len(result.payload.structured_findings) == 1
    issue = result.payload.structured_findings[0]
    assert issue.issue_type == "paper_blueprint_mismatch"
    assert issue.blocking is True
    assert issue.owner_step_id == "paper_assembly"
    assert [location.location_key for location in issue.locations] == [
        "paper:question:Q1"
    ]


@pytest.mark.asyncio
async def test_paper_audit_missing_explanation_is_not_blocking() -> None:
    """当前对解析不做特殊要求：正式题缺解析不阻止发布，只要答案齐全。"""
    context = _audit_context(2, required_count=2)
    paper = context["dependency_outputs"]["paper_assembly"].payload
    paper.explanations["Q1"] = None

    result = await AuditAgent(PassingAuditModel()).run(context)

    assert result.payload.decision == "pass"
    assert not any("缺少解析" in finding for finding in result.payload.findings)


@pytest.mark.asyncio
async def test_paper_audit_revises_when_any_selected_question_lacks_answer() -> None:
    context = _audit_context(2, required_count=2)
    paper = context["dependency_outputs"]["paper_assembly"].payload
    paper.answer_key["Q2"] = ""

    result = await AuditAgent(PassingAuditModel()).run(context)

    assert result.payload.decision == "revise"
    assert any("缺少标准答案" in finding and "Q2" in finding for finding in result.payload.findings)


@pytest.mark.asyncio
async def test_paper_audit_revises_wrong_exact_type_distribution() -> None:
    context = _audit_context(15, required_count=15)
    blueprint = context["dependency_outputs"]["paper_blueprint"].payload
    blueprint.required_question_type_distribution = {
        "单项选择题": 10,
        "多项选择题": 5,
    }
    paper = context["dependency_outputs"]["paper_assembly"].payload
    for item in paper.items[-3:]:
        item.question.question_type = "多项选择题"

    result = await AuditAgent(PassingAuditModel()).run(context)

    assert result.payload.decision == "revise"
    assert any("题型分布" in finding for finding in result.payload.findings)


@pytest.mark.asyncio
async def test_paper_audit_accepts_generated_short_answer_without_options() -> None:
    context = _audit_context(1, required_count=2)
    blueprint = context["dependency_outputs"]["paper_blueprint"].payload
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
    generated = QuestionDetail(
        question_id="GENERATED_SHORT_ANSWER",
        question_type="简答题",
        stem="请简述四君子汤的配伍意义。",
        reference_answer="人参为君，白术为臣，茯苓为佐，甘草为使。",
        analysis="考查君臣佐使的配伍逻辑。",
        options=[],
        origin="generated",
        source_tier="model_knowledge",
        tags=[],
        source_metadata={},
        bridges=[],
        retrieval=QuestionRetrievalMetadata(
            channels=[], channel_scores={}, fusion_score=0.0
        ),
    )
    paper = context["dependency_outputs"]["paper_assembly"].payload
    paper.items.append(
        ExamPaperItem(
            sequence=2,
            unit_id="U2",
            question=generated,
            selection_rationale="补足简答题单元。",
        )
    )
    paper.answer_key[generated.question_id] = generated.reference_answer
    paper.explanations[generated.question_id] = generated.analysis

    result = await AuditAgent(PassingAuditModel()).run(context)

    assert result.payload.decision == "pass"
    assert not any("缺少选项" in finding for finding in result.payload.findings)


@pytest.mark.asyncio
async def test_paper_audit_does_not_loop_on_soft_revision_after_one_repair() -> None:
    context = _audit_context(2, required_count=2)
    context["audit_feedback"] = _envelope(
        "audit_previous",
        "audit_result",
        {"decision": "revise", "findings": ["请优化知识覆盖表达。"]},
    )

    result = await AuditAgent(RevisingAuditModel()).run(context)

    assert result.payload.decision == "pass"
    assert any("非阻断建议" in finding for finding in result.payload.findings)


@pytest.mark.asyncio
async def test_paper_audit_does_not_rebuild_valid_paper_for_model_only_advice() -> None:
    result = await AuditAgent(RevisingAuditModel()).run(
        _audit_context(2, required_count=2)
    )

    assert result.payload.decision == "pass"
    assert any("优化" in finding for finding in result.payload.findings)


@pytest.mark.asyncio
async def test_paper_compiler_transport_failure_is_retryable() -> None:
    with pytest.raises(ModelResponseError) as exc_info:
        await AuditAgent(PaperCompilerTransportFailureModel()).run(
            _audit_context(2, required_count=2)
        )

    assert exc_info.value.reason == "transport_error"


@pytest.mark.asyncio
async def test_paper_compiler_format_drift_uses_source_bound_local_repair() -> None:
    """compiler 格式漂移时仍用原始 finding 安全触发局部返修。"""
    result = await AuditAgent(PaperCompilerFormatDriftModel()).run(
        _audit_context(2, required_count=2)
    )

    assert result.payload.decision == "revise"
    assert any(
        "题干直接泄露答案" in finding
        for finding in result.payload.findings
    )


@pytest.mark.asyncio
async def test_valid_paper_is_not_released_when_audit_format_drifts() -> None:
    result = await AuditAgent(InvalidAuditModel()).run(
        _audit_context(2, required_count=2)
    )

    assert result.payload.decision == "needs_human_review"
    assert any("格式" in finding for finding in result.payload.findings)


@pytest.mark.asyncio
async def test_paper_audit_revises_when_generated_question_carries_fabricated_difficulty() -> None:
    context = _audit_context(2, required_count=2)
    blueprint = context["dependency_outputs"]["paper_blueprint"].payload
    blueprint.units[0].target_difficulty = 3
    blueprint.units[0].difficulty_is_hard_constraint = True
    paper = context["dependency_outputs"]["paper_assembly"].payload
    paper.items[0].question = paper.items[0].question.model_copy(
        update={"origin": "generated", "difficulty": 3}
    )

    result = await AuditAgent(PassingAuditModel()).run(context)

    assert result.payload.decision == "revise"
    assert any("难度标注" in finding for finding in result.payload.findings)


@pytest.mark.asyncio
async def test_paper_audit_revises_when_selected_question_violates_hard_difficulty() -> None:
    context = _audit_context(2, required_count=2)
    blueprint = context["dependency_outputs"]["paper_blueprint"].payload
    blueprint.units[0].target_difficulty = 3
    blueprint.units[0].difficulty_is_hard_constraint = True
    paper = context["dependency_outputs"]["paper_assembly"].payload
    paper.items[0].question = paper.items[0].question.model_copy(
        update={"origin": "formal", "difficulty": 5}
    )

    result = await AuditAgent(PassingAuditModel()).run(context)

    assert result.payload.decision == "revise"
    assert any("难度3" in finding and "其他难度" in finding for finding in result.payload.findings)


@pytest.mark.asyncio
async def test_paper_audit_passes_legitimate_difficulty_downgrade_with_disclosure() -> None:
    context = _audit_context(2, required_count=2)
    blueprint = context["dependency_outputs"]["paper_blueprint"].payload
    blueprint.units[0].target_difficulty = 3
    blueprint.units[0].difficulty_is_hard_constraint = True
    paper = context["dependency_outputs"]["paper_assembly"].payload
    paper.items[0].question = paper.items[0].question.model_copy(
        update={"origin": "formal", "difficulty": 3}
    )
    paper.items[1].question = paper.items[1].question.model_copy(
        update={"origin": "formal", "difficulty": None}
    )
    paper.difficulty_source_summary = PaperDifficultySourceSummary(
        target_difficulty=3,
        difficulty_is_hard_constraint=True,
        total_questions=2,
        exact_difficulty_count=1,
        unlabeled_official_count=1,
        notice="本卷共2题；其中难度3的正式题1道；未标注难度的正式题1道。",
    )

    result = await AuditAgent(PassingAuditModel()).run(context)

    assert result.payload.decision == "pass"


class RecordingAuditModel(PassingAuditModel):
    def __init__(self):
        self.audit_payloads = []

    async def complete_json(self, role, payload, on_delta=None):
        if role == "audit_agent":
            self.audit_payloads.append(payload)
        return await super().complete_json(role, payload, on_delta=on_delta)


class MixedAuditModel:
    """U1 passes; U2 replies with an invalid payload (unit-local drift)."""

    async def complete_json(self, role, payload, on_delta=None):
        if role == "paper_audit_findings_compiler":
            return {"status": "compiled", "contract_version": "1.0", "issues": []}
        if role == "audit_agent":
            unit_id = payload["payload"]["paper_blueprint"]["unit"]["unit_id"]
            if unit_id == "U2":
                return {"result": "本单元审核结果"}
            return {
                "decision": "pass",
                "findings": [],
                "audit_report": "本单元审核通过。",
            }
        return {"decision": "pass", "findings": [], "audit_report": "通过。"}


class MultiUnitRevisingAuditModel:
    """U1 passes; U2 reports a blocking finding."""

    async def complete_json(self, role, payload, on_delta=None):
        if role == "paper_audit_findings_compiler":
            return {
                "status": "compiled",
                "contract_version": "1.0",
                "issues": [
                    {
                        "issue_type": "content_quality",
                        "message": "单元2存在知识覆盖问题。",
                        "blocking": True,
                        "source_anchors": [
                            {
                                "source_field": "findings",
                                "source_quote": "单元2存在知识覆盖问题。",
                            }
                        ],
                    }
                ],
            }
        if role == "audit_agent":
            unit_id = payload["payload"]["paper_blueprint"]["unit"]["unit_id"]
            if unit_id == "U2":
                return {
                    "decision": "revise",
                    "findings": ["单元2存在知识覆盖问题。"],
                    "audit_report": "单元2需要修订。",
                }
            return {
                "decision": "pass",
                "findings": [],
                "audit_report": "单元1审核通过。",
            }
        return {"decision": "pass", "findings": [], "audit_report": "通过。"}


class NativeStructuredAuditModel:
    def __init__(self) -> None:
        self.compiler_calls = 0

    async def complete_json(self, role, payload, on_delta=None):
        if role == "paper_audit_findings_compiler":
            self.compiler_calls += 1
            raise AssertionError("原生结构化问题不得再次调用旧 findings compiler")
        if role == "audit_agent":
            question_id = payload["payload"]["exam_paper"]["items"][0][
                "question"
            ]["question_id"]
            return {
                "decision": "revise",
                "findings": [],
                "structured_findings": [
                    {
                        "issue_type": "factual_error",
                        "message": "答案与解析的论证不一致。",
                        "blocking": True,
                        "location_keys": [f"paper:explanation:{question_id}"],
                    }
                ],
                "audit_report": "当前题目的解析需要修订。",
            }
        return {"decision": "pass", "findings": [], "audit_report": "通过。"}


def _multi_unit_context() -> dict:
    """A three-unit paper: U1 (2 questions), U2 and U3 (1 question each)."""
    context = _audit_context(2, required_count=4)
    blueprint = context["dependency_outputs"]["paper_blueprint"].payload
    pool = context["dependency_outputs"]["question_pool"].payload
    paper = context["dependency_outputs"]["paper_assembly"].payload
    for unit_id, module, query in (
        ("U2", "配伍意义", "四君子汤 配伍意义"),
        ("U3", "方剂组成", "六味地黄丸 组成"),
    ):
        question = _question(f"Q{unit_id}")
        blueprint.units.append(
            BlueprintUnit(
                unit_id=unit_id,
                sequence=len(blueprint.units) + 1,
                knowledge_module=module,
                learning_objective=f"掌握{module}",
                retrieval_query=query,
                question_type_preferences=["单项选择题"],
                required_question_count=1,
            )
        )
        pool.units.append(
            UnitQuestionCandidates(
                unit_id=unit_id,
                retrieval_query=query,
                resolved_kp_ids=[],
                requested_limit=1,
                required_question_count=1,
                items=[question],
            )
        )
        paper.items.append(
            ExamPaperItem(
                sequence=len(paper.items) + 1,
                unit_id=unit_id,
                question=question,
                selection_rationale=f"满足{module}单元。",
            )
        )
        paper.answer_key[question.question_id] = "A"
        paper.explanations[question.question_id] = "解析"
    return context


@pytest.mark.asyncio
async def test_paper_audit_keeps_bounded_question_analysis_but_not_duplicate_map() -> None:
    context = _audit_context(1, required_count=1)
    model = CapturingAuditModel()

    result = await AuditAgent(model).run(context)

    assert result.payload.decision == "pass"
    exam_paper = model.audit_payload["payload"]["exam_paper"]
    assert "explanations" not in exam_paper
    assert exam_paper["items"][0]["question"]["analysis"] == "解析"
    assert exam_paper["items"][0]["question"]["stem"] == "题干Q1"
    assert "selection_rationale" in exam_paper["items"][0]


@pytest.mark.asyncio
async def test_unit_paper_audit_omits_whole_paper_difficulty_disclosure() -> None:
    context = _audit_context(1, required_count=1)
    paper = context["dependency_outputs"]["paper_assembly"].payload
    paper.items[0].question = paper.items[0].question.model_copy(
        update={
            "origin": "generated",
            "source_tier": "model_knowledge",
            "difficulty": None,
        }
    )
    paper.difficulty_source_summary = PaperDifficultySourceSummary(
        target_difficulty=2,
        difficulty_is_hard_constraint=True,
        total_questions=1,
        generated_count=1,
        notice=(
            "本卷共1题；系统生成的补充题1道。"
            "补充题没有真实难度标注，系统不会将其伪装为指定难度。"
        ),
    )
    model = CapturingAuditModel()

    result = await AuditAgent(model).run(context)

    assert result.payload.decision == "pass"
    exam_paper = model.audit_payload["payload"]["exam_paper"]
    question = exam_paper["items"][0]["question"]
    assert question["source_tier"] == "model_knowledge"
    assert question["difficulty"] is None
    assert "difficulty_source_summary" not in exam_paper


@pytest.mark.asyncio
async def test_paper_audit_splits_multi_unit_paper_across_unit_scoped_calls() -> None:
    context = _multi_unit_context()
    model = RecordingAuditModel()

    result = await AuditAgent(model).run(context)

    assert result.payload.decision == "pass"
    assert len(model.audit_payloads) == 3
    blueprint = context["dependency_outputs"]["paper_blueprint"].payload
    for payload, unit in zip(model.audit_payloads, blueprint.units):
        inner = payload["payload"]
        items = inner["exam_paper"]["items"]
        assert items, "每个单元的审核请求必须携带该单元的入卷题目"
        assert all(item["unit_id"] == unit.unit_id for item in items)
        candidate_units = inner["candidate_pool_summary"]
        assert len(candidate_units) == 1
        assert candidate_units[0]["unit_id"] == unit.unit_id
        assert "warnings" not in candidate_units[0]
        sibling_ids = {
            sibling["unit_id"]
            for sibling in inner["paper_blueprint"]["sibling_units"]
        }
        assert unit.unit_id not in sibling_ids
        assert len(sibling_ids) == 2
        # 单元级裁剪必须自洽，避免审核模型看到整卷声明与本单元题量错配
        exam_paper = inner["exam_paper"]
        assert "整卷共 4 题" in exam_paper["instructions"]
        assert exam_paper["answer_key"], "裁剪后仍应保留本单元的答案键"
        assert set(exam_paper["answer_key"].keys()) == {
            item["question"]["question_id"] for item in items
        }, "答案键只能保留本单元的题目"
        scope = exam_paper["audit_scope"]
        assert scope["unit_id"] == unit.unit_id
        assert scope["unit_question_count"] == len(items)
        assert scope["whole_paper_question_count"] == 4
        assert "coverage_summary" not in exam_paper
        assert "final_coverage_summary" not in exam_paper
        assert "unresolved_constraints" not in exam_paper
        assert "difficulty_source_summary" not in exam_paper
        assert "total_score" not in exam_paper
    assert "单元1" in result.payload.audit_report
    assert "单元2" in result.payload.audit_report
    assert "单元3" in result.payload.audit_report


@pytest.mark.asyncio
async def test_paper_audit_aggregates_revise_over_pass_with_unit_prefix() -> None:
    result = await AuditAgent(MultiUnitRevisingAuditModel()).run(
        _multi_unit_context()
    )

    # 单元2 明确给出了 blocking=true 且要求 revise。即使兼容编译器将其
    # 归入 content_quality，也不能再被全局降级后直接发布。
    assert result.payload.decision == "revise"
    assert any(
        "[单元2" in finding and "知识覆盖" in finding
        for finding in result.payload.findings
    )
    assert result.payload.structured_findings


@pytest.mark.asyncio
async def test_native_structured_paper_issue_binds_system_location() -> None:
    model = NativeStructuredAuditModel()

    result = await AuditAgent(model).run(_audit_context(1, required_count=1))

    assert result.payload.decision == "revise"
    assert model.compiler_calls == 0
    issue = result.payload.structured_findings[0]
    assert issue.issue_type == "factual_error"
    assert issue.owner_step_id == "paper_assembly"
    assert [location.location_key for location in issue.locations] == [
        "paper:explanation:Q1"
    ]


@pytest.mark.asyncio
async def test_paper_audit_unit_local_drift_blocks_whole_paper_publication() -> None:
    result = await AuditAgent(MixedAuditModel()).run(_multi_unit_context())

    # 任一单元没有形成有效审核结论，整卷就不能声称完成审核。
    assert result.payload.decision == "needs_human_review"
    assert any("格式" in finding for finding in result.payload.findings)
