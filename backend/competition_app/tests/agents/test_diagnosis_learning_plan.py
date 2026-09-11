from types import SimpleNamespace

import pytest

from competition_app.agents.diagnosis import DiagnosisAgent
from competition_app.agents.knowledge_base import KnowledgeBaseAgent
from competition_app.contracts.base import AgentEnvelope
from competition_app.contracts.default_route import (
    DefaultRoutePhase,
    DefaultRouteSource,
    ResolvedPlanningRoute,
)
from competition_app.contracts.knowledge import (
    EvidenceItem,
    EvidencePack,
    LearningFocusEvidence,
)
from competition_app.contracts.textbook_route import (
    ResolvedTextbookRoute,
    TextbookLearningRoute,
    TextbookPrerequisiteRule,
    TextbookRouteStage,
)
from competition_app.llm.stub import StubChatModel


@pytest.mark.asyncio
@pytest.mark.parametrize("reason", ["business_schema_invalid", "invalid_json", "transport_error"])
async def test_legacy_compiler_retry_format_failure_preserves_revision_feedback(reason, monkeypatch) -> None:
    from competition_app.contracts.plan_compilation import PlanCompilationEnvelope
    from competition_app.llm.openai_compatible import ModelResponseError

    class RevisionReached(Exception):
        pass

    class DraftModel:
        def __init__(self):
            self.calls = []

        async def complete_json(self, role, payload, **kwargs):
            data = payload["payload"]
            self.calls.append(data)
            if "compiler_revision_issues" in data:
                assert data["previous_plan_document"] == "待补全阶段时长的规划正文"
                assert data["compiler_revision_issues"][0]["field_path"] == "/total_duration_days"
                raise RevisionReached()
            return {"plan_document": "待补全阶段时长的规划正文"}

    class Compiler:
        calls = 0

        async def compile(self, *args, **kwargs):
            self.calls += 1
            if self.calls == 2:
                raise ModelResponseError("invalid retry response", reason=reason)
            return PlanCompilationEnvelope.model_validate({
                "source_digest": "a" * 64,
                "result": {"status": "needs_revision", "issues": [{
                    "code": "missing_required_field", "category": "missing",
                    "field_path": "/total_duration_days",
                    "source_refs": ["plan_document"],
                }]},
            })

    context = build_context("diagnosis")
    context["plan_scope"] = "long_term"
    context["system_data"]["completed_courses"] = ["中医诊断学"]
    context["dependency_outputs"] = {
        "knowledge": await build_knowledge(build_context("knowledge")),
        "route_resolution": textbook_route_output(),
    }
    model = DraftModel()
    agent = DiagnosisAgent(model)
    # The old outer retry applies only to non-fixed routes. Fixed routes now
    # own their extraction budget inside the compiler across all call sites.
    monkeypatch.setattr(agent, "_compiler_route_context", lambda *args: {})
    agent.plan_contract_compiler = Compiler()
    expected = ModelResponseError if reason == "transport_error" else RevisionReached
    with pytest.raises(expected):
        await agent.run(context)
    assert agent.plan_contract_compiler.calls == 2
    assert len(model.calls) == (1 if reason == "transport_error" else 2)


@pytest.mark.asyncio
@pytest.mark.parametrize("issue_path", ["/field_anchors", "/plan_document/stages"])
async def test_fixed_route_extraction_exhaustion_never_rewrites_author_document(issue_path):
    from competition_app.agents.diagnosis import PlanCompilationError
    from competition_app.contracts.plan_compilation import PlanCompilationEnvelope
    from competition_app.tests.agents.test_fixed_route_binding import fixture

    doc, _, _ = fixture()

    class DraftModel:
        calls = 0

        async def complete_json(self, role, payload, **kwargs):
            self.calls += 1
            assert "compiler_revision_issues" not in payload["payload"]
            return {"plan_document": doc}

    class Compiler:
        calls = 0

        async def compile(self, *args, **kwargs):
            self.calls += 1
            assert kwargs["diagnosis_output"]["plan_document"] == doc
            assert kwargs["trusted_route"]["binding_mode"] == "fixed_route_v1"
            return PlanCompilationEnvelope.model_validate({
                "source_digest": "a" * 64, "revision_count": 1,
                "result": {"status": "needs_revision", "issues": [{
                    "code": "schema_invalid", "category": "invalid",
                    "field_path": issue_path,
                }]},
            })

    context = build_context("diagnosis")
    context["plan_scope"] = "long_term"
    context["dependency_outputs"] = {
        "knowledge": await build_knowledge(build_context("knowledge")),
        "route_resolution": textbook_route_output(),
    }
    model = DraftModel()
    agent = DiagnosisAgent(model)
    agent.plan_contract_compiler = Compiler()
    with pytest.raises(PlanCompilationError, match="不触发作者重写"):
        await agent.run(context)
    assert model.calls == 1
    assert agent.plan_contract_compiler.calls == 1


def test_unscoped_output_repairs_empty_books_from_trusted_route() -> None:
    normalized = DiagnosisAgent._normalize_unscoped_planning_output(
        {
            "long_term_plan_stages": [
                {"stage": 1, "book": [], "goal": ""},
                {"stage": 2, "book": [], "goal": "阶段二"},
            ]
        },
        {
            "phases": [
                {"books": ["《中医学基础》"], "objective": "建立中医基础"},
                {"books": ["《方剂学》"], "objective": "掌握方剂基础"},
            ]
        },
    )

    assert normalized["long_term_plan_stages"] == [
        {"stage": 1, "book": ["《中医学基础》"], "goal": "建立中医基础"},
        {"stage": 2, "book": ["《方剂学》"], "goal": "阶段二"},
    ]


class FakeRetrievalTool:
    async def build_evidence_pack(self, query: str) -> EvidencePack:
        return EvidencePack(
            evidence_pack_id="EP_1",
            query=query,
            resolved_kp_ids=["KP_FJ_001"],
            evidence_items=[
                EvidenceItem(
                    evidence_id="E_1",
                    source_id="教材:1",
                    content_summary="四君子汤由人参、白术、茯苓、甘草组成。",
                    authority_level="textbook",
                    confidence=0.95,
                    bridge_layer="strict",
                )
            ],
        )


class CapturingDiagnosisModel:
    def __init__(self) -> None:
        self.payload = None

    async def complete_json(self, role, payload, on_delta=None):
        self.payload = payload
        return {
            "summary": "当前基础尚可，但方剂组成回忆不稳定。",
            "risk_flags": ["组成记忆不稳"],
            "recommendations": ["先主动回忆，再对照教材纠错。"],
            "uncertainty": [],
            "long_term_plan_content": (
                "【最终目标】两周内建立四君子汤组成、功效与辨证应用的稳定联系。"
                "【能力路径与阶段】组成记忆→功效理解→辨证应用。"
                "【阶段里程碑】完成组成、功效与应用的闭卷说明；截止两周后。"
                "【资源预算】每天12分钟，缓冲时间待确认。"
                "【重规划条件】连续两次回忆不达标时调整计划。"
                "【保温底线】每周一次组成回忆。"
            ),
            "short_term_plan_content": (
                "【当前主目标】今天完成四君子汤组成主动回忆。"
                "【长期目标保温】本周保留一次组成回忆。"
                "【时间分配】12分钟用于主目标，保温安排待确认。"
                "【具体任务块】默写组成并口述各药作用，产出组成清单和配伍说明，"
                "完成标准为四味药正确并说明君臣佐使。"
                "【复习任务】完成后安排一次纠错复述。"
                "【反馈指标】记录完成率、正确率与错因。"
            ),
            "priority_mode": "temporary_focus",
            "adjustment_reason": "近期正确率下降且复习间隔偏长。",
            "route_context": {
                "goal_type": "model-invented",
                "goal_name": "模型改写目标",
                "planning_status": "provisional",
                "match_reason": "model_claim",
                "route_id": "MODEL_ROUTE",
                "route_version": 999,
                "route_status": None,
                "phases": [],
                "sources": [],
                "assumptions": ["模型新增假设", "解析器假设"],
                "unknowns_to_confirm": ["模型待确认项", "解析器待确认项"],
                "runtime_checks": [],
            },
            "goal_contract": {
                "goal_type": "course",
                "goal_name": "四君子汤学习",
                "observable_ability": "能够闭卷说明组成、功效与配伍。",
                "acceptance_evidence": ["闭卷说明记录"],
            },
            "milestones": [
                {
                    "milestone_id": "M1",
                    "name": "完成基础辨析",
                    "success_criteria": "能够闭卷完成组成与配伍辨析。",
                    "evidence_required": ["闭卷作答记录", "错因订正记录"],
                }
            ],
            "short_term_learning_package": {
                "time_window_weeks": 2,
                "current_goal": "两周内稳定回忆四君子汤组成。",
                "task_blocks": ["主动回忆", "教材核对", "错因订正"],
                "expected_output": "回忆清单与错因记录。",
                "completion_criteria": "连续两次闭卷回忆四味药全部正确。",
            },
            "recovery_policy": {
                "trigger_conditions": ["连续两次任务未达标"],
                "recovery_actions": ["降低单次任务负荷并回到组成回忆"],
            },
            "recommendation_trace": {
                "default_route": "依据课程路线的基础辨析阶段。",
                "user_state": "近期组成回忆不稳定。",
                "time_constraint": "当前可用15分钟。",
                "current_task": "先完成12分钟主动回忆。",
            },
            "assumptions": ["模型新增假设", "解析器假设"],
            "unknowns_to_confirm": ["模型待确认项", "解析器待确认项"],
            "learning_task": {
                "task_type": "active_recall",
                "task_content": "默写四君子汤组成并口述各药作用。",
                "estimated_minutes": 12,
                "expected_output": "一份组成清单和一段配伍说明。",
                "completion_criteria": "四味药全部正确且能说明君臣佐使关系。",
            },
        }


def test_scoped_long_plan_uses_system_stages_and_completes_fixed_sections() -> None:
    route_context = DiagnosisAgent._trusted_route_context(approved_route_output().payload)

    output = DiagnosisAgent._expand_scoped_planning_output(
        "long_term",
        {
            "long_term_plan_content": "系统掌握方剂学核心知识。",
            "long_term_plan_stages": [
                {"stage": 1, "book": ["模型虚构教材"], "goal": "模型改写目标"}
            ],
        },
        {"available_minutes": 30},
        route_context,
    )

    assert "【最终目标】系统掌握方剂学核心知识。" in output.long_term_plan_content
    assert "【能力路径与阶段】" in output.long_term_plan_content
    assert "【重规划条件】" in output.long_term_plan_content
    assert [stage.goal for stage in output.long_term_plan_stages] == [
        phase["objective"] for phase in route_context["phases"]
    ]
    assert all("模型虚构教材" not in stage.book for stage in output.long_term_plan_stages)


