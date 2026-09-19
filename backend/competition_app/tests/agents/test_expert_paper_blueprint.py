import pytest

from competition_app.agents.expert import ExpertAgent
from competition_app.agents.paper_blueprint import PaperBlueprintAgent
from competition_app.contracts.base import AgentEnvelope
from competition_app.contracts.knowledge import (
    EvidenceItem,
    EvidencePack,
    QuestionDetail,
    QuestionRetrievalMetadata,
)
from competition_app.llm.prompt_skills import prompt_skill_registry
from competition_app.llm.schemas import PaperBlueprintModelOutput
from competition_app.llm.stub import StubChatModel


def paper_context() -> dict:
    evidence = EvidencePack(
        evidence_pack_id="EP_PAPER_1",
        query="四君子汤",
        resolved_kp_ids=["KP_FJ_001"],
        evidence_items=[
            EvidenceItem(
                evidence_id="E_1",
                source_id="方剂学:00336",
                content_summary="四君子汤由人参、白术、茯苓、甘草组成。",
                authority_level="textbook",
                confidence=0.93,
                bridge_layer="vector",
            )
        ],
    )
    evidence._question_details = [
        QuestionDetail(
            question_id="Q_1",
            question_type="single_choice",
            stem="关于四君子汤组成的教学练习题。",
            reference_answer="A",
            analysis="内部解析，不应发送给 Expert。",
            tags=["方剂学", "四君子汤"],
            source_metadata={},
            bridges=[],
            retrieval=QuestionRetrievalMetadata(
                channels=["vector"], channel_scores={"vector": 0.9}, fusion_score=0.9
            ),
        )
    ]
    return {
        "case_id": "CASE_PAPER_1",
        "trace_id": "TRACE_PAPER_1",
        "request_id": "REQ_PAPER_1",
        "execution_id": "EXE_PAPER_1",
        "step_id": "expert",
        "learner_id": "L1",
        "task_type": "paper_generation",
        "available_minutes": 60,
        "exam_constraints": {"exam_type": "练习", "total_score": "待用户确认"},
        "dependency_outputs": {
            "knowledge": AgentEnvelope(
                artifact_id="ART_KNOWLEDGE_1",
                artifact_type="evidence_pack",
                case_id="CASE_PAPER_1",
                trace_id="TRACE_PAPER_1",
                request_id="REQ_PAPER_1",
                execution_id="EXE_PAPER_1",
                step_id="knowledge",
                producer="knowledge_base_agent",
                task_type="paper_generation",
                learner_id="L1",
                payload=evidence,
            )
        },
    }


@pytest.mark.asyncio
async def test_expert_generates_blueprint_resource_for_paper_generation() -> None:
    result = await ExpertAgent(StubChatModel()).run(paper_context())

    assert result.payload.title == "四君子汤试卷蓝图"
    assert "【蓝图矩阵】" in result.payload.content["试卷蓝图"]
    assert "【发布前验收】" in result.payload.content["试卷蓝图"]
    assert result.payload.question_consumption.resource_type == "practice"
    assert result.payload.question_consumption.selected_question_ids == []


@pytest.mark.asyncio
async def test_paper_blueprint_prompt_exposes_candidates_without_answers() -> None:
    class CapturingModel(StubChatModel):
        def __init__(self) -> None:
            self.payload = None

        async def complete_json(self, role, payload, on_delta=None):
            if role == "expert_agent":
                self.payload = payload
            return await super().complete_json(role, payload, on_delta)

    model = CapturingModel()
    await ExpertAgent(model).run(paper_context())

    payload = model.payload["payload"]
    assert "prompt_skill" not in payload
    assert model.payload["prompt_skill_id"] == "expert.generate_exam_paper_blueprint"
    assert "试卷蓝图" in model.payload["task_instructions"]
    assert payload["paper_generation"]["enabled"] is True
    assert payload["question_candidate_catalog"] == [
        {
            "question_id": "Q_1",
            "question_type": "single_choice",
            "tags": ["方剂学", "四君子汤"],
            "kp_ids": [],
            "channels": ["vector"],
            "bridge_layers": [],
        }
    ]
    assert "reference_answer" not in str(payload)
    assert "内部解析" not in str(payload)


def test_paper_generation_skill_is_registered_for_expert() -> None:
    skill = prompt_skill_registry.load("expert_agent", "paper_generation")

    assert skill.skill_id == "expert.generate_exam_paper_blueprint"
    assert "试卷蓝图" in skill.instructions
    assert "完整题目、答案与评分细则" in skill.instructions


