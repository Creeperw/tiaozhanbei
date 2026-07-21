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
        {"stage": 1, "book": ["《方剂学》"], "goal": "掌握基础理论"}
    ]
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
            {"stage": 1, "book": ["《中药学》"], "goal": "建立药性基础"},
            {
                "stage": 2,
                "book": [
                    "《方剂学》",
                    "《中医内科学》",
                    "《伤寒论选读》",
                    "《金匮要略》",
                    "《温病学》",
                ],
                "goal": "建立治法和配伍能力",
            },
        ]
    return ThreeLayerPlanningModelOutput.model_validate(data)


def test_validator_accepts_minor_format_and_wording_differences() -> None:
    result = PlanningValidator().validate(output(), route(), available_minutes=20)
    assert result.valid, result.issues


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


def test_validator_rejects_missing_core_region() -> None:
    value = output().model_copy(update={"daily_task_content": "只有一行普通任务"})
    result = PlanningValidator().validate(value, route(), available_minutes=20)
    assert not result.valid
    assert any("当日" in issue or "核心" in issue for issue in result.issues)


def test_validator_rejects_book_outside_route() -> None:
    value = output().model_copy(
        update={"daily_task_content": output().daily_task_content + "阅读《针灸学》。"}
    )
    result = PlanningValidator().validate(value, route(), available_minutes=20)
    assert not result.valid
    assert any("教材" in issue for issue in result.issues)


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
                        "book": ["《国家医师资格考试大纲》"],
                        "goal": "掌握基础理论",
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


def test_validator_rejects_completely_unrelated_daily_task() -> None:
    value = output().model_copy(
        update={
            "daily_task_content": (
                "## 今日目标\n练习游泳。\n## 分步动作和时间分配\n下水练习。\n"
                "## 客观完成标准\n游完五百米。"
            )
        }
    )
    result = PlanningValidator().validate(value, route(), available_minutes=20)
    assert not result.valid
    assert any("失配" in issue for issue in result.issues)


def test_validator_rejects_daily_actions_disguised_as_short_term_plan() -> None:
    value = output(
        short_term_plan_content=(
            "## 当前周期目标\n本周掌握四君子汤。\n"
            "## 具体任务块\n今晚浏览知识卡、绘制配伍图并完成5道题。\n"
            "## 复习任务\n两天后闭卷默写，具体时间待系统调度。"
        )
    )

    result = PlanningValidator().validate(value, route(), available_minutes=20)

    assert not result.valid
    assert any("今日任务" in issue for issue in result.issues)
    assert any("完整周期" in issue for issue in result.issues)
    assert any("系统调度" in issue for issue in result.issues)


def test_validator_rejects_model_owned_fixed_review_interval() -> None:
    value = output(
        short_term_plan_content=(
            "## 当前周期目标\n本周掌握四君子汤。\n"
            "## 具体任务块\n周初学习组成，周中完成辨析，周末综合验收。\n"
            "## 复习任务\n已学内容每满7天复习一次；具体时间待系统调度。"
        )
    )

    result = PlanningValidator().validate(value, route(), available_minutes=20)

    assert not result.valid
    assert any("系统调度" in issue for issue in result.issues)


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


def test_validator_accepts_natural_cycle_nodes_and_classic_short_titles() -> None:
    value = output(
        long_term_plan_content=(
            output().long_term_plan_content
            + "\n经典阶段阅读《伤寒论》《金匮》《温病》，形成条文辨析记录。"
        ),
        short_term_plan_content=(
            "## 当前周期目标\n本周完成方剂基础辨析。\n"
            "## 具体任务块\n本周先完成治法框架，下个节点完成方证验收。\n"
            "## 复习任务\n具体时间待系统调度。"
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


def test_validator_rejects_short_plan_book_outside_selected_stage() -> None:
    value = output(
        short_term_plan_content=(
            output().short_term_plan_content
            + "\n本周期改用《中药学》推进，周末验收。"
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

    assert not result.valid
    assert any("所选阶段" in issue for issue in result.issues)
