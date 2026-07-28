from __future__ import annotations

import hashlib
from typing import Any

from pydantic import TypeAdapter, ValidationError

from competition_app.contracts.agent_context import build_model_context
from competition_app.contracts.paper_blueprint_compilation import (
    CompiledPaperBlueprintResult,
    PaperBlueprintCompilationEnvelope,
    PaperBlueprintCompilerResult,
    PaperBlueprintNeedsRevision,
)
from competition_app.llm.base import ChatModel
from competition_app.llm.prompt_skills import prompt_skill_registry
from competition_app.llm.stub import StubChatModel


_RESULT_ADAPTER = TypeAdapter(PaperBlueprintCompilerResult)


def _coerce_blueprint(raw: dict) -> dict:
    """Coerce common type mismatches from DeepSeek output."""
    contract = raw.get("contract", {})
    if isinstance(contract, dict):
        for unit in contract.get("units", []):
            if isinstance(unit, dict):
                if "required_question_count" in unit:
                    try: unit["required_question_count"] = int(unit["required_question_count"])
                    except (ValueError, TypeError): unit["required_question_count"] = 1
                if "score_total" in unit and unit["score_total"] is not None:
                    try: unit["score_total"] = float(unit["score_total"])
                    except (ValueError, TypeError): unit["score_total"] = None
                for key in ("unit_key", "knowledge_module", "learning_objective", "retrieval_query"):
                    if not unit.get(key): unit[key] = unit.get("unit_key") or unit.get("knowledge_module") or "学习单元"
                for key in ("question_type_preferences", "selection_rules"):
                    if key in unit and not isinstance(unit[key], list):
                        unit[key] = [str(unit[key])] if unit[key] else []
        for key in ("duration_minutes",):
            if key in contract and contract[key] is not None:
                try: contract[key] = int(contract[key])
                except (ValueError, TypeError): contract[key] = None
        if "total_score" in contract and contract["total_score"] is not None:
            try: contract["total_score"] = float(contract["total_score"])
            except (ValueError, TypeError): contract["total_score"] = None
        for key in ("title", "scope_summary"):
            if not contract.get(key): contract[key] = "试卷"
        if not isinstance(contract.get("units"), list) or len(contract.get("units", [])) == 0:
            contract["units"] = [{"unit_key": "默认单元", "knowledge_module": "综合练习", "learning_objective": "巩固知识点", "retrieval_query": "综合练习", "required_question_count": 5}]
        if not isinstance(contract.get("field_anchors"), dict):
            contract["field_anchors"] = {}
    return raw


class PaperBlueprintCompilerAgent:
    """Internal compiler that extracts a minimal blueprint from prose."""

    def __init__(self, chat_model: ChatModel | None = None) -> None:
        self.chat_model = chat_model or StubChatModel()

    async def compile(
        self,
        context: dict[str, Any],
        *,
        blueprint_document: str,
    ) -> PaperBlueprintCompilationEnvelope:
        skill = prompt_skill_registry.load(
            "paper_blueprint_compiler", "compile_paper_blueprint"
        )
        raw = await self.chat_model.complete_json(
            "paper_blueprint_compiler",
            build_model_context(
                context,
                target_agent="paper_blueprint_compiler",
                prompt_skill=skill,
                payload={
                    "blueprint_document": blueprint_document,
                    "output_schema": _RESULT_ADAPTER.json_schema(),
                },
                permission_note=(
                    "内部编译器只可逐字提取自然语言蓝图中的最小合同；不得创作、"
                    "补写、改写或生成任何系统ID、候选数量与持久化状态。"
                ),
            ),
        )
        result = self._parse(raw)
        issues = self._source_issues(result, blueprint_document)
        if issues:
            result = PaperBlueprintNeedsRevision(
                status="needs_revision",
                issues=issues,
            )
        return PaperBlueprintCompilationEnvelope(
            result=result,
            source_digest=hashlib.sha256(
                blueprint_document.encode("utf-8")
            ).hexdigest(),
        )

    @staticmethod
    def _parse(raw: Any) -> PaperBlueprintCompilerResult:
        try:
            return _RESULT_ADAPTER.validate_python(raw)
        except ValidationError:
            pass
        try:
            if isinstance(raw, dict):
                raw = _coerce_blueprint(raw)
            return _RESULT_ADAPTER.validate_python(raw)
        except ValidationError as exc:
            return PaperBlueprintNeedsRevision(
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
    def _source_issues(
        result: PaperBlueprintCompilerResult,
        blueprint_document: str,
    ) -> list[dict[str, Any]]:
        if not isinstance(result, CompiledPaperBlueprintResult):
            return []
        issues: list[dict[str, Any]] = []
        required_paths = {"/title", "/scope_summary", "/units"}
        for field_path in required_paths:
            if field_path not in result.contract.field_anchors:
                issues.append(
                    {
                        "code": "source_anchor_missing",
                        "field_path": field_path,
                    }
                )
        for field_path, anchors in result.contract.field_anchors.items():
            if not anchors:
                issues.append(
                    {
                        "code": "source_anchor_missing",
                        "field_path": field_path,
                    }
                )
                continue
            for anchor in anchors:
                if anchor.source_quote not in blueprint_document:
                    issues.append(
                        {
                            "code": "source_anchor_invalid",
                            "field_path": field_path,
                            "detail": anchor.source_quote,
                        }
                    )
        verbatim_values: list[tuple[str, str]] = [
            ("/title", result.contract.title),
            ("/scope_summary", result.contract.scope_summary),
        ]
        if result.contract.duration_minutes is not None:
            verbatim_values.append(
                ("/duration_minutes", str(result.contract.duration_minutes))
            )
        if result.contract.total_score is not None:
            verbatim_values.append(
                ("/total_score", format(result.contract.total_score, "g"))
            )
        for index, unit in enumerate(result.contract.units):
            unit_path = f"/units/{index}"
            verbatim_values.extend(
                [
                    (f"{unit_path}/knowledge_module", unit.knowledge_module),
                    (f"{unit_path}/learning_objective", unit.learning_objective),
                    (f"{unit_path}/retrieval_query", unit.retrieval_query),
                    (
                        f"{unit_path}/required_question_count",
                        str(unit.required_question_count),
                    ),
                    *[
                        (f"{unit_path}/question_type_preferences", value)
                        for value in unit.question_type_preferences
                    ],
                    *[
                        (f"{unit_path}/selection_rules", value)
                        for value in unit.selection_rules
                    ],
                ]
            )
            if unit.score_total is not None:
                verbatim_values.append(
                    (f"{unit_path}/score_total", format(unit.score_total, "g"))
                )
        for field_path, value in verbatim_values:
            if value and value not in blueprint_document:
                issues.append(
                    {
                        "code": "source_anchor_invalid",
                        "field_path": field_path,
                        "detail": value,
                    }
                )
        return issues