def route_output(payload: ResolvedPlanningRoute) -> AgentEnvelope[ResolvedPlanningRoute]:
    return AgentEnvelope(
        artifact_id="ART_ROUTE",
        artifact_type="resolved_planning_route",
        case_id="CASE_1",
        trace_id="TRACE_1",
        request_id="REQ_1",
        execution_id="EXE_1",
        step_id="route_resolution",
        producer="default_route_resolver",
        task_type="resolved_planning_route",
        learner_id="L1",
        payload=payload,
    )


def approved_route_output() -> AgentEnvelope[ResolvedPlanningRoute]:
    return route_output(
        ResolvedPlanningRoute(
            goal_type="course",
            goal_name="四君子汤系统学习",
            planning_status="approved_route",
            match_reason="canonical_name",
            route_id="ROUTE_TRUSTED",
            route_version=7,
            route_status="approved",
            planning_label="course_default_route",
            phases=[
                DefaultRoutePhase(
                    phase_id="P1",
                    name="基础辨析",
                    objective="建立组成、功效与配伍联系。",
                    books=["《方剂学》"],
                    exit_evidence=["闭卷辨析记录"],
                    source_refs=["SRC_1"],
                )
            ],
            sources=[
                DefaultRouteSource(
                    source_id="SRC_1",
                    source_type="textbook",
                    title="方剂学教材",
                    source_version="2025",
                )
            ],
            runtime_checks=["核验当前教材版本"],
        )
    )


def provisional_route_output() -> AgentEnvelope[ResolvedPlanningRoute]:
    return route_output(
        ResolvedPlanningRoute(
            goal_type="course",
            goal_name="四君子汤个性化学习",
            planning_status="provisional",
            match_reason="no_safe_match",
            assumptions=["解析器假设", "共同项"],
            unknowns_to_confirm=["解析器待确认项", "共同待确认项"],
        )
    )


def textbook_route_output(
    *, planning_status: str = "resolved"
) -> AgentEnvelope[ResolvedPlanningRoute]:
    textbook = TextbookLearningRoute(
        route_id="textbook_formula",
        route_version=1,
        status="approved",
        goal_name="方剂教材主线",
        stages=[
            TextbookRouteStage(
                stage_id="stage-1",
                order=1,
                name="中药基础",
                objective="建立常用中药性能功效基础。",
                books=["《中药学》"],
                exit_evidence=["完成中药性能功效测验"],
                source_refs=["USER_ROUTE"],
            ),
            TextbookRouteStage(
                stage_id="stage-2",
                order=2,
                name="方剂辨析",
                objective="建立治法、方剂和证候联系。",
                books=["《方剂学》", "《中医内科学》"],
                exit_evidence=["完成方证辨析记录"],
                source_refs=["USER_ROUTE"],
            ),
        ],
        prerequisites=[
            TextbookPrerequisiteRule(
                course="中医诊断学",
                before_stage_id="stage-2",
                reason="进入方剂阶段前需要中医诊断基础。",
            )
        ],
        source_refs=["USER_ROUTE"],
        reviewed_by="USER",
    )
    textbook_resolution = ResolvedTextbookRoute(
        planning_status=planning_status,
        match_reason="test",
        route=textbook,
        clarification_questions=(
            ["请确认具体考试名称。"]
            if planning_status == "needs_clarification"
            else []
        ),
    )
    base = approved_route_output().payload.model_copy(
        update={"textbook_route": textbook_resolution}
    )
    return route_output(base)


class TextbookSelectingDiagnosisModel:
    def __init__(self) -> None:
        self.payload = None

    async def complete_json(self, role, payload, on_delta=None):
        self.payload = payload
        if role == "plan_contract_compiler":
            from competition_app.llm.stub import StubChatModel

            return await StubChatModel().complete_json(role, payload, on_delta)
        return {
            "long_term_plan_content": (
                "## 目标契约\n建立方剂辨析能力。\n"
                "## 长期阶段路径\n按教材主线推进。\n"
                "## 长期重规划触发器\n考试目标或稳定证据变化时调整。"
            ),
            "short_term_plan_content": (
                "## 当前周期目标\n完成四君子汤与补气剂专项辨析。\n"
                "## 本周期任务\n周初回忆四君子汤，周中完成补气类方比较，周末综合验收。"
            ),
            "daily_task_content": (
                "## 今日目标\n回忆四君子汤组成。\n"
                "## 分步动作\n闭卷回忆后核对《方剂学》。\n"
                "## 客观完成标准\n组成完整且能解释配伍。"
            ),
            "estimated_minutes": 15,
            "expected_output": "一份四君子汤回忆记录。",
            "completion_criteria": "组成完整且配伍说明正确。",
            "total_duration_days": 60,
            "long_term_plan_stages": [
                {
                    "stage": 1,
                    "stage_name": "中药基础",
                    "book": ["《中药学》"],
                    "goal": "建立常用中药性能功效基础。",
                    "duration_days": 30,
                    "schedule_summary": (
                        "使用《中药学》梳理常用中药性能功效，形成分类笔记，"
                        "并以闭卷辨析记录验收。"
                    ),
                },
                {
                    "stage": 2,
                    "stage_name": "方证联系",
                    "book": ["《方剂学》", "《中医内科学》"],
                    "goal": "建立治法、方剂和证候联系。",
                    "duration_days": 30,
                    "schedule_summary": (
                        "使用《方剂学》和《中医内科学》完成方证比较，形成对照表，"
                        "并以综合案例说明验收。"
                    ),
                },
            ],
            "short_term_duration_days": 7,
            "short_term_progression_nodes": [
                "周初完成《方剂学》四君子汤主动回忆与教材核对。",
                "周末完成补气类方比较并提交综合验收记录。",
            ],
            "learning_chapter": "《方剂学》补益剂章",
            "focus_knowledge_points": ["四君子汤组成", "四君子汤配伍作用"],
            "selected_textbook_route_id": "textbook_formula",
            "selected_stage_id": "stage-2",
            "selected_books": ["《方剂学》"],
            "selection_reason": "用户正在学习补气剂，需要优先建立方证联系。",
        }


class TemporaryFormulaFocusDiagnosisModel:
    def __init__(self) -> None:
        self.payloads = []

    async def complete_json(self, role, payload, on_delta=None):
        self.payloads.append(payload)
        if role == "plan_contract_compiler":
            document = payload["payload"]["diagnosis_output"]["plan_document"]
            values = {
                "short_term_plan_content": document, "duration_days": 7,
                "progression_nodes": ["完成四君子汤、参苓白术散和理中丸教材核对。", "提交四君子汤、参苓白术散和理中丸闭卷比较表。"],
                "expected_output": "一份四君子汤、参苓白术散和理中丸闭卷比较表。",
                "completion_criteria": "能够闭卷比较四君子汤、参苓白术散和理中丸，不据此申报阶段完成。",
                "selected_stage_id": "stage-2", "selected_books": ["《方剂学》"],
            }
            anchors = {
                f"/{key}": [{"source_field": "plan_document", "source_quote": str(item)}
                            for item in (value if isinstance(value, list) else [value])]
                for key, value in values.items()
            }
            return {"status": "compiled", "contract_version": "1.0",
                    "contract": {"scope": "short_term", **values, "field_anchors": anchors}}
        if payload["payload"].get("phase") == "assess_planning_focus":
            catalog = payload["payload"]["focus_identity_catalog"]
            stage_no = next(item["stage_no"] for item in catalog["stages"] if item["stage_id"] == "stage-2")
            book_no = next(item["book_no"] for item in catalog["books"] if item["stage_no"] == stage_no and item["name"] == "《方剂学》")
            return {
                "status": "sufficient", "focus_object_nos": [1, 2, 3],
                "focus_stage_no": stage_no, "focus_book_nos": [book_no],
                "evidence_links": [
                    {"object_no": 1, "evidence_id": "E_SJZT"},
                    {"object_no": 2, "evidence_id": "E_SLBS"},
                    {"object_no": 3, "evidence_id": "E_LZW"},
                ],
                "cross_stage_mode": "introductory_preview",
                "source_quote": payload["payload"]["user_request"],
                "reason": "本次明确要求临时入门预习三方，不推进父阶段。",
            }
        overlay = payload["payload"].get("temporary_focus_overlay")
        if not overlay:
            raise AssertionError("short-term focus overlay must reach Diagnosis")
        if payload["payload"].get("revision_issues"):
            raise AssertionError(payload["payload"]["revision_issues"])
        return {
            "plan_document": (
                "## 【当前主目标】\n\n"
                "未来7天入门预习《方剂学》，重点覆盖四君子汤、参苓白术散和理中丸。"
                "本专题不代表完成方剂阶段，也不改变长期阶段。\n\n"
                "### 当前阶段\n\nstage-2\n\n"
                "### 当前教材\n\n- 《方剂学》\n\n"
                "## 【具体任务块】\n\n"
                "### 推进节点\n\n"
                "- 完成四君子汤、参苓白术散和理中丸教材核对。\n"
                "- 提交四君子汤、参苓白术散和理中丸闭卷比较表。\n\n"
                "### 预期产出\n\n"
                "一份四君子汤、参苓白术散和理中丸闭卷比较表。\n\n"
                "### 完成标准\n\n"
                "能够闭卷比较四君子汤、参苓白术散和理中丸，不据此申报阶段完成。\n\n"
                "## 【长期目标保温】\n\n保留长期 stage-1 的原有学习安排。\n\n"
                "## 【复习任务】\n\n仅描述三方教材纠错，不自行指定复习日期。\n\n"
                "## 【反馈指标】\n\n记录完成率、正确率、遗漏项、错因和实际耗时。"
            ),
        }


class StandardDiagnosisModel:
    async def complete_json(self, role, payload, on_delta=None):
        return {
            "diagnosis": "当前需要优先巩固四君子汤的组成与配伍。",
            "risks": ["方剂组成回忆不稳定"],
            "recommendations": ["先完成一次主动回忆，再对照教材纠错。"],
            "long_term_plan": "【最终目标】建立方剂基础知识体系。",
            "short_term_plan": "【当前主目标】完成四君子汤主动回忆。",
            "next_task": "默写四君子汤组成并口述配伍作用。",
            "task_minutes": 12,
            "expected_output": "一份方剂组成与配伍说明。",
            "completion_standard": "四味药全部正确且能说明配伍作用。",
            "uncertainties": [],
        }


