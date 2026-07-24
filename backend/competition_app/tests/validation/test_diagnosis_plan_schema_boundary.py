import pytest
from pydantic import ValidationError

from competition_app.contracts.learning_plan import LearningPlanProposal
from competition_app.llm.schemas import (
    LearningAnalysisModelOutput,
    ThreeLayerPlanningModelOutput,
    validate_training_style_output,
)


def three_layer_output() -> dict:
    return {
        "long_term_plan_content": (
            "## 目标契约\n系统掌握方剂学。\n"
            "## 长期阶段路径\n基础理论到方剂应用。\n"
            "## 长期重规划触发器\n目标或期限稳定变化时调整。"
        ),
        "short_term_plan_content": (
            "## 当前周期目标\n未来一周完成基础复习。\n"
            "## 每日任务表\n每日完成一次回忆。\n"
            "## 短期重规划触发器\n连续未完成时调整。"
        ),
        "daily_task_content": (
            "## 今日目标\n回忆四君子汤组成。\n"
            "## 分步动作和时间分配\n闭卷默写后核对。\n"
            "## 客观完成标准\n四味药全部正确。"
        ),
        "estimated_minutes": 15,
        "expected_output": "一份闭卷默写记录。",
        "completion_criteria": "四味药全部正确。",
        "long_term_plan_stages": [
            {
                "stage": 1,
                "book": ["《方剂学》"],
                "goal": "建立方剂组成、功效与配伍之间的联系。",
            }
        ],
    }


def test_three_layer_model_boundary_is_minimal_and_has_independent_content() -> None:
    parsed = ThreeLayerPlanningModelOutput.model_validate(three_layer_output())

    assert set(parsed.model_dump()) == {
        "long_term_plan_content",
        "short_term_plan_content",
        "daily_task_content",
        "learning_chapter",
        "focus_knowledge_points",
        "estimated_minutes",
        "expected_output",
        "completion_criteria",
        "long_term_plan_stages",
        "selected_textbook_route_id",
        "selected_stage_id",
            "selected_books",
            "selection_reason",
            "selected_path_candidate_id",
        }
    assert parsed.daily_task_content != parsed.short_term_plan_content


def test_three_layer_model_boundary_normalizes_null_optional_book_selection() -> None:
    candidate = {**three_layer_output(), "selected_books": None}

    parsed = ThreeLayerPlanningModelOutput.model_validate(candidate)

    assert parsed.selected_books == []


@pytest.mark.parametrize(
    "field",
    [
        "long_term_plan_action",
        "short_term_plan_action",
        "daily_task_action",
        "priority_mode",
        "knowledge_point_ids",
    ],
)
def test_three_layer_model_boundary_rejects_system_owned_fields(field: str) -> None:
    candidate = {**three_layer_output(), field: "model-generated"}

    with pytest.raises(ValidationError):
        ThreeLayerPlanningModelOutput.model_validate(candidate)


def test_three_layer_model_boundary_rejects_nested_route_objects() -> None:
    candidate = {**three_layer_output(), "route_context": {"route_id": "MODEL_ROUTE"}}

    with pytest.raises(ValidationError):
        ThreeLayerPlanningModelOutput.model_validate(candidate)


