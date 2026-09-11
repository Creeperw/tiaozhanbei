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


def test_short_term_allows_exact_system_authorized_cross_stage_focus() -> None:
    contract = short_term_contract(7).model_copy(
        update={
            "short_term_plan_content": (
                "未来7天入门预习《方剂学》，依次学习四君子汤、参苓白术散和理中丸；"
                "前段完成三方组成与功用表，中段完成主治辨析，周期末提交闭卷比较表。"
                "本专题不代表完成方剂阶段，也不改变长期阶段。"
            ),
            "progression_nodes": [
                "完成四君子汤、参苓白术散和理中丸的组成与功用核对。",
                "提交四君子汤、参苓白术散和理中丸闭卷比较表。",
            ],
            "expected_output": "一份四君子汤、参苓白术散和理中丸闭卷比较表。",
            "completion_criteria": "能够闭卷比较四君子汤、参苓白术散和理中丸，不据此申报阶段完成。",
            "selected_stage_id": "stage-2",
        }
    )

    result = PlanContractValidator().validate(
        contract,
        parent_plan_constraints={
            "current_stage_id": "stage-1",
            "current_stage_duration_days": 90,
            "temporary_focus_overlay": {
                "mode": "temporary_cross_stage",
                "progression_stage_id": "stage-1",
                "focus_stage_id": "stage-2",
                "focus_books": ["《方剂学》"],
                "focus_names": ["四君子汤", "参苓白术散", "理中丸"],
                "prerequisite_mode": "introductory_preview",
            },
        },
    )

    assert result.valid is True
    assert result.issues == []


def test_structural_validator_leaves_prose_coverage_to_audit() -> None:
    contract = short_term_contract(7).model_copy(
        update={
            "short_term_plan_content": (
                "未来7天入门预习《方剂学》四君子汤，形成组成与功用笔记。"
                "本专题不代表完成方剂阶段，也不改变长期阶段。"
            ),
            "progression_nodes": ["完成四君子汤教材核对。", "提交四君子汤闭卷记录。"],
            "expected_output": "一份四君子汤笔记。",
            "completion_criteria": "能够闭卷说明四君子汤组成。",
            "selected_stage_id": "stage-2",
        }
    )

    result = PlanContractValidator().validate(
        contract,
        parent_plan_constraints={
            "current_stage_id": "stage-1",
            "current_stage_duration_days": 90,
            "temporary_focus_overlay": {
                "mode": "temporary_cross_stage",
                "progression_stage_id": "stage-1",
                "focus_stage_id": "stage-2",
                "focus_books": ["《方剂学》"],
                "focus_names": ["四君子汤", "参苓白术散", "理中丸"],
                "prerequisite_mode": "introductory_preview",
            },
        },
    )

    assert result.valid is True  # not publication approval; Audit must check full scope
    assert result.issues == []


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


def test_long_term_prose_book_coverage_is_left_to_semantic_audit() -> None:
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

    assert result.valid is True
    assert result.issues == []


def test_long_term_route_validation_ignores_title_mark_typography() -> None:
    contract = CompiledLongTermContract(
        scope="long_term",
        long_term_plan_content="第一阶段学习《中医学基础》和《医古文》。",
        total_duration_days=60,
        stages=[
            CompiledLongTermStage(
                stage=1,
                stage_name="中医基础与文化语言",
                books=["中医学基础", "医古文"],
                goal="建立中医基础概念和医古文阅读基础。",
                duration_days=60,
                schedule_summary="先学习《中医学基础》，再学习《医古文》。",
            )
        ],
    )

    result = PlanContractValidator().validate(
        contract,
        trusted_route={
            "stages": [
                {
                    "name": "中医基础与文化语言",
                    "books": ["《中医学基础》", "《医古文》"],
                }
            ]
        },
    )

    assert result.valid is True
    assert result.issues == []