class MixedStandardStructuredDiagnosisModel(StandardDiagnosisModel):
    async def complete_json(self, role, payload, on_delta=None):
        result = await super().complete_json(role, payload, on_delta)
        result.update(
            goal_contract={
                "goal_type": "course",
                "goal_name": "模型目标",
                "observable_ability": "能够闭卷说明组成与配伍。",
                "acceptance_evidence": ["闭卷说明记录"],
            },
            assumptions=["模型混合输出假设"],
            unknowns_to_confirm=["模型混合输出待确认项"],
        )
        return result


class MixedStandardMetadataOnlyDiagnosisModel(StandardDiagnosisModel):
    async def complete_json(self, role, payload, on_delta=None):
        result = await super().complete_json(role, payload, on_delta)
        result.update(
            long_term_plan_action="reuse",
            short_term_plan_action="reuse",
            priority_mode="recovery",
            adjustment_reason="沿用系统计划并补充临时假设。",
            assumptions=["仅混入的模型假设"],
            unknowns_to_confirm=["仅混入的待确认项"],
        )
        return result


class RewritingReuseDiagnosisModel(CapturingDiagnosisModel):
    async def complete_json(self, role, payload, on_delta=None):
        result = await super().complete_json(role, payload, on_delta)
        result.update(
            long_term_plan_action="reuse",
            short_term_plan_action="reuse",
            long_term_plan_content="模型改写的长期正文。",
            short_term_plan_content="模型改写的短期正文。",
        )
        return result


class StringRouteContextDiagnosisModel(CapturingDiagnosisModel):
    async def complete_json(self, role, payload, on_delta=None):
        result = await super().complete_json(role, payload, on_delta)
        result["route_context"] = "模型将路线上下文错误地输出为自然语言。"
        return result


class StringStructuredPlanningFieldsDiagnosisModel(CapturingDiagnosisModel):
    async def complete_json(self, role, payload, on_delta=None):
        result = await super().complete_json(role, payload, on_delta)
        result.update(
            route_context="approved_route",
            goal_contract="能够掌握当前课程并通过阶段测评。",
            short_term_learning_package="未来两周完成基础学习任务。",
            recovery_policy="任务未完成时降低负荷后恢复主线。",
            recommendation_trace="默认路线到当前任务。",
        )
        return result


def build_context(step_id: str) -> dict:
    return {
        "user_request": "根据学情安排学习计划",
        "planning_request_scope": {"mode": "route", "objects": [],
                                   "source_quote": "根据学情安排学习计划",
                                   "clarification_question": None},
        "case_id": "CASE_1",
        "trace_id": "TRACE_1",
        "request_id": "REQ_1",
        "execution_id": "EXE_1",
        "step_id": step_id,
        "learner_id": "L1",
        "topic": "四君子汤",
        "available_minutes": 15,
        "user_profile": {"learning_goals": ["掌握方剂组成与配伍"]},
        "learning_profile": {"behavior_metrics": {"recent_accuracy": 0.6}},
        "system_data": {"current_stage_id": "T1"},
        "user_knowledge_states": [
            {"kp_id": "KP_FJ_001", "mastery_score": 0.58, "review_status": "due"}
        ],
        "dependency_outputs": {},
    }


async def build_knowledge(context: dict):
    return await KnowledgeBaseAgent(FakeRetrievalTool()).run(context)


def set_requested_focus(context, names):
    request = "本周仅入门预习，不推进长期阶段，专题为" + "、".join(names)
    context.update(user_request=request, planning_request_scope={
        "mode": "explicit_focus", "objects": names, "source_quote": request,
        "clarification_question": None,
    })


@pytest.mark.asyncio
async def test_stub_diagnosis_proposes_long_short_plans_and_one_task() -> None:
    diagnosis_context = build_context("diagnosis")
    diagnosis_context["dependency_outputs"] = {
        "knowledge": await build_knowledge(build_context("knowledge")),
        "route_resolution": approved_route_output(),
    }

    result = (await DiagnosisAgent(StubChatModel()).run(diagnosis_context)).payload

    proposal = result.learning_plan_proposal
    assert proposal.long_term_plan_content
    assert proposal.short_term_plan_content
    assert proposal.task_proposal.estimated_minutes > 0
    assert proposal.task_proposal.task_content


@pytest.mark.asyncio
async def test_diagnosis_without_route_resolution_builds_explicit_provisional_fallback() -> None:
    diagnosis_context = build_context("diagnosis")
    diagnosis_context["dependency_outputs"] = {
        "knowledge": await build_knowledge(build_context("knowledge"))
    }

    proposal = (
        await DiagnosisAgent(StandardDiagnosisModel()).run(diagnosis_context)
    ).payload.learning_plan_proposal

    assert proposal.planning_route.planning_status == "provisional"
    assert proposal.planning_route.match_reason == "missing_route_resolution"
    assert proposal.assumptions
    assert proposal.unknowns_to_confirm
    assert "临时规划" in proposal.long_term_plan_content
    assert "临时规划" in proposal.short_term_plan_content


@pytest.mark.asyncio
async def test_diagnosis_accepts_standard_model_output_without_summary_field() -> None:
    diagnosis_context = build_context("diagnosis")
    diagnosis_context["dependency_outputs"] = {
        "knowledge": await build_knowledge(build_context("knowledge"))
    }

    result = (await DiagnosisAgent(StandardDiagnosisModel()).run(diagnosis_context)).payload

    assert result.summary == "当前需要优先巩固四君子汤的组成与配伍。"
    assert result.learning_plan_proposal.task_proposal.task_content == "默写四君子汤组成并口述配伍作用。"
    assert result.learning_plan_proposal.long_term_plan_content
    assert result.learning_plan_proposal.short_term_plan_content
    assert result.learning_plan_proposal.goal_contract.acceptance_evidence
    assert result.learning_plan_proposal.milestones[0].evidence_required
    assert result.learning_plan_proposal.short_term_learning_package.time_window_weeks in {1, 2}
    assert result.learning_plan_proposal.recovery_policy.recovery_actions
    assert result.learning_plan_proposal.recommendation_trace.current_task


@pytest.mark.asyncio
async def test_diagnosis_ignores_structured_fields_from_mixed_standard_output() -> None:
    diagnosis_context = build_context("diagnosis")
    diagnosis_context["dependency_outputs"] = {
        "knowledge": await build_knowledge(build_context("knowledge")),
        "route_resolution": provisional_route_output(),
    }

    proposal = (
        await DiagnosisAgent(MixedStandardStructuredDiagnosisModel()).run(diagnosis_context)
    ).payload.learning_plan_proposal

    assert proposal.long_term_plan_content
    assert proposal.short_term_plan_content
    assert proposal.goal_contract.observable_ability == "能够完成当前任务：默写四君子汤组成并口述配伍作用。"
    assert proposal.assumptions == ["解析器假设", "共同项"]
    assert proposal.unknowns_to_confirm == [
        "解析器待确认项",
        "共同待确认项",
    ]


@pytest.mark.asyncio
async def test_diagnosis_preserves_metadata_only_mixed_standard_output() -> None:
    diagnosis_context = build_context("diagnosis")
    diagnosis_context.update(
        current_long_term_plan={"content": "可信长期正文。"},
        current_short_term_plan={"content": "可信短期正文。"},
    )
    diagnosis_context["dependency_outputs"] = {
        "knowledge": await build_knowledge(build_context("knowledge")),
        "route_resolution": provisional_route_output(),
    }

    proposal = (
        await DiagnosisAgent(MixedStandardMetadataOnlyDiagnosisModel()).run(diagnosis_context)
    ).payload.learning_plan_proposal

    assert proposal.long_term_plan_action == "reuse"
    assert proposal.short_term_plan_action == "reuse"
    assert proposal.priority_mode == "recovery"
    assert proposal.adjustment_reason == "沿用系统计划并补充临时假设。"
    assert proposal.assumptions == ["解析器假设", "共同项"]
    assert proposal.unknowns_to_confirm == ["解析器待确认项", "共同待确认项"]


@pytest.mark.asyncio
async def test_diagnosis_restores_trusted_plan_text_when_model_claims_reuse() -> None:
    diagnosis_context = build_context("diagnosis")
    diagnosis_context.update(
        current_long_term_plan={"content": "系统保存的长期正文。"},
        current_short_term_plan={"content": "系统保存的短期正文。"},
    )
    diagnosis_context["dependency_outputs"] = {
        "knowledge": await build_knowledge(build_context("knowledge")),
        "route_resolution": provisional_route_output(),
    }

    proposal = (
        await DiagnosisAgent(RewritingReuseDiagnosisModel()).run(diagnosis_context)
    ).payload.learning_plan_proposal

    assert proposal.long_term_plan_content == "系统保存的长期正文。"
    assert proposal.short_term_plan_content == "系统保存的短期正文。"


@pytest.mark.asyncio
async def test_diagnosis_ignores_non_object_model_route_context() -> None:
    diagnosis_context = build_context("diagnosis")
    diagnosis_context["dependency_outputs"] = {
        "knowledge": await build_knowledge(build_context("knowledge")),
        "route_resolution": approved_route_output(),
    }

    proposal = (
        await DiagnosisAgent(StringRouteContextDiagnosisModel()).run(diagnosis_context)
    ).payload.learning_plan_proposal

    assert proposal.planning_route is not None
    assert proposal.planning_route.route_id == "ROUTE_TRUSTED"
    assert proposal.planning_route.route_version == 7
    assert proposal.planning_route.planning_status == "approved_route"


@pytest.mark.asyncio
async def test_diagnosis_falls_back_for_non_object_optional_planning_fields() -> None:
    diagnosis_context = build_context("diagnosis")
    diagnosis_context["dependency_outputs"] = {
        "knowledge": await build_knowledge(build_context("knowledge")),
        "route_resolution": approved_route_output(),
    }

    proposal = (
        await DiagnosisAgent(StringStructuredPlanningFieldsDiagnosisModel()).run(
            diagnosis_context
        )
    ).payload.learning_plan_proposal

    assert proposal.goal_contract.acceptance_evidence
    assert proposal.short_term_learning_package.task_blocks
    assert proposal.recovery_policy.recovery_actions
    assert proposal.recommendation_trace.current_task


