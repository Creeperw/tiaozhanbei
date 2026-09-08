from competition_app.contracts.default_route import DefaultRoutePhase, ResolvedPlanningRoute
from competition_app.contracts.textbook_route import (
    ResolvedTextbookRoute,
    TextbookLearningRoute,
    TextbookPrerequisiteRule,
    TextbookRouteStage,
)
from competition_app.llm.schemas import ThreeLayerPlanningModelOutput
from competition_app.services.planning_validator import PlanningValidator
from competition_app.tests.validation.test_diagnosis_plan_schema_boundary import (
    three_layer_output,
)


def route() -> ResolvedPlanningRoute:
    return ResolvedPlanningRoute(
        goal_type="course",
        goal_name="方剂学",
        planning_status="approved_route",
        match_reason="canonical_name",
        route_id="formula",
        route_version=1,
        route_status="approved",
        phases=[
            DefaultRoutePhase(
                phase_id="P1",
                name="基础阶段",
                objective="掌握基础理论",
                books=["《方剂学》"],
                exit_evidence=["闭卷测评"],
            )
        ],
    )


def textbook_bound_route() -> ResolvedPlanningRoute:
    textbook = TextbookLearningRoute(
        route_id="textbook_formula",
        route_version=1,
        status="approved",
        goal_name="方剂学教材主线",
        stages=[
            TextbookRouteStage(
                stage_id="stage-1",
                order=1,
                name="基础中药",
                objective="建立药性基础",
                books=["《中药学》"],
                exit_evidence=["完成药性测验"],
                source_refs=["USER_ROUTE"],
            ),
            TextbookRouteStage(
                stage_id="stage-2",
                order=2,
                name="方剂学习",
                objective="建立治法和配伍能力",
                books=[
                    "《方剂学》",
                    "《中医内科学》",
                    "《伤寒论选读》",
                    "《金匮要略》",
                    "《温病学》",
                ],
                exit_evidence=["完成方证辨析"],
                source_refs=["USER_ROUTE"],
            ),
        ],
        prerequisites=[
            TextbookPrerequisiteRule(
                course="中医诊断学",
                before_stage_id="stage-2",
                reason="进入方剂阶段前需完成诊断基础。",
            )
        ],
        source_refs=["USER_ROUTE"],
        reviewed_by="USER",
    )
    return route().model_copy(
        update={
            "textbook_route": ResolvedTextbookRoute(
                planning_status="resolved",
                match_reason="test",
                route=textbook,
            )
        }
    )


def output(**changes) -> ThreeLayerPlanningModelOutput:
    data = three_layer_output()
    data["long_term_plan_stages"] = [
        {
            "stage": 1,
            "stage_name": "基础阶段",
            "book": ["《方剂学》"],
            "goal": "掌握基础理论",
            "duration_days": 30,
            "schedule_summary": (
                "使用《方剂学》梳理基础理论，形成知识框架，并以闭卷测评验收。"
            ),
        }
    ]
    data["total_duration_days"] = 30
    data["short_term_duration_days"] = 7
    data["short_term_progression_nodes"] = [
        "周初完成《方剂学》四君子汤组成与功用回忆。",
        "周末完成类方辨析与综合验收。",
    ]
    data["learning_chapter"] = "《方剂学》补益剂章"
    data["focus_knowledge_points"] = ["四君子汤组成", "四君子汤功用"]
    data["long_term_plan_content"] += "\n基础阶段使用《方剂学》，提交闭卷测评后晋级。"
    data["short_term_plan_content"] += "\n本周复习《方剂学》中的四君子汤。"
    data["short_term_plan_content"] += (
        "\n周初完成组成与功用回忆，周中进行类方辨析，周末完成综合验收。"
    )
    data["daily_task_content"] += "\n对应本周任务：复习《方剂学》四君子汤。"
    data.update(changes)
    if (
        data.get("selected_textbook_route_id") == "textbook_formula"
        and "long_term_plan_stages" not in changes
    ):
        data["long_term_plan_stages"] = [
            {
                "stage": 1,
                "stage_name": "基础中药",
                "book": ["《中药学》"],
                "goal": "建立药性基础",
                "duration_days": 30,
                "schedule_summary": (
                    "使用《中药学》完成药性分类笔记，并以药性测验验收。"
                ),
            },
            {
                "stage": 2,
                "stage_name": "方剂学习",
                "book": [
                    "《方剂学》",
                    "《中医内科学》",
                    "《伤寒论选读》",
                    "《金匮要略》",
                    "《温病学》",
                ],
                "goal": "建立治法和配伍能力",
                "duration_days": 30,
                "schedule_summary": (
                    "使用《方剂学》《中医内科学》《伤寒论选读》《金匮要略》"
                    "和《温病学》完成方证比较表，并以方证辨析验收。"
                ),
            },
        ]
        data["total_duration_days"] = 60
    return ThreeLayerPlanningModelOutput.model_validate(data)


