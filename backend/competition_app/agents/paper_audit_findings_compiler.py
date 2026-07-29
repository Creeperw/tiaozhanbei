from __future__ import annotations

import hashlib
import json
from typing import Any

from pydantic import TypeAdapter, ValidationError

from competition_app.contracts.agent_context import build_model_context
from competition_app.contracts.paper_audit_compilation import (
    CompiledPaperAuditFindings,
    PaperAuditCompilationEnvelope,
    PaperAuditCompilerResult,
    PaperAuditNeedsRevision,
)
from competition_app.llm.base import ChatModel
from competition_app.llm.prompt_skills import prompt_skill_registry
from competition_app.llm.stub import StubChatModel


_RESULT_ADAPTER = TypeAdapter(PaperAuditCompilerResult)


class PaperAuditFindingsCompilerAgent:
    """Internal compiler that classifies only verbatim paper-audit findings."""

    def __init__(self, chat_model: ChatModel | None = None) -> None:
        self.chat_model = chat_model or StubChatModel()

    async def compile(
        self,
        context: dict[str, Any],
        *,
        audit_report: str,
        findings: list[str],
    ) -> PaperAuditCompilationEnvelope:
        sources = {
            "audit_report": audit_report,
            "findings": findings,
        }
        skill = prompt_skill_registry.load(
            "paper_audit_findings_compiler", "compile_paper_audit_findings"
        )
        raw = await self.chat_model.complete_json(
            "paper_audit_findings_compiler",
            build_model_context(
                context,
                target_agent="paper_audit_findings_compiler",
                prompt_skill=skill,
                payload={
                    **sources,
                    "output_schema": _RESULT_ADAPTER.json_schema(),
                },
                permission_note=(
                    "内部编译器只可逐字提取并分类审核原稿中的问题；不得创作问题、"
                    "决定审核结果、生成返修步骤或系统字段。"
                ),
            ),
        )
        result = self._parse(raw)
        integrity_issues = self._source_issues(result, sources)
        if integrity_issues:
            result = PaperAuditNeedsRevision(
                status="needs_revision",
                issues=integrity_issues,
            )
        return PaperAuditCompilationEnvelope(
            result=result,
            source_digest=self._digest(sources),
        )

    @staticmethod
    def _parse(raw: Any) -> PaperAuditCompilerResult:
        try:
            return _RESULT_ADAPTER.validate_python(raw)
        except ValidationError:
            pass
        try:
            return _RESULT_ADAPTER.validate_python(raw)
        except ValidationError as exc:
            return PaperAuditNeedsRevision(
                status="needs_revision",
                issues=[
                    {
                        "code": "schema_invalid",
                        "field_path": "/",
                        "detail": str(exc),
                    }
                ],
            )

    @classmethod
    def _source_issues(
        cls,
        result: PaperAuditCompilerResult,
        sources: dict[str, Any],
    ) -> list[dict[str, Any]]:
        if not isinstance(result, CompiledPaperAuditFindings):
            return []
        searchable = {
            "audit_report": str(sources["audit_report"]),
            "findings": json.dumps(sources["findings"], ensure_ascii=False),
        }
        issues: list[dict[str, Any]] = []
        for index, issue in enumerate(result.issues):
            field_path = f"/issues/{index}"
            if not issue.source_anchors:
                issues.append(
                    {
                        "code": "source_anchor_missing",
                        "field_path": field_path,
                    }
                )
                continue
            anchored_text = " ".join(
                anchor.source_quote for anchor in issue.source_anchors
            )
            if issue.message not in anchored_text:
                issues.append(
                    {
                        "code": "message_not_verbatim",
                        "field_path": f"{field_path}/message",
                    }
                )
            for anchor in issue.source_anchors:
                if anchor.source_quote not in searchable[anchor.source_field]:
                    issues.append(
                        {
                            "code": "source_anchor_invalid",
                            "field_path": f"{field_path}/source_anchors",
                            "detail": anchor.source_field,
                        }
                    )
        return issues

    @staticmethod
    def _digest(value: dict[str, Any]) -> str:
        serialized = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(serialized.encode("utf-8")).hexdigest()
