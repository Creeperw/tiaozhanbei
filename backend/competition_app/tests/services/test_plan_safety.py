from types import SimpleNamespace

import pytest

from competition_app.agents.audit import AuditAgent
from competition_app.contracts.local_repair import RepairIssue
from competition_app.services import plan_safety
from competition_app.services.learning_plan import validate_medical_education_safety
from competition_app.tests.agents.test_audit_resource import _short_plan_context


DISCLAIMER = "本规划为中医药教学与备考语境下的长期学习安排，方剂与证候内容仅作为考试知识训练，不构成对任何真实患者的个体化诊疗、处方或疗效承诺。"


def proposal(content=DISCLAIMER):
    return SimpleNamespace(model_dump=lambda mode="json": {"short_term_plan_content": content})


def approval(value):
    return plan_safety.issue_plan_safety_approval(
        proposal=value, learner_id="LEARNER", scope="short_term", audit_id="AUDIT",
    )


@pytest.mark.parametrize("fault", ["missing", "tampered", "text_changed", "user", "scope", "expired", "future", "restart"])
def test_approval_fails_closed(fault, monkeypatch):
    value = proposal()
    token = approval(value)
    user, scope = "LEARNER", "short_term"
    if fault == "missing":
        token = None
    elif fault == "tampered":
        token["proposal_digest"] = "a" * 64
    elif fault == "text_changed":
        value = proposal(DISCLAIMER + "修改过的计划")
    elif fault == "user":
        user = "OTHER"
    elif fault == "scope":
        scope = "long_term"
    elif fault == "expired":
        monkeypatch.setattr(plan_safety.time, "time", lambda: token["expires_at"])
    elif fault == "future":
        monkeypatch.setattr(plan_safety.time, "time", lambda: token["issued_at"] - 1)
    else:
        monkeypatch.setattr(plan_safety, "_SIGNING_KEY", b"new-process-key")
    with pytest.raises(ValueError, match="medical safety approval"):
        plan_safety.verify_plan_safety_approval(token, proposal=value, learner_id=user, scope=scope)


def test_approval_survives_serialization_but_not_mutation():
    import json
    value = proposal()
    token = json.loads(json.dumps(approval(value)))
    plan_safety.verify_plan_safety_approval(token, proposal=value, learner_id="LEARNER", scope="short_term")


@pytest.mark.asyncio
@pytest.mark.parametrize("safety", ["safe", "unsafe", "uncertain", None])
async def test_explicit_safety_required_even_when_overall_decision_passes(safety, monkeypatch):
    context = _short_plan_context()
    value = proposal()
    context["dependency_outputs"]["diagnosis"].payload.learning_plan_proposal = value
    calls = []

    class Model:
        async def complete_json(self, role, payload):
            calls.append(payload)
            assert payload["payload"]["natural_language_plan"] == value.model_dump()
            assert "medical_safety" in payload["payload"]["output_schema"]["required"]
            assert "免责声明只约束" in payload["task_instructions"]
            result = {"decision": "pass", "findings": [], "audit_report": "已核验全文医疗边界。"}
            if safety is not None:
                result["medical_safety"] = safety
            return result

    agent = AuditAgent(Model())
    async def no_issues(*args, **kwargs):
        return []
    monkeypatch.setattr(agent, "_compile_model_issues", no_issues)
    result = (await agent.run(context)).payload
    assert len(calls) == 1
    if safety == "safe":
        assert result.decision == "pass"
        plan_safety.verify_plan_safety_approval(
            result.medical_safety_approval, proposal=value,
            learner_id=context["learner_id"], scope="short_term",
        )
    else:
        assert result.decision == "needs_human_review"
        assert result.medical_safety_approval is None


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["safety_violation", "unresolved"])
async def test_contradictory_safety_finding_never_becomes_advisory(kind, monkeypatch):
    class Model:
        async def complete_json(self, role, payload):
            return {"decision": "pass", "medical_safety": "safe", "findings": ["正文存在安全疑点"], "audit_report": "需复核"}
    agent = AuditAgent(Model())
    async def issues(*args, **kwargs):
        return [RepairIssue(issue_id="ISSUE", issue_type=kind, message="需要安全复核", blocking=True)]
    monkeypatch.setattr(agent, "_compile_model_issues", issues)
    result = (await agent.run(_short_plan_context())).payload
    assert result.decision == "needs_human_review"
    assert result.medical_safety_approval is None


def test_legacy_guard_still_rejects_real_patient_instruction():
    value = SimpleNamespace(
        long_term_plan_content="为当前真实患者给出个体化诊断结论。",
        short_term_plan_content="", goal_contract=None, milestones=[],
        short_term_learning_package=None, recovery_policy=None, task_proposal=None,
    )
    with pytest.raises(ValueError, match="medical education safety boundary"):
        validate_medical_education_safety(value)


