from __future__ import annotations

from typing import Any
from uuid import uuid4

from competition_app.agents.common import envelope
from competition_app.contracts.base import AgentEnvelope
from competition_app.contracts.agent_context import build_model_context
from competition_app.contracts.resource import AuditResult
from competition_app.llm.base import ChatModel
from competition_app.llm.prompt_skills import prompt_skill_registry
from competition_app.llm.stub import StubChatModel
from competition_app.llm.schemas import AuditModelOutput
from pydantic import ValidationError


class AuditAgent:
    def __init__(self, chat_model: ChatModel | None = None) -> None:
        self.chat_model = chat_model or StubChatModel()

    async def run(self, context: dict[str, Any]) -> AgentEnvelope[AuditResult]:
        prompt_skill = prompt_skill_registry.load(
            "audit_agent", str(context.get("task_type", "personalized_review_card"))
        )
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
            "target_difficulty": expert.target_difficulty,
            "estimated_minutes": expert.estimated_minutes,
            "claim_texts": [claim.text for claim in expert.claims],
            "safety_notes": expert.safety_notes,
        }
        semantic_evidence = [
            {"text": item.content_summary, "authority": item.authority_level}
            for item in evidence.evidence_items
        ]
        diagnosis = getattr(context["dependency_outputs"].get("diagnosis"), "payload", None)
        schedule = getattr(context["dependency_outputs"].get("schedule"), "payload", None)
        knowledge_explanation = str(context.get("task_type")) == "knowledge_explanation"
        paper_generation = str(context.get("task_type")) == "paper_generation"
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
                        "target_difficulty": getattr(diagnosis, "target_difficulty", 2),
                    },
                    "acceptance_criteria": {
                        "available_minutes": context.get("available_minutes"),
                        "teaching_only": True,
                        "paper_generation": paper_generation,
                        "knowledge_explanation": knowledge_explanation,
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
        if selected_task and expert.target_kp_id != selected_task.primary_kp_id:
            deterministic_findings.append("资源目标知识点与复习调度任务不一致。")
        if expert.estimated_minutes > int(context.get("available_minutes", 15)):
            deterministic_findings.append("资源预计时长超过用户本次可用时间。")
        model_decision = model_output.decision
        decision = "revise" if missing or deterministic_findings else model_decision
        if (
            (knowledge_explanation or str(context.get("task_type")) == "personalized_review_card")
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
            findings=[
                *([f"缺少证据的声明: {', '.join(missing)}"] if missing else []),
                *deterministic_findings,
                *([] if missing or deterministic_findings else model_output.findings),
            ],
            verified_claim_ids=[claim.claim_id for claim in expert.claims if claim.claim_id not in missing],
        )
        return envelope(context, "audit_agent", "audit_result", result)

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
                            "exam_paper": paper.model_dump(mode="json"),
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
        if set(paper.answer_key) != set(selected_ids):
            deterministic_findings.append("答案键与入卷题目不一致。")
        decision = "revise" if deterministic_findings else model_output.decision
        if audit_format_drifted and not deterministic_findings:
            decision = "pass"
        if decision == "revise" and not deterministic_findings:
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
            findings=[*deterministic_findings, *model_output.findings],
            verified_claim_ids=[],
        )
        return envelope(context, "audit_agent", "audit_result", result)

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
