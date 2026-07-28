from __future__ import annotations

import hashlib
import json
from typing import Any

from pydantic import TypeAdapter, ValidationError

from competition_app.contracts.agent_context import build_model_context
from competition_app.contracts.plan_compilation import (
    CompiledPlanContractResult,
    PlanCompilationEnvelope,
    PlanContractCompilerResult,
    PlanContractNeedsRevision,
)
from competition_app.llm.base import ChatModel
from competition_app.llm.prompt_skills import prompt_skill_registry
from competition_app.llm.stub import StubChatModel


_RESULT_ADAPTER = TypeAdapter(PlanContractCompilerResult)


class PlanContractCompilerAgent:
    """Internal, non-presentational agent that extracts a plan contract."""

    def __init__(self, chat_model: ChatModel | None = None) -> None:
        self.chat_model = chat_model or StubChatModel()

    async def compile(
        self,
        context: dict[str, Any],
        *,
        plan_scope: str,
        diagnosis_output: dict[str, Any],
        trusted_route: dict[str, Any],
        parent_plan_constraints: dict[str, Any],
    ) -> PlanCompilationEnvelope:
        skill = prompt_skill_registry.load(
            "plan_contract_compiler", "compile_plan_contract"
        )
        source_digest = self._digest(diagnosis_output)
        payload = {
            "plan_scope": plan_scope,
            "diagnosis_output": diagnosis_output,
            "trusted_route": trusted_route,
            "parent_plan_constraints": parent_plan_constraints,
            "output_schema": TypeAdapter(PlanContractCompilerResult).json_schema(),
        }
        raw = await self.chat_model.complete_json(
            "plan_contract_compiler",
            build_model_context(
                context,
                target_agent="plan_contract_compiler",
                prompt_skill=skill,
                payload=payload,
                permission_note=(
                    "内部编译器只可提取当前层规划并引用原文；不得创作、补写、"
                    "改写计划，不得生成系统ID、路线事实或持久化字段。"
                ),
            ),
        )
        result = self._parse(raw)
        issues = self._source_issues(result, diagnosis_output, plan_scope)
        if issues:
            result = PlanContractNeedsRevision(
                status="needs_revision",
                issues=issues,
            )
        return PlanCompilationEnvelope(
            result=result,
            source_digest=source_digest,
        )

    @staticmethod
    def _parse(raw: Any) -> PlanContractCompilerResult:
        try:
            return _RESULT_ADAPTER.validate_python(raw)
        except ValidationError as exc:
            return PlanContractNeedsRevision(
                status="needs_revision",
                issues=[
                    {
                        "code": "schema_invalid",
                        "category": "invalid",
                        "field_path": "/",
                        "source_refs": [str(exc)],
                    }
                ],
            )

    @staticmethod
    def _digest(value: dict[str, Any]) -> str:
        serialized = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(serialized.encode("utf-8")).hexdigest()

    @classmethod
    def _source_issues(
        cls,
        result: PlanContractCompilerResult,
        diagnosis_output: dict[str, Any],
        plan_scope: str,
    ) -> list[dict[str, Any]]:
        if not isinstance(result, CompiledPlanContractResult):
            return []
        contract = result.contract
        if contract.scope != plan_scope:
            return [
                {
                    "code": "scope_violation",
                    "category": "invalid",
                    "field_path": "/scope",
                }
            ]
        issues: list[dict[str, Any]] = []
        searchable_sources = {
            str(key): cls._source_text(value)
            for key, value in diagnosis_output.items()
        }
        for field_path, anchors in contract.field_anchors.items():
            if not anchors:
                issues.append(
                    {
                        "code": "source_anchor_missing",
                        "category": "invalid",
                        "field_path": field_path,
                    }
                )
                continue
            for anchor in anchors:
                source = searchable_sources.get(anchor.source_field)
                if source is None or anchor.source_quote not in source:
                    issues.append(
                        {
                            "code": "source_anchor_invalid",
                            "category": "invalid",
                            "field_path": field_path,
                            "source_refs": [anchor.source_field],
                        }
                    )
        required_paths = cls._required_anchor_paths(contract.scope)
        for field_path in required_paths:
            if field_path not in contract.field_anchors:
                issues.append(
                    {
                        "code": "source_anchor_missing",
                        "category": "invalid",
                        "field_path": field_path,
                    }
                )
        return issues

    @staticmethod
    def _source_text(value: Any) -> str:
        if isinstance(value, str):
            return value
        return json.dumps(value, ensure_ascii=False, sort_keys=True)

    @staticmethod
    def _required_anchor_paths(scope: str) -> set[str]:
        if scope == "long_term":
            return {"/long_term_plan_content", "/total_duration_days", "/stages"}
        if scope == "short_term":
            return {
                "/short_term_plan_content",
                "/duration_days",
                "/progression_nodes",
                "/selected_books",
            }
        return {
            "/daily_task_content",
            "/learning_chapter",
            "/focus_knowledge_points",
            "/estimated_minutes",
            "/expected_output",
            "/completion_criteria",
        }
