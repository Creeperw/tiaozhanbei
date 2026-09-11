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
        request = build_model_context(
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
        )
        for attempt in range(2):
            raw = await self.chat_model.complete_json("audit_findings_compiler", request)
            try:
                result = _RESULT_ADAPTER.validate_python(raw)
            except ValidationError:
                result = AuditFindingsNeedRevision(
                    status="needs_revision",
                    issues=[{"code": "schema_invalid", "field_path": "/"}],
                )
            integrity = self._integrity_issues(
                result,
                sources=sources,
                allowed_location_keys={item.location_key for item in location_catalog},
            )
            if integrity:
                result = AuditFindingsNeedRevision(status="needs_revision", issues=integrity)
            if result.status == "compiled" or attempt == 1:
                break
            request["payload"]["compilation_feedback"] = {
                "issues": [item.model_dump(mode="json") for item in result.issues],
                "instruction": (
                    "仅修正本次源约束编译的协议、引用或位置错误。重新阅读原审核材料，"
                    "由你判断问题类型及否定语义；不得补写问题或决定发布。"
                ),
            }
        return AuditFindingsCompilationEnvelope(
            result=result,
            source_digest=self._digest(sources),
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
            # that as an integrity failure for the same compiler to correct;
            # program code must not infer issue semantics as a fallback.
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
