from __future__ import annotations

import hashlib
import re
from typing import Any

from pydantic import TypeAdapter, ValidationError

from competition_app.contracts.agent_context import build_model_context
from competition_app.contracts.paper_assembly_compilation import (
    CompiledPaperAssemblyResult,
    PaperAssemblyCompilationEnvelope,
    PaperAssemblyCompilerResult,
    PaperAssemblyNeedsRevision,
)
from competition_app.llm.base import ChatModel
from competition_app.llm.prompt_skills import prompt_skill_registry
from competition_app.llm.stub import StubChatModel


_RESULT_ADAPTER = TypeAdapter(PaperAssemblyCompilerResult)


class PaperAssemblyCompilerAgent:
    """Internal compiler for prose candidate selection and generated questions."""

    def __init__(self, chat_model: ChatModel | None = None) -> None:
        self.chat_model = chat_model or StubChatModel()

    async def compile(
        self,
        context: dict[str, Any],
        *,
        assembly_document: str,
        candidate_catalog: list[dict[str, Any]],
    ) -> PaperAssemblyCompilationEnvelope:
        skill = prompt_skill_registry.load(
            "paper_assembly_compiler", "compile_paper_assembly"
        )
        raw = await self.chat_model.complete_json(
            "paper_assembly_compiler",
            build_model_context(
                context,
                target_agent="paper_assembly_compiler",
                prompt_skill=skill,
                payload={
                    "assembly_document": assembly_document,
                    "candidate_catalog": candidate_catalog,
                    "output_schema": _RESULT_ADAPTER.json_schema(),
                },
                permission_note=(
                    "内部编译器只可逐字提取组装原稿中的候选选择和原创题；不得创作、"
                    "改写题目或生成系统ID、顺序、答案键及状态。"
                ),
            ),
        )
        result = self._parse(raw)
        issues = self._integrity_issues(
            result,
            assembly_document=assembly_document,
            candidate_catalog=candidate_catalog,
        )
        if issues:
            result = PaperAssemblyNeedsRevision(status="needs_revision", issues=issues)
        return PaperAssemblyCompilationEnvelope(
            result=result,
            source_digest=hashlib.sha256(
                assembly_document.encode("utf-8")
            ).hexdigest(),
        )

    @staticmethod
    def _parse(raw: Any) -> PaperAssemblyCompilerResult:
        try:
            return _RESULT_ADAPTER.validate_python(raw)
        except ValidationError as exc:
            return PaperAssemblyNeedsRevision(
                status="needs_revision",
                issues=[
                    {
                        "code": "schema_invalid",
                        "field_path": "/",
                        "detail": str(exc),
                    }
                ],
            )

    @staticmethod
    def _integrity_issues(
        result: PaperAssemblyCompilerResult,
        *,
        assembly_document: str,
        candidate_catalog: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        if not isinstance(result, CompiledPaperAssemblyResult):
            return []
        candidates = {
            (str(unit["unit_id"]), str(item["question_id"]))
            for unit in candidate_catalog
            for item in unit.get("items", [])
        }
        question_units = {
            str(item["question_id"]): str(unit["unit_id"])
            for unit in candidate_catalog
            for item in unit.get("items", [])
        }
        issues: list[dict[str, Any]] = []
        title_anchors = (
            result.contract.field_anchors.get("/title")
            or result.contract.field_anchors.get("title")
            or []
        )
        if not title_anchors:
            issues.append({"code": "source_anchor_missing", "field_path": "/title"})
        anchor_groups = list(result.contract.field_anchors.values())
        anchor_groups.extend(item.source_anchors for item in result.contract.selected_items)
        anchor_groups.extend(item.source_anchors for item in result.contract.generated_items)
        for anchors in anchor_groups:
            for anchor in anchors:
                if not PaperAssemblyCompilerAgent._source_contains(
                    assembly_document,
                    anchor.source_quote,
                ):
                    issues.append(
                        {
                            "code": "source_anchor_invalid",
                            "field_path": "/source_anchors",
                            "detail": anchor.source_quote,
                        }
                    )
        for index, selected in enumerate(result.contract.selected_items):
            if (selected.unit_id, selected.question_id) in candidates:
                continue
            code = (
                "candidate_unit_mismatch"
                if selected.question_id in question_units
                else "candidate_unknown"
            )
            issues.append(
                {
                    "code": code,
                    "field_path": f"/selected_items/{index}/question_id",
                    "detail": selected.question_id,
                }
            )
        if title_anchors and not any(
            PaperAssemblyCompilerAgent._source_contains(
                anchor.source_quote,
                result.contract.title,
            )
            for anchor in title_anchors
        ):
            issues.append(
                {
                    "code": "source_anchor_invalid",
                    "field_path": "/title",
                    "detail": result.contract.title,
                }
            )
        known_units = {str(unit["unit_id"]) for unit in candidate_catalog}
        for index, selected in enumerate(result.contract.selected_items):
            anchored = " ".join(
                anchor.source_quote for anchor in selected.source_anchors
            )
            for field_name, value in (
                ("unit_id", selected.unit_id),
                ("question_id", selected.question_id),
            ):
                # The selected pair is independently checked against the
                # read-only candidate catalog above.  Compiler models
                # occasionally anchor only the question line even though the
                # unit label is present in the surrounding source block.
                # Accept that formatting variation only when the value is
                # still present in the original assembly document; never use
                # the catalog as a substitute for a missing source mention.
                if not (
                    PaperAssemblyCompilerAgent._source_contains(anchored, value)
                    or PaperAssemblyCompilerAgent._source_contains(
                        assembly_document, value
                    )
                ):
                    issues.append(
                        {
                            "code": "source_anchor_invalid",
                            "field_path": f"/selected_items/{index}/{field_name}",
                            "detail": value,
                        }
                    )
        for index, generated in enumerate(result.contract.generated_items):
            anchored = " ".join(
                anchor.source_quote for anchor in generated.source_anchors
            )
            values = [
                generated.unit_id,
                generated.question_type,
                generated.stem,
                *generated.options,
                generated.reference_answer,
                generated.explanation,
                *generated.source_basis_refs,
            ]
            for value in values:
                if value and not PaperAssemblyCompilerAgent._source_contains(
                    anchored,
                    value,
                ):
                    issues.append(
                        {
                            "code": "source_anchor_invalid",
                            "field_path": f"/generated_items/{index}",
                            "detail": value,
                        }
                    )
            if generated.unit_id not in known_units:
                issues.append(
                    {
                        "code": "candidate_unit_mismatch",
                        "field_path": f"/generated_items/{index}/unit_id",
                        "detail": generated.unit_id,
                    }
                )
        return issues

    @staticmethod
    def _source_contains(source: str, value: str) -> bool:
        """Allow Markdown layout differences while keeping facts source-bound."""
        if value in source:
            return True

        def canonical(text: str) -> str:
            return re.sub(
                r"[\s*_#>`~\-•:：;；,.，。、“”\"'()（）【】]+",
                "",
                text,
            )

        normalized_value = canonical(value)
        return bool(normalized_value) and normalized_value in canonical(source)