@pytest.mark.parametrize("fault", [None, "changed", "expired", "missing", "forged"])
def test_service_checks_exact_approval_before_any_write(fault, monkeypatch, tmp_path):
    from competition_app.services.default_route import DefaultRouteRepository
    from competition_app.services.learning_plan import LearningPlanService
    from competition_app.tests.services.test_learning_plan_service import structured_proposal, DATA_DIRECTORY
    from competition_app.contracts.learning_plan import LongTermPlanStage
    repository = DefaultRouteRepository.from_directory(DATA_DIRECTORY)
    value = structured_proposal(repository)
    value.long_term_plan_content = DISCLAIMER
    value.long_term_plan_stages = [LongTermPlanStage(stage=1, book=["《中医基础理论》"], goal="建立基础", duration_days=30)]
    service = LearningPlanService(repository)
    token = plan_safety.issue_plan_safety_approval(
        proposal=value, learner_id="LEARNER", scope="long_term", audit_id="AUDIT",
    )
    if fault == "changed":
        value.long_term_plan_content += "审核后追加的其他内容"
    elif fault == "expired":
        monkeypatch.setattr(plan_safety.time, "time", lambda: token["expires_at"])
    elif fault == "missing":
        token = None
    elif fault == "forged":
        token = {"medical_safety": "safe"}
    if fault:
        with pytest.raises(ValueError):
            service.materialize_long_term("LEARNER", value, medical_safety_approval=token)
        assert service.get_current("LEARNER") is None
    else:
        result = service.materialize_long_term("LEARNER", value, medical_safety_approval=token)
        assert result.long_term_plan.content == DISCLAIMER
        assert service.get_current("LEARNER").long_term_plan.content == DISCLAIMER


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", [None, "missing", "changed", "other_user", "wrong_subject", "prerequisite_changed", "scope_changed", "focus_changed", "focus_removed"])
async def test_adapter_requires_matching_safety_review_before_publishing(fault):
    from competition_app.agents.learning_plan_service import LearningPlanServiceAdapter
    from competition_app.contracts.learning_plan import LongTermPlanStage
    from competition_app.contracts.resource import AuditResult
    from competition_app.services.default_route import DefaultRouteRepository
    from competition_app.services.plan_audit import plan_audit_subject_digest
    from competition_app.tests.services.test_learning_plan_service import structured_proposal, DATA_DIRECTORY

    repository = DefaultRouteRepository.from_directory(DATA_DIRECTORY)
    value = structured_proposal(repository)
    value.long_term_plan_content = DISCLAIMER
    value.long_term_plan_stages = [LongTermPlanStage(stage=1, book=["《中医基础理论》"], goal="基础", duration_days=30)]
    token = plan_safety.issue_plan_safety_approval(
        proposal=value, learner_id="LEARNER", scope="long_term", audit_id="AUDIT",
    )
    if fault == "missing":
        token = None
    elif fault == "changed":
        value.long_term_plan_content += "修改后的正文"
    assessment = {"route_id": "tcm", "judgments": [{"course": "中医诊断学", "status": "unknown"}]}
    scope = {"mode": "explicit_focus", "objects": ["中医学基础"]}
    focus = {"status": "sufficient", "focus_names": ["中医学基础"]}
    evidence = {"prerequisite_assessment": assessment,
                "planning_request_scope": scope, "planning_focus_assessment": focus}
    audit = AuditResult(
        audit_result_id="AUDIT", decision="pass", plan_scope="long_term",
        subject_type="resource" if fault == "wrong_subject" else "long_term_plan",
        subject_digest=plan_audit_subject_digest(
            plan_scope="long_term", proposal=value, compiled_plan_contract=None,
            prerequisite_assessment=assessment,
            planning_request_scope=scope,
            planning_focus_assessment=focus,
        ), medical_safety_approval=token,
    )
    audit = AuditResult.model_validate_json(audit.model_dump_json())
    if fault == "prerequisite_changed":
        assessment["judgments"][0]["status"] = "satisfied"
    elif fault == "scope_changed":
        scope["objects"] = ["方剂学"]
    elif fault == "focus_changed":
        focus["status"] = "unresolved"
    elif fault == "focus_removed":
        evidence.pop("planning_focus_assessment")
    context = {
        "case_id": "CASE", "trace_id": "TRACE", "request_id": "REQUEST",
        "execution_id": "EXECUTION", "step_id": "learning_plan", "task_type": "learning_plan",
        "learner_id": "OTHER" if fault == "other_user" else "LEARNER",
        "dependency_outputs": {
            "diagnosis": SimpleNamespace(payload=SimpleNamespace(
                plan_scope="long_term", learning_plan_proposal=value,
                compiled_plan_contract=None,
                audit_evidence=evidence,
            )), "audit": SimpleNamespace(payload=audit),
        },
    }
    adapter = LearningPlanServiceAdapter(route_repository=repository)
    if fault:
        with pytest.raises((ValueError, RuntimeError)):
            await adapter.run(context)
        assert adapter.service.get_current(context["learner_id"]) is None
    else:
        result = await adapter.run(context)
        assert result.payload.long_term_plan.content == DISCLAIMER


@pytest.mark.parametrize("changed", [False, True])
def test_short_term_service_requires_same_reviewed_text(changed):
    from competition_app.services.default_route import DefaultRouteRepository
    from competition_app.services.learning_plan import LearningPlanService
    from competition_app.contracts.learning_plan import LongTermPlanStage
    from competition_app.tests.services.test_learning_plan_service import structured_proposal, DATA_DIRECTORY
    repository = DefaultRouteRepository.from_directory(DATA_DIRECTORY)
    service = LearningPlanService(repository)
    value = structured_proposal(repository)
    value.long_term_plan_stages = [LongTermPlanStage(stage=1, book=["《中医基础理论》"], goal="基础", duration_days=30)]
    parent = service.materialize_long_term("LEARNER", value).long_term_plan.model_dump(mode="json")
    value.short_term_plan_content = DISCLAIMER
    token = approval(value)
    if changed:
        value.short_term_plan_content += "改动"
        with pytest.raises(ValueError, match="medical safety approval"):
            service.materialize_short_term("LEARNER", value, current_long_term_plan=parent, medical_safety_approval=token)
        assert service.get_current("LEARNER").short_term_plan is None
    else:
        result = service.materialize_short_term("LEARNER", value, current_long_term_plan=parent, medical_safety_approval=token)
        assert result.short_term_plan.content == DISCLAIMER