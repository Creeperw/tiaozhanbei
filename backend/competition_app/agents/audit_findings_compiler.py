from __future__ import annotations

import hashlib
import json
from typing import Any

from pydantic import TypeAdapter, ValidationError

from competition_app.contracts.agent_context import build_model_context
from competition_app.contracts.audit_compilation import (
    AuditFindingsCompilationEnvelope,
    AuditFindingsCompilerResult,
    AuditFindingsNeedRevision,
    AuditLocation,
    CompiledAuditFindings,
)
from competition_app.llm.base import ChatModel
from competition_app.llm.prompt_skills import prompt_skill_registry
from competition_app.llm.stub import StubChatModel


_RESULT_ADAPTER = TypeAdapter(AuditFindingsCompilerResult)


class AuditFindingsCompilerAgent:
    """Compile verbatim audit prose into located issues without planning repair."""

    def __init__(self, chat_model: ChatModel | None = None) -> None:
        self.chat_model = chat_model or StubChatModel()

    async def compile(
        self,
        context: dict[str, Any],
        *,
        subject_type: str,
        audit_report: str,
        findings: list[str],
        location_catalog: list[AuditLocation],
    ) -> AuditFindingsCompilationEnvelope:
        sources = {"audit_report": audit_report, "findings": findings}
        skill = prompt_skill_registry.load(
            "audit_findings_compiler", "compile_audit_findings"
        )
        raw = await self.chat_model.complete_json(
            "audit_findings_compiler",
            build_model_context(
                context,
                target_agent="audit_findings_compiler",
                prompt_skill=skill,
                payload={
                    **sources,
                    "subject_type": subject_type,
                    "location_catalog": [
                        item.model_dump(mode="json") for item in location_catalog
                    ],
                    "output_schema": _RESULT_ADAPTER.json_schema(),
                },
                permission_note=(
                    "源约束Compiler只可逐字提取审核原稿中的问题并选择系统提供的位置；"
                    "不得重新审核、补写问题、指定智能体、生成返修链或决定发布。"
                ),
            ),
        )
        try:
            result = _RESULT_ADAPTER.validate_python(raw)
        except ValidationError:
            # A source-bounded deterministic fallback keeps ordinary business
            # findings repairable when a provider ignores the compiler role.
            # It copies every finding verbatim and uses only the system-owned
            # whole-subject location; it never invents content or step IDs.
            result = self._deterministic_fallback(
                findings,
                subject_type=subject_type,
                location_catalog=location_catalog,
            )
        integrity = self._integrity_issues(
            result,
            sources=sources,
            allowed_location_keys={item.location_key for item in location_catalog},
        )
        if integrity:
            result = AuditFindingsNeedRevision(
                status="needs_revision",
                issues=integrity,
            )
        return AuditFindingsCompilationEnvelope(
            result=result,
            source_digest=self._digest(sources),
        )

    @staticmethod
    def _deterministic_fallback(
        findings: list[str],
        *,
        subject_type: str,
        location_catalog: list[AuditLocation],
    ) -> CompiledAuditFindings:
        whole = next(
            (
                item.location_key
                for item in location_catalog
                if item.location_type == "whole_subject"
            ),
            location_catalog[0].location_key,
        )

        def issue_type(message: str) -> str:
            if "证据" in message and any(
                word in message for word in ("缺少", "缺失", "不足", "无依据")
            ):
                return "missing_evidence"
            if any(word in message for word in ("诊疗", "处方", "剂量", "安全越界")):
                return "safety_violation"
            # 知识性正误：审核/专家辨识出的"判断错误"必须可返修，
            # 不能像表达偏好那样无痕放行。
            if any(
                word in message
                for word in (
                    "事实错误", "判定错误", "判断错误", "答案错误", "概念错误",
                    "判错", "选错", "写错", "错误结论", "知识点错误",
                    "说法错误", "不准确", "以偏概全", "因果颠倒",
                    "明确相反", "直接相反", "明确否定", "直接否定",
                )
            ):
                return "factual_error"
            # A generic source disagreement is not automatically a factual
            # error.  This branch intentionally follows the explicit-error
            # branch so prose such as "与教材明确相反，属于事实错误" cannot
            # be downgraded merely because it also contains "证据冲突".
            if "证据" in message and any(word in message for word in ("冲突", "矛盾")):
                return "conflicting_evidence"
            if subject_type in {"long_term_plan", "short_term_plan"}:
                return "plan_quality"
            if subject_type == "exam_paper":
                return "paper_item_invalid"
            return "content_quality"

        return CompiledAuditFindings(
            status="compiled",
            issues=[
                {
                    "issue_type": issue_type(message),
                    "message": message,
                    "blocking": not any(
                        word in message
                        for word in (
                            "建议", "可选", "还可以", "可在", "可继续",
                            "略", "后续", "进一步优化", "润色", "非阻断",
                        )
                    ),
                    "location_keys": [whole],
                    "source_anchors": [
                        {"source_field": "findings", "source_quote": message}
                    ],
                }
                for message in findings
                if str(message).strip()
            ],
        )

    @staticmethod
    def _integrity_issues(
        result: AuditFindingsCompilerResult,
        *,
        sources: dict[str, Any],
        allowed_location_keys: set[str],
    ) -> list[dict[str, Any]]:
        if not isinstance(result, CompiledAuditFindings):
            return []
        if sources["findings"] and not result.issues:
            # The compiler is a source-preserving boundary, not a second
            # auditor.  Silently dropping every supplied finding would turn a
            # model-detected factual error into an empty repair plan.  Mark
            # that as an integrity failure so the caller can use the bounded
            # deterministic fallback (verbatim finding + system-owned whole
            # subject location) rather than inventing content or escalating.
            return [
                {
                    "code": "schema_invalid",
                    "field_path": "/issues",
                    "detail": "source_finding_not_compiled",
                }
            ]
        searchable = {
            "audit_report": str(sources["audit_report"]),
            "findings": json.dumps(sources["findings"], ensure_ascii=False),
        }
        issues: list[dict[str, Any]] = []
        for index, finding in enumerate(result.issues):
            path = f"/issues/{index}"
            anchored = " ".join(item.source_quote for item in finding.source_anchors)
            if finding.message not in anchored:
                issues.append(
                    {"code": "message_not_verbatim", "field_path": f"{path}/message"}
                )
            for anchor in finding.source_anchors:
                if anchor.source_quote not in searchable[anchor.source_field]:
                    issues.append(
                        {
                            "code": "source_anchor_invalid",
                            "field_path": f"{path}/source_anchors",
                            "detail": anchor.source_field,
                        }
                    )
            invalid_locations = set(finding.location_keys) - allowed_location_keys
            if invalid_locations:
                issues.append(
                    {
                        "code": "location_not_allowed",
                        "field_path": f"{path}/location_keys",
                        "detail": ", ".join(sorted(invalid_locations)),
                    }
                )
        return issues

    @staticmethod
    def _digest(value: dict[str, Any]) -> str:
        raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()