def test_blueprint_source_status_is_system_owned_when_model_returns_prose() -> None:
    normalized = PaperBlueprintAgent._normalize_blueprint(
        {
            "title": "四君子汤章节模拟卷蓝图",
            "source_status": "依据用户诉求与教学目标生成",
            "scope_summary": "四君子汤组成、功效主治和配伍意义",
            "units": [
                {
                    "knowledge_module": "组成",
                    "learning_objective": "识记组成",
                    "retrieval_query": "四君子汤 组成",
                    "required_question_count": 2,
                }
            ],
        },
        {
            "user_request": "请围绕四君子汤生成章节模拟卷",
            "available_minutes": 45,
            "exam_constraints": {},
        },
    )

    assert normalized["source_status"] == "user_provided_unverified"
    assert any("依据用户诉求与教学目标生成" in item for item in normalized["assumptions"])


def test_blueprint_source_status_prefers_user_constraint() -> None:
    normalized = PaperBlueprintAgent._normalize_blueprint(
        {"source_status": "practice_sample", "units": []},
        {"exam_constraints": {"source_status": "official"}},
    )

    assert normalized["source_status"] == "official"


def test_blueprint_compiler_miss_has_request_bound_fallback() -> None:
    fallback = PaperBlueprintAgent._fallback_blueprint_from_request(
        {
            "user_request": (
                "请围绕《中医学基础》阴阳学说生成一份3题单选练习试卷，"
                "难度2，并附答案解析。"
            ),
            "exam_constraints": {
                "topic": "《中医学基础》阴阳学说",
                "question_count": 3,
                "question_types": ["单项选择题"],
                "difficulty": 2,
            },
        },
        explicit_count=3,
        explicit_types=["单项选择题"],
    )

    assert fallback["title"] == "《中医学基础》阴阳学说练习试卷"
    assert fallback["scope_summary"] == "《中医学基础》阴阳学说"
    assert fallback["units"][0]["required_question_count"] == 3
    assert fallback["units"][0]["question_type_preferences"] == ["单项选择题"]
    assert fallback["units"][0]["target_difficulty"] == 2


def test_blueprint_compiler_miss_never_slices_user_prose_into_title() -> None:
    """降级蓝图不得把用户原话切片当成试卷标题或主题。"""

    fallback = PaperBlueprintAgent._fallback_blueprint_from_request(
        {
            "user_request": (
                "请给我一份全是填空题的试卷，至少25题，"
                "覆盖四君子汤组成、功效主治和配伍意义。"
            ),
            "exam_constraints": {},
        },
        explicit_count=None,
        explicit_types=[],
    )

    assert fallback["title"] == ""
    assert fallback["units"][0]["required_question_count"] == (
        PaperBlueprintAgent._FALLBACK_UNCONSTRAINED_UNIT_QUESTION_COUNT
    )
    assert any(
        "不是发布硬门槛" in item for item in fallback["assumptions"]
    )
    assert fallback["acceptance_criteria"] == [
        "题目范围只覆盖用户当前请求指定的主题。",
        "答案与解析必须通过审核后才能发布。",
    ]


def test_blueprint_normalizes_loose_live_model_values_before_validation() -> None:
    normalized = PaperBlueprintAgent._normalize_blueprint(
        {
            "title": "测" * 400,
            "scope_summary": "范围" * 800,
            "total_score": "待用户确认",
            "assumptions": ["按当前阶段", {"期限": "待确认"}],
            "acceptance_criteria": "完成后复核",
            "units": [
                {
                    "knowledge_module": "经典辨证" * 100,
                    "learning_objective": None,
                    "search_query": "伤寒论 第三阶段",
                    "question_type_preferences": "案例分析题",
                    "required_question_count": "待确认",
                    "score_total": "待确认",
                    "candidate_limit": 500,
                    "selection_rule": "覆盖核心概念",
                }
            ],
        },
        {
            "user_request": "请给我第三阶段的测试卷",
            "exam_constraints": {},
        },
    )

    assert len(normalized["title"]) == 300
    assert len(normalized["scope_summary"]) == 1_000
    assert normalized["total_score"] is None
    assert normalized["units"][0]["required_question_count"] == 1
    assert normalized["units"][0]["candidate_limit"] == 50
    assert normalized["units"][0]["score_total"] is None
    assert normalized["units"][0]["question_type_preferences"] == ["案例分析题"]
    PaperBlueprintModelOutput.model_validate(normalized)


def test_blueprint_does_not_treat_session_budget_as_exam_duration() -> None:
    normalized = PaperBlueprintAgent._normalize_blueprint(
        {
            "duration_minutes": 165,
            "units": [{
                "knowledge_module": "测试",
                "learning_objective": "完成测试",
                "retrieval_query": "测试",
                "required_question_count": 10,
            }],
        },
        {
            "user_request": "给我第三阶段的测试卷",
            "available_minutes": 165,
            "exam_constraints": {},
        },
    )

    assert normalized["duration_minutes"] is None
    assert any("实际题目工作量" in item for item in normalized["assumptions"])


