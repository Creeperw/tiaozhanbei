from __future__ import annotations

from typing import Any
from urllib.parse import urlparse
from uuid import uuid4

from competition_app.agents.common import envelope
from competition_app.agents.paper_audit_findings_compiler import (
    PaperAuditFindingsCompilerAgent,
)
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
from pydantic import ValidationError


class AuditAgent:
    OFFICIAL_CURRENT_FACT_HOSTS = {
        "nmec.org.cn",
        "weather.com.cn",
        "cma.cn",
    }

    def __init__(
        self,
        chat_model: ChatModel | None = None,
        paper_findings_compiler: PaperAuditFindingsCompilerAgent | None = None,
    ) -> None:
        self.chat_model = chat_model or StubChatModel()
        self.paper_findings_compiler = (
            paper_findings_compiler
            or PaperAuditFindingsCompilerAgent(self.chat_model)
        )

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
        try:
            model_output = AuditModelOutput.model_validate(await self.chat_model.complete_json(
                "audit_agent", build_model_context(
                    context,
                    target_agent="audit_agent",
                    prompt_skill=prompt_skill,
                    payload={
                    "semantic_resource": semantic_resource,
                    "semantic_evidence": semantic_evidence,
                    "learning_profile": {
                        "summary": getattr(diagnosis, "summary", ""),
                        "risk_flags": getattr(diagnosis, "risk_flags", []),
                    },
                    "acceptance_criteria": {
                        "available_minutes": context.get("available_minutes"),
                        "teaching_only": True,
                        "paper_generation": paper_generation,
                        "knowledge_explanation": knowledge_explanation,
                        "external_information_request": external_information_request,
                        "exam_constraints": context.get("exam_constraints", {}),
                    },
                    "output_schema": AuditModelOutput.model_json_schema(),
                    },
                    permission_note="只输出审核决定和发现；不得生成主要教学内容、学习规划或修改系统状态。",
                ),
            ))
        except ValidationError:
            model_output = AuditModelOutput(
                decision="needs_human_review",
                findings=["审核模型输出不符合协议，已转人工复核。"],
            )
            if context.get("terminal_trace"):
                context["terminal_trace"].validation("audit_agent", valid=False, detail="AuditModelOutput")
        else:
            if context.get("terminal_trace"):
                context["terminal_trace"].validation("audit_agent", valid=True, detail="AuditModelOutput")
        deterministic_findings: list[str] = []
        selected_task = getattr(schedule, "selected_task", None)
        if not external_information_request:
            if selected_task and expert.target_kp_id != selected_task.primary_kp_id:
                deterministic_findings.append("资源目标知识点与复习调度任务不一致。")
            if expert.estimated_minutes > int(context.get("available_minutes", 15)):
                deterministic_findings.append("资源预计时长超过用户本次可用时间。")
        model_decision = model_output.decision
        decision = "revise" if missing or deterministic_findings else model_decision
        nonofficial_web_evidence = (
            external_information_request
            and any(
                item.resource_type == "web"
                and item.authority_level != "system_notice"
                and not self._is_official_current_fact_source(item.source_url)
                for item in evidence.evidence_items
            )
        )
        if nonofficial_web_evidence:
            model_output = model_output.model_copy(
                update={
                    "findings": [
                        *model_output.findings,
                        "实时信息提示：信息来自非官方网页，请以官方渠道为准。",
                    ]
                }
            )
        if (
            decision == "revise"
            and not missing
            and not deterministic_findings
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
        if (
            (
                knowledge_explanation
                or str(context.get("task_type"))
                in {"personalized_review_card", "general_learning_support"}
            )
            and context.get("audit_feedback") is not None
            and decision == "revise"
            and not missing
            and not deterministic_findings
        ):
            decision = "pass"
            model_output = model_output.model_copy(
                update={
                    "findings": [
                        *model_output.findings,
                        "资源已完成一次受控修订；剩余教学范围、表达、负荷或教材口径建议作为非阻断建议保留。",
                    ]
                }
            )
        if decision not in {"pass", "revise", "reject", "needs_human_review"}:
            decision = "needs_human_review"
        result = AuditResult(
            audit_result_id=f"AUDIT_{uuid4().hex}",
            decision=decision,
            audit_report=model_output.audit_report,
            findings=[
                *([f"缺少证据的声明: {', '.join(missing)}"] if missing else []),
                *deterministic_findings,
                *([] if missing or deterministic_findings else model_output.findings),
            ],
            structured_findings=(
                self._resource_repair_issues(
                    missing_claim_ids=missing,
                    deterministic_findings=deterministic_findings,
                    model_findings=model_output.findings,
                )
                if decision == "revise"
                else []
            ),
            verified_claim_ids=[claim.claim_id for claim in expert.claims if claim.claim_id not in missing],
            subject_type="resource",
        )
        return envelope(context, "audit_agent", "audit_result", result)

    @staticmethod
    def _is_official_current_fact_source(source_url: str | None) -> bool:
        host = (urlparse(source_url or "").hostname or "").lower()
        return (
            host == "gov.cn"
            or host.endswith(".gov.cn")
            or any(
                host == official_host or host.endswith(f".{official_host}")
                for official_host in AuditAgent.OFFICIAL_CURRENT_FACT_HOSTS
            )
        )

    @staticmethod
    def _resource_repair_issues(
        *,
        missing_claim_ids: list[str],
        deterministic_findings: list[str],
        model_findings: list[str],
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

        def append(issue_type: str, message: str) -> None:
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
                )
            )

        if missing_claim_ids:
            append(
                "missing_evidence",
                f"缺少证据的声明: {', '.join(missing_claim_ids)}",
            )
        for finding in deterministic_findings:
            append("content_quality", finding)
        for finding in model_findings:
            text = str(finding)
            issue_type = (
                "missing_evidence"
                if "证据" in text
                and any(word in text for word in ("缺少", "缺失", "不足", "无依据"))
                else "conflicting_evidence"
                if "证据" in text and any(word in text for word in ("冲突", "矛盾"))
                else "content_quality"
            )
            append(issue_type, text)
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
            model_output = AuditModelOutput(
                decision="needs_human_review",
                findings=["规划审核模型输出不符合协议。"],
                audit_report="规划审核模型输出不符合协议，已关闭自动发布并转人工复核。",
            )
        decision = "revise" if deterministic_findings else model_output.decision
        if (
            decision == "revise"
            and not deterministic_findings
            and not model_output.findings
        ):
            decision = "pass"
        if (
            context.get("audit_feedback") is not None
            and decision == "revise"
            and not deterministic_findings
        ):
            # One bounded Diagnosis repair has already completed and the
            # executable contract now passes every deterministic route,
            # hierarchy and source check.  A second model-only revision would
            # create a non-converging loop over wording preferences, so retain
            # those comments as advisory findings and allow publication.
            decision = "pass"
            model_output = model_output.model_copy(
                update={
                    "findings": [
                        *model_output.findings,
                        "规划已完成一次受控修订；剩余表达或节奏建议作为非阻断建议保留。",
                    ]
                }
            )
        findings = [*deterministic_findings, *model_output.findings]
        structured_findings = [
            RepairIssue(
                issue_id=f"PLAN_ISSUE_{index}",
                issue_type="plan_quality",
                message=finding,
                owner_step_id="diagnosis",
                affected_step_ids=["diagnosis"],
            )
            for index, finding in enumerate(findings, start=1)
        ] if decision == "revise" else []
        result = AuditResult(
            audit_result_id=f"AUDIT_{uuid4().hex}",
            decision=decision,
            audit_report=model_output.audit_report,
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
        result = AuditResult(
            audit_result_id=f"AUDIT_{uuid4().hex}",
            decision=decision,
            audit_report=model_output.audit_report,
            findings=[*deterministic_findings, *model_output.findings],
            structured_findings=(
                self._paper_repair_issues(
                    deterministic_findings=deterministic_findings,
                    compiled_model_issues=compiled_model_issues,
                )
                if decision == "revise"
                else []
            ),
            verified_claim_ids=[],
        )
        return envelope(context, "audit_agent", "audit_result", result)

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
    ) -> list[RepairIssue]:
        issues: list[RepairIssue] = []
        seen: set[tuple[str, str]] = set()
        for message in deterministic_findings:
            key = ("paper_blueprint_mismatch", message)
            if key in seen:
                continue
            seen.add(key)
            issues.append(
                RepairIssue(
                    issue_id=f"PAPER_SYSTEM_ISSUE_{len(issues) + 1}",
                    issue_type="paper_blueprint_mismatch",
                    message=message,
                    owner_step_id="paper_assembly",
                    affected_step_ids=["paper_blueprint", "question_pool", "paper_assembly"],
                    severity="high",
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
                )
            )
        return issues

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
