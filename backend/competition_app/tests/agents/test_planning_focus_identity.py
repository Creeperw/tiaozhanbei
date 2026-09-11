from copy import deepcopy
from unittest.mock import AsyncMock, Mock

import pytest

from competition_app.agents.diagnosis import DiagnosisAgent
from competition_app.contracts.execution import ExecutionPlan, ExecutionStep
from competition_app.contracts.planning_request import PlanningRequestScope, PlanningFocusProtocolError
from competition_app.runtime.agent_registry import AgentRegistry
from competition_app.runtime.orchestrator import Orchestrator
from competition_app.runtime.langgraph_orchestrator import LangGraphOrchestrator
from competition_app.services.planning_focus_identity import PlanningFocusIdentityCatalog
from competition_app.tests.agents.test_planning_focus_assessment import inputs, numbered_judgment


def catalog():
    _, route, context, _ = inputs()
    return PlanningFocusIdentityCatalog(PlanningRequestScope.model_validate(context['planning_request_scope']), route, ['E1'])


def test_binding_preserves_names_without_normalization_or_mutation():
    value = catalog()
    raw = numbered_judgment(reason='中医学基础和《中医学基础》是本次语义描述，不作关键词处理。', evidence_links=[{'object_no': 1, 'evidence_id': 'E1'}])
    original = deepcopy(raw)
    result = value.bind(raw)
    assert result.focus_names == result.focus_books == ['《中医学基础》']
    assert result.focus_stage_id == 'S1'
    assert result.evidence_links[0].name == '《中医学基础》'
    assert result.reason == raw['reason']
    assert raw == original


@pytest.mark.parametrize('changes', [
    {'focus_object_nos': []}, {'focus_object_nos': [1, 1]}, {'focus_object_nos': [2]},
    {'focus_object_nos': [True]}, {'focus_object_nos': ['1']}, {'focus_object_nos': [1.0]},
    {'focus_stage_no': True}, {'focus_stage_no': '1'}, {'focus_stage_no': 1.0},
    {'focus_stage_no': 99}, {'focus_stage_no': None},
    {'focus_book_nos': [True]}, {'focus_book_nos': ['1']}, {'focus_book_nos': [1.0]},
    {'focus_book_nos': [2]}, {'focus_book_nos': [99]}, {'focus_book_nos': [1, 1]},
    {'focus_book_nos': []}, {'focus_books': ['中医学基础']}, {'focus_names': ['中医学基础']},
    {'evidence_links': [{'object_no': 2, 'evidence_id': 'E1'}]},
    {'evidence_links': [{'object_no': 1, 'evidence_id': 'invented'}]},
])
def test_invalid_numbered_identity_fails_closed(changes):
    with pytest.raises(ValueError):
        catalog().bind(numbered_judgment(**changes))


def test_request_schema_constrains_choices_and_empty_catalog():
    schema = catalog().schema()
    props = schema['properties']
    assert props['focus_object_nos']['items']['enum'] == [1]
    assert props['focus_book_nos']['items']['enum'] == [1, 2]
    assert props['focus_stage_no']['anyOf'][0]['enum'] == [1, 2]
    assert 'focus_names' not in props and schema['additionalProperties'] is False
    _, _, context, _ = inputs()
    empty = PlanningFocusIdentityCatalog(PlanningRequestScope.model_validate(context['planning_request_scope']), {}, [])
    props = empty.schema()['properties']
    assert props['focus_book_nos']['maxItems'] == props['evidence_links']['maxItems'] == 0
    assert props['focus_stage_no'] == {'anyOf': [{'type': 'null'}]}
    assert empty.bind(numbered_judgment(status='unresolved', focus_stage_no=None, focus_book_nos=[])).status == 'unresolved'


def test_same_book_in_two_stages_has_distinct_references():
    _, route, context, _ = inputs()
    route['textbook_route']['route']['stages'][1]['books'] = ['《中医学基础》']
    value = PlanningFocusIdentityCatalog(PlanningRequestScope.model_validate(context['planning_request_scope']), route, [])
    with pytest.raises(ValueError):
        value.bind(numbered_judgment(focus_book_nos=[2]))
    assert value.bind(numbered_judgment(focus_stage_no=2, focus_book_nos=[2])).focus_stage_id == 'S2'