def test_blueprint_preserves_explicit_exam_duration() -> None:
    normalized = PaperBlueprintAgent._normalize_blueprint(
        {"duration_minutes": 60, "units": []},
        {
            "user_request": "请生成一份作答时间165分钟的测试卷",
            "available_minutes": 25,
            "exam_constraints": {"duration_minutes": 165},
        },
    )

    assert normalized["duration_minutes"] == 165


def test_blueprint_does_not_infer_exam_duration_from_user_prose() -> None:
    """用户原话里的时长是自由文本，硬约束只认结构化约束通道。"""

    normalized = PaperBlueprintAgent._normalize_blueprint(
        {"duration_minutes": 60, "units": []},
        {
            "user_request": "请生成一份作答时间165分钟的测试卷",
            "available_minutes": 25,
            "exam_constraints": {},
        },
    )

    assert normalized["duration_minutes"] is None
    assert any(
        "正式时长按实际题目工作量计算" in item
        for item in normalized["assumptions"]
    )


def test_blueprint_resolves_requested_stage_from_current_long_term_plan() -> None:
    scope = PaperBlueprintAgent._requested_learning_scope({
        "user_request": "给我第三阶段的测试卷",
        "current_long_term_plan": {
            "stages": [
                {"stage": 2, "book": ["《方剂学》"], "goal": "掌握方剂"},
                {
                    "stage": 3,
                    "book": ["《伤寒论选读》", "《生理学》"],
                    "goal": "连接经典辨证与现代医学基础",
                },
            ]
        },
    })

    assert scope == {
        "requested_stage": 3,
        "books": ["《伤寒论选读》", "《生理学》"],
        "goal": "连接经典辨证与现代医学基础",
        "source": "当前长期规划的结构化阶段",
    }


def test_blueprint_contextual_paper_inherits_nearest_user_topic() -> None:
    scope = PaperBlueprintAgent._conversation_scope_context(
        {
            "user_request": "给我一套相关试卷",
            "messages": [
                {"role": "user", "content": "你好"},
                {"role": "assistant", "content": "你好！"},
                {"role": "user", "content": "给我讲解一下感冒的知识点"},
                {"role": "assistant", "content": "感冒讲解正文"},
                {"role": "user", "content": "给我一套相关试卷"},
            ],
        }
    )

    assert scope["is_contextual_followup"] is True
    assert scope["previous_user_request"] == "给我讲解一下感冒的知识点"
    assert "不得改用画像目标" in scope["resolution_rule"]


def test_explicit_paper_topic_does_not_inherit_previous_turn() -> None:
    assert (
        PaperBlueprintAgent._conversation_scope_context(
            {
                "user_request": "请生成一份四君子汤试卷",
                "messages": [
                    {"role": "user", "content": "给我讲解感冒"},
                    {"role": "user", "content": "请生成一份四君子汤试卷"},
                ],
            }
        )
        == {}
    )


def test_contextual_paper_retry_skips_previous_referential_paper_turn() -> None:
    scope = PaperBlueprintAgent._conversation_scope_context(
        {
            "user_request": "给我一套相关试卷",
            "messages": [
                {"role": "user", "content": "给我讲解一下感冒的知识点"},
                {"role": "assistant", "content": "感冒讲解正文"},
                {"role": "user", "content": "再给我一套相关试卷"},
                {"role": "assistant", "content": "上次生成失败"},
                {"role": "user", "content": "给我一套相关试卷"},
            ],
        }
    )

    assert scope["previous_user_request"] == "给我讲解一下感冒的知识点"


def test_blueprint_resolves_requested_stage_from_long_term_plan_content() -> None:
    scope = PaperBlueprintAgent._requested_learning_scope({
        "user_request": "给我第三阶段的测试卷",
        "current_long_term_plan": {
            "content": (
                "【能力路径与阶段】第一阶段建立基础；第二阶段学习方药；"
                "第三阶段融合经典辨证体系与现代医学基础；第四阶段进入临床。\n"
                "【阶段里程碑】\n"
                "1. 基础阶段：完成基础测验。\n"
                "2. 方药阶段：完成方药辨析。\n"
                "3. 经典与现代医学基础：完成经典条文辨析和现代医学基础综合测验。\n"
                "4. 临床阶段：完成病例分析。"
            )
        },
    })

    assert scope["requested_stage"] == 3
    assert scope["resolution"] == "已从当前长期规划正文解析"
    assert "经典辨证体系与现代医学基础" in scope["stage_description"]
    assert "经典条文辨析" in scope["stage_milestone"]


