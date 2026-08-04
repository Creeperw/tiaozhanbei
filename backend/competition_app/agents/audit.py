from __future__ import annotations

from typing import Any
from uuid import uuid4

from competition_app.agents.common import envelope
from competition_app.agents.paper_audit_findings_compiler import (
    PaperAuditFindingsCompilerAgent,
)
from competition_app.agents.audit_findings_compiler import AuditFindingsCompilerAgent
from competition_app.contracts.base import AgentEnvelope
from competition_app.contracts.agent_context import build_model_context
from competition_app.contracts.resource import AuditResult
from competition_app.llm.base import ChatModel
from competition_app.llm.prompt_skills import prompt_skill_registry
from competition_app.llm.stub import StubChatModel
from competition_app.llm.schemas import AuditModelOutput
from competition_app.services.plan_contract_validator import PlanContractValidator
from competition_app.services.plan_audit import plan_audit_subject_digest
from competition_app.contracts.local_repair import RepairIssue
from competition_app.contracts.audit_compilation import AuditLocation
from competition_app.runtime.audit_issue_resolver import AuditIssueResolver
from competition_app.services.audit_policy import build_resource_acceptance_policy
from pydantic import ValidationError


class AuditAgent:
    def __init__(
        self,
        chat_model: ChatModel | None = None,
        paper_findings_compiler: PaperAuditFindingsCompilerAgent | None = None,
        audit_findings_compiler: AuditFindingsCompilerAgent | None = None,
    ) -> None:
        self.chat_model = chat_model or StubChatModel()
        self.paper_findings_compiler = (
            paper_findings_compiler
            or PaperAuditFindingsCompilerAgent(self.chat_model)
        )
        self.audit_findings_compiler = (
            audit_findings_compiler or AuditFindingsCompilerAgent(self.chat_model)
        )
        self.audit_issue_resolver = AuditIssueResolver()

    async def run(self, context: dict[str, Any]) -> AgentEnvelope[AuditResult]:
        prompt_skill = prompt_skill_registry.load(
            "audit_agent", str(context.get("task_type", "personalized_review_card"))
        )
        if (
            context.get("audit_subject") in {"long_term_plan", "short_term_plan"}
            or (
                str(context.get("task_type")) == "learning_plan"
                and str(context.get("plan_scope"))
                in {"long_term", "short_term", "daily_task", "unspecified"}
            )
        ):
            return await self._audit_learning_plan(context, prompt_skill)
        if str(context.get("task_type")) == "paper_generation":
            return await self._audit_exam_paper(context, prompt_skill)
        expert = context["dependency_outputs"]["expert"].payload
        evidence = context["dependency_outputs"]["knowledge"].payload
        evidence_ids = {item.evidence_id for item in evidence.evidence_items}
        missing = [
            claim.claim_id
            for claim in expert.claims
            if not claim.evidence_ids or not set(claim.evidence_ids).issubset(evidence_ids)
        ]
        semantic_resource = {
            "title": expert.title,
            "content": expert.content,
            "estimated_minutes": expert.estimated_minutes,
            "claim_texts": [claim.text for claim in expert.claims],
            "safety_notes": expert.safety_notes,
        }
        semantic_evidence = [
            {
                "text": item.content_summary,
                "authority": item.authority_level,
                "resource_type": item.resource_type,
                "source_url": item.source_url,
            }
            for item in evidence.evidence_items
        ]
        diagnosis = getattr(context["dependency_outputs"].get("diagnosis"), "payload", None)
        schedule = getattr(context["dependency_outputs"].get("schedule"), "payload", None)
        plan_payload = getattr(
            context["dependency_outputs"].get("learning_plan"), "payload", None
        )
        formal_learning_task = getattr(plan_payload, "learning_task", None)
        acceptance_policy = build_resource_acceptance_policy(
            context,
            formal_learning_task=formal_learning_task,
        )
        semantic_resource["question_consumption"] = (
            expert.question_consumption.model_dump(mode="json")
            if expert.question_consumption is not None
            else None
        )
        semantic_resource["provenance"] = {
            "question_origin": expert.provenance.question_origin,
            "selected_question_count": len(expert.provenance.selected_question_ids),
            "selected_video_count": len(
                expert.provenance.selected_video_evidence_ids
            ),
            "selected_reference_count": len(
                expert.provenance.selected_reference_evidence_ids
            ),
            "generated_sections": expert.provenance.generated_sections,
            "materialized_sections": expert.provenance.materialized_sections,
        }
        knowledge_explanation = str(context.get("task_type")) == "knowledge_explanation"
        paper_generation = str(context.get("task_type")) == "paper_generation"
        external_information_request = bool(
            context.get("external_information_request")
            or any(
                marker in str(context.get("user_request") or "").lower()
                for marker in (
                    "天气", "气温", "降雨", "下雨", "空气质量", "台风",
                    "距离下次", "考试时间", "考试日期", "什么时候考试",
                    "报名时间", "截止日期", "日程", "赛程", "最新消息",
                    "当前时间", "今天几号", "现在几点",
                )
            )
        )
        # Run system-owned hard gates before asking the semantic auditor.  A
        # deterministic failure already has an exact repair owner; spending a
        # model call first can only add conflicting prose and latency.
        preflight_findings: list[str] = []
        formal_question_ids = {
            item.question_id for item in getattr(evidence, "_question_details", [])
        }
        if not set(expert.provenance.selected_question_ids).issubset(
            formal_question_ids
        ):
            preflight_findings.append(
                "资源包含无法在正式候选池中验证来源的练习题。"
            )
        evidence_resource_ids = {
            item.evidence_id
            for item in evidence.evidence_items
            if item.resource_type in {"video", "reference"}
        }
        if not {
            *expert.provenance.selected_video_evidence_ids,
            *expert.provenance.selected_reference_evidence_ids,
        }.issubset(evidence_resource_ids):
            preflight_findings.append(
                "资源包含无法在本次证据中验证来源的视频或参考资料。"
            )
        selected_task = getattr(schedule, "selected_task", None)
        if not external_information_request:
            if selected_task and expert.target_kp_id != selected_task.primary_kp_id:
                preflight_findings.append("资源目标知识点与复习调度任务不一致。")
            if expert.estimated_minutes > int(context.get("available_minutes", 15)):
                preflight_findings.append("资源预计时长超过用户本次可用时间。")
        if missing or preflight_findings:
            resource_locations = self._resource_location_catalog(expert)
            findings = [
                *([f"缺少证据的声明: {', '.join(missing)}"] if missing else []),
                *preflight_findings,
            ]
            result = AuditResult(
                audit_result_id=f"AUDIT_{uuid4().hex}",
                decision="revise",
                audit_report=(
                    "系统确定性硬门禁发现可定位问题，已跳过语义审核并直接进入最小范围返修。"
                ),
                findings=findings,
                structured_findings=self._resource_repair_issues(
                    missing_claim_ids=missing,
                    deterministic_findings=preflight_findings,
                    compiled_model_issues=[],
                    location_catalog=resource_locations,
                ),
                verified_claim_ids=[
                    claim.claim_id
                    for claim in expert.claims
                    if claim.claim_id not in missing
                ],
                subject_type="resource",
            )
            return envelope(context, "audit_agent", "audit_result", result)
        protocol_valid = True
        try:
            model_output = AuditModelOutput.model_validate(await self.chat_model.complete_json(
                "audit_agent", build_model_context(
                    context,
                    target_agent="audit_agent",
                    prompt_skill=prompt_skill,
                    payload={
                    "semantic_resource": semantic_resource,
                    "semantic_evidence": semantic_evidence,
                    "learning_profile": acceptance_policy.learner_fit_facts,
                    "acceptance_policy": acceptance_policy.model_dump(mode="json"),
                    "formal_learning_task": acceptance_policy.formal_learning_task,
                    "formal_task_available": acceptance_policy.formal_task_available,
                    "task_specific_flags": {
                        "paper_generation": paper_generation,
                        "knowledge_explanation": knowledge_explanation,
                        "external_information_request": bool(external_information_request),
                        "must_stay_within_user_syllabus": bool(context.get("user_syllabus")),
                    },
                    "output_schema": AuditModelOutput.model_json_schema(),
                    },
                    permission_note="只输出审核决定和发现；不得生成主要教学内容、学习规划或修改系统状态。",
                ),
            ))
        except ValidationError:
            protocol_valid = False
            model_output = AuditModelOutput(
                decision="needs_human_review",
                findings=["审核模型输出不符合协议，已转人工复核。"],
            )
            if context.get("terminal_trace"):
                context["terminal_trace"].validation("audit_agent", valid=False, detail="AuditModelOutput")
        else:
            if context.get("terminal_trace"):
                context["terminal_trace"].validation("audit_agent", valid=True, detail="AuditModelOutput")
        resource_locations = self._resource_location_catalog(expert)
        if protocol_valid:
            compiled_model_issues = await self._compile_model_issues(
                context,
                subject_type="resource",
                audit_report=model_output.audit_report,
                findings=model_output.findings,
                location_catalog=resource_locations,
                issue_id_prefix="RESOURCE_MODEL_ISSUE",
            )
        else:
            compiled_model_issues = [
                RepairIssue(
                    issue_id="RESOURCE_MODEL_ISSUE_PROTOCOL",
                    issue_type="unresolved",
                    message=model_output.findings[0],
                    severity="high",
                    origin="audit_model",
                    blocking=True,
                    locations=resource_locations[:1],
                    policy_id="audit:invalid_protocol",
                )
            ]
        # A finding attached to an explicit pass is an audit note, not a
        # repair order. The compiler still locates it for traceability, while
        # the system-owned decision boundary prevents it from opening a loop.
        if model_output.decision == "pass":
            compiled_model_issues = [
                issue.model_copy(update={"blocking": False})
                for issue in compiled_model_issues
            ]
        model_blocking_issues = [
            issue for issue in compiled_model_issues if issue.blocking
        ]
        unsafe_or_unresolved = any(
            issue.issue_type in {"safety_violation", "unresolved"}
            for issue in model_blocking_issues
        )
        deterministic_findings: list[str] = []
        formal_question_ids = {
            item.question_id for item in getattr(evidence, "_question_details", [])
        }
        selected_question_ids = set(expert.provenance.selected_question_ids)
        if not selected_question_ids.issubset(formal_question_ids):
            deterministic_findings.append("资源包含无法在正式候选池中验证来源的练习题。")
        evidence_resource_ids = {
            item.evidence_id
            for item in evidence.evidence_items
            if item.resource_type in {"video", "reference"}
        }
        selected_resource_ids = {
            *expert.provenance.selected_video_evidence_ids,
            *expert.provenance.selected_reference_evidence_ids,
        }
        if not selected_resource_ids.issubset(evidence_resource_ids):
            deterministic_findings.append("资源包含无法在本次证据中验证来源的视频或参考资料。")
        selected_task = getattr(schedule, "selected_task", None)
        # A current-fact answer (weather, exam dates, etc.) is deliberately
        # independent of the learner's currently scheduled knowledge point.
        # The scheduler may still be present in the shared execution context,
        # but it must not turn an otherwise valid web answer into a target-KP
        # mismatch.  Likewise, time-budget checks apply to generated learning
        # resources, not to a concise factual lookup.
        if not external_information_request:
            if selected_task and expert.target_kp_id != selected_task.primary_kp_id:
                deterministic_findings.append("资源目标知识点与复习调度任务不一致。")
            if expert.estimated_minutes > int(context.get("available_minutes", 15)):
                deterministic_findings.append("资源预计时长超过用户本次可用时间。")
        model_decision = model_output.decision
        decision = (
            "revise"
            if missing or deterministic_findings
            else (
                model_decision
                if model_decision in {"reject", "needs_human_review"}
                else "needs_human_review"
            )
            if unsafe_or_unresolved
            else "revise"
            if model_blocking_issues
            else "pass"
            if model_decision == "revise"
            else model_decision
        )
        if (
            compiled_model_issues
            and not model_blocking_issues
            and not missing
            and not deterministic_findings
        ):
            decision = "pass"
        if (
            context.get("audit_feedback") is None
            and decision in {"reject", "needs_human_review"}
            and model_blocking_issues
            and all(
                issue.issue_type not in {"safety_violation", "unresolved"}
                for issue in model_blocking_issues
            )
        ):
            decision = "revise"
        # External facts are already bounded by the web evidence pack and the
        # dedicated prompt.  Do not fail closed merely because a general audit
        # model asks for a pedagogical revision (for example, an exercise,
        # textbook structure, or a learner-task alignment that does not apply
        # to a weather/date lookup).  A source-backed answer can be published
        # while retaining the model's findings as non-blocking audit notes.
        if (
            external_information_request
            and not missing
            and not deterministic_findings
            and model_decision in {"revise", "needs_human_review"}
        ):
            decision = "pass"
            model_output = model_output.model_copy(
                update={
                    "findings": [
                        *model_output.findings,
                        "外部事实已具备网络证据；审核建议作为非阻断提示保留。",
                    ]
                }
            )
        if (
            model_decision == "revise"
            and not missing
            and not deterministic_findings
            and not model_blocking_issues
            and not model_output.findings
        ):
            # A repair workflow requires at least one actionable finding. When
            # all deterministic gates pass and the model supplies none, there
            # is nothing safe to rerun, so treat the result as a pass instead
            # of creating an empty, terminal repair plan.
            decision = "pass"
            model_output = model_output.model_copy(
                update={
                    "audit_report": (
                        model_output.audit_report
                        + " 确定性门禁均已通过，且未发现可执行的修订项。"
                    )[:8_000]
                }
            )
        if decision not in {"pass", "revise", "reject", "needs_human_review"}:
            decision = "needs_human_review"
        final_findings = [
            *([f"缺少证据的声明: {', '.join(missing)}"] if missing else []),
            *deterministic_findings,
            *(
                [
                    finding
                    if str(finding).startswith("非阻断建议：")
                    else f"非阻断建议：{finding}"
                    for finding in model_output.findings
                ]
                if decision == "pass"
                else model_output.findings
            ),
        ]
        audit_report = model_output.audit_report
        if decision == "pass" and any(
            marker in audit_report
            for marker in ("必须修订", "不能发布", "不可发布", "阻断性问题")
        ):
            audit_report = "系统确定性门禁与统一验收策略均已通过；模型原阻断措辞已降为非阻断建议。"
        result = AuditResult(
            audit_result_id=f"AUDIT_{uuid4().hex}",
            decision=decision,
            audit_report=audit_report,
            findings=final_findings,
            structured_findings=(
                self._resource_repair_issues(
                    missing_claim_ids=missing,
                    deterministic_findings=deterministic_findings,
                    compiled_model_issues=compiled_model_issues,
                    location_catalog=resource_locations,
                )
                if decision == "revise"
                else []
            ),
            verified_claim_ids=[claim.claim_id for claim in expert.claims if claim.claim_id not in missing],
            subject_type="resource",
        )
        return envelope(context, "audit_agent", "audit_result", result)

    @staticmethod
    def _resource_location_catalog(expert: Any) -> list[AuditLocation]:
        locations = [
            AuditLocation(
                location_key="resource:whole",
                subject_type="resource",
                location_type="whole_subject",
                display_label="当前教学资源",
            ),
            AuditLocation(
                location_key="resource:questions",
                subject_type="resource",
                location_type="section",
                display_label="练习资源选择",
            ),
            AuditLocation(
                location_key="resource:references",
                subject_type="resource",
                location_type="section",
                display_label="视频与参考资料选择",
            ),
            AuditLocation(
                location_key="resource:target_kp_id",
                subject_type="resource",
                location_type="field",
                display_label="资源目标知识点",
            ),
            AuditLocation(
                location_key="resource:estimated_minutes",
                subject_type="resource",
                location_type="field",
                display_label="资源预计时长",
            ),
        ]
        content = getattr(expert, "content", None)
        if isinstance(content, dict):
            for key in list(content)[:12]:
                locations.append(
                    AuditLocation(
                        location_key=f"resource:content:{key}",
                        subject_type="resource",
                        location_type="section",
                        display_label=f"资源正文 {key}",
                    )
                )
        return locations

    async def _compile_model_issues(
        self,
        context: dict[str, Any],
        *,
        subject_type: str,
        audit_report: str,
        findings: list[str],
        location_catalog: list[AuditLocation],
        issue_id_prefix: str,
    ) -> list[RepairIssue]:
        if not findings:
            return []
        compilation = await self.audit_findings_compiler.compile(
            context,
            subject_type=subject_type,
            audit_report=audit_report,
            findings=findings,
            location_catalog=location_catalog,
        )
        if compilation.result.status != "compiled":
            return [
                RepairIssue(
                    issue_id=f"{issue_id_prefix}_UNRESOLVED",
                    issue_type="unresolved",
                    message="审核问题无法可靠定位，自动返修已关闭。",
                    severity="high",
                    origin="audit_model",
                    blocking=True,
                    locations=location_catalog[:1],
                    policy_id="audit:compiler_needs_revision",
                )
            ]
        return self.audit_issue_resolver.resolve(
            compilation.result.issues,
            location_catalog=location_catalog,
            issue_id_prefix=issue_id_prefix,
        )

    @staticmethod
    def _resource_repair_issues(
        *,
        missing_claim_ids: list[str],
        deterministic_findings: list[str],
        compiled_model_issues: list[RepairIssue],
        location_catalog: list[AuditLocation],
    ) -> list[RepairIssue]:
        """Compile resource audit prose into a bounded Expert repair contract.

        Knowledge explanations and personalized review cards both publish an
        Expert-authored resource. Their audit prose may vary, but every
        automatically repairable issue in this branch must be owned by the
        existing ``expert`` step. Without this contract the generic repair
        controller has to infer ownership from wording and can incorrectly
        stop otherwise repairable runs.
        """

        issues: list[RepairIssue] = []
        seen: set[tuple[str, str]] = set()

        locations = {item.location_key: item for item in location_catalog}

        def append(issue_type: str, message: str, location_key: str) -> None:
            normalized = str(message).strip()
            key = (issue_type, normalized)
            if not normalized or key in seen:
                return
            seen.add(key)
            issues.append(
                RepairIssue(
                    issue_id=f"RESOURCE_ISSUE_{len(issues) + 1}",
                    issue_type=issue_type,
                    message=normalized,
                    owner_step_id="expert",
                    affected_step_ids=["expert"],
                    severity="high" if issue_type == "missing_evidence" else "medium",
                    origin="deterministic",
                    locations=[locations[location_key]] if location_key in locations else [],
                    policy_id=f"resource:{issue_type}",
                )
            )

        if missing_claim_ids:
            append(
                "missing_evidence",
                f"缺少证据的声明: {', '.join(missing_claim_ids)}",
                "resource:whole",
            )
        for finding in deterministic_findings:
            location_key = (
                "resource:target_kp_id"
                if "目标知识点" in finding
                else "resource:estimated_minutes"
                if "预计时长" in finding
                else "resource:whole"
            )
            append("content_quality", finding, location_key)
        for issue in compiled_model_issues:
            if not issue.blocking:
                continue
            key = (issue.issue_type, issue.message)
            if key in seen:
                continue
            seen.add(key)
            issues.append(issue)
        return issues

    async def _audit_learning_plan(self, context: dict[str, Any], prompt_skill):
        diagnosis = context["dependency_outputs"]["diagnosis"].payload
        plan_scope = str(getattr(diagnosis, "plan_scope", ""))
        if plan_scope == "daily_task":
            result = AuditResult(
                audit_result_id=f"AUDIT_{uuid4().hex}",
                decision="pass",
                audit_report="当日任务不进入长期/短期规划发布审核，节点已安全跳过。",
                findings=[],
                plan_scope="daily_task",
            )
            return envelope(context, "audit_agent", "audit_result", result)
        if getattr(diagnosis, "requires_clarification", False):
            result_plan_scope = (
                plan_scope
                if plan_scope in {"long_term", "short_term", "daily_task"}
                else None
            )
            result = AuditResult(
                audit_result_id=f"AUDIT_{uuid4().hex}",
                decision="pass",
                audit_report=(
                    "当前尚未形成可发布规划，审核节点仅确认应继续执行结构化追问；"
                    "该结果不是规划发布许可，LearningPlanService 不得写入计划。"
                ),
                findings=["规划前置条件未满足，等待用户补充后重新生成并审核规划。"],
                plan_scope=result_plan_scope,
                subject_type=(
                    "long_term_plan" if result_plan_scope == "long_term"
                    else "short_term_plan" if result_plan_scope == "short_term"
                    else None
                ),
            )
            return envelope(context, "audit_agent", "audit_result", result)
        proposal = getattr(diagnosis, "learning_plan_proposal", None)
        compilation = getattr(diagnosis, "compiled_plan_contract", None)
        trusted_route = dict(getattr(diagnosis, "trusted_plan_route", {}) or {})
        parent_plan_constraints = dict(
            getattr(diagnosis, "parent_plan_constraints", {}) or {}
        )
        deterministic_findings: list[str] = []
        contract = None
        if proposal is None:
            deterministic_findings.append("规划提案缺失，不能进入发布流程。")
        if compilation is None or compilation.result.status != "compiled":
            deterministic_findings.append("内部规划合同未成功编译或缺少来源锚点。")
        else:
            contract = compilation.result.contract
            validation = PlanContractValidator().validate(
                contract,
                trusted_route=trusted_route,
                parent_plan_constraints=parent_plan_constraints,
            )
            deterministic_findings.extend(validation.issues)
        subject_digest = plan_audit_subject_digest(
            plan_scope=plan_scope,
            proposal=proposal,
            compiled_plan_contract=compilation,
            parent_plan_constraints=parent_plan_constraints,
        )
        protocol_valid = True
        try:
            model_output = AuditModelOutput.model_validate(
                await self.chat_model.complete_json(
                    "audit_agent",
                    build_model_context(
                        context,
                        target_agent="audit_agent",
                        prompt_skill=prompt_skill,
                        payload={
                            "plan_scope": plan_scope,
                            "natural_language_plan": (
                                proposal.model_dump(mode="json")
                                if proposal is not None
                                else None
                            ),
                            "compiled_contract": (
                                contract.model_dump(mode="json")
                                if contract is not None
                                else None
                            ),
                            "deterministic_findings": deterministic_findings,
                            "trusted_route": trusted_route,
                            "parent_plan_constraints": parent_plan_constraints,
                            "producer_evidence": dict(
                                getattr(diagnosis, "audit_evidence", {}) or {}
                            ),
                            "output_schema": AuditModelOutput.model_json_schema(),
                        },
                        permission_note=(
                            "只输出详细自然语言审核报告、审核决定和问题；"
                            "不得生成或重写规划，不得输出系统摘要或修改状态。"
                        ),
                    ),
                )
            )
        except ValidationError:
            protocol_valid = False
            model_output = AuditModelOutput(
                decision="needs_human_review",
                findings=["规划审核模型输出不符合协议。"],
                audit_report="规划审核模型输出不符合协议，已关闭自动发布并转人工复核。",
            )
        if (
            context.get("audit_feedback") is None
            and model_output.decision in {"reject", "needs_human_review"}
            and not model_output.findings
        ):
            model_output = model_output.model_copy(
                update={
                    "findings": [
                        "当前规划未达到发布要求，请依据可信路线、父计划约束和用户条件重新生成。"
                    ]
                }
            )
        plan_locations = self._plan_location_catalog(plan_scope, proposal, contract)
        if protocol_valid:
            compiled_model_issues = await self._compile_model_issues(
                context,
                subject_type=(
                    "long_term_plan" if plan_scope == "long_term" else "short_term_plan"
                ),
                audit_report=model_output.audit_report,
                findings=model_output.findings,
                location_catalog=plan_locations,
                issue_id_prefix="PLAN_MODEL_ISSUE",
            )
        else:
            compiled_model_issues = [
                RepairIssue(
                    issue_id="PLAN_MODEL_ISSUE_PROTOCOL",
                    issue_type="unresolved",
                    message=model_output.findings[0],
                    severity="high",
                    origin="audit_model",
                    blocking=True,
                    locations=plan_locations[:1],
                    policy_id="audit:invalid_protocol",
                )
            ]
        if model_output.decision == "pass":
            compiled_model_issues = [
                issue.model_copy(update={"blocking": False})
                for issue in compiled_model_issues
            ]
        deterministic_issues = self._plan_deterministic_issues(
            deterministic_findings,
            location_catalog=plan_locations,
        )
        model_blocking_issues = [
            issue for issue in compiled_model_issues if issue.blocking
        ]
        unsafe_or_unresolved = any(
            issue.issue_type in {"safety_violation", "unresolved"}
            for issue in model_blocking_issues
        )
        decision = (
            "revise"
            if deterministic_issues
            else (
                model_output.decision
                if model_output.decision in {"reject", "needs_human_review"}
                else "needs_human_review"
            )
            if unsafe_or_unresolved
            else "revise"
            if model_blocking_issues
            else "pass"
            if model_output.decision == "revise"
            else model_output.decision
        )
        if compiled_model_issues and not model_blocking_issues and not deterministic_issues:
            decision = "pass"
        if (
            context.get("audit_feedback") is None
            and decision in {"reject", "needs_human_review"}
            and model_blocking_issues
            and not unsafe_or_unresolved
        ):
            # A planning proposal is generated content. If its route/contract
            # gates are sound, a model-level rejection is actionable feedback
            # for Diagnosis rather than a terminal workflow state. Route it
            # through the existing bounded local-repair loop.
            decision = "revise"
        if (
            context.get("audit_feedback") is not None
            and decision == "reject"
            and not unsafe_or_unresolved
        ):
            # This is the second audit after one bounded repair.  A model-level
            # reject at this point almost always reflects a hard conflict that
            # cannot be removed by rewriting the plan (for example a user-claimed
            # deadline that differs from a system display).  Failing the whole
            # workflow here is equivalent to human review without the recovery
            # path, so downgrade the terminal decision to a recoverable
            # needs_human_review instead of reject.
            decision = "needs_human_review"
            model_output = model_output.model_copy(
                update={
                    "audit_report": (
                        str(model_output.audit_report or "")
                        + " 已完成一轮受控返修，剩余阻断问题无法仅靠重写规划消除，"
                        "已转为人工复核。"
                    )[:8_000]
                }
            )
        findings = [*deterministic_findings, *model_output.findings]
        audit_report = self._decision_consistent_report(
            decision, model_output.audit_report
        )
        if decision == "pass":
            findings = [
                finding
                if str(finding).startswith("非阻断建议：")
                else f"非阻断建议：{finding}"
                for finding in findings
            ]
        structured_findings = (
            [*deterministic_issues, *model_blocking_issues]
            if decision == "revise"
            else []
        )
        result = AuditResult(
            audit_result_id=f"AUDIT_{uuid4().hex}",
            decision=decision,
            audit_report=audit_report,
            findings=findings,
            structured_findings=structured_findings,
            subject_digest=subject_digest,
            subject_type=(
                "long_term_plan" if plan_scope == "long_term"
                else "short_term_plan"
            ),
            parent_subject_digest=(
                getattr(
                    getattr(
                        context.get("dependency_outputs", {}).get("audit_long"),
                        "payload",
                        None,
                    ),
                    "subject_digest",
                    None,
                )
                if plan_scope == "short_term"
                else None
            ),
            plan_scope=plan_scope,
        )
        return envelope(context, "audit_agent", "audit_result", result)

    @staticmethod
    def _plan_location_catalog(
        plan_scope: str,
        proposal: Any,
        contract: Any,
    ) -> list[AuditLocation]:
        subject_type = (
            "long_term_plan" if plan_scope == "long_term" else "short_term_plan"
        )
        locations = [
            AuditLocation(
                location_key="plan:whole",
                subject_type=subject_type,
                location_type="whole_subject",
                display_label="当前规划全文",
            ),
            AuditLocation(
                location_key="plan:natural_language",
                subject_type=subject_type,
                location_type="section",
                display_label="自然语言规划正文",
            ),
            AuditLocation(
                location_key="plan:compiled_contract",
                subject_type=subject_type,
                location_type="field",
                display_label="内部规划合同",
            ),
        ]
        value = contract.model_dump(mode="json") if hasattr(contract, "model_dump") else {}
        if isinstance(value, dict):
            stages = value.get("stages") or value.get("long_term_plan_stages") or []
            nodes = value.get("progression_nodes") or []
            for index, item in enumerate(stages[:12], start=1):
                key = str(item.get("stage_id") or index) if isinstance(item, dict) else str(index)
                locations.append(
                    AuditLocation(
                        location_key=f"plan:stage:{key}",
                        subject_type=subject_type,
                        location_type="stage",
                        display_label=f"规划第{index}阶段",
                    )
                )
            for index, item in enumerate(nodes[:12], start=1):
                key = str(item.get("node_id") or index) if isinstance(item, dict) else str(index)
                locations.append(
                    AuditLocation(
                        location_key=f"plan:node:{key}",
                        subject_type=subject_type,
                        location_type="progression_node",
                        display_label=f"短期计划第{index}个推进节点",
                    )
                )
        return locations

    @staticmethod
    def _plan_deterministic_issues(
        findings: list[str],
        *,
        location_catalog: list[AuditLocation],
    ) -> list[RepairIssue]:
        locations = {item.location_key: item for item in location_catalog}
        issues = []
        for index, finding in enumerate(findings, start=1):
            parent_constraint = any(
                marker in finding
                for marker in ("父计划", "长期阶段", "所属长期", "阶段期限", "阶段书目")
            )
            issue_type = (
                "plan_parent_constraint" if parent_constraint else "plan_contract_invalid"
            )
            location_key = (
                "plan:whole" if parent_constraint else "plan:compiled_contract"
            )
            issues.append(
                RepairIssue(
                    issue_id=f"PLAN_SYSTEM_ISSUE_{index}",
                    issue_type=issue_type,
                    message=finding,
                    owner_step_id="diagnosis",
                    affected_step_ids=["diagnosis"],
                    severity="high",
                    origin="deterministic",
                    blocking=True,
                    locations=(
                        [locations[location_key]] if location_key in locations else []
                    ),
                    policy_id=f"plan:{issue_type}",
                )
            )
        return issues

    @staticmethod
    def _trusted_plan_route(context: dict[str, Any]) -> dict[str, Any]:
        route_output = context.get("dependency_outputs", {}).get("route_resolution")
        route = getattr(route_output, "payload", None)
        return route.model_dump(mode="json") if hasattr(route, "model_dump") else {}

    @staticmethod
    def _parent_plan_constraints(context: dict[str, Any]) -> dict[str, Any]:
        if str(context.get("plan_scope")) != "short_term":
            return {}
        parent = context.get("current_long_term_plan") or {}
        stages = parent.get("stages", []) if isinstance(parent, dict) else []
        if not stages:
            return {}
        current = next(
            (stage for stage in stages if stage.get("status") in {"active", "current"}),
            stages[0],
        )
        return {
            "current_stage_id": current.get("stage_id"),
            "current_stage_duration_days": current.get("duration_days"),
        }

    async def _audit_exam_paper(self, context: dict[str, Any], prompt_skill):
        dependencies = context["dependency_outputs"]
        blueprint = dependencies["paper_blueprint"].payload
        pool = dependencies["question_pool"].payload
        paper = dependencies["paper_assembly"].payload
        audit_format_drifted = False
        try:
            model_output = AuditModelOutput.model_validate(
                await self.chat_model.complete_json(
                    "audit_agent",
                    build_model_context(
                        context,
                        target_agent="audit_agent",
                        prompt_skill=prompt_skill,
                        payload={
                            "paper_blueprint": blueprint.model_dump(mode="json"),
                            "candidate_pool_summary": [
                                {
                                    "unit_id": unit.unit_id,
                                    "candidate_ids": [item.question_id for item in unit.items],
                                    "warnings": unit.warnings,
                                }
                                for unit in pool.units
                            ],
                            "exam_paper": self._compact_exam_paper_for_audit(paper),
                            "output_schema": AuditModelOutput.model_json_schema(),
                        },
                        permission_note=(
                            "审核完整试卷、答案与蓝图覆盖；不得修改题目、答案或系统状态。"
                        ),
                    ),
                )
            )
        except ValidationError:
            audit_format_drifted = True
            model_output = AuditModelOutput(
                decision="needs_human_review",
                findings=["组卷审核模型输出格式不符合约定，系统改用确定性硬门禁判定。"],
                audit_report=(
                    "组卷审核模型输出格式不符合约定。系统已关闭模型问题驱动的自动返修，"
                    "仅执行确定性硬门禁。"
                ),
            )
        candidate_ids = {item.question_id for unit in pool.units for item in unit.items}
        selected_ids = [item.question.question_id for item in paper.items]
        deterministic_findings: list[str] = []
        required_total = (
            blueprint.required_total_question_count
            if blueprint.question_count_is_hard_constraint
            else None
        )
        if required_total is not None and len(paper.items) != required_total:
            deterministic_findings.append(
                f"用户明确要求{required_total}题，当前试卷仅有{len(paper.items)}题。"
            )
        required_by_type = {
            self._normalize_question_type(question_type): count
            for question_type, count in blueprint.required_question_type_distribution.items()
            if count > 0
        }
        if required_by_type:
            actual_by_type: dict[str, int] = {}
            for item in paper.items:
                question_type = self._normalize_question_type(
                    item.question.question_type
                )
                actual_by_type[question_type] = actual_by_type.get(question_type, 0) + 1
            if actual_by_type != required_by_type:
                expected = "、".join(
                    f"{question_type}{count}题"
                    for question_type, count in required_by_type.items()
                )
                actual = "、".join(
                    f"{question_type}{count}题"
                    for question_type, count in actual_by_type.items()
                ) or "无题目"
                deterministic_findings.append(
                    f"题型分布不符合用户硬约束：要求{expected}，实际{actual}。"
                )
        if len(selected_ids) != len(set(selected_ids)):
            deterministic_findings.append("试卷存在重复题目。")
        selected_by_unit: dict[str, list[ExamPaperItem]] = {}
        for item in paper.items:
            selected_by_unit.setdefault(item.unit_id, []).append(item)
        for unit in blueprint.units:
            if unit.question_type_preferences:
                invalid = [
                    item.question.question_id
                    for item in selected_by_unit.get(unit.unit_id, [])
                    if not self._matches_question_type(
                        item.question.question_type, unit.question_type_preferences
                    )
                ]
                if invalid:
                    deterministic_findings.append(
                        f"蓝图单元{unit.unit_id}存在题型不匹配题目：{', '.join(invalid)}。"
                    )
        normalized_stems = [
            "".join(character for character in item.question.stem if character.isalnum())
            for item in paper.items
        ]
        if len(normalized_stems) != len(set(normalized_stems)):
            deterministic_findings.append("试卷存在题干重复的题目。")
        if not set(selected_ids).issubset(candidate_ids):
            outside = [
                item for item in paper.items
                if item.question.question_id not in candidate_ids
                and item.question.origin != "generated"
            ]
            if outside:
                deterministic_findings.append("试卷包含候选池之外的正式题库题目。")
        incomplete_generated = [
            item.question.question_id
            for item in paper.items
            if item.question.origin == "generated"
            and (
                (
                    "选择" in item.question.question_type
                    and len(item.question.options) < 2
                )
                or not item.question.reference_answer.strip()
                or not (item.question.analysis or "").strip()
            )
        ]
        if incomplete_generated:
            deterministic_findings.append(
                "原创题缺少题型所需选项、答案或解析: "
                + ", ".join(incomplete_generated)
            )
        missing_answers = [
            question_id
            for question_id in selected_ids
            if not str(paper.answer_key.get(question_id) or "").strip()
        ]
        if missing_answers:
            deterministic_findings.append(
                "入卷题目缺少标准答案: " + ", ".join(missing_answers)
            )
        missing_explanations = [
            question_id
            for question_id in selected_ids
            if not str(paper.explanations.get(question_id) or "").strip()
        ]
        if missing_explanations:
            deterministic_findings.append(
                "入卷题目缺少解析: " + ", ".join(missing_explanations)
            )
        if set(paper.answer_key) != set(selected_ids):
            deterministic_findings.append("答案键与入卷题目不一致。")
        # 难度真实性确定性审核：仅真实标注参与匹配；生成题无真实难度标注。
        hard_difficulty_units = [
            unit
            for unit in blueprint.units
            if getattr(unit, "target_difficulty", None) is not None
            and getattr(unit, "difficulty_is_hard_constraint", False)
        ]
        if hard_difficulty_units:
            fabricated = [
                item.question.question_id
                for item in paper.items
                if item.question.origin == "generated"
                and item.question.difficulty is not None
            ]
            if fabricated:
                deterministic_findings.append(
                    "生成补充题不得携带难度标注（系统无真实难度证据）: "
                    + ", ".join(fabricated)
                )
            unlabeled_generated = [
                item.question.question_id
                for item in paper.items
                if item.question.origin == "generated"
            ]
            summary = getattr(paper, "difficulty_source_summary", None)
            if unlabeled_generated and (
                summary is None or summary.generated_count != len(unlabeled_generated)
            ):
                deterministic_findings.append(
                    "试卷难度来源统计与生成题数量不一致，用户无法获知补充题来源。"
                )
            for unit in hard_difficulty_units:
                unit_items = selected_by_unit.get(unit.unit_id, [])
                wrong_difficulty = [
                    item.question.question_id
                    for item in unit_items
                    if item.question.origin != "generated"
                    and item.question.difficulty not in (None, unit.target_difficulty)
                ]
                if wrong_difficulty:
                    deterministic_findings.append(
                        f"蓝图单元{unit.unit_id}要求难度{unit.target_difficulty}，"
                        f"入卷正式题存在其他难度：{', '.join(wrong_difficulty)}。"
                    )
        compiled_model_issues: list[Any] = []
        if model_output.findings:
            findings_compilation = await self.paper_findings_compiler.compile(
                context,
                audit_report=model_output.audit_report,
                findings=model_output.findings,
            )
            compiled_model_issues = (
                findings_compilation.result.issues
                if findings_compilation.result.status == "compiled"
                else []
            )
        compiled_model_issues = [
            issue.model_copy(update={"blocking": False})
            if self._paper_issue_contradicts_system_policy(
                issue.message,
                blueprint=blueprint,
                paper=paper,
            )
            else issue
            for issue in compiled_model_issues
        ]
        model_blocking_findings = [
            issue.message for issue in compiled_model_issues if issue.blocking
        ]
        decision = (
            "revise"
            if deterministic_findings or model_blocking_findings
            else model_output.decision
        )
        if audit_format_drifted and not deterministic_findings:
            decision = "pass"
        if (
            decision == "revise"
            and not deterministic_findings
            and not model_blocking_findings
        ):
            # The system owns count/type/source/answer gates. Once those pass,
            # model-only wording or coverage advice must not trigger a full
            # paper rebuild and another large structured response.
            decision = "pass"
            if context.get("audit_feedback") is not None:
                model_output = model_output.model_copy(
                    update={
                        "findings": [
                            *model_output.findings,
                            "试卷确定性门禁已通过；剩余知识覆盖、表达或命题偏好作为非阻断建议保留。",
                        ]
                    }
                )
        if (
            context.get("audit_feedback") is not None
            and decision == "revise"
            and not deterministic_findings
            and not model_blocking_findings
        ):
            decision = "pass"
            model_output = model_output.model_copy(
                update={
                    "findings": [
                        *model_output.findings,
                        "试卷已完成一次受控修订；剩余知识覆盖、表达或命题偏好作为非阻断建议保留。",
                    ]
                }
            )
        if (
            decision == "revise"
            and not deterministic_findings
            and not model_blocking_findings
            and not blueprint.question_count_is_hard_constraint
        ):
            # Model-level improvement notes are advisory. Candidate shortages or
            # missing scores are not blockers unless the user supplied those as
            # explicit hard constraints and deterministic validation detected a
            # violation. This prevents model-assumed question counts from making
            # an otherwise valid practice paper impossible to publish.
            decision = "pass"
        paper_findings = [*deterministic_findings, *model_output.findings]
        if decision == "pass":
            paper_findings = [
                finding
                if str(finding).startswith("非阻断建议：")
                else f"非阻断建议：{finding}"
                for finding in paper_findings
            ]
        result = AuditResult(
            audit_result_id=f"AUDIT_{uuid4().hex}",
            decision=decision,
            audit_report=self._decision_consistent_report(
                decision, model_output.audit_report
            ),
            findings=paper_findings,
            structured_findings=(
                self._paper_repair_issues(
                    deterministic_findings=deterministic_findings,
                    compiled_model_issues=compiled_model_issues,
                    location_catalog=self._paper_location_catalog(blueprint, paper),
                )
                if decision == "revise"
                else []
            ),
            verified_claim_ids=[],
        )
        return envelope(context, "audit_agent", "audit_result", result)

    @staticmethod
    def _paper_issue_contradicts_system_policy(
        message: str,
        *,
        blueprint: Any,
        paper: Any,
    ) -> bool:
        """Downgrade model requirements that contradict system hard/soft policy."""

        text = "".join(str(message or "").split())
        if not blueprint.question_count_is_hard_constraint and any(
            marker in text
            for marker in ("补齐题量", "补足题量", "建议题量", "题数不足", "题量不足")
        ):
            return True
        summary = getattr(paper, "difficulty_source_summary", None)
        if (
            summary is not None
            and any(
                marker in text
                for marker in ("难度不匹配", "难度不足", "难度不符合", "难度要求未满足")
            )
            and (
                summary.unlabeled_official_count > 0
                or summary.generated_count > 0
                or summary.unmet_count > 0
            )
        ):
            # 系统已按“指定难度正式题→未标注正式题→网络参考→生成补充题”
            # 降级补足并向用户透明说明来源；模型对难度的笼统抱怨不阻断该合规降级。
            return True
        generated = [
            item.question
            for item in paper.items
            if item.question.origin == "generated"
        ]
        generated_complete = bool(generated) and all(
            question.reference_answer.strip()
            and (question.analysis or "").strip()
            and (
                "选择" not in question.question_type
                or len(question.options) >= 2
            )
            for question in generated
        )
        rejects_generated_origin = any(
            marker in text
            for marker in (
                "不在正式候选池", "候选池之外", "必须来自正式候选",
                "模型生成题不允许", "model_knowledge不允许",
            )
        )
        identifies_real_item_error = any(
            marker in text
            for marker in ("偏离主题", "题型错误", "答案错误", "解析错误", "重复题")
        )
        return bool(
            generated_complete
            and rejects_generated_origin
            and not identifies_real_item_error
        )

    @staticmethod
    def _decision_consistent_report(decision: str, report: str) -> str:
        report = str(report or "").strip()
        if decision == "pass" and any(
            marker in report
            for marker in ("必须修订", "不能发布", "不可发布", "阻断性问题")
        ):
            return "系统确定性门禁与任务验收策略均已通过；模型原阻断措辞已作为非阻断建议处理。"
        return report or "审核已依据系统确定性门禁与任务验收策略完成。"

    @staticmethod
    def _compact_exam_paper_for_audit(paper: Any) -> dict[str, Any]:
        """Keep the semantic paper while excluding retrieval payload bloat.

        Question ``source_metadata`` and channel-level retrieval diagnostics
        are persisted for provenance, but they are not needed for the semantic
        audit and can be hundreds of kilobytes.  Sending them to the judge
        increases latency and makes schema drift more likely.
        """

        return {
            "paper_draft_id": paper.paper_draft_id,
            "blueprint_id": paper.blueprint_id,
            "candidate_pool_id": paper.candidate_pool_id,
            "title": paper.title,
            "instructions": paper.instructions,
            "duration_minutes": paper.duration_minutes,
            "total_score": paper.total_score,
            "items": [
                {
                    "sequence": item.sequence,
                    "unit_id": item.unit_id,
                    "score": item.score,
                    "selection_rationale": item.selection_rationale,
                    "question": {
                        "question_id": item.question.question_id,
                        "question_type": item.question.question_type,
                        "stem": item.question.stem,
                        "options": item.question.options,
                        "reference_answer": item.question.reference_answer,
                        "analysis": item.question.analysis,
                        "origin": item.question.origin,
                        "source_tier": item.question.source_tier,
                        "tags": item.question.tags,
                        "kp_ids": sorted(
                            {bridge.kp_id for bridge in item.question.bridges}
                        ),
                    },
                }
                for item in paper.items
            ],
            "answer_key": paper.answer_key,
            "explanations": paper.explanations,
            "coverage_summary": paper.coverage_summary,
            "unresolved_constraints": paper.unresolved_constraints,
        }

    @staticmethod
    def _paper_repair_issues(
        *,
        deterministic_findings: list[str],
        compiled_model_issues: list[Any],
        location_catalog: list[AuditLocation],
    ) -> list[RepairIssue]:
        issues: list[RepairIssue] = []
        seen: set[tuple[str, str]] = set()
        locations = {item.location_key: item for item in location_catalog}

        def matching_locations(message: str) -> list[AuditLocation]:
            preferred_types = (
                {"explanation"}
                if "解析" in message
                else {"answer_key"}
                if any(marker in message for marker in ("标准答案", "答案键"))
                else {"question"}
                if any(marker in message for marker in ("题目", "题干", "题型", "重复题"))
                else set()
            )
            matched = [
                item
                for key, item in locations.items()
                if key != "paper:whole"
                and (not preferred_types or item.location_type in preferred_types)
                and (
                    item.display_label in message
                    or key.rsplit(":", 1)[-1] in message
                )
            ]
            return matched[:8] or [locations["paper:whole"]]

        for message in deterministic_findings:
            issue_type = (
                "answer_or_explanation_invalid"
                if any(marker in message for marker in ("答案", "解析", "答案键"))
                else "paper_item_invalid"
            )
            key = (issue_type, message)
            if key in seen:
                continue
            seen.add(key)
            issues.append(
                RepairIssue(
                    issue_id=f"PAPER_SYSTEM_ISSUE_{len(issues) + 1}",
                    issue_type=issue_type,
                    message=message,
                    owner_step_id="paper_assembly",
                    affected_step_ids=["paper_assembly"],
                    severity="high",
                    origin="deterministic",
                    locations=matching_locations(message),
                    policy_id=f"paper:{issue_type}",
                )
            )
        for compiled in compiled_model_issues:
            if not compiled.blocking:
                continue
            key = (compiled.issue_type, compiled.message)
            if key in seen:
                continue
            seen.add(key)
            issue_type = compiled.issue_type
            owner = (
                "paper_assembly"
                if issue_type != "unresolved"
                else None
            )
            issues.append(
                RepairIssue(
                    issue_id=f"PAPER_MODEL_ISSUE_{len(issues) + 1}",
                    issue_type=issue_type,
                    message=compiled.message,
                    owner_step_id=owner,
                    affected_step_ids=([owner] if owner else []),
                    severity="medium",
                    origin="audit_model",
                    locations=matching_locations(compiled.message),
                    source_anchors=[
                        item.model_dump(mode="json")
                        if hasattr(item, "model_dump")
                        else item
                        for item in list(
                            getattr(compiled, "source_anchors", []) or []
                        )
                    ],
                    policy_id=f"paper:{issue_type}",
                )
            )
        return issues

    @staticmethod
    def _paper_location_catalog(blueprint: Any, paper: Any) -> list[AuditLocation]:
        locations = [
            AuditLocation(
                location_key="paper:whole",
                subject_type="exam_paper",
                location_type="whole_subject",
                display_label="当前试卷全文",
            )
        ]
        for unit in list(getattr(blueprint, "units", []) or [])[:30]:
            locations.append(
                AuditLocation(
                    location_key=f"paper:unit:{unit.unit_id}",
                    subject_type="exam_paper",
                    location_type="unit",
                    display_label=f"蓝图单元{unit.unit_id}",
                )
            )
        for item in list(getattr(paper, "items", []) or [])[:100]:
            question_id = str(item.question.question_id)
            locations.extend(
                [
                    AuditLocation(
                        location_key=f"paper:question:{question_id}",
                        subject_type="exam_paper",
                        location_type="question",
                        display_label=f"试卷题目{question_id}",
                    ),
                    AuditLocation(
                        location_key=f"paper:answer:{question_id}",
                        subject_type="exam_paper",
                        location_type="answer_key",
                        display_label=f"题目{question_id}的答案",
                    ),
                    AuditLocation(
                        location_key=f"paper:explanation:{question_id}",
                        subject_type="exam_paper",
                        location_type="explanation",
                        display_label=f"题目{question_id}的解析",
                    ),
                ]
            )
        return locations

    @staticmethod
    def _normalize_question_type(value: str) -> str:
        normalized = value.strip().replace(" ", "").replace("_", "")
        if "案例" in normalized or "病例" in normalized:
            return "简答题"
        aliases = {
            "单选题": "单项选择题",
            "单项选择": "单项选择题",
            "多选题": "多项选择题",
            "多项选择": "多项选择题",
            "选择题": "选择题",
            "简答": "简答题",
            "问答": "简答题",
            "问答题": "简答题",
        }
        return aliases.get(normalized, normalized)

    @classmethod
    def _matches_question_type(cls, actual: str, preferences: list[str]) -> bool:
        actual_type = cls._normalize_question_type(actual)
        allowed = {cls._normalize_question_type(value) for value in preferences}
        if "选择题" in allowed:
            return actual_type in {"单项选择题", "多项选择题"}
        return actual_type in allowed