@pytest.mark.asyncio
async def test_precise_feedback_and_same_catalog_on_correction():
    bad = numbered_judgment(focus_book_nos=[2])
    model = Mock(complete_json=AsyncMock(side_effect=[bad, numbered_judgment()]))
    await DiagnosisAgent(model)._assess_planning_focus(*inputs())
    first, second = [call.args[1]['payload'] for call in model.complete_json.call_args_list]
    assert first['focus_identity_catalog'] == second['focus_identity_catalog']
    feedback = second['protocol_feedback']
    assert feedback['previous_output'] == bad
    assert feedback['issues'][0] == {'field': 'focus_book_nos', 'code': 'books_must_belong_to_selected_stage', 'actual': [2], 'allowed': [1]}


@pytest.mark.asyncio
async def test_exact_quote_and_cross_stage_checks_remain_active():
    for changes in ({'source_quote': '不是用户原文'}, {'cross_stage_mode': 'introductory_preview'}):
        model = Mock(complete_json=AsyncMock(return_value=numbered_judgment(**changes)))
        with pytest.raises(PlanningFocusProtocolError):
            await DiagnosisAgent(model)._assess_planning_focus(*inputs())
        assert model.complete_json.await_count == 2
        assert model.complete_json.call_args.args[1]['payload']['protocol_feedback']['issues'][0]['message']


@pytest.mark.asyncio
@pytest.mark.parametrize('engine', [Orchestrator, LangGraphOrchestrator])
async def test_protocol_budget_is_not_multiplied_by_orchestrator(engine):
    model = Mock(complete_json=AsyncMock(return_value=numbered_judgment(focus_book_nos=[99])))
    diagnosis = DiagnosisAgent(model)

    class FocusAgent:
        async def run(self, context):
            return await diagnosis._assess_planning_focus(*inputs())

    registry = AgentRegistry()
    registry.register('diagnosis_agent', FocusAgent())
    plan = ExecutionPlan(plan_id='P1', task_type='learning_plan', steps=[ExecutionStep(step_id='diagnosis', agent='diagnosis_agent', max_retries=1)])
    result = await engine(registry).execute(plan, {})
    assert result.status == 'failed'
    assert result.error_type == 'PlanningFocusProtocolError'
    assert model.complete_json.await_count == 2
    assert not any(item.status == 'retrying' for item in result.trace)


@pytest.mark.asyncio
async def test_actual_messages_include_numbered_catalog_and_precise_feedback():
    from competition_app.llm.openai_compatible import OpenAICompatibleChatModel

    bad = numbered_judgment(focus_book_nos=[2])
    model = Mock(complete_json=AsyncMock(side_effect=[bad, numbered_judgment()]))
    knowledge, route, context, parent = inputs()
    context['current_learning_state'] = {'available_books': [{'book_id': '中医学基础'}]}
    await DiagnosisAgent(model)._assess_planning_focus(knowledge, route, context, parent)
    renderer = object.__new__(OpenAICompatibleChatModel)
    for call in model.complete_json.call_args_list:
        messages = renderer._build_messages('diagnosis_agent', call.args[1], strict_json=False, business_json=True)
        system, user = messages[0]['content'], messages[1]['content']
        assert 'focus_object_nos' in system and 'focus_book_nos' in system
        assert 'focus_identity_catalog' in user
        assert 'object_no' in user and 'book_no' in user and 'stage_no' in user
        assert '《中医学基础》' in user
    assert 'books_must_belong_to_selected_stage' in user
    assert 'previous_output' in user and 'allowed' in user
    assert 'books_must_belong_to_selected_stage' not in system


@pytest.mark.asyncio
async def test_numbered_cross_stage_choice_does_not_grant_permission():
    knowledge, route, context, parent = inputs()
    model = Mock(complete_json=AsyncMock(return_value=numbered_judgment(focus_stage_no=2, focus_book_nos=[2])))
    agent = DiagnosisAgent(model)
    assessment = await agent._assess_planning_focus(knowledge, route, context, parent)
    context['planning_focus_assessment'] = assessment.model_dump(mode='json')
    overlay, error = agent._resolve_temporary_focus_overlay(knowledge, route, context, parent)
    assert overlay is None and error