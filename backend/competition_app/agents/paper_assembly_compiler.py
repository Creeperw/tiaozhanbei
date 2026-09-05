from __future__ import annotations

import hashlib
import logging
import re
from copy import deepcopy
from typing import Any

from pydantic import TypeAdapter, ValidationError

from competition_app.contracts.agent_context import build_model_context
from competition_app.contracts.paper_assembly_compilation import (
    CompiledPaperAssemblyResult,
    PaperAssemblyCandidateCatalogSnapshot,
    PaperAssemblyCompilationEnvelope,
    PaperAssemblyCompilerResult,
    PaperAssemblyNeedsRevision,
    PaperAssemblySelectionCompilation,
)
from competition_app.llm.base import ChatModel
from competition_app.llm.prompt_skills import prompt_skill_registry
from competition_app.llm.stub import StubChatModel


_RESULT_ADAPTER = TypeAdapter(PaperAssemblyCompilerResult)
_LOGGER = logging.getLogger("competition_app.paper_assembly_compiler")


class PaperAssemblySystemBindingCompiler:
    """Resolve model-selected candidate numbers without another model call.

    This is the production compiler for the v2 paper-selection path.  The
    caller supplies an immutable catalog snapshot and the model can only
    choose a run-local integer.  Question identity, unit binding, canonical
    content, and provenance therefore remain system-owned.
    """

    @staticmethod
    def compile(
        *,
        snapshot: PaperAssemblyCandidateCatalogSnapshot,
        selected_candidates: list[Any],
        expected_execution_id: str,
    ) -> PaperAssemblySelectionCompilation:
        issues: list[dict[str, Any]] = []
        if snapshot.execution_id != expected_execution_id:
            issues.append(
                {
                    "code": "catalog_execution_mismatch",
                    "detail": "candidate catalog does not belong to this execution",
                }
            )
            return PaperAssemblySelectionCompilation(
                catalog_id=snapshot.catalog_id,
                catalog_digest=snapshot.catalog_digest,
                selected_items=[],
                issues=issues,
            )

        by_number = {item.candidate_no: item for item in snapshot.candidates}
        seen: set[int] = set()
        resolved: list[dict[str, Any]] = []
        for choice in selected_candidates:
            candidate_no = int(getattr(choice, "candidate_no", 0) or 0)
            rationale = str(getattr(choice, "rationale", "") or "").strip()
            if candidate_no in seen:
                issues.append(
                    {
                        "code": "candidate_no_duplicate",
                        "candidate_no": candidate_no,
                        "detail": "duplicate candidate number was ignored",
                    }
                )
                continue
            seen.add(candidate_no)
            binding = by_number.get(candidate_no)
            if binding is None:
                issues.append(
                    {
                        "code": "candidate_no_unknown",
                        "candidate_no": candidate_no,
                        "detail": "candidate number is not present in the frozen catalog",
                    }
                )
                continue
            if not binding.selectable:
                issues.append(
                    {
                        "code": "candidate_not_selectable",
                        "candidate_no": candidate_no,
                        "detail": "candidate is not selectable in this execution",
                    }
                )
                continue
            resolved.append(
                {
                    "candidate_no": candidate_no,
                    "binding_id": binding.binding_id,
                    "unit_id": binding.unit_id,
                    "question_id": binding.question_id,
                    "selection_rationale": rationale or "模型从系统候选目录中选择。",
                }
            )
        return PaperAssemblySelectionCompilation(
            catalog_id=snapshot.catalog_id,
            catalog_digest=snapshot.catalog_digest,
            selected_items=resolved,
            issues=issues,
        )