def test_blueprint_planning_context_keeps_plan_content_and_structured_stages() -> None:
    planning_context = PaperBlueprintAgent._planning_context({
        "current_long_term_plan": {
            "plan_id": "SHOULD_NOT_BE_SENT",
            "content": "长期规划正文",
            "stages": [{"stage": 3, "book": ["《伤寒论选读》"], "goal": "经典辨证"}],
        },
        "current_short_term_plan": {
            "plan_id": "SHOULD_NOT_BE_SENT",
            "content": "短期计划正文",
        },
        "current_learning_task": {
            "task_id": "SHOULD_NOT_BE_SENT",
            "task_content": "今日完成经典条文辨析",
            "completion_criteria": "正确说明辨证依据",
        },
    })

    assert planning_context["long_term_plan"]["content"] == "长期规划正文"
    assert planning_context["long_term_plan"]["stages"][0]["stage"] == 3
    assert planning_context["short_term_plan"]["content"] == "短期计划正文"
    assert planning_context["daily_task"]["task_content"] == "今日完成经典条文辨析"
    assert "SHOULD_NOT_BE_SENT" not in str(planning_context)


@pytest.mark.asyncio
async def test_blueprint_prompt_receives_current_planning_context() -> None:
    class CapturingBlueprintModel(StubChatModel):
        def __init__(self) -> None:
            self.payload = None

        async def complete_json(self, role, payload, on_delta=None):
            if role == "expert_agent":
                self.payload = payload
            return await super().complete_json(role, payload, on_delta)

    model = CapturingBlueprintModel()
    context = {
        **paper_context(),
        "step_id": "paper_blueprint",
        "user_request": "给我第三阶段的测试卷",
        "exam_constraints": {},
        "current_long_term_plan": {
            "content": "第三阶段融合经典辨证体系与现代医学基础。",
            "stages": [{"stage": 3, "book": ["《伤寒论选读》"], "goal": "经典辨证"}],
        },
    }

    await PaperBlueprintAgent(model).run(context)

    payload = model.payload["payload"]
    assert payload["planning_context"]["long_term_plan"]["content"]
    assert payload["planning_context"]["long_term_plan"]["stages"][0]["stage"] == 3
    assert payload["learning_scope"]["books"] == ["《伤寒论选读》"]


@pytest.mark.asyncio
async def test_blueprint_prompt_receives_diagnosis_learning_progress() -> None:
    from competition_app.agents.diagnosis import DiagnosisResult

    class CapturingBlueprintModel(StubChatModel):
        def __init__(self) -> None:
            self.payload = None

        async def complete_json(self, role, payload, on_delta=None):
            if role == "expert_agent":
                self.payload = payload
            return await super().complete_json(role, payload, on_delta)

    model = CapturingBlueprintModel()
    context = {
        **paper_context(),
        "step_id": "paper_blueprint",
        "user_request": "请根据我的学习进度生成一份练习试卷",
        "exam_constraints": {},
        "dependency_outputs": {
            "diagnosis": AgentEnvelope(
                artifact_id="ART_DIAGNOSIS_1",
                artifact_type="diagnosis_result",
                case_id="CASE_PAPER_1",
                trace_id="TRACE_PAPER_1",
                request_id="REQ_PAPER_1",
                execution_id="EXE_PAPER_1",
                step_id="diagnosis",
                producer="diagnosis_agent",
                task_type="paper_generation",
                learner_id="L1",
                payload=DiagnosisResult(
                    summary="你最近在复习四君子汤，掌握率 72%，薄弱点在配伍意义。",
                    learner_data={
                        "schema_version": "1.0",
                        "query_kind": "progress_summary",
                        "evidence_status": "observed",
                        "snapshot": {
                            "evidence_status": "observed",
                            "recent_topic": "四君子汤",
                            "weak_points": ["配伍意义"],
                        },
                    },
                ),
            )
        },
    }

    await PaperBlueprintAgent(model).run(context)

    payload = model.payload["payload"]
    assert payload["learning_progress"]["available"] is True
    assert payload["learning_progress"]["evidence_status"] == "observed"
    assert payload["learning_progress"]["summary"] == (
        "你最近在复习四君子汤，掌握率 72%，薄弱点在配伍意义。"
    )
    assert payload["learning_progress"]["snapshot"]["weak_points"] == ["配伍意义"]


def test_blueprint_learning_progress_marks_unavailable_without_diagnosis() -> None:
    progress = PaperBlueprintAgent._learning_progress_payload(None)

    assert progress["available"] is False
    assert "reason" in progress