def test_validator_accepts_minor_format_and_wording_differences() -> None:
    result = PlanningValidator().validate(output(), route(), available_minutes=20)
    assert result.valid, result.issues


def test_validator_accepts_explicit_long_term_duration_and_weekly_budget() -> None:
    value = output().model_copy(
        update={
            "long_term_plan_content": (
                output().long_term_plan_content
                + "\n【最终目标】在9个月内完成备考。"
                + "\n【资源预算】每周最多10小时，建议投入8小时并保留2小时缓冲。"
            )
        }
    )

    result = PlanningValidator().validate(
        value,
        route(),
        available_minutes=20,
        explicit_user_request=(
            "我计划用9个月备考，每周可学习10小时，请制定长期规划。"
        ),
        short_term_action="reuse",
        daily_task_action="reuse",
    )

    assert result.valid, result.issues


def test_validator_accepts_stage_schedule_time_windows() -> None:
    value = output(
        selected_textbook_route_id="textbook_formula",
        selected_stage_id="stage-1",
        selected_books=["《中药学》"],
        selection_reason="从基础中药阶段开始。",
    ).model_copy(
        update={
            "long_term_plan_content": (
                output().long_term_plan_content
                + "\n【最终目标】在9个月内完成备考。"
                + "\n【能力路径与阶段】第1—3个月完成基础中药；"
                + "第4—9个月进入方剂学习并完成综合验收。"
                + "\n【资源预算】每周最多10小时，建议投入8小时并保留2小时缓冲。"
            )
        }
    )

    result = PlanningValidator().validate(
        value,
        textbook_bound_route(),
        available_minutes=20,
        explicit_user_request=(
            "我计划用9个月备考，每周可学习10小时，请制定长期规划。"
        ),
        short_term_action="reuse",
        daily_task_action="reuse",
        confirmed_prerequisite_courses={"中医诊断学"},
    )

    assert result.valid, result.issues


def test_validator_rejects_deferred_prerequisite_training_in_long_plan() -> None:
    value = output(
        long_term_plan_content=(
            output().long_term_plan_content
            + "\n中医诊断学前置训练将在后续计划中另行确认。"
        ),
        selected_textbook_route_id="textbook_formula",
        selected_stage_id="stage-1",
        selected_books=["《中药学》"],
        selection_reason="从基础中药阶段开始，逐步推进。",
    )

    result = PlanningValidator().validate(
        value,
        textbook_bound_route(),
        unmet_prerequisite_courses={"中医诊断学"},
    )

    assert not result.valid
    assert any("前置训练" in issue and "中医诊断学" in issue for issue in result.issues)


def test_validator_accepts_scheduled_prerequisite_training_in_long_plan() -> None:
    value = output(
        long_term_plan_content=(
            output().long_term_plan_content
            + "\n阶段一先安排中医诊断学基本证候识别训练，为期30天，"
            + "使用《中医诊断学》配套练习，以完成基本证候辨析为验收标准。"
        ),
        selected_textbook_route_id="textbook_formula",
        selected_stage_id="stage-1",
        selected_books=["《中药学》"],
        selection_reason="先补中医诊断学前置训练，再进入方剂阶段。",
    )

    result = PlanningValidator().validate(
        value,
        textbook_bound_route(),
        unmet_prerequisite_courses={"中医诊断学"},
    )

    assert result.valid, result.issues