class PaperAssemblyCompilerAgent:
    """Legacy adapter for historical prose assembly snapshots.

    New paper-generation requests use :class:`PaperAssemblySystemBindingCompiler`.
    This LLM-backed path remains only for old checkpoints and focused backward-
    compatibility tests; it must never be the authority for canonical question
    identity or provenance.
    """

    def __init__(self, chat_model: ChatModel | None = None) -> None:
        self.chat_model = chat_model or StubChatModel()

    async def compile(
        self,
        context: dict[str, Any],
        *,
        assembly_document: str,
        candidate_catalog: list[dict[str, Any]],
        generated_unit_binding: str | None = None,
    ) -> PaperAssemblyCompilationEnvelope:
        skill = prompt_skill_registry.load(
            "paper_assembly_compiler", "compile_paper_assembly"
        )
        anchor_catalog = self._build_anchor_catalog(assembly_document)
        raw = await self.chat_model.complete_json(
            "paper_assembly_compiler",
            build_model_context(
                context,
                target_agent="paper_assembly_compiler",
                prompt_skill=skill,
                payload={
                    "assembly_document": assembly_document,
                    "candidate_catalog": candidate_catalog,
                    "anchor_catalog": anchor_catalog,
                    "generated_unit_binding": generated_unit_binding,
                    "output_schema": _RESULT_ADAPTER.json_schema(),
                },
                permission_note=(
                    "内部编译器只可逐字提取组装原稿中的候选选择和原创题；不得创作、"
                    "改写题目或生成系统ID、顺序、答案键及状态。"
                    + (
                        f"本次缺口补题由系统确定性绑定到单元{generated_unit_binding}；"
                        "该值只能原样写入原创题unit_id，不得从正文推断其他单元。"
                        if generated_unit_binding
                        else ""
                    )
                ),
            ),
        )
        result = self._parse(self._materialize_anchor_ids(raw, anchor_catalog))
        if generated_unit_binding and isinstance(
            result, CompiledPaperAssemblyResult
        ):
            contract = result.contract.model_copy(
                update={
                    "generated_items": [
                        item.model_copy(update={"unit_id": generated_unit_binding})
                        for item in result.contract.generated_items
                    ]
                }
            )
            result = result.model_copy(update={"contract": contract})
        issues = self._integrity_issues(
            result,
            assembly_document=assembly_document,
            candidate_catalog=candidate_catalog,
            generated_unit_binding=generated_unit_binding,
        )
        if issues:
            _LOGGER.warning(
                "paper assembly compiler rejected source digest=%s issue_codes=%s",
                hashlib.sha256(assembly_document.encode("utf-8")).hexdigest()[:16],
                sorted({str(item.get("code") or "unknown") for item in issues}),
            )
            result = PaperAssemblyNeedsRevision(status="needs_revision", issues=issues)
        return PaperAssemblyCompilationEnvelope(
            result=result,
            source_digest=hashlib.sha256(
                assembly_document.encode("utf-8")
            ).hexdigest(),
        )

    @staticmethod
    def _build_anchor_catalog(document: str) -> list[dict[str, str]]:
        """Create stable, source-owned anchors from the assembly document.

        The model may select an ID, but it never gets to author the quote that
        becomes provenance. Explicit markers are preferred; ordinary numbered
        ``所属单元`` blocks remain supported for older expert prompts.
        """
        anchors: list[dict[str, str]] = []

        # The title is an execution-critical field, but ordinary Expert prose
        # does not contain explicit HTML anchor markers.  Build a stable,
        # source-owned title anchor from the first non-empty line so the
        # compiler never has to invent or paraphrase a title quote.  The
        # integrity checker still verifies the extracted title against this
        # exact source line.
        title_line = PaperAssemblyCompilerAgent._find_title_line(document)
        if title_line:
            anchors.append(
                {
                    "anchor_id": "DOC_TITLE",
                    "source_field": "assembly_document",
                    "content": title_line,
                }
            )
        marked = re.compile(
            r"<!--\s*ANCHOR_ID\s*:\s*(?P<id>[A-Za-z0-9_.:-]+)\s*-->"
            r"(?P<body>.*?)"
            r"<!--\s*END_ANCHOR\s*-->",
            re.DOTALL | re.IGNORECASE,
        )
        for match in marked.finditer(document):
            anchors.append(
                {
                    "anchor_id": match.group("id"),
                    "source_field": "assembly_document",
                    "content": match.group("body").strip(),
                }
            )

        # Business agents write learner-readable Markdown. ``所属单元`` is
        # commonly wrapped as ``**所属单元**`` and a document may begin with
        # a horizontal rule. The old regex accepted only the bare label, so a
        # complete generated question could still have no source-owned anchor.
        unit_starts = list(
            re.finditer(
                r"(?im)^[ \t]*(?:[-+*]\s+)?"
                r"(?:(?:\*\*|__)\s*)?所属单元\s*(?:(?:\*\*|__)\s*)?"
                r"[：:].*$",
                document,
            )
        )
        for index, match in enumerate(unit_starts, start=1):
            next_unit = (
                unit_starts[index].start()
                if index < len(unit_starts)
                else len(document)
            )
            boundary = re.search(
                r"(?m)^\s*(?:#{1,6}\s+|[-*_]{3,}\s*$)",
                document[match.end() : next_unit],
            )
            end = (
                match.end() + boundary.start()
                if boundary is not None
                else next_unit
            )
            body = document[match.start() : end].strip()
            if not body:
                continue
            anchors.append(
                {
                    "anchor_id": f"DOC_BLOCK_{index:03d}",
                    "source_field": "assembly_document",
                    "content": body,
                }
            )

        # Candidate-selection lines are intentionally separate anchors. They
        # need not be full question blocks because the catalog validates the
        # question_id independently.
        for index, match in enumerate(
            re.finditer(
                r"(?P<line>[^\n]*(?:选用候选题|候选题)[^\n]*)",
                document,
            ),
            start=1,
        ):
            line = match.group("line").strip()
            if line:
                anchors.append(
                    {
                        "anchor_id": f"SELECTION_{index:03d}",
                        "source_field": "assembly_document",
                        "content": line,
                    }
                )
        return anchors

    @staticmethod
    def _find_title_line(document: str) -> str:
        """Return a real source title line, never a Markdown separator."""

        meaningful: list[str] = []
        for raw_line in document.splitlines():
            line = raw_line.strip()
            if not line or re.fullmatch(r"[-*_=>`~\s]{3,}", line):
                continue
            meaningful.append(line)
            if "试卷标题" in line:
                return line
        return meaningful[0] if meaningful else ""

    @staticmethod
    def _materialize_anchor_ids(raw: Any, anchor_catalog: list[dict[str, str]]) -> Any:
        if not isinstance(raw, dict):
            return raw
        value = deepcopy(raw)
        by_id = {item["anchor_id"]: item for item in anchor_catalog}

        def materialize(anchor_list: Any) -> list[dict[str, Any]]:
            if not isinstance(anchor_list, list):
                return []
            output = []
            for anchor in anchor_list:
                if not isinstance(anchor, dict):
                    continue
                anchor_id = str(anchor.get("anchor_id") or "").strip()
                if anchor_id:
                    source = by_id.get(anchor_id)
                    if source:
                        output.append(
                            {
                                "source_field": "assembly_document",
                                "source_quote": source["content"],
                            }
                        )
                    else:
                        output.append(
                            {
                                "source_field": "assembly_document",
                                "source_quote": f"__UNKNOWN_ANCHOR__:{anchor_id}",
                            }
                        )
                elif anchor.get("source_quote"):
                    output.append(anchor)
            return output

        contract = value.get("contract")
        if not isinstance(contract, dict):
            return value
        for key in ("selected_items", "generated_items"):
            for item in contract.get(key, []) or []:
                if isinstance(item, dict) and item.get("source_anchor_ids"):
                    item["source_anchors"] = [
                        {"anchor_id": anchor_id}
                        for anchor_id in item["source_anchor_ids"]
                    ]
                if isinstance(item, dict):
                    item["source_anchors"] = materialize(item.get("source_anchors"))
        field_anchors = contract.get("field_anchors", {})
        if isinstance(field_anchors, dict):
            contract["field_anchors"] = {
                key: materialize(anchors) for key, anchors in field_anchors.items()
            }
        return value

    @staticmethod
    def _parse(raw: Any) -> PaperAssemblyCompilerResult:
        try:
            return _RESULT_ADAPTER.validate_python(raw)
        except ValidationError:
            pass
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
        generated_unit_binding: str | None = None,
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
            if not generated.source_anchors:
                issues.append(
                    {
                        "code": "source_anchor_missing",
                        "field_path": f"/generated_items/{index}/source_anchors",
                    }
                )
                continue
            anchored = " ".join(
                anchor.source_quote for anchor in generated.source_anchors
            )
            values = [
                # A per-unit gap-generation call is already scoped by the
                # orchestrator.  Its unit ID is system-owned context rather
                # than a fact the prose model must repeat.  Multi-unit paper
                # assembly still requires the ID to be source-anchored.
                None if generated_unit_binding else generated.unit_id,
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
            if (
                generated_unit_binding
                and generated.unit_id != generated_unit_binding
            ):
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