def test_blueprint_learning_progress_marks_unavailable_when_no_evidence() -> None:
    from competition_app.agents.diagnosis import DiagnosisResult

    progress = PaperBlueprintAgent._learning_progress_payload(
        AgentEnvelope(
            artifact_id="ART_DIAGNOSIS_2",
            artifact_type="diagnosis_result",
            case_id="C1",
            trace_id="T1",
            request_id="R1",
            execution_id="E1",
            step_id="diagnosis",
            producer="diagnosis_agent",
            task_type="paper_generation",
            learner_id="L1",
            payload=DiagnosisResult(
                summary="暂时没有学习记录。",
                learner_data={
                    "schema_version": "1.0",
                    "query_kind": "progress_summary",
                    "evidence_status": "unavailable",
                    "reason": "learner data tools are unavailable",
                },
            ),
        )
    )

    assert progress["available"] is False
    assert progress["evidence_status"] == "unavailable"


def test_blueprint_balances_question_types_when_user_did_not_specify_them() -> None:
    units = PaperBlueprintAgent._normalize_question_type_mix(
        [
            {"knowledge_module": "基础", "question_type_preferences": ["单项选择题"]},
            {"knowledge_module": "理解", "question_type_preferences": ["多项选择题"]},
            {"knowledge_module": "应用", "question_type_preferences": ["单项选择题"]},
        ],
        explicit_types=[],
    )

    assert units[0]["question_type_preferences"] == ["单项选择题"]
    assert units[1]["question_type_preferences"] == ["简答题"]
    assert units[2]["question_type_preferences"] == ["案例分析题"]


def test_blueprint_keeps_user_requested_choice_specialty() -> None:
    units = PaperBlueprintAgent._normalize_question_type_mix(
        [
            {"knowledge_module": "基础", "question_type_preferences": ["简答题"]},
            {"knowledge_module": "应用", "question_type_preferences": []},
        ],
        explicit_types=["单项选择题", "多项选择题"],
    )

    assert all(
        unit["question_type_preferences"] == ["单项选择题", "多项选择题"]
        for unit in units
    )


def test_explicit_coverage_excludes_stale_exam_constraint_topics() -> None:
    topics = PaperBlueprintAgent._explicit_coverage_topics(
        {
            "user_request": (
                "请给我一份全是填空题的试卷，至少25题，"
                "覆盖四君子汤组成、功效主治和配伍意义；必须提供答案和解析。"
            ),
            "exam_constraints": {
                "focus_topics": [
                    "四君子汤组成",
                    "四君子汤功效主治",
                    "四君子汤配伍意义",
                ]
            },
        }
    )
    units = PaperBlueprintAgent._constrain_units_to_explicit_coverage(
        [
            {"knowledge_module": "四君子汤组成", "retrieval_query": "四君子汤组成"},
            {"knowledge_module": "四君子汤功效主治", "retrieval_query": "四君子汤功效主治"},
            {"knowledge_module": "四君子汤配伍意义", "retrieval_query": "四君子汤配伍意义"},
            {"knowledge_module": "相近方辨析", "retrieval_query": "四君子汤相近方辨析"},
        ],
        coverage_topics=topics,
    )

    assert topics == ["四君子汤组成", "四君子汤功效主治", "四君子汤配伍意义"]
    assert [unit["knowledge_module"] for unit in units] == topics
    assert all("相近方" not in unit["retrieval_query"] for unit in units)


def test_explicit_coverage_ignores_prose_only_coverage_list() -> None:
    """原话里的“覆盖 A、B、C”不再被切分，组卷范围不靠关键词切分决定。"""

    topics = PaperBlueprintAgent._explicit_coverage_topics(
        {
            "user_request": (
                "请给我一份全是填空题的试卷，至少25题，"
                "覆盖四君子汤组成、功效主治和配伍意义；必须提供答案和解析。"
            ),
            "exam_constraints": {},
        }
    )

    assert topics == []


@pytest.mark.parametrize(
    ("context", "expected"),
    [
        ({"exam_constraints": {"question_count": 20}}, 20),
        ({"exam_constraints": {"question_count": "25"}}, 25),
        ({"exam_constraints": {"question_count": 0}}, None),
        (
            {
                "user_request": "给我一份全是填空题的试卷，至少25题",
                "exam_constraints": {"question_count": 4},
            },
            4,
        ),
        (
            {
                "user_request": "请给我一份包含20个题目的选择题专项训练卷。",
                "exam_constraints": {},
            },
            None,
        ),
        (
            {
                "user_request": "请生成20道单项选择题。",
                "exam_constraints": {},
            },
            None,
        ),
        (
            {
                "user_request": "请出20道关于四君子汤的题。",
                "exam_constraints": {},
            },
            None,
        ),
    ],
)
def test_blueprint_extracts_only_explicit_question_count(
    context: dict, expected: int | None
) -> None:
    """整卷题量只认结构化约束通道，不从用户原话里抓“数字+题”。"""

    assert PaperBlueprintAgent._explicit_question_count(context) == expected