def valid_model_output() -> dict:
    return {
        "summary": "当前基础尚可，需要强化主动回忆。",
        "risk_flags": ["复习间隔偏长"],
        "recommendations": ["先回忆再核对"],
        "uncertainty": [],
        "long_term_plan_content": (
            "【最终目标】建立组成、功效和应用之间的联系。"
            "【能力路径与阶段】记忆→理解→应用。"
            "【阶段里程碑】完成闭卷说明；截止时间待确认。"
            "【资源预算】投入与缓冲时间待确认。"
            "【重规划条件】连续两次任务不达标时调整。"
            "【保温底线】每周一次知识卡回忆。"
        ),
        "short_term_plan_content": (
            "【当前主目标】完成一次主动回忆和纠错复述。"
            "【长期目标保温】保留一次知识卡回忆。"
            "【时间分配】12分钟用于主任务。"
            "【具体任务块】默写并解释配伍，产出组成清单，完成标准为组成完整。"
            "【复习任务】完成后安排纠错复述。"
            "【反馈指标】记录完成率、正确率和错因。"
        ),
        "priority_mode": "temporary_focus",
        "adjustment_reason": "近期正确率下降。",
        "learning_task": {
            "task_type": "active_recall",
            "task_content": "默写方剂组成并解释配伍。",
            "estimated_minutes": 12,
            "expected_output": "组成清单和配伍说明。",
            "completion_criteria": "组成完整且配伍解释正确。",
        },
    }


@pytest.mark.parametrize(
    "field",
    [
        "plan_id",
        "task_id",
        "short_term_plan_id",
        "user_id",
        "created_at",
        "updated_at",
        "due_at",
        "status",
        "version",
    ],
)
def test_training_protocol_rejects_every_system_owned_plan_field(field: str) -> None:
    candidate = {**valid_model_output(), field: "model-generated"}

    with pytest.raises(ValueError, match=field):
        validate_training_style_output(LearningAnalysisModelOutput, candidate, [])


def test_nested_task_rejects_system_owned_fields() -> None:
    candidate = valid_model_output()
    candidate["learning_task"] = {
        **candidate["learning_task"],
        "task_id": "MODEL_TASK_1",
    }

    with pytest.raises(ValueError, match="training output contract validation failed"):
        validate_training_style_output(LearningAnalysisModelOutput, candidate, [])


def test_formal_proposal_contract_cannot_smuggle_system_fields() -> None:
    parsed = validate_training_style_output(
        LearningAnalysisModelOutput,
        valid_model_output(),
        [],
    )
    proposal = {
        "long_term_plan_content": parsed.long_term_plan_content,
        "short_term_plan_content": parsed.short_term_plan_content,
        "priority_mode": parsed.priority_mode,
        "adjustment_reason": parsed.adjustment_reason,
        "task_proposal": parsed.learning_task.model_dump(),
        "plan_id": "MODEL_PLAN_1",
    }

    with pytest.raises(ValidationError):
        LearningPlanProposal.model_validate(proposal)


@pytest.mark.parametrize("field", ["long_term_plan_content", "short_term_plan_content"])
def test_learning_plan_model_requires_user_defined_natural_language_sections(field: str) -> None:
    candidate = valid_model_output()
    candidate[field] = "没有使用规定栏目。"

    with pytest.raises(ValueError, match="missing sections"):
        validate_training_style_output(LearningAnalysisModelOutput, candidate, [])


def test_diagnosis_adapter_completes_short_plan_content_before_validation() -> None:
    from competition_app.agents.diagnosis import DiagnosisAgent

    candidate = valid_model_output()
    candidate["long_term_plan_content"] = "当前先巩固四君子汤组成与配伍。"

    adapted = DiagnosisAgent._adapt_model_output(candidate, {})

    assert all(
        section in adapted["long_term_plan_content"]
        for section in (
            "【最终目标】",
            "【能力路径与阶段】",
            "【阶段里程碑】",
            "【资源预算】",
            "【重规划条件】",
            "【保温底线】",
        )
    )


def test_diagnosis_adapter_normalizes_live_plan_actions_and_priority() -> None:
    from competition_app.agents.diagnosis import DiagnosisAgent

    candidate = valid_model_output()
    candidate["long_term_plan_action"] = "沿用"
    candidate["short_term_plan_action"] = "制定"
    candidate["priority_mode"] = "initial_recall"

    adapted = DiagnosisAgent._adapt_model_output(candidate, {})

    assert adapted["long_term_plan_action"] == "reuse"
    assert adapted["short_term_plan_action"] == "update"
    assert adapted["priority_mode"] == "temporary_focus"