@pytest.mark.asyncio
async def test_diagnosis_model_schema_is_natural_language_first() -> None:
    model = CapturingDiagnosisModel()
    diagnosis_context = build_context("diagnosis")
    diagnosis_context["dependency_outputs"] = {
        "knowledge": await build_knowledge(build_context("knowledge")),
        "route_resolution": approved_route_output(),
    }

    proposal = (await DiagnosisAgent(model).run(diagnosis_context)).payload.learning_plan_proposal

    schema_properties = model.payload["payload"]["output_schema"]["properties"]
    assert {
        "route_context",
        "goal_contract",
        "milestones",
        "short_term_learning_package",
        "recovery_policy",
        "recommendation_trace",
        "assumptions",
        "unknowns_to_confirm",
    }.isdisjoint(schema_properties)
    assert proposal.planning_route is not None
    assert proposal.planning_route.route_id == "ROUTE_TRUSTED"
    assert proposal.goal_contract is not None
    assert proposal.milestones
    assert proposal.short_term_learning_package is not None
    assert proposal.recovery_policy is not None
    assert proposal.recommendation_trace is not None
    assert proposal.short_term_focus is not None
    assert proposal.short_term_focus.focus_type == "due_review"
    assert proposal.short_term_focus.focus_label == "四君子汤"
    assert proposal.short_term_focus.knowledge_point_ids == ["KP_FJ_001"]


@pytest.mark.asyncio
async def test_standard_model_task_is_bounded_by_available_minutes() -> None:
    diagnosis_context = build_context("diagnosis")
    diagnosis_context["available_minutes"] = 5
    diagnosis_context["dependency_outputs"] = {
        "knowledge": await build_knowledge(build_context("knowledge")),
        "route_resolution": approved_route_output(),
    }

    proposal = (
        await DiagnosisAgent(StandardDiagnosisModel()).run(diagnosis_context)
    ).payload.learning_plan_proposal

    assert proposal.task_proposal.estimated_minutes == 5


@pytest.mark.asyncio
async def test_structured_task_minutes_are_bounded_without_rewriting_plan_text() -> None:
    model = CapturingDiagnosisModel()
    diagnosis_context = build_context("diagnosis")
    diagnosis_context["available_minutes"] = 5
    diagnosis_context["dependency_outputs"] = {
        "knowledge": await build_knowledge(build_context("knowledge")),
        "route_resolution": provisional_route_output(),
    }

    proposal = (await DiagnosisAgent(model).run(diagnosis_context)).payload.learning_plan_proposal

    assert proposal.task_proposal.estimated_minutes == 5
    assert "12分钟" in proposal.short_term_plan_content
    assert "12分钟" not in proposal.task_proposal.task_content
    assert "12分钟" not in proposal.recommendation_trace.time_constraint
    assert "12分钟" not in proposal.recommendation_trace.current_task
    assert "【时间分配】" in proposal.short_term_plan_content
    assert proposal.recommendation_trace.time_constraint == "当前可用时间预算为5分钟。"
    assert "15分钟" not in proposal.recommendation_trace.time_constraint
    assert proposal.recommendation_trace.current_task == proposal.task_proposal.task_content


@pytest.mark.asyncio
async def test_diagnosis_maps_only_semantic_model_content_into_plan_proposal() -> None:
    model = CapturingDiagnosisModel()
    diagnosis_context = build_context("diagnosis")
    diagnosis_context["dependency_outputs"] = {
        "knowledge": await build_knowledge(build_context("knowledge")),
        "route_resolution": approved_route_output(),
    }

    result = (await DiagnosisAgent(model).run(diagnosis_context)).payload

    assert model.payload["target_agent"] == "diagnosis_agent"
    assert model.payload["source_agent"] == "orchestrator"
    assert model.payload["permission_note"]
    diagnosis_payload = model.payload["payload"]
    # Scope-only context must stay out of unscoped calls: no daily-task video
    # path, no load policy, no long-term prerequisite-training policy.
    assert set(diagnosis_payload) == {
        "user_request",
        "goals",
        "learner_context",
        "time_constraints",
        "learning_evidence",
        "learning_state",
        "path_candidates",
        "path_candidate_policy",
        "default_route",
        "existing_plans",
        "plan_actions",
        "plan_scope",
        "output_schema",
        "original_user_request",
        "request_context",
        "shared_context",
    }
    assert "compressed_conversation_summary" not in diagnosis_payload
    assert "prerequisite_training_policy" not in diagnosis_payload
    assert "learning_path_progress" not in diagnosis_payload
    assert "learning_path_progress_instruction" not in diagnosis_payload
    assert "task_load_policy" not in diagnosis_payload
    assert "task_load_policy_instruction" not in diagnosis_payload
    assert diagnosis_payload["shared_context"]["external_information"] == []
    assert "learning_monitoring" not in diagnosis_payload["shared_context"]
    assert "current_long_term_plan" not in diagnosis_payload["shared_context"]
    assert diagnosis_payload["learning_state"] == {}
    assert diagnosis_payload["path_candidates"] == {
        "eligible": [],
        "blocked": [],
    }
    evidence = diagnosis_payload["learning_evidence"]
    assert evidence["behavior_summary"] == {"current_stage_id": "T1"}
    assert diagnosis_payload["goals"] == ["掌握方剂组成与配伍"]
    assert set(diagnosis_payload["learner_context"]) == {
        "learning_goal",
        "learning_background",
        "completed_courses",
        "learner_group",
        "custom_requirements",
    }
    assert diagnosis_payload["time_constraints"]["available_minutes_today"] == 15
    assert evidence["evidence_summaries"] == ["四君子汤由人参、白术、茯苓、甘草组成。"]
    route_context = diagnosis_payload["default_route"]
    assert route_context["planning_status"] == "approved_route"
    assert route_context["phases"][0]["exit_evidence"] == ["闭卷辨析记录"]
    assert "route_id" not in route_context
    assert "sources" not in route_context
    assert "runtime_checks" not in route_context
    assert "user_knowledge_states" not in evidence
    assert "prompt_skill" not in diagnosis_payload
    assert model.payload["prompt_skill_id"] == "diagnosis.create_learning_plan"
    instructions = model.payload["task_instructions"]
    assert "区分用户事实、系统计算结果、教材证据和模型推断" in instructions
    assert "学习范围、预期形成的可观察能力、应用情境和验收证据" in instructions
    assert "必须逐一对应输入中的每个长期阶段" in instructions
    assert "available_minutes 只是今日任务的上限" in instructions
    assert "不要求用满" in instructions
    assert "【时间分配】" not in instructions
    assert "短期计划不是今日任务" in instructions
    assert "至少写出两个周期节点" in instructions
    assert "两天后" in instructions
    assert "不得自行指定" in instructions
    assert "与今日任务方向一致" in instructions
    assert "不要生成其他结构化规划字段或嵌套对象" in instructions
    assert "临时规划" in instructions
    assert "Diagnosis 不直接编写复习卡正文" not in instructions

    proposal = result.learning_plan_proposal
    assert proposal.priority_mode == "temporary_focus"
    assert proposal.task_proposal.task_type == "active_recall"
    assert proposal.task_proposal.estimated_minutes == 12
    assert proposal.planning_route.route_id == "ROUTE_TRUSTED"
    assert proposal.planning_route.route_version == 7
    assert proposal.planning_route.route_status == "approved"
    assert proposal.planning_route.planning_status == "approved_route"
    assert proposal.goal_contract.goal_type == "course"
    assert proposal.goal_contract.goal_name == "四君子汤系统学习"
    assert proposal.milestones[0].milestone_id == "P1"
    assert proposal.milestones[0].evidence_required == ["闭卷辨析记录"]
    assert proposal.short_term_learning_package.time_window_weeks in {1, 2}
    assert proposal.recovery_policy.recovery_actions
    assert proposal.recommendation_trace.default_route == "遵循 Resolver 提供的已批准路线。"
    assert proposal.recommendation_trace.user_state == result.summary
    assert proposal.recommendation_trace.time_constraint == "当前可用时间预算为15分钟。"
    assert proposal.recommendation_trace.current_task == proposal.task_proposal.task_content
    assert result.stage_id == "T1"
    assert result.weak_kp_ids == ["KP_FJ_001"]
    assert "target_difficulty" not in result.model_dump()
    assert "target_difficulty" not in result.daily_review_policy.model_dump()

    model_owned_data = proposal.model_dump()
    forbidden_top_level_system_fields = {
        "plan_id", "task_id", "user_id", "learner_id", "short_term_plan_id",
        "created_at", "updated_at", "due_at", "status", "version",
        "stage_id", "kp_id", "kp_ids",
    }
    assert forbidden_top_level_system_fields.isdisjoint(model_owned_data)
    assert forbidden_top_level_system_fields.isdisjoint(model_owned_data["task_proposal"])


def test_short_term_output_repairs_only_schedule_presentation_constraints() -> None:
    repaired = DiagnosisAgent._repair_short_term_plan_constraints(
        (
            "【当前主目标】掌握基础概念。\n"
            "【具体任务块】第1-5天每日学习理论，第6-10天完成辨析。\n"
            "【复习任务】闭卷回忆并订正；具体复习时间点由系统根据遗忘曲线另行调度。"
        ),
        "每周可以学习4天，每天4小时",
    )

    assert "第1-5天" not in repaired
    assert "每日" not in repaired
    assert "系统" not in repaired
    assert "前段每个学习日学习理论" in repaired
    assert "中段完成辨析" in repaired
    assert "闭卷回忆并订正" in repaired


def test_short_term_output_removes_per_session_review_timing() -> None:
    repaired = DiagnosisAgent._repair_short_term_plan_constraints(
        (
            "【当前主目标】掌握基础概念。\n"
            "【具体任务块】前段理解，中段辨析，验收段自测。\n"
            "【复习任务】建议在每次新学习开始前，花10分钟快速回顾上一次的思维导图。"
        ),
        "每周4天、每次2小时",
    )

    assert "每次新学习开始前" not in repaired
    assert "10分钟" not in repaired
    assert "快速回顾上一次的思维导图" in repaired


def test_planning_output_does_not_mandate_full_available_capacity() -> None:
    repaired = DiagnosisAgent._repair_full_capacity_mandate(
        (
            "【资源预算】用户每周可投入4天，每次2小时。"
            "建议充分利用晚间时间，保持每周4次、每次2小时的稳定投入。"
            "每阶段保留缓冲。"
        ),
        "每周可以学习4天，每次2小时，主要在晚上",
    )

    assert "保持每周4次、每次2小时" not in repaired
    assert "每周安排3个学习日" in repaired
    assert "每次60—90分钟" in repaired
    assert "用户每周可投入4天，每次2小时" in repaired


def test_short_term_session_count_keeps_capacity_buffer() -> None:
    repaired = DiagnosisAgent._repair_full_capacity_mandate(
        (
            "【具体任务块】前半周期（第1-4次学习）完成概念构建；"
            "后半周期（第5-8次学习）完成辨析；第8次学习结束时验收。"
            "【反馈指标】统计8次计划学习中实际完成的次数。"
        ),
        "每周可以学习4天，每次2小时",
    )

    assert "第1-3次学习" in repaired
    assert "第4-6次学习" in repaired
    assert "第6次学习结束时验收" in repaired
    assert "统计6次计划学习" in repaired
    assert "第8次" not in repaired