def test_blueprint_extracts_exact_structured_question_type_distribution() -> None:
    distribution = PaperBlueprintAgent._explicit_question_type_distribution(
        {
            "exam_constraints": {
                "question_count": 15,
                "question_type_distribution": {
                    "single_choice": 10,
                    "multiple_choice": 5,
                    "fill_blank": 0,
                },
            }
        }
    )

    assert distribution == {"单项选择题": 10, "多项选择题": 5}
    assert sum(distribution.values()) == 15


def test_explicit_count_is_spread_across_blueprint_units() -> None:
    units = PaperBlueprintAgent._normalize_hard_count_units(
        [
            {
                "knowledge_module": "组成",
                "learning_objective": "掌握组成",
                "retrieval_query": "四君子汤 组成",
                "question_type_preferences": ["简答题"],
                "required_question_count": 2,
                "candidate_limit": 4,
            },
            {
                "knowledge_module": "功效主治",
                "learning_objective": "掌握功效主治",
                "retrieval_query": "四君子汤 功效 主治",
                "question_type_preferences": [],
                "required_question_count": 2,
                "candidate_limit": 4,
            },
        ],
        explicit_count=20,
        has_explicit_distribution=False,
    )

    assert sum(unit["required_question_count"] for unit in units) == 20
    assert all(unit["candidate_limit"] > unit["required_question_count"] for unit in units)
    # 题型偏好只由结构化题型要求决定，不再因为原话里出现“选择题”就被强设。
    assert units[0]["question_type_preferences"] == ["简答题"]
    assert units[1]["question_type_preferences"] == []


def _count_units(counts: list[int]) -> list[dict]:
    return [
        {
            "knowledge_module": f"单元{index}",
            "learning_objective": f"掌握单元{index}",
            "retrieval_query": f"单元{index}",
            "question_type_preferences": ["简答题"],
            "required_question_count": required,
            "candidate_limit": 10,
        }
        for index, required in enumerate(counts, start=1)
    ]


def test_explicit_distribution_still_scales_unit_counts_to_the_exact_total() -> None:
    """整卷题型分布存在时，单元题量合计也必须等于整卷题量。

    线上场景（r2c 实测）：用户说“50 道简答题”，编译器把它编译成结构化分型
    数量 {简答题: 50}，于是单元题量的归一化被整段跳过，蓝图模型自己写的 10
    个单元合计 60 原样通过。整卷题量硬约束与逐单元题量硬约束从此永远无法同时
    满足，学习者一直看到“仍有未满足的硬约束”，换任何一份组卷结果都消不掉。

    缩放必须保留“哪个单元更重”的形状，不能平均分配。
    """

    units = PaperBlueprintAgent._normalize_hard_count_units(
        _count_units([10, 5, 5, 5]),
        explicit_count=10,
        has_explicit_distribution=True,
    )

    assert [unit["required_question_count"] for unit in units] == [4, 2, 2, 2]
    assert sum(unit["required_question_count"] for unit in units) == 10


def test_unit_counts_are_scaled_up_when_the_model_underfills() -> None:
    """模型给少了同样要对齐整卷题量，两个方向都不能留矛盾。"""

    units = PaperBlueprintAgent._normalize_hard_count_units(
        _count_units([1, 1, 1]),
        explicit_count=20,
        has_explicit_distribution=True,
    )

    assert [unit["required_question_count"] for unit in units] == [7, 7, 6]
    assert sum(unit["required_question_count"] for unit in units) == 20


def test_unit_count_above_the_requested_total_stays_satisfiable() -> None:
    """整卷题量小于单元数时不得留下不可能满足的逐单元约束。

    每个单元至少 1 题，4 个单元至少要 4 题；用户只要 2 题时只保留前 2 个单元。
    宁可少一个单元，也不要让整份试卷永远过不了硬门禁。
    """

    units = PaperBlueprintAgent._normalize_hard_count_units(
        _count_units([1, 1, 1, 1]),
        explicit_count=2,
        has_explicit_distribution=False,
    )

    assert [unit["required_question_count"] for unit in units] == [1, 1]


def test_explicit_distribution_keeps_unit_level_question_type_mix() -> None:
    """整卷题型分布是结构化时，不得被抹平为每单元同一组题型。"""

    units = PaperBlueprintAgent._normalize_question_type_mix(
        [
            {"knowledge_module": "单选", "question_type_preferences": ["单项选择题"]},
            {"knowledge_module": "多选", "question_type_preferences": ["多项选择题"]},
            {"knowledge_module": "简答", "question_type_preferences": ["简答题"]},
        ],
        explicit_types=["单项选择题", "多项选择题", "简答题"],
        has_explicit_distribution=True,
    )

    assert [unit["question_type_preferences"] for unit in units] == [
        ["单项选择题"],
        ["多项选择题"],
        ["简答题"],
    ]