def test_validator_rejects_precise_claims_when_evidence_quality_is_unknown() -> None:
    value = output().model_copy(
        update={
            "short_term_plan_content": (
                output().short_term_plan_content
                + "\n未来两周聚焦阴阳专题；当前掌握度为0，已有20个薄弱知识点。"
            )
        }
    )

    result = PlanningValidator().validate(
        value,
        route(),
        available_minutes=20,
        explicit_user_request="请制定未来两周的短期计划。",
        evidence_status="insufficient",
        evidence_freshness="unknown",
    )

    assert not result.valid
    assert any("不得断言精确掌握度" in issue for issue in result.issues)


def test_validator_accepts_cautious_focus_when_evidence_is_insufficient() -> None:
    value = output().model_copy(
        update={
            "short_term_plan_content": (
                output().short_term_plan_content
                + "\n未来两周把阴阳专题作为待验证的学习重点。"
            )
        }
    )

    result = PlanningValidator().validate(
        value,
        route(),
        available_minutes=20,
        explicit_user_request="请制定未来两周的短期计划。",
        evidence_status="insufficient",
        evidence_freshness="unknown",
    )

    assert result.valid, result.issues


def test_validator_rejects_placeholder_stage_without_trusted_route_phases() -> None:
    provisional = ResolvedPlanningRoute(
        goal_type="credential",
        goal_name="待解析目标",
        planning_status="provisional",
        match_reason="no_safe_match",
        assumptions=["等待补充目标。"],
    )
    value = output().model_copy(
        update={
            "long_term_plan_stages": [
                {
                    "stage": 1,
                    "book": ["待确认教材"],
                    "goal": "完成当前长期学习阶段目标",
                }
            ]
        }
    )

    result = PlanningValidator().validate(value, provisional, available_minutes=20)

    assert not result.valid
    assert any("禁止发布占位教材" in issue for issue in result.issues)
    assert any("不得使用占位教材" in issue for issue in result.issues)


def test_validator_does_not_require_route_books_to_repeat_in_natural_language_content() -> None:
    value = output().model_copy(
        update={
            "long_term_plan_content": three_layer_output()["long_term_plan_content"]
            + "\n基础阶段完成前置知识学习并提交闭卷测评。",
        }
    )

    result = PlanningValidator().validate(value, route(), available_minutes=20)

    assert result.valid, result.issues


def test_validator_accepts_natural_language_phase_paraphrase_when_books_are_preserved() -> None:
    value = output().model_copy(
        update={
            "long_term_plan_content": three_layer_output()["long_term_plan_content"]
            + "\n使用《方剂学》并提交闭卷测评。",
        }
    )

    result = PlanningValidator().validate(value, route(), available_minutes=20)

    assert result.valid, result.issues


def test_validator_uses_system_owned_reuse_action_to_skip_long_term_revalidation() -> None:
    value = output().model_copy(
        update={
            "long_term_plan_content": "系统已保存的长期正文。",
            "long_term_plan_stages": [
                {
                    "stage": 9,
                    "book": ["《模型误写教材》"],
                    "goal": "模型改写了不应更新的长期目标",
                }
            ],
        }
    )

    result = PlanningValidator().validate(
        value,
        route(),
        available_minutes=20,
        long_term_action="reuse",
    )

    assert result.valid, result.issues


def test_validator_does_not_require_exact_system_phase_label_in_model_text() -> None:
    value = output().model_copy(
        update={
            "long_term_plan_content": three_layer_output()["long_term_plan_content"]
            + "\n第一步使用《方剂学》建立后续辨析所需的理论基础。",
        }
    )

    result = PlanningValidator().validate(value, route(), available_minutes=20)

    assert result.valid, result.issues


def test_validator_allows_classic_title_as_formula_source_not_selected_textbook() -> None:
    value = output().model_copy(
        update={
            "daily_task_content": (
                output().daily_task_content
                + "明确四君子汤出处为《太平惠民和剂局方》，再复述其配伍。"
            )
        }
    )

    result = PlanningValidator().validate(value, route(), available_minutes=20)

    assert result.valid, result.issues