@pytest.mark.asyncio
async def test_diagnosis_merges_provisional_route_uncertainty_in_stable_order() -> None:
    model = CapturingDiagnosisModel()
    diagnosis_context = build_context("diagnosis")
    diagnosis_context["dependency_outputs"] = {
        "knowledge": await build_knowledge(build_context("knowledge")),
        "route_resolution": provisional_route_output(),
    }

    proposal = (await DiagnosisAgent(model).run(diagnosis_context)).payload.learning_plan_proposal

    assert proposal.planning_route.planning_status == "provisional"
    assert proposal.planning_route.route_id is None
    assert proposal.assumptions == ["解析器假设", "共同项"]
    assert proposal.unknowns_to_confirm == [
        "解析器待确认项",
        "共同待确认项",
    ]
    assert proposal.planning_route.assumptions == proposal.assumptions
    assert proposal.planning_route.unknowns_to_confirm == proposal.unknowns_to_confirm


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("route_output", "expected_status"),
    [
        (approved_route_output(), "approved_route"),
        (provisional_route_output(), "provisional"),
    ],
)
async def test_stub_diagnosis_supports_approved_and_provisional_routes(
    route_output: AgentEnvelope[ResolvedPlanningRoute],
    expected_status: str,
) -> None:
    diagnosis_context = build_context("diagnosis")
    diagnosis_context["dependency_outputs"] = {
        "knowledge": await build_knowledge(build_context("knowledge")),
        "route_resolution": route_output,
    }

    result = (await DiagnosisAgent(StubChatModel()).run(diagnosis_context)).payload

    if expected_status == "provisional":
        assert result.requires_clarification
        assert result.learning_plan_proposal is None
        assert result.interrupt_type == "route_resolution"
        return
    proposal = result.learning_plan_proposal

    assert proposal.planning_route.planning_status == expected_status
    if expected_status == "approved_route":
        assert proposal.milestones[0].evidence_required
    assert proposal.daily_task_content == proposal.task_proposal.task_content
    assert proposal.task_proposal.expected_output


@pytest.mark.asyncio
async def test_two_week_request_sets_two_week_short_term_package() -> None:
    diagnosis_context = build_context("diagnosis")
    diagnosis_context["user_request"] = "请制定未来两周学习计划"
    diagnosis_context["dependency_outputs"] = {
        "knowledge": await build_knowledge(build_context("knowledge")),
        "route_resolution": approved_route_output(),
    }

    proposal = (
        await DiagnosisAgent(StubChatModel()).run(diagnosis_context)
    ).payload.learning_plan_proposal

    assert proposal.short_term_learning_package.time_window_weeks == 2


@pytest.mark.asyncio
async def test_stub_diagnosis_respects_small_available_time_budget() -> None:
    diagnosis_context = build_context("diagnosis")
    diagnosis_context["available_minutes"] = 5
    diagnosis_context["dependency_outputs"] = {
        "knowledge": await build_knowledge(build_context("knowledge")),
        "route_resolution": approved_route_output(),
    }

    proposal = (
        await DiagnosisAgent(StubChatModel()).run(diagnosis_context)
    ).payload.learning_plan_proposal

    assert proposal.task_proposal.estimated_minutes == 5
    assert "【时间分配】" not in proposal.short_term_plan_content
    assert "闭卷回忆2分钟" not in proposal.daily_task_content
    assert "再核对教材并订正遗漏" in proposal.daily_task_content


@pytest.mark.asyncio
async def test_stub_reuses_legacy_plan_text_without_requiring_new_section_headers() -> None:
    diagnosis_context = build_context("diagnosis")
    diagnosis_context.update(
        current_long_term_plan={"content": "旧版长期计划正文。"},
        current_short_term_plan={"content": "旧版短期计划正文。"},
    )
    diagnosis_context["dependency_outputs"] = {
        "knowledge": await build_knowledge(build_context("knowledge")),
        "route_resolution": approved_route_output(),
    }

    proposal = (
        await DiagnosisAgent(StubChatModel()).run(diagnosis_context)
    ).payload.learning_plan_proposal

    assert proposal.long_term_plan_action == "reuse"
    assert proposal.short_term_plan_action == "reuse"
    assert proposal.long_term_plan_content == "旧版长期计划正文。"
    assert proposal.short_term_plan_content == "旧版短期计划正文。"


@pytest.mark.asyncio
async def test_stub_reuses_existing_plans_when_creation_request_has_no_change_fact() -> None:
    diagnosis_context = build_context("diagnosis")
    diagnosis_context.update(
        user_request="请制定学习计划",
        current_long_term_plan={"content": "旧版长期计划正文。"},
        current_short_term_plan={"content": "旧版短期计划正文。"},
    )
    diagnosis_context["dependency_outputs"] = {
        "knowledge": await build_knowledge(build_context("knowledge")),
        "route_resolution": approved_route_output(),
    }

    proposal = (
        await DiagnosisAgent(StubChatModel()).run(diagnosis_context)
    ).payload.learning_plan_proposal

    assert proposal.long_term_plan_action == "reuse"
    assert proposal.short_term_plan_action == "reuse"
    assert proposal.long_term_plan_content == "旧版长期计划正文。"
    assert proposal.short_term_plan_content == "旧版短期计划正文。"


@pytest.mark.asyncio
async def test_personalized_review_skill_keeps_resource_generation_boundary() -> None:
    model = CapturingDiagnosisModel()
    diagnosis_context = build_context("diagnosis")
    diagnosis_context["task_type"] = "personalized_review_card"
    diagnosis_context["dependency_outputs"] = {
        "knowledge": await build_knowledge(build_context("knowledge")),
        "route_resolution": approved_route_output(),
    }

    await DiagnosisAgent(model).run(diagnosis_context)

    instructions = model.payload["task_instructions"]
    assert "Diagnosis 不直接编写复习卡正文" in instructions
    assert "不生成 Audit 结论" in instructions
    actions = model.payload["payload"]["plan_actions"]
    assert actions["long_term_action"] == "reuse"
    assert actions["short_term_action"] == "reuse"
    assert actions["daily_task_action"] == "update"


@pytest.mark.asyncio
async def test_due_review_dispatch_uses_confirmed_queue_state_without_model_call() -> None:
    class FailingIfCalledModel:
        async def complete_json(self, role, payload, on_delta=None):
            raise AssertionError("due review dispatch must not re-diagnose the learner")

    diagnosis_context = build_context("diagnosis")
    diagnosis_context.update(
        task_type="personalized_review_card",
        system_operation="due_review_dispatch",
        user_knowledge_states=[{
            "user_id": diagnosis_context["learner_id"],
            "kp_id": "KP_FJ_001",
            "knowledge_mastery": 0.5,
            "answer_accuracy": 0.5,
            "forgetting_coefficient": 0.08,
            "kp_review_status": "到期",
            "calculated_at": "2026-07-18T12:00:00Z",
        }],
    )
    diagnosis_context["dependency_outputs"] = {
        "knowledge": await build_knowledge(build_context("knowledge")),
    }

    result = await DiagnosisAgent(FailingIfCalledModel()).run(diagnosis_context)

    assert result.payload.stage_id == "review_due"
    assert result.payload.weak_kp_ids == ["KP_FJ_001"]
    assert result.payload.daily_review_policy.capacity == 1
    assert result.payload.learning_plan_proposal is None


@pytest.mark.asyncio
async def test_diagnosis_lets_model_select_inside_trusted_textbook_route() -> None:
    model = TextbookSelectingDiagnosisModel()
    diagnosis_context = build_context("diagnosis")
    diagnosis_context["dependency_outputs"] = {
        "knowledge": await build_knowledge(build_context("knowledge")),
        "route_resolution": textbook_route_output(),
    }
    diagnosis_context["system_data"]["completed_courses"] = ["中医诊断学"]

    result = (await DiagnosisAgent(model).run(diagnosis_context)).payload
    proposal = result.learning_plan_proposal

    textbook_input = model.payload["payload"]["default_route"]["textbook_route"]
    assert textbook_input["route_id"] == "textbook_formula"
    assert textbook_input["stages"][1]["stage_id"] == "stage-2"
    assert textbook_input["stages"][1]["books"] == ["《方剂学》", "《中医内科学》"]
    assert proposal.textbook_selection.route_id == "textbook_formula"
    assert proposal.textbook_selection.stage_id == "stage-2"
    assert proposal.textbook_selection.books == ["《方剂学》"]
    assert [stage.book for stage in proposal.long_term_plan_stages] == [
        ["《中药学》"],
        ["《方剂学》", "《中医内科学》"],
    ]
    assert [milestone.milestone_id for milestone in proposal.milestones] == [
        "stage-1",
        "stage-2",
    ]


def test_diagnosis_prefers_authorized_prerequisite_snapshot_over_stale_profile() -> None:
    context = build_context("diagnosis")
    context["system_data"]["completed_courses"] = ["中医诊断学"]
    context["path_candidates"] = {
        "eligible": [],
        "blocked": [],
        "prerequisite_evidence": {
            "route_id": "textbook_formula",
            "required_courses": ["中医诊断学"],
            "satisfied_courses": [],
            "unmet_courses": ["中医诊断学"],
            "unknown_courses": [],
            "source_refs": ["current_user_request"],
        },
    }

    confirmed, unmet = DiagnosisAgent._prerequisite_course_evidence(
        context, DiagnosisAgent._trusted_route_context(textbook_route_output().payload)
    )

    assert confirmed == set()
    assert unmet == {"中医诊断学"}


@pytest.mark.asyncio
async def test_scoped_plan_discards_stale_persisted_textbook_selection() -> None:
    class ScopedDocumentModel(TextbookSelectingDiagnosisModel):
        async def complete_json(self, role, payload, on_delta=None):
            if role == "diagnosis_agent":
                from competition_app.llm.stub_planning import fixed_route_document

                route = payload["payload"]["default_route"]["textbook_route"]
                return {"plan_document": fixed_route_document(route), "prerequisite_judgments": []}
            return await super().complete_json(role, payload, on_delta)

    diagnosis_context = build_context("diagnosis")
    diagnosis_context["dependency_outputs"] = {
        "knowledge": await build_knowledge(build_context("knowledge")),
        "route_resolution": textbook_route_output(),
    }
    diagnosis_context["plan_scope"] = "long_term"
    diagnosis_context["user_profile"] = {
        "learning_goals": ["掌握方剂组成与配伍"],
        "completed_courses": ["中医诊断学"],
    }
    diagnosis_context["current_long_term_plan"] = {
        "content": "旧长期规划",
        "textbook_selection": {
            "route_id": "retired_textbook_route",
            "stage_id": "retired-stage",
            "books": ["《旧版占位教材》"],
            "reason": "旧路线残留",
        },
    }

    result = (
        await DiagnosisAgent(ScopedDocumentModel()).run(diagnosis_context)
    ).payload
    selection = result.learning_plan_proposal.textbook_selection

    assert selection.route_id == "textbook_formula"
    assert selection.stage_id == "stage-1"
    assert selection.books == ["《中药学》"]