def test_explicit_difficulty_accepts_only_clear_numeric_requests() -> None:
    assert PaperBlueprintAgent._explicit_difficulty(
        {"exam_constraints": {"difficulty": 3}}
    ) == 3
    assert PaperBlueprintAgent._explicit_difficulty(
        {"exam_constraints": {"target_difficulty": "4"}}
    ) == 4
    assert PaperBlueprintAgent._explicit_difficulty(
        {"exam_constraints": {"difficulty_level": "五星"}}
    ) == 5
    assert PaperBlueprintAgent._explicit_difficulty(
        {"exam_constraints": {"difficulty": 0}}
    ) is None
    assert PaperBlueprintAgent._explicit_difficulty(
        {"exam_constraints": {"difficulty": True}}
    ) is None
    assert PaperBlueprintAgent._explicit_difficulty(
        {"exam_constraints": {}}
    ) is None
    # 自由文本里的难度描述不再被正则提取，由蓝图模型负责理解。
    assert PaperBlueprintAgent._explicit_difficulty(
        {"user_request": "请出5道难度3的题", "exam_constraints": {}}
    ) is None
    assert PaperBlueprintAgent._explicit_difficulty(
        {"user_request": "来一套四星难度的卷子", "exam_constraints": {}}
    ) is None
    assert PaperBlueprintAgent._explicit_difficulty(
        {"user_request": "给我一套中等难度的卷子", "exam_constraints": {}}
    ) is None


def test_blueprint_normalize_propagates_explicit_difficulty_to_all_units() -> None:
    normalized = PaperBlueprintAgent._normalize_blueprint(
        {
            "title": "难度卷",
            "source_status": "practice_sample",
            "scope_summary": "测试",
            "units": [
                {
                    "knowledge_module": "单元一",
                    "learning_objective": "目标",
                    "retrieval_query": "四君子汤",
                    "required_question_count": 2,
                },
                {
                    "knowledge_module": "单元二",
                    "learning_objective": "目标",
                    "retrieval_query": "理中丸",
                    "required_question_count": 2,
                },
            ],
        },
        {"user_request": "出难度3的题", "exam_constraints": {"difficulty": 3}},
    )

    assert all(unit["target_difficulty"] == 3 for unit in normalized["units"])
    assert all(unit["difficulty_is_hard_constraint"] for unit in normalized["units"])


def test_blueprint_normalize_does_not_infer_difficulty_from_user_prose() -> None:
    normalized = PaperBlueprintAgent._normalize_blueprint(
        {
            "title": "难度卷",
            "source_status": "practice_sample",
            "scope_summary": "测试",
            "units": [
                {
                    "knowledge_module": "单元一",
                    "learning_objective": "目标",
                    "retrieval_query": "四君子汤",
                    "required_question_count": 2,
                },
            ],
        },
        {"user_request": "出难度3的题", "exam_constraints": {}},
    )

    assert normalized["units"][0]["target_difficulty"] is None
    assert normalized["units"][0]["difficulty_is_hard_constraint"] is False


def test_blueprint_normalize_keeps_unit_level_difficulty_over_context() -> None:
    normalized = PaperBlueprintAgent._normalize_blueprint(
        {
            "title": "难度卷",
            "source_status": "practice_sample",
            "scope_summary": "测试",
            "units": [
                {
                    "knowledge_module": "单元一",
                    "learning_objective": "目标",
                    "retrieval_query": "四君子汤",
                    "required_question_count": 2,
                    "target_difficulty": 5,
                    "difficulty_is_hard_constraint": True,
                },
            ],
        },
        {"user_request": "出难度3的题", "exam_constraints": {}},
    )

    unit = normalized["units"][0]
    assert unit["target_difficulty"] == 5
    assert unit["difficulty_is_hard_constraint"] is True


def test_blueprint_normalize_rejects_invalid_difficulty_values() -> None:
    normalized = PaperBlueprintAgent._normalize_blueprint(
        {
            "title": "难度卷",
            "source_status": "practice_sample",
            "scope_summary": "测试",
            "units": [
                {
                    "knowledge_module": "单元一",
                    "learning_objective": "目标",
                    "retrieval_query": "四君子汤",
                    "required_question_count": 2,
                    "target_difficulty": "中等",
                },
            ],
        },
        {"user_request": "组一套卷", "exam_constraints": {}},
    )

    assert normalized["units"][0]["target_difficulty"] is None
    assert normalized["units"][0]["difficulty_is_hard_constraint"] is False