def test_validator_accepts_unambiguous_short_title_for_route_book() -> None:
    long_title_route = route().model_copy(
        update={
            "phases": [
                DefaultRoutePhase(
                    phase_id="P1",
                    name="基础阶段",
                    objective="掌握基础理论",
                    books=["《国家医师资格考试大纲（中医类别，当前年度版）》"],
                    exit_evidence=["闭卷测评"],
                )
            ]
        }
    )
    value = output().model_copy(
        update={
            "long_term_plan_content": output().long_term_plan_content.replace(
                "《方剂学》", "《国家医师资格考试大纲》"
            ),
            "short_term_plan_content": output().short_term_plan_content.replace(
                "《方剂学》", "《国家医师资格考试大纲》"
            ),
                "daily_task_content": output().daily_task_content.replace(
                    "《方剂学》", "《国家医师资格考试大纲》"
                ),
                "long_term_plan_stages": [
                    {
                        "stage": 1,
                        "stage_name": "基础阶段",
                        "book": ["《国家医师资格考试大纲》"],
                        "goal": "掌握基础理论",
                        "duration_days": 30,
                        "schedule_summary": (
                            "使用《国家医师资格考试大纲》梳理考点，形成框架笔记，"
                            "并以闭卷测评验收。"
                        ),
                    }
                ],
            }
        )

    result = PlanningValidator().validate(
        value, long_title_route, available_minutes=20
    )

    assert result.valid, result.issues


def test_validator_only_rejects_serious_timeout() -> None:
    slight = PlanningValidator().validate(
        output(estimated_minutes=22), route(), available_minutes=20
    )
    serious = PlanningValidator().validate(
        output(estimated_minutes=45), route(), available_minutes=20
    )
    assert slight.valid
    assert not serious.valid


def test_validator_accepts_model_textbook_selection_inside_trusted_stage() -> None:
    value = output(
        selected_textbook_route_id="textbook_formula",
        selected_stage_id="stage-2",
        selected_books=["《方剂学》", "《中医内科学》"],
        selection_reason="用户正在准备方证辨析，当前优先并行学习方剂与内科病例。",
    )

    result = PlanningValidator().validate(
        value,
        textbook_bound_route(),
        confirmed_prerequisite_courses={"中医诊断学"},
    )

    assert result.valid, result.issues


def test_validator_rejects_long_term_stages_that_change_trusted_textbook_route() -> None:
    value = output(
        long_term_plan_stages=[
            {"stage": 1, "book": ["《模型虚构教材》"], "goal": "模型改写目标"}
        ],
        selected_textbook_route_id="textbook_formula",
        selected_stage_id="stage-1",
        selected_books=["《中药学》"],
        selection_reason="当前从基础阶段开始。",
    )

    result = PlanningValidator().validate(value, textbook_bound_route())

    assert not result.valid
    assert any("长期阶段" in issue for issue in result.issues)


def test_validator_rejects_unknown_textbook_stage() -> None:
    value = output(
        selected_textbook_route_id="textbook_formula",
        selected_stage_id="stage-99",
        selected_books=["《方剂学》"],
        selection_reason="用户需要复习方剂。",
    )

    result = PlanningValidator().validate(value, textbook_bound_route())

    assert not result.valid
    assert any("阶段" in issue for issue in result.issues)


def test_validator_rejects_textbook_selection_outside_stage() -> None:
    value = output(
        selected_textbook_route_id="textbook_formula",
        selected_stage_id="stage-1",
        selected_books=["《方剂学》"],
        selection_reason="用户希望学习方剂。",
    )

    result = PlanningValidator().validate(value, textbook_bound_route())

    assert not result.valid
    assert any("当前阶段" in issue for issue in result.issues)


def test_validator_rejects_more_than_two_selected_books() -> None:
    value = output(
        selected_textbook_route_id="textbook_formula",
        selected_stage_id="stage-2",
        selected_books=["《方剂学》", "《中医内科学》", "《伤寒论选读》"],
        selection_reason="用户希望并行学习。",
    )

    result = PlanningValidator().validate(value, textbook_bound_route())

    assert not result.valid
    assert any("1—2" in issue for issue in result.issues)