def test_scoped_daily_task_keeps_prerequisite_training_book_from_short_plan() -> None:
    """当日任务承接短期计划的教材选择：短期计划把前置课程教材
    （《中医诊断学》）安排在本周期时，展开不得清空该选择并错误
    回退到阶段一教材。"""
    route_context = DiagnosisAgent._trusted_route_context(
        textbook_route_output().payload
    )

    output = DiagnosisAgent._expand_scoped_planning_output(
        "daily_task",
        {
            "daily_task_content": "今日完成《中医诊断学》四诊部分学习。",
            "estimated_minutes": 30,
            "expected_output": "四诊要点笔记。",
            "completion_criteria": "完成一组四诊辨识练习题。",
        },
        {
            "available_minutes": 30,
            "current_short_term_plan": {
                "content": "本周期完成《中医诊断学》四诊与八纲辨证基础。",
                "textbook_selection": {
                    "route_id": "textbook_formula",
                    "stage_id": "stage-2",
                    "stage_name": "方剂辨析",
                    "books": ["《中医诊断学》"],
                    "reason": "先完成前置训练再进入正式教材。",
                },
            },
        },
        route_context,
    )

    assert output.selected_textbook_route_id == "textbook_formula"
    assert output.selected_stage_id == "stage-2"
    assert output.selected_books == ["《中医诊断学》"]


@pytest.mark.asyncio
async def test_diagnosis_stops_for_textbook_route_clarification_before_model() -> None:
    class ModelMustNotRun:
        async def complete_json(self, role, payload, on_delta=None):
            raise AssertionError("model must not run before route clarification")

    diagnosis_context = build_context("diagnosis")
    diagnosis_context["dependency_outputs"] = {
        "route_resolution": textbook_route_output(
            planning_status="needs_clarification"
        )
    }
    diagnosis_context["plan_scope"] = "long_term"

    result = (await DiagnosisAgent(ModelMustNotRun()).run(diagnosis_context)).payload

    assert result.requires_clarification
    assert result.clarification_questions == ["请确认具体考试名称。"]
    assert result.plan_scope == "long_term"


@pytest.mark.asyncio
async def test_scoped_long_plan_stops_before_model_when_route_has_no_real_books() -> None:
    class ModelMustNotRun:
        async def complete_json(self, role, payload, on_delta=None):
            raise AssertionError("model must not run without publishable route stages")

    diagnosis_context = build_context("diagnosis")
    diagnosis_context["dependency_outputs"] = {
        "route_resolution": provisional_route_output()
    }
    diagnosis_context["plan_scope"] = "long_term"

    result = (await DiagnosisAgent(ModelMustNotRun()).run(diagnosis_context)).payload

    assert result.requires_clarification
    assert result.learning_plan_proposal is None
    assert result.interrupt_type == "route_resolution"
    assert "真实教材" in result.clarification_reason


@pytest.mark.asyncio
async def test_diagnosis_asks_user_when_model_repeatedly_skips_unknown_prerequisite() -> None:
    diagnosis_context = build_context("diagnosis")
    diagnosis_context["dependency_outputs"] = {
        "knowledge": await build_knowledge(build_context("knowledge")),
        "route_resolution": textbook_route_output(),
    }
    diagnosis_context["plan_scope"] = "short_term"
    diagnosis_context["current_long_term_plan"] = {
        "content": "已有有效长期规划",
        "status": "active",
    }

    result = (
        await DiagnosisAgent(TextbookSelectingDiagnosisModel()).run(diagnosis_context)
    ).payload

    assert result.requires_clarification
    assert result.learning_plan_proposal is None
    assert any("中医诊断学" in question for question in result.clarification_questions)
    assert result.plan_scope == "short_term"


@pytest.mark.asyncio
async def test_diagnosis_builds_evidence_authorized_formula_preview_without_advancing_parent() -> None:
    model = TemporaryFormulaFocusDiagnosisModel()
    diagnosis_context = build_context("diagnosis")
    knowledge = EvidencePack(
        evidence_pack_id="EP_FORMULA_FOCUS",
        query="三方短期专题",
        evidence_items=[
            EvidenceItem(
                evidence_id="E_SJZT",
                source_id="方剂学_clean:00335",
                content_summary="四君子汤由人参、白术、茯苓、炙甘草组成。",
                authority_level="textbook",
                confidence=0.99,
                resource_type="textbook",
                source_label="《方剂学》· 第一节 补气剂",
            ),
            EvidenceItem(
                evidence_id="E_SLBS",
                source_id="方剂学_clean:00340",
                content_summary="参苓白术散益气健脾，渗湿止泻。",
                authority_level="textbook",
                confidence=0.99,
                resource_type="textbook",
                source_label="《方剂学》· 第一节 补气剂",
            ),
            EvidenceItem(
                evidence_id="E_LZW",
                source_id="方剂学_clean:00277",
                content_summary="理中丸温中祛寒，补气健脾。",
                authority_level="textbook",
                confidence=0.99,
                resource_type="textbook",
                source_label="《方剂学》· 第一节 温中祛寒剂",
            ),
        ],
        learning_focus_status="supported",
        learning_focus_items=[
            LearningFocusEvidence(
                name="四君子汤",
                evidence_id="E_SJZT",
                source_id="方剂学_clean:00335",
                source_label="《方剂学》· 第一节 补气剂",
            ),
            LearningFocusEvidence(
                name="参苓白术散",
                evidence_id="E_SLBS",
                source_id="方剂学_clean:00340",
                source_label="《方剂学》· 第一节 补气剂",
            ),
            LearningFocusEvidence(
                name="理中丸",
                evidence_id="E_LZW",
                source_id="方剂学_clean:00277",
                source_label="《方剂学》· 第一节 温中祛寒剂",
            ),
        ],
    )
    diagnosis_context.update(
        plan_scope="short_term",
        user_request="请制定未来7天短期学习计划。",
        current_long_term_plan={
            "plan_id": "LONG_1",
            "content": "当前长期计划处于中药基础阶段。",
            "status": "active",
            "stages": [
                {"stage": 1, "duration_days": 90, "book": ["《中药学》"], "goal": "中药基础"},
                {"stage": 2, "duration_days": 90, "book": ["《方剂学》"], "goal": "方剂辨析"},
            ],
            "textbook_selection": {
                "route_id": "textbook_formula",
                "route_version": 1,
                "stage_id": "stage-1",
                "stage_name": "中药基础",
                "books": ["《中药学》"],
                "reason": "长期阶段由服务端核验证据推进。",
            },
        },
        dependency_outputs={
            "knowledge": SimpleNamespace(payload=knowledge),
            "route_resolution": textbook_route_output(),
        },
    )

    set_requested_focus(diagnosis_context, ["四君子汤", "参苓白术散", "理中丸"])
    result = (await DiagnosisAgent(model).run(diagnosis_context)).payload

    proposal = result.learning_plan_proposal
    assert proposal is not None, result.model_dump()
    assert proposal.long_term_plan_action == "reuse"
    assert proposal.textbook_selection.stage_id == "stage-2"
    assert proposal.textbook_selection.books == ["《方剂学》"]
    assert proposal.short_term_focus.progression_stage_id == "stage-1"
    assert proposal.short_term_focus.focus_stage_id == "stage-2"
    assert proposal.short_term_focus.focus_names == ["四君子汤", "参苓白术散", "理中丸"]
    assert all(
        name in proposal.short_term_plan_content
        for name in proposal.short_term_focus.focus_names
    )
    assert result.parent_plan_constraints["current_stage_id"] == "stage-1"
    assert result.parent_plan_constraints["temporary_focus_overlay"]["focus_stage_id"] == "stage-2"


@pytest.mark.asyncio
@pytest.mark.parametrize("focus_status", ["unsupported", "undetermined"])
async def test_diagnosis_stops_when_model_confirms_insufficient_planning_facts(
    focus_status: str,
) -> None:
    class ModelMustNotRun:
        async def complete_json(self, role, payload, on_delta=None):
            assert payload["payload"].get("phase") == "assess_planning_focus"
            return {
                "status": "needs_retrieval", "focus_object_nos": [1, 2, 3],
                "focus_stage_no": None, "focus_book_nos": [], "evidence_links": [],
                "cross_stage_mode": "none", "source_quote": payload["payload"]["user_request"],
                "reason": "具体专题所需教材依据不足，不能生成。",
            }

    knowledge = EvidencePack(
        evidence_pack_id="EP_INCOMPLETE_FOCUS",
        query="三方短期专题",
        learning_focus_status=focus_status,
    )
    diagnosis_context = build_context("diagnosis")
    diagnosis_context.update(
        plan_scope="short_term",
        current_long_term_plan={
            "plan_id": "LONG_1",
            "content": "当前长期计划处于中药基础阶段。",
            "status": "active",
            "stages": [
                {"stage": 1, "duration_days": 90, "book": ["《中药学》"], "goal": "中药基础"},
            ],
            "textbook_selection": {
                "route_id": "textbook_formula",
                "route_version": 1,
                "stage_id": "stage-1",
                "stage_name": "中药基础",
                "books": ["《中药学》"],
                "reason": "长期阶段由服务端核验证据推进。",
            },
        },
        dependency_outputs={
            "knowledge": SimpleNamespace(payload=knowledge),
            "route_resolution": textbook_route_output(),
        },
    )

    set_requested_focus(diagnosis_context, ["四君子汤", "参苓白术散", "理中丸"])
    with pytest.raises(ValueError, match="尚未确认.*充分"):
        await DiagnosisAgent(ModelMustNotRun()).run(diagnosis_context)


