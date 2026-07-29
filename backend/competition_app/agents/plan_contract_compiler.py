from __future__ import annotations

import hashlib
import json
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

        # Diagnosis already emits the execution fields required by the plan
        # service.  Prefer compiling those trusted fields deterministically:
        # this is faster, preserves their exact values, and prevents a second
        # model from inventing conflicts while merely copying a contract.
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

        # The model compiler remains a compatibility fallback for older
        # Diagnosis outputs that only contain prose or use legacy field names.
        skill = prompt_skill_registry.load(
            "plan_contract_compiler", "compile_plan_contract"
        )
        payload = {
            "plan_scope": plan_scope,
            "diagnosis_output": diagnosis_output,
            "trusted_route": trusted_route,
            "parent_plan_constraints": parent_plan_constraints,
            "system_inserted_fields": self._system_inserted_fields(plan_scope),
            "output_schema": self._model_output_schema(),
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
        normalized_raw = self._normalize_model_output(raw, diagnosis_output)
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
        if (
            isinstance(result, PlanContractNeedsRevision)
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
            compiled_stages.append(
                CompiledLongTermStage(
                    stage=int(stage.get("stage") or position),
                    stage_name=str(stage.get("stage_name") or ""),
                    books=books or [],
                    goal=str(stage.get("goal") or ""),
                    duration_days=int(stage.get("duration_days") or 0),
                    schedule_summary=str(stage.get("schedule_summary") or ""),
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
        if not isinstance(path, str) or not path.startswith("/"):
            return str(path)
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
    def _model_output_schema(cls) -> dict[str, Any]:
        """Allow the model to omit full正文 fields owned by deterministic code."""

        schema = deepcopy(TypeAdapter(PlanContractCompilerResult).json_schema())
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
        if not isinstance(value, str) or not value:
            return normalized
        contract[field] = value
        anchors = contract.get("field_anchors")
        if not isinstance(anchors, dict):
            anchors = {}
            contract["field_anchors"] = anchors
        anchors[f"/{field}"] = [
            {
                "source_field": field,
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