def test_validator_rejects_stage_when_prerequisite_is_unconfirmed() -> None:
    value = output(
        selected_textbook_route_id="textbook_formula",
        selected_stage_id="stage-2",
        selected_books=["《方剂学》"],
        selection_reason="用户需要学习方剂。",
    )

    result = PlanningValidator().validate(value, textbook_bound_route())

    assert not result.valid
    assert any("中医诊断学" in issue and "前置" in issue for issue in result.issues)


def test_validator_accepts_evidence_authorized_introductory_cross_stage_focus() -> None:
    value = output(
        short_term_plan_content=(
            "未来7天入门预习《方剂学》，覆盖四君子汤、参苓白术散和理中丸。"
            "前段核对组成与功用，中段比较主治，周期末提交闭卷比较表。"
            "本专题不代表完成方剂阶段，也不改变长期阶段。"
        ),
        short_term_progression_nodes=[
            "完成四君子汤、参苓白术散和理中丸教材核对。",
            "提交四君子汤、参苓白术散和理中丸闭卷比较表。",
        ],
        expected_output="一份四君子汤、参苓白术散和理中丸闭卷比较表。",
        completion_criteria="能够闭卷比较三方，不据此申报阶段完成。",
        selected_textbook_route_id="textbook_formula",
        selected_stage_id="stage-2",
        selected_books=["《方剂学》"],
        selection_reason="系统证据支持本次同路线临时专题预习。",
    )

    result = PlanningValidator().validate(
        value,
        textbook_bound_route(),
        long_term_action="reuse",
        short_term_action="update",
        active_scope="short_term",
        temporary_focus_overlay={
            "mode": "temporary_cross_stage",
            "route_id": "textbook_formula",
            "progression_stage_id": "stage-1",
            "focus_stage_id": "stage-2",
            "focus_books": ["《方剂学》"],
            "focus_names": ["四君子汤", "参苓白术散", "理中丸"],
            "prerequisite_mode": "introductory_preview",
        },
    )

    assert result.valid, result.issues


def test_validator_accepts_prerequisite_training_book_in_short_term_plan() -> None:
    """前置课程未确认完成时，短期计划若把前置教材选入本周期，
    视为前置训练已纳入计划，不触发强前置门禁。"""
    value = output(
        selected_textbook_route_id="textbook_formula",
        selected_stage_id="stage-2",
        selected_books=["《中医诊断学》"],
        selection_reason="先完成《中医诊断学》前置训练，再进入阶段二正式教材。",
    )

    result = PlanningValidator().validate(
        value,
        textbook_bound_route(),
        long_term_action="reuse",
        short_term_action="update",
    )

    assert result.valid, result.issues
    assert not any("中医诊断学" in issue and "前置" in issue for issue in result.issues)


def test_validator_rejects_dependent_selection_even_when_future_remediation_is_mentioned() -> None:
    value = output(
        long_term_plan_content=(
            output().long_term_plan_content
            + "\n进入方剂学习前先补修中医诊断学，并通过基础辨证练习。"
        ),
        selected_textbook_route_id="textbook_formula",
        selected_stage_id="stage-2",
        selected_books=["《方剂学》"],
        selection_reason="用户尚未完成中医诊断学，先补前置基础再进入本阶段。",
    )

    result = PlanningValidator().validate(
        value,
        textbook_bound_route(),
        unmet_prerequisite_courses={"中医诊断学"},
    )

    assert not result.valid
    assert any("所选阶段的强前置尚未确认" in issue for issue in result.issues)


