from unittest.mock import AsyncMock, Mock

import pytest

from competition_app.agents.diagnosis import DiagnosisAgent
from competition_app.contracts.knowledge import EvidencePack


def inputs():
    return (
        EvidencePack(evidence_pack_id="EP_empty", query=""),
        {"textbook_route": {"planning_status": "resolved", "route": {
            "route_id": "R1", "stages": [
                {"stage_id": "S1", "books": ["《中医学基础》"]},
                {"stage_id": "S2", "books": ["方剂学"]},
            ],
        }}},
        {"trace_id": "T", "request_id": "R", "learner_id": "L", "task_type": "learning_plan",
         "user_request": "只安排《中医学基础》的复习，不要查正文，也不要预习方剂学。",
         "planning_request_scope": {"mode": "explicit_focus", "objects": ["《中医学基础》"],
                                    "source_quote": "只安排《中医学基础》的复习", "clarification_question": None}},
        {"current_stage_id": "S1"},
    )


def judgment(**changes):
    return dict(status="sufficient", focus_names=["《中医学基础》"], focus_stage_id="S1",
                focus_books=["《中医学基础》"], evidence_links=[], cross_stage_mode="none",
                source_quote="只安排《中医学基础》的复习", reason="已批准父计划与当前阶段足以安排复习。",
                **changes) if not changes else {**judgment(), **changes}


@pytest.mark.asyncio
async def test_existing_diagnosis_can_confirm_current_book_without_retrieval():
    model = Mock(complete_json=AsyncMock(return_value=numbered_judgment()))
    agent = DiagnosisAgent(model)
    knowledge, route, context, parent = inputs()
    result = await agent._assess_planning_focus(knowledge, route, context, parent)
    assert result.status == "sufficient"
    assert result.evidence_links == []
    call = model.complete_json.call_args
    assert call.args[0] == "diagnosis_agent"
    assert call.args[1]["payload"]["phase"] == "assess_planning_focus"
    context["planning_focus_assessment"] = result.model_dump(mode="json")
    assert agent._resolve_temporary_focus_overlay(knowledge, route, context, parent) == (None, None)
    model.complete_json.assert_awaited_once()


@pytest.mark.parametrize("status", ["needs_retrieval", "unresolved"])
def test_unconfirmed_sufficiency_does_not_publish_or_change_scope(status):
    knowledge, route, context, parent = inputs()
    context["planning_focus_assessment"] = judgment(status=status)
    overlay, error = DiagnosisAgent._resolve_temporary_focus_overlay(knowledge, route, context, parent)
    assert overlay is None and error
    assert context["planning_request_scope"]["objects"] == ["《中医学基础》"]


@pytest.mark.parametrize("changes", [
    {"focus_stage_id": "unknown"}, {"focus_books": ["伪造教材"]},
    {"focus_names": ["方剂学"]}, {"source_quote": "用户没有说过"},
    {"evidence_links": [{"name": "《中医学基础》", "evidence_id": "fake"}]},
    {"cross_stage_mode": "introductory_preview"},
])
def test_program_validates_only_structured_identity_and_provenance(changes):
    knowledge, route, context, parent = inputs()
    context["planning_focus_assessment"] = judgment(**changes)
    with pytest.raises(ValueError):
        DiagnosisAgent._resolve_temporary_focus_overlay(knowledge, route, context, parent)


@pytest.mark.parametrize("mode,allowed", [("none", False), ("introductory_preview", True)])
def test_cross_stage_requires_explicit_model_judgment(mode, allowed):
    knowledge, route, context, parent = inputs()
    context["user_request"] = "不是继续原教材，我想临时入门预习方剂学。"
    context["planning_request_scope"].update(objects=["方剂学"], source_quote=context["user_request"])
    context["planning_focus_assessment"] = judgment(
        focus_names=["方剂学"], focus_stage_id="S2", focus_books=["方剂学"],
        cross_stage_mode=mode, source_quote=context["user_request"],
    )
    overlay, error = DiagnosisAgent._resolve_temporary_focus_overlay(knowledge, route, context, parent)
    assert bool(overlay) is allowed
    assert bool(error) is not allowed


@pytest.mark.asyncio
async def test_invalid_model_identity_gets_one_bounded_correction():
    model = Mock(complete_json=AsyncMock(side_effect=[numbered_judgment(focus_stage_no=99), numbered_judgment()]))
    result = await DiagnosisAgent(model)._assess_planning_focus(*inputs())
    assert result.focus_stage_id == "S1"
    assert model.complete_json.await_count == 2
    assert model.complete_json.call_args.args[1]["payload"]["protocol_feedback"]


def numbered_judgment(**changes):
    value = judgment()
    for key in ("focus_names", "focus_stage_id", "focus_books"):
        value.pop(key)
    return {**value, "focus_object_nos": [1], "focus_stage_no": 1, "focus_book_nos": [1], **changes}