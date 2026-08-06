from __future__ import annotations

import hashlib
import json
import re
from copy import deepcopy
from typing import Any

from pydantic import TypeAdapter, ValidationError

from competition_app.contracts.agent_context import build_model_context
from competition_app.contracts.plan_compilation import (
    CompiledDailyTaskContract,
    CompiledLongTermContract,
    CompiledLongTermStage,
    CompiledPlanContractResult,
    CompiledShortTermContract,
    PlanCompilationEnvelope,
    PlanContractCompilerResult,
    PlanContractNeedsRevision,
)
from competition_app.llm.base import ChatModel
from competition_app.llm.openai_compatible import ModelResponseError
from competition_app.llm.prompt_skills import prompt_skill_registry
from competition_app.llm.stub import StubChatModel


_RESULT_ADAPTER = TypeAdapter(PlanContractCompilerResult)


class PlanContractCompilerAgent:
    """Internal, non-presentational agent that extracts a plan contract."""

    _SOURCE_FIELD_ALIASES: dict[str, tuple[str, ...]] = {
        "long_term_plan_content": (
            "长期规划正文",
            "长期计划正文",
        ),
        "long_term_plan_stages": (
            "stages",
            "阶段列表",
            "长期规划阶段",
            "长期计划阶段",
        ),
        "total_duration_days": (
            "总周期天数",
            "长期计划天数",
            "规划总天数",
        ),
        "short_term_plan_content": (
            "短期计划正文",
            "短期规划正文",
        ),
        "duration_days": (
            "计划周期",
            "短期周期",
            "周期天数",
            "短期计划天数",
        ),
        "progression_nodes": (
            "推进节点",
            "短期推进节点",
            "周期节点",
        ),
        "expected_output": (
            "预期产出",
            "预期成果",
            "可观察产出",
        ),
        "completion_criteria": (
            "完成标准",
            "完成条件",
            "验收标准",
            "完成判定标准",
        ),
        "selected_stage_id": (
            "选定阶段ID",
            "阶段ID",
            "当前阶段ID",
        ),
        "selected_books": (
            "选用教材",
            "当前教材",
            "具体教材",
            "教材列表",
        ),
        "daily_task_content": (
            "当日任务正文",
            "今日任务正文",
            "今日任务",
        ),
        "learning_chapter": (
            "学习章节",
            "今日章节",
            "教材章节",
        ),
        "focus_knowledge_points": (
            "重点知识点",
            "聚焦知识点",
            "今日知识点",
        ),
        "estimated_minutes": (
            "预计用时（分钟）",
            "预计用时(分钟)",
            "预计用时",
            "预计时长",
            "预计分钟数",
        ),
    }

    _CONTRACT_FIELD_ALIASES: dict[str, tuple[str, ...]] = {
        "stages": ("long_term_plan_stages", "阶段列表", "长期规划阶段"),
        "total_duration_days": ("总周期天数", "规划总天数"),
        "duration_days": ("计划周期", "短期周期", "周期天数"),
        "progression_nodes": ("推进节点", "短期推进节点", "周期节点"),
        "expected_output": ("预期产出", "预期成果", "可观察产出"),
        "completion_criteria": (
            "完成标准",
            "完成条件",
            "验收标准",
            "完成判定标准",
        ),
        "selected_stage_id": ("选定阶段ID", "阶段ID", "当前阶段ID"),
        "selected_books": ("选用教材", "当前教材", "具体教材", "教材列表"),
        "learning_chapter": ("学习章节", "今日章节", "教材章节"),
        "focus_knowledge_points": ("重点知识点", "聚焦知识点", "今日知识点"),
        "estimated_minutes": (
            "预计用时（分钟）",
            "预计用时(分钟)",
            "预计用时",
            "预计时长",
            "预计分钟数",
        ),
    }

    _RECOVERABLE_ISSUE_CODES = frozenset(
        {
            "missing_required_field",
            "missing_source",
        }
    )

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
        source_digest = self._digest(diagnosis_output)

        # New Diagnosis drafts are natural-language documents.  They must
        # always pass through the compiler model; otherwise a structured
        # Diagnosis response can silently become the execution contract and
        # the Compiler is reduced to a no-op wrapper.
        #
        # Keep the deterministic path only for legacy callers/tests that still
        # provide the old field-by-field payload and do not provide a document.
        # This compatibility path is intentionally unreachable for the new
        # business-agent boundary.
        if "plan_document" not in diagnosis_output:
            direct_result = self._compile_from_direct_sources(
                diagnosis_output,
                plan_scope,
                parent_plan_constraints,
            )
            if direct_result is not None:
                direct_issues = self._source_issues(
                    direct_result,
                    diagnosis_output,
                    plan_scope,
                )
                if not direct_issues:
                    return PlanCompilationEnvelope(
                        result=direct_result,
                        source_digest=source_digest,
                    )

        # The model compiler extracts a strict contract from the document.
        skill = prompt_skill_registry.load(
            "plan_contract_compiler", "compile_plan_contract"
        )
        payload = {
            "plan_scope": plan_scope,
            "diagnosis_output": diagnosis_output,
            "trusted_route": trusted_route,
            "parent_plan_constraints": parent_plan_constraints,
            "system_inserted_fields": (
                []
                if "plan_document" in diagnosis_output
                else self._system_inserted_fields(plan_scope)
            ),
            "output_schema": self._model_output_schema(include_managed_text=False),
        }
        try:
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
        except ModelResponseError as exc:
            # The provider returned no content at all (for example the
            # thinking model emitted only reasoning).  A complete prose
            # document must still be compilable deterministically; treat
            # this exactly like a ``needs_revision`` model result and run
            # the same plan_document fallback instead of failing the whole
            # planning run.
            if (
                isinstance(diagnosis_output.get("plan_document"), str)
                and diagnosis_output["plan_document"].strip()
            ):
                doc_result = self._compile_from_plan_document(
                    diagnosis_output["plan_document"],
                    plan_scope,
                    parent_plan_constraints,
                )
                if doc_result is not None:
                    doc_issues = self._source_issues(
                        doc_result,
                        diagnosis_output,
                        plan_scope,
                    )
                    if not doc_issues:
                        return PlanCompilationEnvelope(
                            result=doc_result,
                            source_digest=source_digest,
                        )
            raise
        normalized_raw = self._normalize_model_output(raw, diagnosis_output)
        # The compiler model may return extracted field values but omit the
        # corresponding field_anchors entries (unstable extraction).  Backfill
        # missing anchors from diagnosis_output verbatim text; values that do
        # not actually appear in the diagnosis output keep failing strict
        # source validation below.
        normalized_raw = self._backfill_anchors(
            normalized_raw,
            diagnosis_output,
            plan_scope,
        )
        result = self._parse(
            self._inject_system_fields(
                normalized_raw,
                diagnosis_output,
                plan_scope,
            )
        )
        issues = self._source_issues(result, diagnosis_output, plan_scope)
        result_issues = (
            result.issues
            if isinstance(result, PlanContractNeedsRevision)
            else issues
        )
        # The compiler model can give up on a natural-language document even
        # when every value is present.  Fall back to a deterministic parse of
        # the labeled plan_document; strict source validation still applies to
        # whatever the parser produces.
        if (
            result_issues
            and isinstance(diagnosis_output.get("plan_document"), str)
            and diagnosis_output["plan_document"].strip()
        ):
            doc_result = self._compile_from_plan_document(
                diagnosis_output["plan_document"],
                plan_scope,
                parent_plan_constraints,
            )
            if doc_result is not None:
                doc_issues = self._source_issues(
                    doc_result,
                    diagnosis_output,
                    plan_scope,
                )
                if not doc_issues:
                    result = doc_result
                    issues = []
        if (
            "plan_document" not in diagnosis_output
            and isinstance(result, PlanContractNeedsRevision)
            and result_issues
            and self._can_recover_from_direct_sources(result_issues)
        ):
            recovered = self._compile_from_direct_sources(
                diagnosis_output,
                plan_scope,
                parent_plan_constraints,
            )
            if recovered is not None:
                recovered_issues = self._source_issues(
                    recovered,
                    diagnosis_output,
                    plan_scope,
                )
                if not recovered_issues:
                    result = recovered
                    issues = []
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
        except ValidationError:
            pass
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

    @classmethod
    def _normalize_model_output(
        cls,
        raw: Any,
        diagnosis_output: dict[str, Any],
    ) -> Any:
        """Accept known Chinese labels without weakening source validation."""

        if not isinstance(raw, dict):
            return raw
        normalized = deepcopy(raw)
        if normalized.get("status") == "compiled":
            contract = normalized.get("contract")
            if isinstance(contract, dict):
                normalized["contract"] = cls._normalize_contract(
                    contract,
                    diagnosis_output,
                )
        elif normalized.get("status") == "needs_revision":
            issues = normalized.get("issues")
            if isinstance(issues, list):
                normalized["issues"] = [
                    cls._normalize_issue(issue, diagnosis_output)
                    if isinstance(issue, dict)
                    else issue
                    for issue in issues
                ]
        return normalized

    @classmethod
    def _normalize_contract(
        cls,
        contract: dict[str, Any],
        diagnosis_output: dict[str, Any],
    ) -> dict[str, Any]:
        normalized: dict[str, Any] = {}
        for key, value in contract.items():
            canonical = cls._canonical_contract_field(key)
            if canonical == "field_anchors" and isinstance(value, dict):
                normalized[canonical] = cls._normalize_field_anchors(
                    value,
                    diagnosis_output,
                )
            elif canonical == "stages" and isinstance(value, list):
                normalized[canonical] = [
                    cls._normalize_stage(item) if isinstance(item, dict) else item
                    for item in value
                ]
            elif canonical not in normalized:
                normalized[canonical] = value
        return normalized

    @classmethod
    def _normalize_stage(cls, stage: dict[str, Any]) -> dict[str, Any]:
        aliases = {
            "stage": ("阶段", "阶段序号"),
            "stage_name": ("阶段名称", "名称"),
            "books": ("book", "教材", "具体教材"),
            "goal": ("目标", "阶段目标"),
            "duration_days": ("阶段天数", "周期天数"),
            "schedule_summary": ("阶段安排", "学习安排", "安排摘要"),
            "acceptance": ("验收标准", "晋级条件", "通过条件", "完成标准", "验收条款"),
        }
        normalized: dict[str, Any] = {}
        for key, value in stage.items():
            canonical = key
            for target, candidates in aliases.items():
                if cls._same_label(key, target, *candidates):
                    canonical = target
                    break
            if canonical == "books" and isinstance(value, str):
                value = [value]
            if canonical not in normalized:
                normalized[canonical] = value
        return normalized

    @classmethod
    def _normalize_field_anchors(
        cls,
        anchors: dict[str, Any],
        diagnosis_output: dict[str, Any],
    ) -> dict[str, Any]:
        normalized: dict[str, Any] = {}
        for path, values in anchors.items():
            canonical_path = cls._canonical_contract_path(path)
            normalized.setdefault(canonical_path, [])
            if not isinstance(values, list):
                values = [values]
            for value in values:
                if not isinstance(value, dict):
                    normalized[canonical_path].append(value)
                    continue
                item = dict(value)
                source_field = item.get("source_field")
                resolved = cls._resolve_source_field(
                    source_field,
                    diagnosis_output,
                )
                if resolved is not None:
                    item["source_field"] = resolved
                normalized[canonical_path].append(item)
        return normalized

    @classmethod
    def _normalize_issue(
        cls,
        issue: dict[str, Any],
        diagnosis_output: dict[str, Any],
    ) -> dict[str, Any]:
        normalized = dict(issue)
        if isinstance(normalized.get("field_path"), str):
            normalized["field_path"] = cls._canonical_contract_path(
                normalized["field_path"]
            )
        refs = normalized.get("source_refs")
        if isinstance(refs, list):
            normalized["source_refs"] = [
                cls._resolve_source_field(ref, diagnosis_output) or ref
                for ref in refs
            ]
        conflicts = normalized.get("conflicting_source_refs")
        if isinstance(conflicts, list):
            normalized["conflicting_source_refs"] = [
                cls._resolve_source_field(ref, diagnosis_output) or ref
                for ref in conflicts
            ]
        return normalized

    @classmethod
    def _can_recover_from_direct_sources(cls, issues: list[Any]) -> bool:
        return bool(issues) and all(
            getattr(issue, "code", None) in cls._RECOVERABLE_ISSUE_CODES
            for issue in issues
        )

    @classmethod
    def _compile_from_direct_sources(
        cls,
        diagnosis_output: dict[str, Any],
        plan_scope: str,
        parent_plan_constraints: dict[str, Any],
    ) -> CompiledPlanContractResult | None:
        """Build only from explicit Diagnosis fields, never from prose guesses."""

        try:
            if plan_scope == "long_term":
                return cls._compile_long_term_from_sources(diagnosis_output)
            if plan_scope == "short_term":
                return cls._compile_short_term_from_sources(
                    diagnosis_output,
                    parent_plan_constraints,
                )
            if plan_scope == "daily_task":
                return cls._compile_daily_task_from_sources(diagnosis_output)
        except (TypeError, ValueError, ValidationError):
            return None
        return None

    @classmethod
    def _compile_long_term_from_sources(
        cls,
        diagnosis_output: dict[str, Any],
    ) -> CompiledPlanContractResult | None:
        content_key, content = cls._source_value(
            diagnosis_output,
            "long_term_plan_content",
        )
        total_key, total = cls._source_value(
            diagnosis_output,
            "total_duration_days",
        )
        stages_key, stages = cls._source_value(
            diagnosis_output,
            "long_term_plan_stages",
        )
        if not isinstance(content, str) or not content.strip():
            return None
        if not isinstance(stages, list) or not stages:
            return None
        compiled_stages: list[CompiledLongTermStage] = []
        for position, value in enumerate(stages, start=1):
            if not isinstance(value, dict):
                return None
            stage = cls._normalize_stage(value)
            books = stage.get("books")
            if isinstance(books, str):
                books = [books]
            acceptance = stage.get("acceptance")
            if isinstance(acceptance, str):
                acceptance = [acceptance]
            acceptance = (
                [str(item).strip() for item in acceptance if str(item).strip()]
                if isinstance(acceptance, list)
                else []
            )
            compiled_stages.append(
                CompiledLongTermStage(
                    stage=int(stage.get("stage") or position),
                    stage_name=str(stage.get("stage_name") or ""),
                    books=books or [],
                    goal=str(stage.get("goal") or ""),
                    duration_days=int(stage.get("duration_days") or 0),
                    schedule_summary=str(stage.get("schedule_summary") or ""),
                    acceptance=acceptance,
                )
            )
        if not isinstance(total, int) or total <= 0:
            return None
        if sum(stage.duration_days for stage in compiled_stages) != total:
            return None
        contract = CompiledLongTermContract(
            scope="long_term",
            long_term_plan_content=content,
            total_duration_days=total,
            stages=compiled_stages,
            field_anchors={
                "/long_term_plan_content": cls._anchors_for_value(
                    content_key,
                    content,
                ),
                "/total_duration_days": cls._anchors_for_value(total_key, total),
                "/stages": cls._anchors_for_value(stages_key, stages),
            },
        )
        return CompiledPlanContractResult(status="compiled", contract=contract)

    @classmethod
    def _compile_short_term_from_sources(
        cls,
        diagnosis_output: dict[str, Any],
        parent_plan_constraints: dict[str, Any],
    ) -> CompiledPlanContractResult | None:
        content_key, content = cls._source_value(
            diagnosis_output,
            "short_term_plan_content",
        )
        duration_key, duration = cls._source_value(diagnosis_output, "duration_days")
        nodes_key, nodes = cls._source_value(diagnosis_output, "progression_nodes")
        output_key, expected_output = cls._source_value(
            diagnosis_output,
            "expected_output",
        )
        criteria_key, completion_criteria = cls._source_value(
            diagnosis_output,
            "completion_criteria",
        )
        books_key, books = cls._source_value(diagnosis_output, "selected_books")
        stage_key, selected_stage_id = cls._source_value(
            diagnosis_output,
            "selected_stage_id",
        )
        limit = parent_plan_constraints.get("current_stage_duration_days")
        if limit is not None and isinstance(duration, int) and duration > limit:
            return None
        if not isinstance(content, str) or not content.strip():
            return None
        if not isinstance(duration, int) or duration <= 0:
            return None
        if not isinstance(nodes, list) or len(nodes) < 2:
            return None
        if not isinstance(expected_output, str) or not expected_output.strip():
            return None
        if not isinstance(completion_criteria, str) or not completion_criteria.strip():
            return None
        if not isinstance(books, list) or not books:
            return None
        field_anchors = {
            "/short_term_plan_content": cls._anchors_for_value(
                content_key,
                content,
            ),
            "/duration_days": cls._anchors_for_value(duration_key, duration),
            "/progression_nodes": cls._anchors_for_value(nodes_key, nodes),
            "/expected_output": cls._anchors_for_value(output_key, expected_output),
            "/completion_criteria": cls._anchors_for_value(
                criteria_key,
                completion_criteria,
            ),
            "/selected_books": cls._anchors_for_value(books_key, books),
        }
        if stage_key is not None and selected_stage_id is not None:
            field_anchors["/selected_stage_id"] = cls._anchors_for_value(
                stage_key,
                selected_stage_id,
            )
        contract = CompiledShortTermContract(
            scope="short_term",
            short_term_plan_content=content,
            duration_days=duration,
            progression_nodes=nodes,
            expected_output=expected_output,
            completion_criteria=completion_criteria,
            selected_stage_id=(
                selected_stage_id if isinstance(selected_stage_id, str) else None
            ),
            selected_books=books,
            field_anchors=field_anchors,
        )
        return CompiledPlanContractResult(status="compiled", contract=contract)

    @classmethod
    def _compile_daily_task_from_sources(
        cls,
        diagnosis_output: dict[str, Any],
    ) -> CompiledPlanContractResult | None:
        values = {
            field: cls._source_value(diagnosis_output, field)
            for field in (
                "daily_task_content",
                "learning_chapter",
                "focus_knowledge_points",
                "estimated_minutes",
                "expected_output",
                "completion_criteria",
            )
        }
        content_key, content = values["daily_task_content"]
        chapter_key, chapter = values["learning_chapter"]
        points_key, points = values["focus_knowledge_points"]
        minutes_key, minutes = values["estimated_minutes"]
        output_key, expected_output = values["expected_output"]
        criteria_key, completion_criteria = values["completion_criteria"]
        if (
            not isinstance(content, str)
            or not content.strip()
            or not isinstance(chapter, str)
            or not chapter.strip()
            or not isinstance(points, list)
            or not points
            or not isinstance(minutes, int)
            or minutes <= 0
            or not isinstance(expected_output, str)
            or not expected_output.strip()
            or not isinstance(completion_criteria, str)
            or not completion_criteria.strip()
        ):
            return None
        contract = CompiledDailyTaskContract(
            scope="daily_task",
            daily_task_content=content,
            learning_chapter=chapter,
            focus_knowledge_points=points,
            estimated_minutes=minutes,
            expected_output=expected_output,
            completion_criteria=completion_criteria,
            field_anchors={
                "/daily_task_content": cls._anchors_for_value(content_key, content),
                "/learning_chapter": cls._anchors_for_value(chapter_key, chapter),
                "/focus_knowledge_points": cls._anchors_for_value(points_key, points),
                "/estimated_minutes": cls._anchors_for_value(minutes_key, minutes),
                "/expected_output": cls._anchors_for_value(output_key, expected_output),
                "/completion_criteria": cls._anchors_for_value(
                    criteria_key,
                    completion_criteria,
                ),
            },
        )
        return CompiledPlanContractResult(status="compiled", contract=contract)

    @classmethod
    def _source_value(
        cls,
        sources: dict[str, Any],
        canonical: str,
    ) -> tuple[str | None, Any]:
        if canonical in sources:
            return canonical, sources[canonical]
        for key, value in sources.items():
            if cls._canonical_source_field(key) == canonical:
                return str(key), value
        return None, None

    @classmethod
    def _resolve_source_field(
        cls,
        candidate: Any,
        sources: dict[str, Any],
    ) -> str | None:
        if not isinstance(candidate, str):
            return None
        if candidate in sources:
            return candidate
        # The compiler model sometimes emits nested JSON paths such as
        # ``plan_document.short_term_plan_content.duration_days``.  Resolve the
        # root source field so the anchor still points at the actual
        # diagnosis_output key.
        root = candidate.split(".")[0]
        if root in sources:
            return root
        canonical = cls._canonical_source_field(candidate)
        for key in sources:
            if cls._canonical_source_field(key) == canonical:
                return str(key)
        return None

    @classmethod
    def _canonical_source_field(cls, value: Any) -> str:
        if not isinstance(value, str):
            return ""
        for canonical, aliases in cls._SOURCE_FIELD_ALIASES.items():
            if cls._same_label(value, canonical, *aliases):
                return canonical
        return value

    @classmethod
    def _canonical_contract_field(cls, value: Any) -> str:
        if not isinstance(value, str):
            return str(value)
        for canonical, aliases in cls._CONTRACT_FIELD_ALIASES.items():
            if cls._same_label(value, canonical, *aliases):
                return canonical
        return value

    @classmethod
    def _canonical_contract_path(cls, path: Any) -> str:
        if not isinstance(path, str):
            return str(path)
        if not path.startswith("/"):
            # The compiler model sometimes emits ``contract.duration_days``
            # instead of ``/duration_days``.  Normalize that form as well.
            if path.startswith("contract."):
                return "/" + cls._canonical_contract_field(path[len("contract."):])
            return path
        parts = path.split("/")
        if len(parts) < 2:
            return path
        parts[1] = cls._canonical_contract_field(parts[1])
        return "/".join(parts)

    @staticmethod
    def _same_label(value: str, canonical: str, *aliases: str) -> bool:
        normalized = PlanContractCompilerAgent._normalize_label(value)
        return normalized in {
            PlanContractCompilerAgent._normalize_label(item)
            for item in (canonical, *aliases)
        }

    @staticmethod
    def _normalize_label(value: Any) -> str:
        if not isinstance(value, str):
            return ""
        return "".join(
            char
            for char in value.strip().lower()
            if char not in " \t\r\n_：:，,。；;（）()[]【】"
        )

    @classmethod
    def _anchors_for_value(
        cls,
        source_field: str | None,
        value: Any,
    ) -> list[dict[str, str]]:
        if source_field is None:
            return []
        if isinstance(value, list):
            return [
                {
                    "source_field": source_field,
                    "source_quote": cls._source_text(item),
                }
                for item in value
                if item is not None
            ]
        return [
            {
                "source_field": source_field,
                "source_quote": cls._source_text(value),
            }
        ]

    @staticmethod
    def _digest(value: dict[str, Any]) -> str:
        serialized = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(serialized.encode("utf-8")).hexdigest()

    @staticmethod
    def _system_inserted_fields(plan_scope: str) -> list[str]:
        """Fields copied verbatim by the backend after model extraction."""

        field_by_scope = {
            "long_term": "long_term_plan_content",
            "short_term": "short_term_plan_content",
            "daily_task": "daily_task_content",
        }
        field = field_by_scope.get(plan_scope)
        return [field] if field else []

    @classmethod
    def _model_output_schema(
        cls,
        *,
        include_managed_text: bool = False,
    ) -> dict[str, Any]:
        """Build the compiler schema for document or legacy sources."""

        schema = deepcopy(TypeAdapter(PlanContractCompilerResult).json_schema())
        if include_managed_text:
            return schema
        managed_fields = {
            "long_term_plan_content",
            "short_term_plan_content",
            "daily_task_content",
        }

        def strip_managed_fields(value: Any) -> None:
            if isinstance(value, dict):
                properties = value.get("properties")
                if isinstance(properties, dict):
                    for field in managed_fields:
                        properties.pop(field, None)
                required = value.get("required")
                if isinstance(required, list):
                    value["required"] = [
                        item for item in required if item not in managed_fields
                    ]
                for nested in value.values():
                    strip_managed_fields(nested)
            elif isinstance(value, list):
                for nested in value:
                    strip_managed_fields(nested)

        strip_managed_fields(schema)
        return schema

    @staticmethod
    def _parse_plan_document_sections(
        plan_document: str,
    ) -> dict[str, list[str]]:
        """Split a plan document into canonical semantic sections.

        Older drafts used English compiler tags while production Diagnosis
        writes natural-language Chinese headings.  Both are accepted here so
        a complete business document does not become unpublished solely
        because the extraction model returned an invalid schema.
        """

        import re

        aliases = {
            "当前主目标": "current_goal",
            "长期目标保温": "maintenance",
            "具体任务块": "task_blocks",
            "推进节点": "progression_nodes",
            "周期节点": "progression_nodes",
            "复习任务": "review_tasks",
            "反馈指标": "feedback_metrics",
            "预期产出": "expected_output",
            "可观察产出": "expected_output",
            "完成标准": "completion_criteria",
            "验收标准": "completion_criteria",
            "当前教材": "selected_books",
            "选用教材": "selected_books",
            "具体教材": "selected_books",
            "所属长期阶段": "selected_stage_id",
            "当前长期阶段": "selected_stage_id",
            "当前阶段": "selected_stage_id",
            "周期天数": "duration_days",
            "计划周期": "duration_days",
            "短期周期": "duration_days",
            "今日任务": "daily_task_content",
            "当日任务正文": "daily_task_content",
            "学习章节": "learning_chapter",
            "今日章节": "learning_chapter",
            "教材章节": "learning_chapter",
            "重点知识点": "focus_knowledge_points",
            "聚焦知识点": "focus_knowledge_points",
            "今日知识点": "focus_knowledge_points",
            "预计用时": "estimated_minutes",
            "预计时长": "estimated_minutes",
            "预计分钟数": "estimated_minutes",
        }
        english_tag_re = re.compile(r"^([a-zA-Z_][a-zA-Z0-9_]*)\s*[：:]")
        heading_re = re.compile(r"^#{1,6}\s*(.+?)\s*$")
        bracket_heading_re = re.compile(r"^【([^】]+)】\s*(.*)$")
        label_re = re.compile(r"^([^：:]{1,24})\s*[：:]\s*(.*)$")
        sections: dict[str, list[str]] = {}
        current: str | None = None
        current_lines: list[str] = []

        def canonical_label(value: str) -> str | None:
            compact = re.sub(r"[\s·（(].*$", "", value.strip())
            if compact in aliases:
                return aliases[compact]
            for label, canonical in aliases.items():
                if label in value:
                    return canonical
            return None

        def start_section(name: str, rest: str = "") -> None:
            nonlocal current, current_lines
            if current is not None:
                sections.setdefault(current, []).extend(current_lines)
            current = name
            current_lines = [rest.strip()] if rest.strip() else []

        for raw_line in plan_document.split("\n"):
            line = raw_line.strip()
            match = english_tag_re.match(line)
            if match:
                start_section(match.group(1), line[match.end():])
                continue
            heading = heading_re.match(line)
            if heading:
                canonical = canonical_label(heading.group(1))
                if canonical:
                    start_section(canonical)
                    continue
            bracket = bracket_heading_re.match(line)
            if bracket:
                canonical = canonical_label(bracket.group(1))
                if canonical:
                    start_section(canonical, bracket.group(2))
                    continue
            labeled = label_re.match(line)
            if labeled:
                canonical = canonical_label(labeled.group(1))
                if canonical:
                    start_section(canonical, labeled.group(2))
                    continue
            if current is not None and line:
                current_lines.append(line)
        if current is not None:
            sections.setdefault(current, []).extend(current_lines)
        return sections

    @classmethod
    def _compile_from_plan_document(
        cls,
        plan_document: str,
        plan_scope: str,
        parent_plan_constraints: dict[str, Any],
    ) -> CompiledPlanContractResult | None:
        """Deterministically compile a labeled plan_document.

        Fallback used when the compiler model returns ``needs_revision`` or
        anchors that cannot be verified.  Only values written verbatim in the
        document are used; anything missing keeps the revision path alive.
        """

        sections = cls._parse_plan_document_sections(plan_document)
        if plan_scope == "short_term":
            return cls._compile_short_term_from_plan_document(
                plan_document,
                sections,
                parent_plan_constraints,
            )
        if plan_scope == "long_term":
            # A long-term stage has six independent semantic fields.  The old
            # fallback derived several of them from one free-form line and
            # even supplied ``duration_days=1``.  That turns a compiler into a
            # plan author and can publish a contract that the Diagnosis never
            # wrote.  Long-term documents therefore require the model
            # compiler (and its field-level source anchors); an incomplete
            # model result must go through Diagnosis revision instead.
            return None
        if plan_scope == "daily_task":
            return cls._compile_daily_task_from_plan_document(
                plan_document,
                sections,
            )
        return None

    @classmethod
    def _strip_list_prefix(cls, lines: list[str]) -> list[str]:
        values: list[str] = []
        for line in lines:
            text = line.strip()
            for prefix in ("- ", "• ", "· ", "1. ", "2. ", "3. "):
                if text.startswith(prefix):
                    text = text[len(prefix):].strip()
                    break
            if text:
                values.append(text)
        return values

    @classmethod
    def _extract_books(
        cls,
        lines: list[str],
        *,
        allow_bare: bool = False,
    ) -> list[str]:
        """Extract clean book names from list items or inline ``《A》、《B》``.

        The Diagnosis document writes books either as ``- 《中医学基础》`` list
        items or as an inline ``selected_books：《中医学基础》、《方剂学》``
        line.  Both forms must yield separate book entries.

        Bare names (without book-mark quotes) are only accepted when
        ``allow_bare`` is set (the dedicated book-selection section).  Free-form
        prose lines such as task blocks must never become book names.
        """

        import re

        books: list[str] = []
        for line in lines:
            text = line.strip()
            for prefix in ("- ", "• ", "· "):
                if text.startswith(prefix):
                    text = text[len(prefix):].strip()
                    break
            # Collect every 《...》 occurrence on the line.
            found = re.findall(r"《([^》]+)》", text)
            if found:
                for name in found:
                    book = f"《{name}》".strip()
                    if book and book not in books:
                        books.append(book)
                continue
            if not allow_bare:
                continue
            # Bare names separated by commas / slashes (no book-mark quotes).
            if text and any(sep in text for sep in ("、", "，", ",", "/", "；", ";")):
                for part in re.split(r"[、，,;；/]", text):
                    part = part.strip()
                    if part and part not in books:
                        books.append(part)
                continue
            if text and text not in books:
                books.append(text)
        return books

    @classmethod
    def _section_value(cls, sections: dict[str, list[str]], label: str) -> str:
        lines = sections.get(label) or []
        return "\n".join(lines).strip()

    @classmethod
    def _section_int(cls, sections: dict[str, list[str]], label: str) -> int | None:
        value = cls._section_value(sections, label)
        import re

        match = re.search(r"\d+", value)
        if not match:
            return None
        try:
            return int(match.group(0))
        except ValueError:
            return None

    @classmethod
    def _compile_short_term_from_plan_document(
        cls,
        plan_document: str,
        sections: dict[str, list[str]],
        parent_plan_constraints: dict[str, Any],
    ) -> CompiledPlanContractResult | None:
        duration = cls._section_int(sections, "duration_days")
        if duration is None:
            duration = cls._extract_short_term_duration(plan_document)
        if duration is None or duration <= 0:
            return None
        limit = parent_plan_constraints.get("current_stage_duration_days")
        if limit is not None and duration > limit:
            return None
        nodes = cls._strip_list_prefix(sections.get("progression_nodes") or [])
        if len(nodes) < 2:
            nodes = cls._extract_progression_nodes(
                sections.get("task_blocks") or []
            )
        if len(nodes) < 2:
            return None
        expected_output = cls._section_value(sections, "expected_output")
        completion_criteria = cls._section_value(sections, "completion_criteria")
        if not expected_output or not completion_criteria:
            return None
        books = cls._extract_books(
            sections.get("selected_books") or [],
            allow_bare=True,
        )
        if not books:
            books = cls._extract_books(
                (sections.get("current_goal") or [])
                + (sections.get("task_blocks") or [])
            )
        if not books or len(books) > 2:
            return None
        stage_id = cls._section_value(sections, "selected_stage_id") or None
        if stage_id and stage_id.lower() in {"null", "none", "无"}:
            stage_id = None
        content = plan_document.strip()
        if not content:
            return None
        field_anchors = {
            "/short_term_plan_content": [
                {"source_field": "plan_document", "source_quote": content}
            ],
            "/duration_days": [
                {
                    "source_field": "plan_document",
                    "source_quote": cls._duration_source_quote(plan_document)
                    or str(duration),
                }
            ],
            "/progression_nodes": [
                {"source_field": "plan_document", "source_quote": node}
                for node in nodes
            ],
            "/expected_output": [
                {
                    "source_field": "plan_document",
                    "source_quote": expected_output,
                }
            ],
            "/completion_criteria": [
                {
                    "source_field": "plan_document",
                    "source_quote": completion_criteria,
                }
            ],
            "/selected_books": [
                {"source_field": "plan_document", "source_quote": book}
                for book in books
            ],
        }
        if stage_id:
            field_anchors["/selected_stage_id"] = [
                {"source_field": "plan_document", "source_quote": stage_id}
            ]
        contract = CompiledShortTermContract(
            scope="short_term",
            short_term_plan_content=content,
            duration_days=duration,
            progression_nodes=nodes,
            expected_output=expected_output,
            completion_criteria=completion_criteria,
            selected_stage_id=stage_id,
            selected_books=books,
            field_anchors=field_anchors,
        )
        return CompiledPlanContractResult(status="compiled", contract=contract)

    @staticmethod
    @staticmethod
    def _duration_source_quote(plan_document: str) -> str | None:
        """Return the verbatim duration phrase actually written in the document.

        A derived duration (for example ``两周`` → 14) cannot be anchored by
        the numeric value because the number is not verbatim in the prose.
        Anchor the original phrase instead so strict source validation still
        passes only when the duration genuinely appears in the document.
        """

        import re

        day_match = re.search(r"\d{1,3}\s*天", plan_document)
        if day_match:
            return day_match.group(0)
        week_match = re.search(
            r"(?:[一两二三四五六七八九十]{1,3})\s*(?:个)?\s*(?:完整)?周|"
            r"(?:\d{1,2})\s*(?:个)?\s*(?:完整)?周",
            plan_document,
        )
        if week_match:
            return week_match.group(0)
        return None

    @staticmethod
    def _extract_short_term_duration(plan_document: str) -> int | None:
        patterns = (
            r"(?:本|当前|整个)?(?:短期)?(?:计划|周期)[^。；;\n]{0,16}?(\d{1,3})\s*天",
            r"未来\s*(\d{1,3})\s*天",
            r"(?:共|为期)\s*(\d{1,3})\s*天",
            r"(\d{1,3})\s*天(?:内|周期|计划)",
        )
        for pattern in patterns:
            match = re.search(pattern, plan_document)
            if match:
                return int(match.group(1))
        week_match = re.search(
            r"(?:本|未来|为期|共)?\s*(\d{1,2})\s*(?:个)?(?:完整)?周",
            plan_document,
        )
        if week_match:
            return int(week_match.group(1)) * 7
        week_cn_match = re.search(
            r"([一两二三四五六七八九十]{1,3})\s*(?:个)?\s*(?:完整)?周",
            plan_document,
        )
        if week_cn_match:
            token = week_cn_match.group(1)
            cn_digits = {
                "一": 1, "两": 2, "二": 2, "三": 3, "四": 4,
                "五": 5, "六": 6, "七": 7, "八": 8, "九": 9,
            }
            if token in cn_digits:
                return cn_digits[token] * 7
            if token == "十":
                return 10 * 7
            if token.startswith("十"):
                tail = cn_digits.get(token[1:], 0)
                return (10 + tail) * 7
            if token.endswith("十"):
                head = cn_digits.get(token[0], 0)
                return head * 10 * 7
        if "一周" in plan_document or "本周" in plan_document:
            return 7
        return None

    @classmethod
    def _extract_progression_nodes(cls, lines: list[str]) -> list[str]:
        nodes: list[str] = []
        for value in cls._strip_list_prefix(lines):
            for part in re.split(r"[；;]", value):
                text = part.strip()
                if not text:
                    continue
                if re.match(
                    r"^(?:第[一二三四五六七八九十\d]+(?:个)?(?:节点|阶段|步)|"
                    r"节点[一二三四五六七八九十\d]+|先|随后|然后|最后)",
                    text,
                ):
                    nodes.append(text)
        return nodes[:12]

    @classmethod
    def _compile_long_term_from_plan_document(
        cls,
        plan_document: str,
        sections: dict[str, list[str]],
    ) -> CompiledPlanContractResult | None:
        content = plan_document.strip()
        if not content:
            return None
        stages_lines = sections.get("long_term_plan_stages") or sections.get("stages") or []
        compiled_stages: list[CompiledLongTermStage] = []
        for line in cls._strip_list_prefix(stages_lines):
            import re

            match = re.search(r"《([^》]+)》", line)
            book = match.group(1) if match else None
            stage_no = re.search(r"(?:阶段|stage)[\s#]*(\d+)", line, re.IGNORECASE)
            if not book or not stage_no:
                continue
            compiled_stages.append(
                CompiledLongTermStage(
                    stage=int(stage_no.group(1)),
                    stage_name=line[:40],
                    books=[f"《{book}》"],
                    goal=line[:120],
                    duration_days=1,
                    schedule_summary=line[:200],
                )
            )
        if not compiled_stages:
            return None
        total = cls._section_int(sections, "total_duration_days")
        if total is None or total <= 0:
            return None
        contract = CompiledLongTermContract(
            scope="long_term",
            long_term_plan_content=content,
            total_duration_days=total,
            stages=compiled_stages,
            field_anchors={
                "/long_term_plan_content": [
                    {"source_field": "plan_document", "source_quote": content}
                ],
                "/total_duration_days": [
                    {"source_field": "plan_document", "source_quote": str(total)}
                ],
                "/stages": [
                    {"source_field": "plan_document", "source_quote": line}
                    for line in cls._strip_list_prefix(stages_lines)
                ],
            },
        )
        return CompiledPlanContractResult(status="compiled", contract=contract)

    @classmethod
    def _compile_daily_task_from_plan_document(
        cls,
        plan_document: str,
        sections: dict[str, list[str]],
    ) -> CompiledPlanContractResult | None:
        content = plan_document.strip()
        chapter = cls._section_value(sections, "learning_chapter")
        points = [
            part.strip()
            for value in cls._strip_list_prefix(
                sections.get("focus_knowledge_points") or []
            )
            for part in re.split(r"[；;、，,]", value)
            if part.strip()
        ]
        minutes = cls._section_int(sections, "estimated_minutes")
        expected_output = cls._section_value(sections, "expected_output")
        completion_criteria = cls._section_value(sections, "completion_criteria")
        if (
            not content
            or not chapter
            or not points
            or minutes is None
            or minutes <= 0
            or not expected_output
            or not completion_criteria
        ):
            return None
        contract = CompiledDailyTaskContract(
            scope="daily_task",
            daily_task_content=content,
            learning_chapter=chapter,
            focus_knowledge_points=points,
            estimated_minutes=minutes,
            expected_output=expected_output,
            completion_criteria=completion_criteria,
            field_anchors={
                "/daily_task_content": [
                    {"source_field": "plan_document", "source_quote": content}
                ],
                "/learning_chapter": [
                    {"source_field": "plan_document", "source_quote": chapter}
                ],
                "/focus_knowledge_points": [
                    {"source_field": "plan_document", "source_quote": point}
                    for point in points
                ],
                "/estimated_minutes": [
                    {"source_field": "plan_document", "source_quote": str(minutes)}
                ],
                "/expected_output": [
                    {
                        "source_field": "plan_document",
                        "source_quote": expected_output,
                    }
                ],
                "/completion_criteria": [
                    {
                        "source_field": "plan_document",
                        "source_quote": completion_criteria,
                    }
                ],
            },
        )
        return CompiledPlanContractResult(status="compiled", contract=contract)

    @classmethod
    def _backfill_anchors(
        cls,
        raw: Any,
        diagnosis_output: dict[str, Any],
        plan_scope: str,
    ) -> Any:
        """Restore field_anchors entries the compiler model omitted or
        mis-quoted.

        The compiler sometimes returns extracted contract values without the
        matching ``field_anchors`` entries, or quotes them in a rewritten
        (non-verbatim) form.  When every value of a required field can be
        located verbatim inside ``diagnosis_output``, re-anchor it there so the
        strict source check below still passes only for values that genuinely
        came from the diagnosis output.  Values that cannot be found are left
        untouched and keep failing with ``source_anchor_missing`` /
        ``source_anchor_invalid``.
        """

        if not isinstance(raw, dict) or raw.get("status") != "compiled":
            return raw
        contract = raw.get("contract")
        if not isinstance(contract, dict):
            return raw
        anchors = contract.get("field_anchors")
        if not isinstance(anchors, dict):
            return raw
        searchable_sources = {
            str(key): cls._source_text(value)
            for key, value in diagnosis_output.items()
        }
        required = cls._required_anchor_paths(plan_scope)
        rebuilt = deepcopy(raw)
        rebuilt_contract = rebuilt["contract"]
        rebuilt_anchors = rebuilt_contract["field_anchors"]

        def _anchors_verbatim(path: str) -> bool:
            entries = rebuilt_anchors.get(path)
            if not isinstance(entries, list) or not entries:
                return False
            for entry in entries:
                if not isinstance(entry, dict):
                    return False
                source = searchable_sources.get(str(entry.get("source_field")))
                quote = entry.get("source_quote")
                if (
                    source is None
                    or not isinstance(quote, str)
                    or not cls._quote_grounded(quote, source, path)
                ):
                    return False
            return True

        def _resolve_path(path: str) -> Any:
            """Resolve a JSON-pointer style field path against the contract.

            Supports nested stage paths such as ``/stages/0/schedule_summary``
            so the backfill below can re-anchor stage-level fields whose
            extracted value appears verbatim in the diagnosis output.
            """
            if not path.startswith("/"):
                return None
            node: Any = rebuilt_contract
            for part in path[1:].split("/"):
                if isinstance(node, dict) and part in node:
                    node = node[part]
                elif isinstance(node, list) and part.isdigit():
                    index = int(part)
                    node = node[index] if index < len(node) else None
                else:
                    return None
            return node

        def _backfill(path: str) -> None:
            if _anchors_verbatim(path):
                return
            value = _resolve_path(path)
            if value is None:
                return
            candidates = value if isinstance(value, list) else [value]
            if not candidates:
                return
            recovered: list[dict[str, str]] = []
            for item in candidates:
                if item is None:
                    continue
                quote = cls._source_text(item)
                if not quote:
                    recovered = []
                    break
                source_field = next(
                    (
                        key
                        for key, source in searchable_sources.items()
                        if cls._quote_grounded(quote, source, path)
                    ),
                    None,
                )
                if source_field is None:
                    recovered = []
                    break
                recovered.append(
                    {"source_field": source_field, "source_quote": quote}
                )
            if recovered:
                rebuilt_anchors[path] = recovered

        # Top-level required paths first, then every nested field path the
        # compiler anchored (e.g. ``/stages/0/schedule_summary``).  A stage
        # field whose value is a verbatim excerpt of the plan document is
        # re-anchored instead of failing strict source validation.
        for field_path in required:
            _backfill(field_path)
        for field_path in list(anchors):
            if field_path not in required:
                _backfill(field_path)
        return rebuilt

    @classmethod
    def _inject_system_fields(
        cls,
        raw: Any,
        diagnosis_output: dict[str, Any],
        plan_scope: str,
    ) -> Any:
        """Inject exact正文 and its anchor before final contract validation."""

        if not isinstance(raw, dict) or raw.get("status") != "compiled":
            return raw
        field_names = cls._system_inserted_fields(plan_scope)
        if not field_names:
            return raw
        normalized = deepcopy(raw)
        contract = normalized.get("contract")
        if not isinstance(contract, dict):
            return normalized
        field = field_names[0]
        value = diagnosis_output.get(field)
        source_field = field
        # New Diagnosis drafts carry a natural-language plan_document instead
        # of the legacy structured field.  The model schema strips managed
        # fields, so the backend must inject the document as the managed正文,
        # and anchor it to the plan_document source so source validation can
        # verify it verbatim.
        if (
            (not isinstance(value, str) or not value)
            and isinstance(diagnosis_output.get("plan_document"), str)
            and diagnosis_output["plan_document"].strip()
        ):
            value = diagnosis_output["plan_document"]
            source_field = "plan_document"
        if not isinstance(value, str) or not value:
            return normalized
        contract[field] = value
        anchors = contract.get("field_anchors")
        if not isinstance(anchors, dict):
            anchors = {}
            contract["field_anchors"] = anchors
        anchors[f"/{field}"] = [
            {
                "source_field": source_field,
                "source_quote": value,
            }
        ]
        return normalized

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
                if source is None or not cls._quote_grounded(
                    anchor.source_quote, source, field_path
                ):
                    issues.append(
                        {
                            "code": "source_anchor_invalid",
                            "category": "invalid",
                            "field_path": field_path,
                            "source_refs": [anchor.source_field],
                        }
                    )
        required_paths = cls._required_anchor_paths(contract.scope)
        # The compiler model anchors each stage through per-field sub-paths
        # (``/stages/0/stage_name``, ``/stages/0/books``, ...) instead of a
        # single top-level ``/stages`` anchor.  When every stage's required
        # sub-field is anchored verbatim, the top-level collection is
        # considered anchored as well; otherwise the strict per-field check
        # above already reported the concrete gap.
        effective_anchors = set(contract.field_anchors)
        if "/stages" not in effective_anchors and contract.scope == "long_term":
            stages_value = getattr(contract, "stages", None)
            stage_count = len(stages_value) if isinstance(stages_value, list) else 0
            required_sub_fields = {
                "stage",
                "stage_name",
                "books",
                "goal",
                "duration_days",
                "schedule_summary",
            }
            covered_by_stage: dict[int, set[str]] = {}
            for path in contract.field_anchors:
                parts = path.split("/")
                if (
                    len(parts) >= 4
                    and parts[1] == "stages"
                    and parts[2].isdigit()
                ):
                    covered_by_stage.setdefault(int(parts[2]), set()).add(parts[3])
            all_stages_covered = (
                stage_count > 0
                # Contract list positions are zero-based JSON-pointer indexes.
                # Merely comparing counts lets indexes such as {1, 2} satisfy
                # a two-stage contract while stage 0 has no evidence.
                and set(covered_by_stage) == set(range(stage_count))
                and all(
                    required_sub_fields <= covered
                    for covered in covered_by_stage.values()
                )
            )
            if all_stages_covered:
                effective_anchors.add("/stages")
        for field_path in required_paths:
            if field_path not in effective_anchors:
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

    # Field paths whose values are summarised prose rather than verbatim
    # extracts.  The compiler legitimately compresses, rephrases or elides
    # interstitial sentences when extracting these; source validation
    # degrades to per-sentence grounding so legitimate summaries pass while
    # fabricated sentences still fail.  Fact/constraint fields (books,
    # duration_days, stage_name, ...) keep strict verbatim anchoring.
    _SUMMARY_ANCHOR_HINTS = (
        "schedule_summary",
        "expected_output",
        "completion_criteria",
        "goal",
    )
    _SENTENCE_SPLIT = re.compile(r"[。；;！？!?\n]+")

    @classmethod
    def _quote_grounded(cls, quote: str, source: str, field_path: str) -> bool:
        """True when a quote is grounded in the source text.

        Verbatim membership always counts.  For summarised prose fields the
        quote may skip non-essential sentences, so every sentence must appear
        verbatim as a contiguous substring of the source; any fabricated or
        rewritten sentence still fails.
        """
        if quote in source:
            return True
        if not any(hint in field_path for hint in cls._SUMMARY_ANCHOR_HINTS):
            return False
        sentences = [
            sentence
            for sentence in cls._SENTENCE_SPLIT.split(quote)
            if sentence.strip()
        ]
        if not sentences:
            return False
        return all(sentence in source for sentence in sentences)

    @staticmethod
    def _required_anchor_paths(scope: str) -> set[str]:
        if scope == "long_term":
            return {"/long_term_plan_content", "/total_duration_days", "/stages"}
        if scope == "short_term":
            return {
                "/short_term_plan_content",
                "/duration_days",
                "/progression_nodes",
                "/expected_output",
                "/completion_criteria",
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