def test_validator_accepts_natural_cycle_nodes_and_classic_short_titles() -> None:
    value = output(
        long_term_plan_content=(
            output().long_term_plan_content
            + "\n经典阶段阅读《伤寒论》《金匮》《温病》，形成条文辨析记录。"
        ),
        short_term_plan_content=(
            "## 当前周期目标\n本周完成方剂基础辨析。\n"
            "## 具体任务块\n本周先完成治法框架，下个节点完成方证验收。\n"
            "## 复习任务\n完成知识点题目后，闭卷复述治法与方证联系，以遗漏项清零为通过。"
        ),
        selected_textbook_route_id="textbook_formula",
        selected_stage_id="stage-2",
        selected_books=["《方剂学》"],
        selection_reason="已有中医诊断学完成证据，当前进入方剂辨析。",
    )

    result = PlanningValidator().validate(
        value,
        textbook_bound_route(),
        confirmed_prerequisite_courses={"中医诊断学"},
    )

    assert result.valid, result.issues


def test_validator_accepts_force_prerequisite_daily_task_with_prerequisite_chapter() -> None:
    value = output(
        learning_chapter="《中医诊断学》望诊章",
        focus_knowledge_points=["望神", "望色"],
        daily_task_content=(
            "## 今日目标\n完成《中医诊断学》望诊章前置训练。\n"
            "## 分步动作和时间分配\n阅读望诊要点并完成诊断学练习。\n"
            "## 客观完成标准\n完成望诊章节测评。"
        ),
        expected_output="一份《中医诊断学》望诊章学习记录。",
        completion_criteria="完成望诊章测评且正确率达标。",
    )

    result = PlanningValidator().validate(
        value,
        textbook_bound_route(),
        active_scope="daily_task",
        force_prerequisite_daily_task=True,
        required_prerequisite_courses={"中医诊断学"},
        long_term_action="reuse",
        short_term_action="reuse",
    )

    assert result.valid, result.issues


def test_validator_rejects_force_prerequisite_daily_task_with_stage2_chapter() -> None:
    value = output(
        learning_chapter="《方剂学》补益剂章",
        focus_knowledge_points=["四君子汤组成", "四君子汤功用"],
        daily_task_content=(
            "## 今日目标\n复习《方剂学》四君子汤。\n"
            "## 分步动作和时间分配\n闭卷默写后核对。\n"
            "## 客观完成标准\n四味药全部正确。"
        ),
        expected_output="一份闭卷默写记录。",
        completion_criteria="四味药全部正确。",
    )

    result = PlanningValidator().validate(
        value,
        textbook_bound_route(),
        active_scope="daily_task",
        force_prerequisite_daily_task=True,
        required_prerequisite_courses={"中医诊断学"},
    )

    assert not result.valid
    assert any(
        issue.startswith("daily_task_prerequisite_required")
        for issue in result.issues
    )


def test_daily_task_schema_rejects_missing_expected_output_before_validator() -> None:
    with pytest.raises(ValidationError):
        output(
            learning_chapter="《中医诊断学》望诊章",
            focus_knowledge_points=["望神", "望色"],
            expected_output="",
            completion_criteria="完成望诊章测评且正确率达标。",
        )


def test_daily_task_schema_rejects_missing_completion_criteria_before_validator() -> None:
    with pytest.raises(ValidationError):
        output(
            learning_chapter="《中医诊断学》望诊章",
            focus_knowledge_points=["望神", "望色"],
            expected_output="一份《中医诊断学》望诊章学习记录。",
            completion_criteria="",
        )


def test_validator_force_prerequisite_daily_task_does_not_affect_non_force_mode() -> None:
    # 非 force 模式下（NONE 初始请求、用户确认前置课程完成），
    # 即使 learning_chapter 是 stage-2 教材也不触发前置课程强制校验。
    value = output(
        learning_chapter="《方剂学》补益剂章",
        focus_knowledge_points=["四君子汤组成", "四君子汤功用"],
        daily_task_content=(
            "## 今日目标\n复习《方剂学》四君子汤。\n"
            "## 分步动作和时间分配\n闭卷默写后核对。\n"
            "## 客观完成标准\n四味药全部正确。"
        ),
        expected_output="一份闭卷默写记录。",
        completion_criteria="四味药全部正确。",
    )

    result = PlanningValidator().validate(
        value,
        textbook_bound_route(),
        active_scope="daily_task",
        confirmed_prerequisite_courses={"中医诊断学"},
        long_term_action="reuse",
        short_term_action="reuse",
    )

    assert result.valid, result.issues

import pytest
from pydantic import ValidationError