@pytest.mark.asyncio
async def test_diagnosis_rejects_tampered_focus_evidence_before_model_call() -> None:
    class ModelMustNotRun:
        async def complete_json(self, role, payload, on_delta=None):
            raise AssertionError("Diagnosis model must not run with tampered evidence")

    knowledge = EvidencePack(
        evidence_pack_id="EP_TAMPERED_FOCUS",
        query="四君子汤短期专题",
        evidence_items=[
            EvidenceItem(
                evidence_id="E_SJZT",
                source_id="方剂学_clean:00335",
                content_summary="四君子汤由人参、白术、茯苓、炙甘草组成。",
                authority_level="textbook",
                confidence=0.99,
                resource_type="textbook",
                source_label="《方剂学》· 第一节 补气剂",
            )
        ],
        learning_focus_status="supported",
        learning_focus_items=[
            LearningFocusEvidence(
                name="四君子汤",
                evidence_id="E_SJZT",
                source_id="伪造教材:00001",
                source_label="《方剂学》· 第一节 补气剂",
            )
        ],
    )
    diagnosis_context = build_context("diagnosis")
    diagnosis_context.update(
        plan_scope="short_term",
        current_long_term_plan={
            "plan_id": "LONG_1",
            "content": "当前长期计划处于中药基础阶段。",
            "status": "active",
            "stages": [{"stage": 1, "duration_days": 90, "book": ["《中药学》"], "goal": "中药基础"}],
            "textbook_selection": {
                "route_id": "textbook_formula",
                "route_version": 1,
                "stage_id": "stage-1",
                "stage_name": "中药基础",
                "books": ["《中药学》"],
                "reason": "长期阶段由服务端核验证据推进。",
            },
        },
        dependency_outputs={
            "knowledge": SimpleNamespace(payload=knowledge),
            "route_resolution": textbook_route_output(),
        },
    )

    set_requested_focus(diagnosis_context, ["四君子汤"])
    with pytest.raises(ValueError, match="一致性复核"):
        await DiagnosisAgent(ModelMustNotRun()).run(diagnosis_context)


@pytest.mark.asyncio
async def test_diagnosis_clarifies_when_focus_book_maps_to_multiple_route_stages() -> None:
    class ModelMustNotRun:
        async def complete_json(self, role, payload, on_delta=None):
            assert payload["payload"].get("phase") == "assess_planning_focus"
            return {
                "status": "unresolved", "focus_object_nos": [1],
                "focus_stage_no": None, "focus_book_nos": [], "evidence_links": [],
                "cross_stage_mode": "none", "source_quote": payload["payload"]["user_request"],
                "reason": "同一本教材出现在多个阶段，尚不能可靠确定本次阶段。",
            }

    route = textbook_route_output().payload
    textbook_resolution = route.textbook_route.model_copy(
        update={
            "route": route.textbook_route.route.model_copy(
                update={
                    "stages": [
                        *route.textbook_route.route.stages,
                        TextbookRouteStage(
                            stage_id="stage-3",
                            order=3,
                            name="方剂强化",
                            objective="强化方剂辨析。",
                            books=["《方剂学》"],
                            exit_evidence=["完成方剂综合测验"],
                            source_refs=["USER_ROUTE"],
                        ),
                    ]
                }
            )
        }
    )
    ambiguous_route = route_output(
        route.model_copy(update={"textbook_route": textbook_resolution})
    )
    knowledge = EvidencePack(
        evidence_pack_id="EP_AMBIGUOUS_FOCUS",
        query="四君子汤短期专题",
        evidence_items=[
            EvidenceItem(
                evidence_id="E_SJZT",
                source_id="方剂学_clean:00335",
                content_summary="四君子汤由人参、白术、茯苓、炙甘草组成。",
                authority_level="textbook",
                confidence=0.99,
                resource_type="textbook",
                source_label="《方剂学》· 第一节 补气剂",
            )
        ],
        learning_focus_status="supported",
        learning_focus_items=[
            LearningFocusEvidence(
                name="四君子汤",
                evidence_id="E_SJZT",
                source_id="方剂学_clean:00335",
                source_label="《方剂学》· 第一节 补气剂",
            )
        ],
    )
    diagnosis_context = build_context("diagnosis")
    diagnosis_context.update(
        plan_scope="short_term",
        current_long_term_plan={
            "plan_id": "LONG_1",
            "content": "当前长期计划处于中药基础阶段。",
            "status": "active",
            "stages": [{"stage": 1, "duration_days": 90, "book": ["《中药学》"], "goal": "中药基础"}],
            "textbook_selection": {
                "route_id": "textbook_formula",
                "route_version": 1,
                "stage_id": "stage-1",
                "stage_name": "中药基础",
                "books": ["《中药学》"],
                "reason": "长期阶段由服务端核验证据推进。",
            },
        },
        dependency_outputs={
            "knowledge": SimpleNamespace(payload=knowledge),
            "route_resolution": ambiguous_route,
        },
    )

    set_requested_focus(diagnosis_context, ["四君子汤"])
    with pytest.raises(ValueError, match="尚未确认.*充分"):
        await DiagnosisAgent(ModelMustNotRun()).run(diagnosis_context)


@pytest.mark.asyncio
async def test_daily_task_missing_short_plan_preserves_requested_scope() -> None:
    diagnosis_context = build_context("diagnosis")
    diagnosis_context.update(
        {
            "plan_scope": "daily_task",
            "enforce_planning_readiness": True,
            "current_long_term_plan": {
                "plan_id": "LONG_1",
                "learner_id": "L1",
                "version": 1,
                "status": "active",
                "content": "有效长期规划",
            },
            "current_short_term_plan": {},
        }
    )

    result = (await DiagnosisAgent(StubChatModel()).run(diagnosis_context)).payload

    assert result.requires_clarification
    assert result.interrupt_type == "planning_prerequisite"
    assert result.plan_scope == "daily_task"
    assert "短期计划" in result.clarification_questions[0]


def test_confirmed_prerequisite_is_reused_from_current_conversation() -> None:
    route = textbook_route_output().payload
    route_context = DiagnosisAgent._trusted_route_context(route)
    context = {
        "user_request": "请根据短期计划安排今天的任务",
        "messages": [
            {"role": "assistant", "content": "你是否完成中医诊断学？"},
            {"role": "user", "content": "我已完成中医诊断学，能够完成基础辨证练习。"},
        ],
    }

    confirmed = DiagnosisAgent._confirmed_prerequisite_courses(context, route_context)

    assert confirmed == {"中医诊断学"}


def test_prerequisite_confirmation_pairs_with_question_and_latest_answer_wins() -> None:
    route_context = DiagnosisAgent._trusted_route_context(textbook_route_output().payload)
    confirmed_context = {
        "user_request": "继续制定计划",
        "messages": [
            {"role": "assistant", "content": "你是否完成中医诊断学？"},
            {"role": "user", "content": "是的，已经完成。"},
        ],
    }
    forgotten_context = {
        **confirmed_context,
        "messages": [
            *confirmed_context["messages"],
            {"role": "user", "content": "不过中医诊断学现在已经忘得差不多了。"},
        ],
    }

    assert DiagnosisAgent._confirmed_prerequisite_courses(
        confirmed_context, route_context
    ) == {"中医诊断学"}
    assert DiagnosisAgent._confirmed_prerequisite_courses(
        forgotten_context, route_context
    ) == set()


def test_negative_prerequisite_answer_is_recorded_as_confirmed_unmet_fact() -> None:
    route_context = DiagnosisAgent._trusted_route_context(textbook_route_output().payload)
    context = {
        "user_request": "没有学过，目前是零基础。",
        "messages": [
            {"role": "assistant", "content": "你是否完成中医诊断学？"},
            {"role": "user", "content": "没有学过，目前是零基础。"},
        ],
    }

    assert DiagnosisAgent._confirmed_prerequisite_courses(context, route_context) == set()
    assert DiagnosisAgent._unmet_prerequisite_courses(
        context, route_context
    ) == {"中医诊断学"}


@pytest.mark.asyncio
async def test_diagnosis_normalizes_latest_payload_fields_into_semantic_facts() -> None:
    model = CapturingDiagnosisModel()
    diagnosis_context = build_context("diagnosis")
    latest_goals = {
        "long_term_goal": "完成中医基础理论系统学习。",
        "short_term_goal": "本周掌握四君子汤。",
    }
    current_status = {
        "status_code": "needs_review",
        "status_name": "需要复习",
        "confidence": 0.86,
        "evidence": ["近七日任务完成率下降"],
        "diagnosed_at": "2026-07-15T00:00:00Z",
    }
    behavior_snapshot = {
        "time_data": {"login_frequency": {"value": 4, "unit": "week"}},
        "task_completion_rate": {"learning_task_completion_rate": {"value": 0.6}},
        "resource_click_rate": {"value": 0.75},
        "data_source": "task_and_resource_events",
        "calculation_version": "v2",
        "calculated_at": "2026-07-15T00:00:00Z",
        "current_stage_id": "T1",
    }
    singular_knowledge_state = {
        "kp_id": "KP_FJ_001",
        "knowledge_mastery": 0.58,
        "kp_review_status": "due",
    }
    diagnosis_context.update(
        user_profile={"goals": latest_goals},
        learning_profile={"current_status": current_status},
        system_data=behavior_snapshot,
        user_knowledge_state=singular_knowledge_state,
    )
    diagnosis_context.pop("user_knowledge_states")
    diagnosis_context["dependency_outputs"] = {
        "knowledge": await build_knowledge(build_context("knowledge"))
    }

    result = (await DiagnosisAgent(model).run(diagnosis_context)).payload

    planning_input = model.payload["payload"]
    assert planning_input["goals"] == latest_goals
    assert planning_input["learning_evidence"]["current_status"] == current_status
    assert planning_input["learning_evidence"]["behavior_summary"] == behavior_snapshot
    assert "user_knowledge_states" not in planning_input["learning_evidence"]
    assert result.stage_id == "T1"
    assert "target_difficulty" not in result.model_dump()


@pytest.mark.asyncio
async def test_diagnosis_does_not_send_internal_knowledge_state_ids() -> None:
    model = CapturingDiagnosisModel()
    diagnosis_context = build_context("diagnosis")
    plural_knowledge_states = [
        {"kp_id": "KP_FJ_001", "mastery_score": 0.58, "review_status": "due"}
    ]
    diagnosis_context.update(
        user_knowledge_states=plural_knowledge_states,
        user_knowledge_state={"kp_id": "KP_OTHER", "mastery_score": 0.1},
    )
    diagnosis_context["dependency_outputs"] = {
        "knowledge": await build_knowledge(build_context("knowledge"))
    }

    await DiagnosisAgent(model).run(diagnosis_context)

    assert "user_knowledge_states" not in model.payload["payload"]["learning_evidence"]


@pytest.mark.asyncio
async def test_diagnosis_retains_legacy_semantic_fact_fallbacks() -> None:
    model = CapturingDiagnosisModel()
    diagnosis_context = build_context("diagnosis")
    diagnosis_context["system_data"] = {}
    diagnosis_context["dependency_outputs"] = {
        "knowledge": await build_knowledge(build_context("knowledge"))
    }

    await DiagnosisAgent(model).run(diagnosis_context)

    planning_input = model.payload["payload"]
    assert planning_input["goals"] == ["掌握方剂组成与配伍"]
    assert planning_input["learning_evidence"]["behavior_summary"] == {"recent_accuracy": 0.6}
    assert "user_knowledge_states" not in planning_input["learning_evidence"]


