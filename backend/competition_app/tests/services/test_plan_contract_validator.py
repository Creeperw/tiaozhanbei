from competition_app.contracts.plan_compilation import (
    CompiledLongTermContract,
    CompiledLongTermStage,
    CompiledShortTermContract,
)
from competition_app.services.plan_contract_validator import PlanContractValidator


def short_term_contract(duration_days: int) -> CompiledShortTermContract:
    return CompiledShortTermContract(
        scope="short_term",
        short_term_plan_content=(
            "未来一个月使用《方剂学》推进补益剂学习，前半程完成教材核对，"
            "后半程完成类方比较并提交闭卷验收记录。"
        ),
        duration_days=duration_days,
        progression_nodes=["完成《方剂学》教材核对。", "完成闭卷比较验收。"],
        expected_output="一份补益剂类方比较表。",
        completion_criteria="能够闭卷比较代表方剂并解释配伍差异。",
        selected_stage_id="stage-1",
        selected_books=["《方剂学》"],
    )


def test_short_term_duration_equal_to_parent_stage_is_valid() -> None:
    result = PlanContractValidator().validate(
        short_term_contract(30),
        parent_plan_constraints={
            "current_stage_id": "stage-1",
            "current_stage_duration_days": 30,
        },
    )

    assert result.valid is True
    assert result.issues == []


def test_short_term_duration_cannot_exceed_parent_stage() -> None:
    result = PlanContractValidator().validate(
        short_term_contract(31),
        parent_plan_constraints={
            "current_stage_id": "stage-1",
            "current_stage_duration_days": 30,
        },
    )

    assert result.valid is False
    assert "短期31天，父阶段30天" in result.issues[0]


def test_long_term_duration_must_equal_stage_sum() -> None:
    contract = CompiledLongTermContract(
        scope="long_term",
        long_term_plan_content=(
            "第一阶段使用《中药学》建立药性基础；"
            "第二阶段使用《方剂学》建立方证联系。"
        ),
        total_duration_days=61,
        stages=[
            CompiledLongTermStage(
                stage=1,
                stage_name="中药基础",
                books=["《中药学》"],
                goal="建立常用中药性能功效基础。",
                duration_days=30,
                schedule_summary="使用《中药学》完成分类笔记并闭卷验收。",
            ),
            CompiledLongTermStage(
                stage=2,
                stage_name="方证联系",
                books=["《方剂学》"],
                goal="建立治法、方剂和证候联系。",
                duration_days=30,
                schedule_summary="使用《方剂学》完成方证比较表并案例验收。",
            ),
        ],
    )

    result = PlanContractValidator().validate(contract)

    assert result.valid is False
    assert "长期规划总期限必须等于各阶段期限之和。" in result.issues


def test_long_term_content_must_name_each_structured_book() -> None:
    contract = CompiledLongTermContract(
        scope="long_term",
        long_term_plan_content="第一阶段完成中药基础学习。",
        total_duration_days=30,
        stages=[
            CompiledLongTermStage(
                stage=1,
                stage_name="中药基础",
                books=["《中药学》"],
                goal="建立常用中药性能功效基础。",
                duration_days=30,
                schedule_summary="使用《中药学》完成分类笔记并闭卷验收。",
            )
        ],
    )

    result = PlanContractValidator().validate(contract)

    assert result.valid is False
    assert any("正文缺少具体书名：《中药学》" in issue for issue in result.issues)