def _smart_paper_diagnosis(*, mastery: list[dict]) -> AgentEnvelope:
    from competition_app.agents.diagnosis import DiagnosisResult

    return AgentEnvelope(
        artifact_id="ART_SMART_DIAGNOSIS",
        artifact_type="learner_data_result",
        case_id="CASE_PAPER_1",
        trace_id="TRACE_PAPER_1",
        request_id="REQ_PAPER_1",
        execution_id="EXE_PAPER_1",
        step_id="diagnosis",
        producer="diagnosis_agent",
        task_type="learner_data_query",
        learner_id="L1",
        payload=DiagnosisResult(
            summary="已读取当前考试下的掌握度证据。",
            learner_data={
                "schema_version": "1.0",
                "query_kind": "next_learning",
                "evidence_status": "observed",
                "snapshot": {
                    "evidence_status": "observed",
                    "mastery_and_review": {
                        "evidence_status": "observed",
                        "mastery": mastery,
                    },
                },
            },
        ),
    )


@pytest.mark.asyncio
async def test_structured_adaptive_blueprint_prefers_diagnosed_weak_topics() -> None:
    context = {
        **paper_context(),
        "step_id": "paper_blueprint",
        "smart_paper_v2": True,
        "exam_constraints": {
            "question_count": 2,
            "question_type_distribution": {"单项选择题": 2},
            "answer_mode": "practice",
            "paper_kind": "adaptive",
            "topic": "",
            "focus_topics": ["前端旧推荐"],
        },
    }
    context["dependency_outputs"] = {
        "diagnosis": _smart_paper_diagnosis(
            mastery=[
                {
                    "kp_id": "KP_HIGH",
                    "kp_name": "方剂功效",
                    "mastery_score": 78,
                },
                {
                    "kp_id": "KP_LOW",
                    "kp_name": "方剂配伍",
                    "mastery_score": 32,
                    "requires_remediation": True,
                },
            ]
        )
    }

    result = await PaperBlueprintAgent().run(context)

    assert result.payload.units[0].knowledge_module == "方剂配伍"
    assert result.payload.scope_summary.startswith("方剂配伍、方剂功效")
    assert "前端旧推荐" in result.payload.scope_summary


@pytest.mark.asyncio
async def test_structured_special_blueprint_never_lets_diagnosis_replace_topic() -> None:
    context = {
        **paper_context(),
        "step_id": "paper_blueprint",
        "smart_paper_v2": True,
        "exam_constraints": {
            "question_count": 1,
            "question_type_distribution": {"单项选择题": 1},
            "answer_mode": "practice",
            "paper_kind": "special",
            "topic": "四君子汤",
            "focus_topics": [],
        },
    }
    context["dependency_outputs"] = {
        "diagnosis": _smart_paper_diagnosis(
            mastery=[
                {
                    "kp_id": "KP_UNRELATED",
                    "kp_name": "温病学",
                    "mastery_score": 1,
                    "requires_remediation": True,
                }
            ]
        )
    }

    result = await PaperBlueprintAgent().run(context)

    assert result.payload.scope_summary == "四君子汤"
    assert [unit.retrieval_query for unit in result.payload.units] == ["四君子汤"]


@pytest.mark.asyncio
async def test_structured_blueprint_takes_the_explanation_switch_from_the_form() -> None:
    """解析交付条件只认表单开关，不认主题正文里的同义表述。"""

    def build(topic: str, switch: bool) -> dict:
        context = {
            **paper_context(),
            "step_id": "paper_blueprint",
            "smart_paper_v2": True,
            "exam_constraints": {
                "question_count": 1,
                "question_type_distribution": {"简答题": 1},
                "answer_mode": "practice",
                "paper_kind": "special",
                "topic": topic,
                "focus_topics": [],
                "requires_explanation": switch,
            },
        }
        context["dependency_outputs"] = {
            "diagnosis": _smart_paper_diagnosis(
                mastery=[
                    {
                        "kp_id": "KP_UNRELATED",
                        "kp_name": "温病学",
                        "mastery_score": 1,
                        "requires_remediation": True,
                    }
                ]
            )
        }
        return context

    switched_on = await PaperBlueprintAgent().run(
        build("太阳病篇", True)
    )
    assert switched_on.payload.requires_explanation is True
    assert "每题必须附解析。" in switched_on.payload.acceptance_criteria

    # 主题正文写着“每题均附详细解析”但开关未打开时，交付条件保持不要求。
    prose_only = await PaperBlueprintAgent().run(
        build("太阳病篇，每题均附详细解析", False)
    )
    assert prose_only.payload.requires_explanation is False
    assert "每题必须附解析。" not in prose_only.payload.acceptance_criteria