@pytest.mark.asyncio
async def test_diagnosis_falls_back_when_latest_goals_is_null() -> None:
    model = CapturingDiagnosisModel()
    diagnosis_context = build_context("diagnosis")
    diagnosis_context["user_profile"] = {
        "goals": None,
        "learning_goals": ["沿用既有方剂组成学习目标"],
    }
    diagnosis_context["dependency_outputs"] = {
        "knowledge": await build_knowledge(build_context("knowledge"))
    }

    await DiagnosisAgent(model).run(diagnosis_context)

    assert model.payload["payload"]["goals"] == ["沿用既有方剂组成学习目标"]


@pytest.mark.asyncio
async def test_diagnosis_injects_bounded_audit_feedback_for_repair() -> None:
    model = CapturingDiagnosisModel()
    diagnosis_context = build_context("diagnosis")
    diagnosis_context["audit_feedback"] = SimpleNamespace(
        payload=SimpleNamespace(
            findings=[
                "第3阶段正文必须列出《人体解剖学》《生理学》。",
                "阶段安排不得使用笼统学科简称。",
            ],
            audit_report="其余阶段已经通过，仅修正第三阶段教材全称。",
        )
    )
    diagnosis_context["dependency_outputs"] = {
        "knowledge": await build_knowledge(build_context("knowledge"))
    }

    await DiagnosisAgent(model).run(diagnosis_context)

    revision = model.payload["payload"]["audit_revision"]
    assert revision["findings"] == [
        "第3阶段正文必须列出《人体解剖学》《生理学》。",
        "阶段安排不得使用笼统学科简称。",
    ]
    assert "强制修订项" in revision["instruction"]
    assert "仅修正第三阶段" in revision["audit_report"]


class PrerequisiteDailyTaskDiagnosisModel:
    """Generates a prerequisite-training daily task (中医诊断学)."""

    def __init__(self) -> None:
        self.payload = None

    async def complete_json(self, role, payload, on_delta=None):
        self.payload = payload
        if role == "plan_contract_compiler":
            from competition_app.llm.stub import StubChatModel

            return await StubChatModel().complete_json(role, payload, on_delta)
        return {
            "long_term_plan_content": (
                "## 目标契约\n建立方剂辨析能力。\n"
                "## 长期阶段路径\n按教材主线推进。\n"
                "## 长期重规划触发器\n考试目标或稳定证据变化时调整。"
            ),
            "short_term_plan_content": (
                "## 当前周期目标\n完成四君子汤与补气剂专项辨析。\n"
                "## 本周期任务\n周初回忆四君子汤，周中完成补气类方比较，周末综合验收。"
            ),
            "daily_task_content": (
                "## 今日目标\n完成《中医诊断学》望诊章前置训练。\n"
                "## 分步动作\n阅读望诊要点并完成诊断学练习。\n"
                "## 客观完成标准\n完成望诊章节测评。"
            ),
            "estimated_minutes": 15,
            "expected_output": "一份《中医诊断学》望诊章学习记录。",
            "completion_criteria": "完成望诊章测评且正确率达标。",
            "total_duration_days": 60,
            "long_term_plan_stages": [
                {
                    "stage": 1,
                    "stage_name": "中药基础",
                    "book": ["《中药学》"],
                    "goal": "建立常用中药性能功效基础。",
                    "duration_days": 30,
                    "schedule_summary": (
                        "使用《中药学》梳理常用中药性能功效，形成分类笔记，"
                        "并以闭卷辨析记录验收。"
                    ),
                },
                {
                    "stage": 2,
                    "stage_name": "方证联系",
                    "book": ["《方剂学》", "《中医内科学》"],
                    "goal": "建立治法、方剂和证候联系。",
                    "duration_days": 30,
                    "schedule_summary": (
                        "使用《方剂学》和《中医内科学》完成方证比较，形成对照表，"
                        "并以综合案例说明验收。"
                    ),
                },
            ],
            "short_term_duration_days": 7,
            "short_term_progression_nodes": [
                "周初完成《中医诊断学》望诊章前置训练。",
                "周末完成望诊章测评并提交记录。",
            ],
            "learning_chapter": "《中医诊断学》望诊章",
            "focus_knowledge_points": ["望神", "望色"],
            "selected_textbook_route_id": "textbook_formula",
            "selected_stage_id": "stage-2",
            "selected_books": ["《中医诊断学》"],
            "selection_reason": "前置课程未完成，今日先完成中医诊断学训练。",
        }


@pytest.mark.asyncio
async def test_diagnosis_force_prerequisite_daily_task_generates_prerequisite_task() -> None:
    """force_prerequisite_daily_task=True 时，Diagnosis 必须生成
    《中医诊断学》前置任务，且不得生成 stage-2 主任务。"""
    model = PrerequisiteDailyTaskDiagnosisModel()
    diagnosis_context = build_context("diagnosis")
    diagnosis_context.update(
        {
            "plan_scope": "daily_task",
            "force_prerequisite_daily_task": True,
            "required_prerequisite_courses": ["中医诊断学"],
            "prerequisite_task_mode": "daily_task_only",
            "current_long_term_plan": {
                "plan_id": "LONG_1",
                "learner_id": "L1",
                "version": 1,
                "status": "active",
                "content": "有效长期规划",
            },
            "current_short_term_plan": {
                "plan_id": "SHORT_1",
                "learner_id": "L1",
                "version": 1,
                "status": "active",
                "content": "有效短期计划",
            },
            "dependency_outputs": {
                "knowledge": await build_knowledge(build_context("knowledge")),
                "route_resolution": textbook_route_output(),
            },
        }
    )

    result = (
        await DiagnosisAgent(model).run(diagnosis_context)
    ).payload

    assert result.learning_plan_proposal is not None
    daily = result.learning_plan_proposal.task_proposal
    assert "中医诊断学" in daily.learning_chapter
    assert "方剂学" not in daily.learning_chapter
    assert daily.expected_output
    assert daily.completion_criteria


@pytest.mark.asyncio
async def test_diagnosis_force_prerequisite_daily_task_keeps_unmet_evidence() -> None:
    """force_prerequisite_daily_task=True 时，daily_task 分支不得 pop 掉
    confirmed/unmet 前置课程信息，Diagnosis 必须能看到 unmet 信息。"""
    model = PrerequisiteDailyTaskDiagnosisModel()
    diagnosis_context = build_context("diagnosis")
    diagnosis_context.update(
        {
            "plan_scope": "daily_task",
            "force_prerequisite_daily_task": True,
            "required_prerequisite_courses": ["中医诊断学"],
            "prerequisite_task_mode": "daily_task_only",
            "current_long_term_plan": {
                "plan_id": "LONG_1",
                "learner_id": "L1",
                "version": 1,
                "status": "active",
                "content": "有效长期规划",
            },
            "current_short_term_plan": {
                "plan_id": "SHORT_1",
                "learner_id": "L1",
                "version": 1,
                "status": "active",
                "content": "有效短期计划",
            },
            "dependency_outputs": {
                "knowledge": await build_knowledge(build_context("knowledge")),
                "route_resolution": textbook_route_output(),
            },
        }
    )

    await DiagnosisAgent(model).run(diagnosis_context)

    payload = model.payload["payload"]
    learning_evidence = payload.get("learning_evidence") or {}
    # force 模式下不得 pop 掉前置课程信息
    assert "unmet_prerequisite_courses" in learning_evidence
    assert "confirmed_prerequisite_courses" in learning_evidence
    assert payload.get("prerequisite_task_mode") == "daily_task_only"
    assert payload.get("required_prerequisite_courses") == ["中医诊断学"]
    assert "前置课程每日任务" in payload.get(
        "prerequisite_daily_task_instruction", ""
    )


def _course_resume_context(state: str) -> dict:
    context = build_context("diagnosis")
    context.update(
        {
            "plan_scope": "daily_task",
            "prerequisite_resume_pending": True,
            "required_prerequisite_courses": ["中医诊断学"],
            "current_long_term_plan": {
                "plan_id": "LONG_1",
                "status": "active",
                "content": "有效长期规划",
            },
            "current_short_term_plan": {
                "plan_id": "SHORT_1",
                "status": "active",
                "content": "有效短期计划",
            },
            "path_candidates": {
                "prerequisite_evidence": {
                    "route_id": "textbook_formula",
                    "required_courses": ["中医诊断学"],
                    "satisfied_courses": (
                        ["中医诊断学"] if state == "satisfied" else []
                    ),
                    "unmet_courses": ["中医诊断学"] if state == "unmet" else [],
                }
            },
        }
    )
    return context


@pytest.mark.asyncio
async def test_diagnosis_course_resume_unmet_enters_prerequisite_task_mode() -> None:
    model = PrerequisiteDailyTaskDiagnosisModel()
    context = _course_resume_context("unmet")
    context["dependency_outputs"] = {
        "knowledge": await build_knowledge(build_context("knowledge")),
        "route_resolution": textbook_route_output(),
    }

    result = (await DiagnosisAgent(model).run(context)).payload

    assert result.requires_clarification is False
    assert result.prerequisite_task_mode == "daily_task_only"
    assert result.required_prerequisite_courses == ["中医诊断学"]
    assert "中医诊断学" in result.learning_plan_proposal.task_proposal.learning_chapter


@pytest.mark.asyncio
async def test_diagnosis_course_resume_unknown_reasks_without_calling_model() -> None:
    model = PrerequisiteDailyTaskDiagnosisModel()
    context = _course_resume_context("unknown")
    context["dependency_outputs"] = {
        "knowledge": await build_knowledge(build_context("knowledge")),
        "route_resolution": textbook_route_output(),
    }

    result = (await DiagnosisAgent(model).run(context)).payload

    assert result.requires_clarification is True
    assert result.interrupt_type == "planning_prerequisite"
    assert result.prerequisite_kind == "course_status_confirmation"
    assert result.required_prerequisite_courses == ["中医诊断学"]
    assert model.payload is None


@pytest.mark.asyncio
async def test_diagnosis_course_resume_satisfied_does_not_force_prerequisite() -> None:
    model = PrerequisiteDailyTaskDiagnosisModel()
    context = _course_resume_context("satisfied")
    context["dependency_outputs"] = {
        "knowledge": await build_knowledge(build_context("knowledge")),
        "route_resolution": textbook_route_output(),
    }

    result = (await DiagnosisAgent(model).run(context)).payload

    assert result.requires_clarification is False
    assert result.prerequisite_task_mode is None
    assert "prerequisite_daily_task_instruction" not in model.payload["payload"]
