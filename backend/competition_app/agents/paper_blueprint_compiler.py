from __future__ import annotations

import hashlib
import re
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
from competition_app.services.smart_paper import (
    SMART_PAPER_TYPE_ALIASES,
    normalize_smart_paper_type,
)


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
                if unit.get("target_difficulty") is not None:
                    try:
                        parsed_difficulty = int(str(unit["target_difficulty"]).strip())
                    except (ValueError, TypeError):
                        parsed_difficulty = 0
                    if not 1 <= parsed_difficulty <= 5:
                        unit["target_difficulty"] = None
                    else:
                        unit["target_difficulty"] = parsed_difficulty
                        unit["difficulty_is_hard_constraint"] = True
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
        if isinstance(contract.get("requires_explanation"), str):
            # 模型可能把布尔字段写成文本；这里只归一化该字段的固定写法，
            # 不对蓝图原稿做任何关键词判断。
            contract["requires_explanation"] = (
                contract["requires_explanation"].strip().lower()
                in {"true", "1", "yes", "是", "需要", "必须"}
            )
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
            has_anchor = field_path in result.contract.field_anchors
            if field_path == "/units" and not has_anchor:
                has_anchor = any(
                    path.startswith("/units/")
                    for path in result.contract.field_anchors
                )
            if not has_anchor:
                issues.append(
                    {
                        "code": "source_anchor_missing",
                        "field_path": field_path,
                    }
                )
        if (
            result.contract.requires_explanation
            and "/requires_explanation" not in result.contract.field_anchors
        ):
            # 解析要求会直接改变组卷时可用候选的范围，因此它必须和题量、题型
            # 一样是“原稿里写明的”而不是模型顺手补的：拿不到逐字引文就交回
            # 返修，由蓝图原稿把交付条件写清楚。
            issues.append(
                {
                    "code": "source_anchor_missing",
                    "field_path": "/requires_explanation",
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
                if not PaperBlueprintCompilerAgent._source_contains(
                    blueprint_document,
                    anchor.source_quote,
                ):
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
        if result.contract.required_question_count is not None:
            verbatim_values.append(
                (
                    "/required_question_count",
                    str(result.contract.required_question_count),
                )
            )
        for question_type in result.contract.question_type_distribution or {}:
            verbatim_values.append(
                (f"/question_type_distribution/{question_type}", question_type)
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
            if unit.target_difficulty is not None:
                verbatim_values.append(
                    (f"{unit_path}/target_difficulty", str(unit.target_difficulty))
                )
        for field_path, value in verbatim_values:
            if not value:
                continue
            if field_path.endswith("/question_type_preferences") or (
                "/question_type_distribution/" in field_path
            ):
                if PaperBlueprintCompilerAgent._question_type_anchored(
                    value,
                    blueprint_document,
                ):
                    continue
            elif PaperBlueprintCompilerAgent._source_contains(
                blueprint_document,
                value,
            ):
                continue
            issues.append(
                {
                    "code": "source_anchor_invalid",
                    "field_path": field_path,
                    "detail": value,
                }
            )
        return issues

    @staticmethod
    def _question_type_anchored(value: str, source: str) -> bool:
        """Whether the source names this question type under any alias.

        题型名是系统枚举，别名表是固定的客观映射：模型把原稿里的“单选题”
        写成规范名“单项选择题”属于同一概念的用词差异，不构成新事实。无法
        归一到已知枚举的值仍需退回逐字校验。
        """

        canonical = normalize_smart_paper_type(value)
        if canonical is None:
            return False
        compact_source = re.sub(r"\s", "", source)
        return any(
            alias in compact_source
            for alias, target in SMART_PAPER_TYPE_ALIASES.items()
            if target == canonical
        )

    @staticmethod
    def _source_contains(source: str, value: str) -> bool:
        """Accept formatting-only differences without accepting new facts.

        逐字锚点的目的是“编译器不得引入原稿没有的事实”，而不是要求模型和
        原稿使用同一套标点。这里剔除全部空白、Markdown 标记和中英文标点后
        再做子串比对；但数字必须整段命中，否则“10”会被“100”的前两位命中，
        等于放行一个原稿并不存在的事实。
        """
        for number in re.findall(r"\d+", value):
            if not re.search(rf"(?<!\d){re.escape(number)}(?!\d)", source):
                return False
        if value in source:
            return True

        def canonical(text: str) -> str:
            return re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", "", text)

        normalized_value = canonical(value)
        if not normalized_value:
            return False
        return normalized_value in canonical(source)
