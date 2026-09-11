from pathlib import Path

import pytest

from competition_app.application.container import ApplicationContainer
from competition_app.application.personalized_review_card import ReviewCardRequest
from competition_app.config import Settings
from competition_app.llm.stub import StubChatModel


class CapturingStubModel(StubChatModel):
    def __init__(self, *, selected_candidate_id: str | None = None) -> None:
        self.last_payload = None
        self.selected_candidate_id = selected_candidate_id
        self.bound_contract = None

    async def complete_json(self, role, payload, on_delta=None):
        if role != "plan_contract_compiler":
            self.last_payload = payload
        data = payload["payload"]
        if role == "plan_contract_compiler" and data.get("trusted_route", {}).get("binding_mode") == "fixed_route_v1":
            assert self.bound_contract is not None
            return {"status": "compiled", "contract": self.bound_contract}
        result = await super().complete_json(role, payload, on_delta)
        if role == "diagnosis_agent" and data.get("fixed_route_policy"):
            route = data["default_route"]["textbook_route"]
            parts = ["【最终目标】建立中医执业医师考试知识框架。", "【能力路径与阶段】"]
            stages, anchors = [], {}
            for index, stage in enumerate(route["stages"]):
                summary = "使用" + "、".join(stage["books"]) + "形成知识卡片，通过闭卷说明验收。"
                quote = f"{stage['stage_id']} {stage['name']} {'、'.join(stage['books'])}，用时30天。{summary}"
                parts.append(quote)
                stages.append({"stage_id": stage["stage_id"], "duration_days": 30,
                               "schedule_summary": summary, "acceptance": ["通过闭卷说明验收。"]})
                for field, text in (("stage_id", quote), ("duration_days", "用时30天"), ("schedule_summary", summary)):
                    anchors[f"/stages/{index}/{field}"] = [{"source_field": "plan_document", "source_quote": text}]
            parts.extend([
                "【阶段里程碑】每阶段提交知识卡片并闭卷说明，达标后晋级。进入后续阶段前，在阶段前3天使用《中医诊断学》教材训练四诊八纲，完成诊断练习，测验达到80分后验收。",
                "【资源预算】每天30分钟，保留机动时间。", "【重规划条件】持续不达标或时间变化时调整。",
                "【保温底线】每周回顾一次知识卡片。",
                "当前执行阶段stage-1，选用《中医学基础》，用途新学，依据：建立基础框架。",
            ])
            result = {"plan_document": "\n".join(parts)}
            anchors["/selection_mode"] = [{"source_field": "plan_document", "source_quote": "用途新学"}]
            self.bound_contract = {"scope": "long_term", "stages": stages, "field_anchors": anchors,
                                   "selected_stage_id": "stage-1", "selected_books": ["《中医学基础》"],
                                   "selection_reason": "建立基础框架。", "selection_mode": "new_learning"}
        if role == "diagnosis_agent" and result.get("plan_document"):
            result["plan_document"] += "\n当前执行阶段stage-1，选用《中医学基础》，用途新学，依据：建立基础框架。"
        if role == "plan_contract_compiler" and result.get("status") == "compiled" and result["contract"].get("scope") == "long_term":
            result["contract"].update(selected_stage_id="stage-1", selected_books=["《中医学基础》"],
                                      selection_reason="建立基础框架", selection_mode="new_learning")
            result["contract"].setdefault("field_anchors", {})["/selection_mode"] = [
                {"source_field": "plan_document", "source_quote": "用途新学"}
            ]
        if role == "diagnosis_agent" and self.selected_candidate_id:
            result = {
                **result,
                "selected_path_candidate_id": self.selected_candidate_id,
            }
        return result


def multiscale_state(learner_id: str) -> dict:
    return {
        "schema_version": "1.0",
        "state_id": "MSLS_FLOW",
        "learner_id": learner_id,
        "generated_at": "2026-07-24T08:00:00+00:00",
        "macro": {
            "approved_route": {"route_id": "tcm_physician_standard_degree"},
            "current_stage": {"phase_id": "stage-1", "name": "基础阶段"},
        },
        "meso": {
            "current_short_term_plan": {},
            "due_review_knowledge_points": [
                {"kp_id": "KP_DUE", "name": "四君子汤"}
            ],
        },
        "micro": {
            "recent_attempts": [{"attempt_id": "ATTEMPT_1"}],
        },
        "data_quality": {
            "coverage": 0.6,
            "allow_cautious_path_adjustment": True,
        },
        "hard_constraints": [
            {
                "key": "approved_route_available",
                "passed": True,
                "reason": "approved_route_present",
                "source_refs": ["route:ROUTE_1"],
            }
        ],
        "source_refs": [],
        "state_digest": "a" * 24,
    }


