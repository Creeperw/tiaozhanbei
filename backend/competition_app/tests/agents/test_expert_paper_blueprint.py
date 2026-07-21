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
                    "difficulty_preference": "综合应用" * 30,
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
            "exam_constraints": {},
        },
    )

    assert normalized["duration_minutes"] == 165


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
    request = (
        "请给我一份全是填空题的试卷，至少25题，"
        "覆盖四君子汤组成、功效主治和配伍意义；必须提供答案和解析。"
    )
    topics = PaperBlueprintAgent._explicit_coverage_topics(request)
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


@pytest.mark.parametrize(
    ("context", "expected"),
    [
        ({"exam_constraints": {"question_count": 20}}, 20),
        (
            {
                "user_request": "给我一份全是填空题的试卷，至少25题",
                "exam_constraints": {"question_count": 4},
            },
            25,
        ),
        ({"user_request": "请给我一份包含20个题目的选择题专项训练卷。"}, 20),
        ({"user_request": "请生成20道单项选择题。"}, 20),
        ({"user_request": "请出20道关于四君子汤的题。"}, 20),
        ({"user_request": "请给我一份选择题专项训练卷。"}, None),
    ],
)
def test_blueprint_extracts_only_explicit_question_count(
    context: dict, expected: int | None
) -> None:
    assert PaperBlueprintAgent._explicit_question_count(context) == expected


def test_choice_specialty_distributes_explicit_twenty_across_blueprint_units() -> None:
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
        user_request="请生成一份选择题专项训练卷，不少于20题。",
    )

    assert sum(unit["required_question_count"] for unit in units) == 20
    assert all(
        unit["question_type_preferences"] == ["单项选择题", "多项选择题"]
        for unit in units
    )
    assert all(unit["candidate_limit"] > unit["required_question_count"] for unit in units)