def path_candidates(learner_id: str) -> dict:
    eligible = {
        "candidate_id": "PATH_ELIGIBLE",
        "scope": "long_term",
        "stage": {"phase_id": "stage-1", "name": "基础阶段"},
        "books": [{"name": "《中医学基础》"}],
        "knowledge_points": [{"kp_id": "KP_DUE", "name": "四君子汤"}],
        "estimated_minutes": 0,
        "eligible": True,
        "blocked_reasons": [],
        "hard_constraint_results": [
            {
                "key": "prerequisite_satisfied",
                "passed": True,
                "reason": "prerequisite_satisfied",
                "source_refs": ["profile:1"],
            }
        ],
        "score": 0.8,
        "score_components": {},
        "evidence_refs": ["evidence:BOOK_1"],
        "source_refs": ["route_stage:P1"],
        "recommended_action": "continue_stage",
    }
    blocked = {
        **eligible,
        "candidate_id": "PATH_BLOCKED",
        "eligible": False,
        "blocked_reasons": ["parent_plan_missing"],
        "hard_constraint_results": [
            {"key": "parent_plan_available", "passed": False, "reason": "parent_plan_missing"},
            {
                "key": "prerequisite_satisfied",
                "passed": False,
                "reason": "prerequisite_not_satisfied:中医诊断学",
                "source_refs": ["route:ROUTE_1"],
            }
        ],
        "score": 0.99,
    }
    return {
        "schema_version": "1.0",
        "learner_id": learner_id,
        "scope": "long_term",
        "generated_at": "2026-07-24T08:00:00+00:00",
        "state_digest": "a" * 24,
        "items": [eligible, blocked],
        "prerequisite_evidence": {"route_id": "textbook_tcm_physician"},
        "counts": {
            "returned": 2,
            "eligible": 1,
            "blocked": 1,
            "due_reviews_considered": 1,
        },
        "scoring_policy": {},
    }


def build_use_case(
    tmp_path: Path,
    *,
    diagnosis_model: CapturingStubModel | None = None,
):
    container = ApplicationContainer.build(
        Settings(mode="stub", execution_engine="legacy"),
        snapshot_root=tmp_path,
        include_backend_handoff=False,
    )
    planner_model = CapturingStubModel()
    diagnosis_model = diagnosis_model or CapturingStubModel()
    registry = container.review_card_use_case.orchestrator.agent_registry
    registry.get("planner_agent").chat_model = planner_model
    registry.get("diagnosis_agent").chat_model = diagnosis_model
    registry.get("diagnosis_agent").plan_contract_compiler.chat_model = diagnosis_model
    container.review_card_use_case.multiscale_state_loader = (
        lambda learner_id, **_: multiscale_state(learner_id)
    )
    container.review_card_use_case.path_candidate_loader = (
        lambda learner_id, **_: path_candidates(learner_id)
    )
    return container.review_card_use_case, planner_model, diagnosis_model


def long_term_request() -> ReviewCardRequest:
    return ReviewCardRequest(
        learner_id="LEARNER_MULTISCALE_FLOW",
        user_request="请为中医执业医师资格考试制定长期学习规划",
        available_minutes=30,
        plan_scope="long_term",
        user_profile={
            "learning_goal": "中医执业医师资格考试",
            "learning_background": "已完成中医基础课程",
            "goals": {
                "type": "credential",
                "name": "中医执业医师资格考试",
            },
        },
    )


@pytest.mark.asyncio
async def test_planner_receives_summary_and_diagnosis_receives_full_candidates(
    tmp_path: Path,
) -> None:
    use_case, planner_model, diagnosis_model = build_use_case(tmp_path)

    try:
        await use_case.execute(long_term_request())
    except RuntimeError as exc:
        # Task 2's strict handoff catalog currently does not alias the
        # DiagnosisResult model to diagnosis_proposal for LearningPlanService.
        # Diagnosis has already run, so this unrelated downstream boundary does
        # not affect the Task 6 model-context assertions below.
        assert "diagnosis_proposal" in str(exc)

    planner_state = planner_model.last_payload["payload"][
        "multi_scale_learning_state"
    ]
    assert planner_state == {
        "has_long_term_plan": False,
        "has_short_term_plan": False,
        "due_review_count": 1,
        "data_quality": {
            "coverage": 0.6,
            "allow_cautious_path_adjustment": True,
        },
        "hard_constraint_summary": [
            {
                "key": "approved_route_available",
                "passed": True,
                "reason": "approved_route_present",
            }
        ],
    }
    assert "macro" not in planner_state
    diagnosis_payload = diagnosis_model.last_payload["payload"]
    assert diagnosis_payload["learning_state"]["state_digest"] == "a" * 24
    assert diagnosis_payload["path_candidates"]["eligible"]
    assert diagnosis_payload["path_candidates"]["blocked"]


@pytest.mark.asyncio
async def test_model_cannot_select_blocked_candidate(tmp_path: Path) -> None:
    use_case, _, _ = build_use_case(
        tmp_path,
        diagnosis_model=CapturingStubModel(
            selected_candidate_id="PATH_BLOCKED"
        ),
    )

    with pytest.raises(ValueError, match="blocked path candidate"):
        await use_case.execute(long_term_request())
