from __future__ import annotations

from typing import Any
from uuid import uuid4

from pydantic import ValidationError

from competition_app.agents.common import envelope
from competition_app.agents.paper_assembly_compiler import PaperAssemblyCompilerAgent
from competition_app.contracts.agent_context import build_model_context
from competition_app.contracts.base import AgentEnvelope
from competition_app.contracts.knowledge import (
    QuestionBridge,
    QuestionDetail,
    QuestionRetrievalMetadata,
)
from competition_app.contracts.paper import (
    ExamPaperDraft,
    ExamPaperItem,
    PaperDifficultySourceSummary,
    QuestionCandidatePool,
)
from competition_app.llm.base import ChatModel
from competition_app.llm.prompt_skills import prompt_skill_registry
from competition_app.llm.schemas import (
    ExamAssemblyModelOutput,
    GeneratedPaperItemModelOutput,
)
from competition_app.llm.stub import StubChatModel


class PaperAssemblyAgent:
    """Expert stage two: select only retrieved candidates and assemble a whole paper."""

    def __init__(
        self,
        chat_model: ChatModel | None = None,
        assembly_compiler: PaperAssemblyCompilerAgent | None = None,
    ) -> None:
        self.chat_model = chat_model or StubChatModel()
        self.assembly_compiler = (
            assembly_compiler or PaperAssemblyCompilerAgent(self.chat_model)
        )

    async def run(self, context: dict[str, Any]) -> AgentEnvelope[ExamPaperDraft]:
        dependencies = context["dependency_outputs"]
        blueprint = dependencies["paper_blueprint"].payload
        candidate_pool: QuestionCandidatePool = dependencies["question_pool"].payload
        skill = prompt_skill_registry.load("expert_agent", "paper_assembly")
        candidate_catalog = [
            {
                "unit_id": unit.unit_id,
                "required_question_count": unit.required_question_count,
                "warnings": unit.warnings,
                "external_question_references": [
                    {
                        "source_id": item.source_id,
                        "content": item.content_summary[:300],
                        "source_url": item.source_url,
                        "confidence": item.confidence,
                        "selectable": False,
                    }
                    for item in unit.external_question_references
                ],
                "items": [
                    {
                        "question_id": item.question_id,
                        "question_type": item.question_type,
                        "stem": item.stem,
                        "tags": item.tags,
                        "has_reference_answer": bool(item.reference_answer.strip()),
                        "has_analysis": bool((item.analysis or "").strip()),
                    }
                    for item in unit.items
                ],
            }
            for unit in candidate_pool.units
        ]
        repair_instruction = dict(context.get("repair_instruction") or {})
        previous_step_output = context.get("previous_step_output")
        previous_payload = getattr(previous_step_output, "payload", previous_step_output)
        previous_paper = (
            self._compact_exam_paper_for_repair(previous_payload)
            if previous_payload is not None
            else None
        )
        raw_output = await self.chat_model.complete_json(
            "expert_agent",
            build_model_context(
                context,
                target_agent="expert_agent",
                prompt_skill=skill,
                payload={
                    "phase": "paper_assembly",
                    "assembly_output_mode": "natural_language_document",
                    "paper_blueprint": blueprint.model_dump(mode="json"),
                    "candidate_pool": candidate_catalog,
                    "hard_question_count": blueprint.required_total_question_count,
                    "candidate_question_count": sum(
                        len(unit.items) for unit in candidate_pool.units
                    ),
                    "required_variant_count": max(
                        0,
                        (blueprint.required_total_question_count or 0)
                        - sum(len(unit.items) for unit in candidate_pool.units),
                    ),
                    "audit_feedback": list(
                        getattr(
                            getattr(
                                context.get("audit_feedback"),
                                "payload",
                                context.get("audit_feedback"),
                            ),
                            "findings",
                            [],
                        )
                    ),
                    "repair_request": (
                        {
                            "issue_ids": repair_instruction.get("issue_ids", []),
                            "locations": repair_instruction.get("locations", []),
                            "instruction": repair_instruction.get(
                                "repair_instruction", ""
                            ),
                            "previous_paper": previous_paper,
                        }
                        if repair_instruction
                        else None
                    ),
                    "output_contract": {
                        "assembly_document": (
                            "完整自然语言组卷原稿；用独立一行明确试卷标题，"
                            "按单元写明选中的候选题ID；每一道原创缺口题用一个连续题目块"
                            "逐项写明所属单元、题型、题干、选项、答案、解析和依据引用。"
                        )
                    },
                },
                permission_note=(
                    "优先从当前候选池选择题目；不得修改正式题库题干或答案。"
                    "若用户明确题量且去重后不足，只能在generated_items中按蓝图题型原创缺口数量；"
                    "选择题必须提供选项，简答题等非选择题使用空选项列表。"
                    "正式候选和扩展参考仍不足时，必须在generated_items中生成恰好补足缺口的变式题；"
                    "不得因为候选池数量不足而返回空的generated_items。"
                ),
            ),
        )
        try:
            if isinstance(raw_output, dict) and raw_output.get("assembly_document"):
                compilation = await self.assembly_compiler.compile(
                    context,
                    assembly_document=str(raw_output["assembly_document"]),
                    # The compiler only validates the selected unit/question
                    # pairs. Re-sending stems, tags and external search results
                    # adds context pressure without improving that decision.
                    candidate_catalog=[
                        {
                            "unit_id": unit["unit_id"],
                            "items": [
                                {"question_id": item["question_id"]}
                                for item in unit["items"]
                            ],
                        }
                        for unit in candidate_catalog
                    ],
                )
                if compilation.result.status != "compiled":
                    details = ", ".join(
                        f"{issue.code}@{issue.field_path}"
                        for issue in compilation.result.issues
                    )
                    raise ValueError(
                        "paper assembly draft could not be compiled: " + details
                    )
                contract = compilation.result.contract
                compiler_output = {
                    "title": contract.title,
                    "instructions": "请按题目顺序作答。",
                    "selected_items": [
                        {
                            "unit_id": item.unit_id,
                            "question_id": item.question_id,
                            "score": item.score,
                            "selection_rationale": "业务 Agent 在自然语言组卷原稿中明确选用。",
                        }
                        for item in contract.selected_items
                    ],
                    "generated_items": [
                        {
                            "unit_id": item.unit_id,
                            "question_type": item.question_type,
                            "stem": item.stem,
                            "options": item.options,
                            "reference_answer": item.reference_answer,
                            "analysis": item.explanation,
                            "selection_rationale": "业务 Agent 在自然语言组卷原稿中明确用于补足缺口。",
                            "source_tier": "model_knowledge",
                        }
                        for item in contract.generated_items
                    ],
                    "coverage_summary": {},
                    "unresolved_constraints": [],
                }
                output = ExamAssemblyModelOutput.model_validate(compiler_output)
            else:
                # Explicit legacy adapter for older model snapshots and tests.
                output = ExamAssemblyModelOutput.model_validate(
                    self._normalize_model_output(raw_output, blueprint.title)
                )
        except ValidationError as exc:
            first_error = exc.errors()[0]
            location = ".".join(str(part) for part in first_error["loc"])
            output = ExamAssemblyModelOutput(
                title=blueprint.title,
                instructions="请按题目顺序作答。",
                selected_items=[],
                generated_items=[],
                coverage_summary={},
                unresolved_constraints=[
                    "模型组装结果格式不完整，系统已改用正式候选池确定性组装"
                    + (f"（{location}）" if location else "")
                    + "。"
                ],
            )
        by_unit = {
            unit.unit_id: {item.question_id: item for item in unit.items}
            for unit in candidate_pool.units
        }
        blueprint_units = {unit.unit_id: unit for unit in blueprint.units}
        pool_units = {unit.unit_id: unit for unit in candidate_pool.units}
        required_by_type = {
            self._normalize_question_type(question_type): count
            for question_type, count in blueprint.required_question_type_distribution.items()
            if count > 0
        }
        selected_by_type = {question_type: 0 for question_type in required_by_type}
        selected_ids: set[str] = set()
        selected_stems: set[str] = set()
        items: list[ExamPaperItem] = []
        system_constraints: list[str] = []
        for sequence, selected in enumerate(output.selected_items, start=1):
            if selected.question_id in selected_ids:
                system_constraints.append(
                    f"题目{selected.question_id}被模型重复选择，"
                    "系统已保留首次选择并丢弃后续重复项。"
                )
                continue
            question = by_unit.get(selected.unit_id, {}).get(selected.question_id)
            if question is None:
                system_constraints.append(
                    f"模型选择的题目{selected.question_id}不在蓝图单元"
                    f"{selected.unit_id}候选池中，系统已丢弃该越界选择。"
                )
                continue
            if not self._has_complete_solution(question):
                system_constraints.append(
                    f"候选题{selected.question_id}缺少标准答案或解析，"
                    "系统已跳过并由完整候选或原创题补足。"
                )
                continue
            normalized_stem = self._normalize_stem(question.stem)
            if normalized_stem in selected_stems:
                system_constraints.append(
                    f"题目{selected.question_id}与已选题题干重复，系统已丢弃。"
                )
                continue
            unit = blueprint_units[selected.unit_id]
            actual_type = self._normalize_question_type(question.question_type)
            if required_by_type and (
                actual_type not in required_by_type
                or selected_by_type[actual_type] >= required_by_type[actual_type]
            ):
                system_constraints.append(
                    f"题目{selected.question_id}的题型{question.question_type}"
                    "不在剩余精确题型配额内，系统已丢弃。"
                )
                continue
            if unit.question_type_preferences and not self._matches_question_type(
                question.question_type, unit.question_type_preferences
            ):
                pool_unit = pool_units[selected.unit_id]
                if not any("近似题型候选" in warning for warning in pool_unit.warnings):
                    raise ValueError("selected question type violates blueprint unit preferences")
                system_constraints.append(
                    f"题目{selected.question_id}为正式题库近似题型候选，"
                    f"实际题型为{question.question_type}，未满足蓝图偏好"
                    f"{','.join(unit.question_type_preferences)}。"
                )
            selected_ids.add(selected.question_id)
            selected_stems.add(normalized_stem)
            if required_by_type:
                selected_by_type[actual_type] += 1
            items.append(
                ExamPaperItem(
                    sequence=sequence,
                    unit_id=selected.unit_id,
                    score=selected.score,
                    question=question,
                    selection_rationale=selected.selection_rationale,
                )
            )
        required_total = (
            sum(required_by_type.values())
            if required_by_type
            else blueprint.required_total_question_count
            if blueprint.question_count_is_hard_constraint
            else None
        )
        gap = max(0, required_total - len(items)) if required_total else 0
        if required_total:
            generated_candidates = output.generated_items[:gap]
        else:
            covered_units = {item.unit_id for item in items}
            generated_candidates = []
            for generated in output.generated_items:
                if generated.unit_id in covered_units:
                    continue
                generated_candidates.append(generated)
                covered_units.add(generated.unit_id)
        for generated in generated_candidates:
            unit = blueprint_units.get(generated.unit_id)
            if unit is None:
                system_constraints.append(
                    f"模型生成题引用了未知蓝图单元{generated.unit_id}，系统已丢弃。"
                )
                continue
            if unit.question_type_preferences and not self._matches_question_type(
                generated.question_type, unit.question_type_preferences
            ):
                system_constraints.append(
                    f"模型生成的{generated.question_type}不符合蓝图单元"
                    f"{generated.unit_id}题型要求，系统已丢弃。"
                )
                continue
            actual_type = self._normalize_question_type(generated.question_type)
            if required_by_type and (
                actual_type not in required_by_type
                or selected_by_type[actual_type] >= required_by_type[actual_type]
            ):
                system_constraints.append(
                    f"模型生成的{generated.question_type}不在剩余精确题型配额内，"
                    "系统已丢弃。"
                )
                continue
            normalized_stem = self._normalize_stem(generated.stem)
            if normalized_stem in selected_stems:
                system_constraints.append("模型生成题与已选题题干重复，系统已丢弃。")
                continue
            question = QuestionDetail(
                question_id=f"GENERATED_{uuid4().hex}",
                question_type=generated.question_type,
                stem=generated.stem,
                reference_answer=generated.reference_answer,
                analysis=generated.analysis,
                options=generated.options,
                origin="generated",
                # The minimal framework does not yet carry verifiable evidence
                # references per generated question. Keep provenance honest by
                # treating every generated gap item as model knowledge.
                source_tier="model_knowledge",
                tags=[unit.knowledge_module],
                source_metadata={
                    "generated_by": "expert_agent",
                    "kp_names": self._unit_kp_name_hints(
                        candidate_pool, generated.unit_id
                    ),
                },
                bridges=self._generated_question_bridges(
                    self._unit_kp_ids(candidate_pool, generated.unit_id),
                    unit_id=generated.unit_id,
                ),
                retrieval=QuestionRetrievalMetadata(
                    channels=[], channel_scores={}, fusion_score=0.0
                ),
            )
            selected_stems.add(normalized_stem)
            if required_by_type:
                selected_by_type[actual_type] += 1
            items.append(
                ExamPaperItem(
                    sequence=len(items) + 1,
                    unit_id=generated.unit_id,
                    score=None,
                    question=question,
                    selection_rationale=generated.selection_rationale,
                )
            )
        if required_total and len(items) < required_total:
            # The model is responsible for ranking and rationale, but hard
            # question counts are system-owned. A retry/revision must not fail
            # merely because the model omitted otherwise valid candidates.
            for unit in candidate_pool.units:
                if len(items) >= required_total:
                    break
                blueprint_unit = blueprint_units[unit.unit_id]
                for candidate in unit.items:
                    if len(items) >= required_total or candidate.question_id in selected_ids:
                        continue
                    if not self._has_complete_solution(candidate):
                        continue
                    if blueprint_unit.question_type_preferences and not self._matches_question_type(
                        candidate.question_type, blueprint_unit.question_type_preferences
                    ):
                        continue
                    actual_type = self._normalize_question_type(candidate.question_type)
                    if required_by_type and (
                        actual_type not in required_by_type
                        or selected_by_type[actual_type] >= required_by_type[actual_type]
                    ):
                        continue
                    normalized_stem = self._normalize_stem(candidate.stem)
                    if normalized_stem in selected_stems:
                        continue
                    selected_ids.add(candidate.question_id)
                    selected_stems.add(normalized_stem)
                    if required_by_type:
                        selected_by_type[actual_type] += 1
                    items.append(
                        ExamPaperItem(
                            sequence=len(items) + 1,
                            unit_id=unit.unit_id,
                            score=None,
                            question=candidate,
                            selection_rationale="系统补足用户明确题量：候选池中未被模型选择的合规题目。",
                        )
                    )
        if required_total and len(items) < required_total:
            generated_gap_items = await self._generate_remaining_gap(
                context=context,
                blueprint=blueprint,
                candidate_pool=candidate_pool,
                current_items=items,
                required_total=required_total,
                required_by_type=required_by_type,
                skill=skill,
            )
            items.extend(generated_gap_items)
        if not items:
            # Keep a soft-count practice paper usable even if the model returns
            # only hallucinated candidate IDs.  The fallback still selects
            # exclusively from the system-owned candidate pool.
            fallback = next(
                (
                    (unit, candidate)
                    for unit in candidate_pool.units
                    for candidate in unit.items
                    if self._has_complete_solution(candidate)
                ),
                None,
            )
            if fallback is not None:
                unit, candidate = fallback
                items.append(
                    ExamPaperItem(
                        sequence=1,
                        unit_id=unit.unit_id,
                        score=None,
                        question=candidate,
                        selection_rationale="模型候选选择全部越界，系统从正式候选池保留一道有效题。",
                    )
                )
                system_constraints.append(
                    "模型未返回有效候选题选择，系统已从正式候选池保留一道题继续组卷。"
                )
        items = [
            item.model_copy(update={"sequence": sequence})
            for sequence, item in enumerate(items, start=1)
        ]
        if required_total and len(items) < required_total:
            system_constraints.append(
                f"用户明确要求{required_total}题，当前仅完成{len(items)}题。"
            )
        if required_by_type:
            actual_by_type = self._count_question_types(items)
            for question_type, required_count in required_by_type.items():
                actual_count = actual_by_type.get(question_type, 0)
                if actual_count != required_count:
                    system_constraints.append(
                        f"用户明确要求{question_type}{required_count}题，"
                        f"当前完成{actual_count}题。"
                    )
        duration_minutes = (
            blueprint.duration_minutes
            or self._recommended_duration_minutes(items)
        )
        difficulty_summary = self._build_difficulty_source_summary(
            blueprint=blueprint,
            items=items,
            candidate_pool=candidate_pool,
            required_total=required_total,
        )
        draft = ExamPaperDraft(
            paper_draft_id=f"PAPER_DRAFT_{uuid4().hex}",
            blueprint_id=blueprint.blueprint_id,
            candidate_pool_id=candidate_pool.pool_id,
            title=output.title,
            instructions=self._build_learner_instructions(
                len(items), duration_minutes,
                [item.question.question_type for item in items],
            ),
            duration_minutes=duration_minutes,
            total_score=blueprint.total_score,
            items=items,
            answer_key={item.question.question_id: item.question.reference_answer for item in items},
            explanations={item.question.question_id: item.question.analysis for item in items},
            coverage_summary=output.coverage_summary,
            unresolved_constraints=[
                *output.unresolved_constraints,
                *system_constraints,
                *[warning for unit in candidate_pool.units for warning in unit.warnings],
            ],
            difficulty_source_summary=difficulty_summary,
        )
        return envelope(context, "expert_agent", "exam_paper_draft", draft)

    @classmethod
    def _build_difficulty_source_summary(
        cls,
        *,
        blueprint: Any,
        items: list[ExamPaperItem],
        candidate_pool: QuestionCandidatePool,
        required_total: int | None,
    ) -> PaperDifficultySourceSummary:
        """向用户透明说明入卷题的难度与来源构成。

        - 仅真实难度标注参与“精确难度”统计；未标注正式题单独计数。
        - 网络参考题不直接入卷（仅支撑出题），单独计数并写入说明。
        - 生成的补充题没有真实难度标注，永不伪装为指定难度。
        """
        targets = {
            unit.target_difficulty
            for unit in blueprint.units
            if getattr(unit, "target_difficulty", None) is not None
        }
        target_difficulty = next(iter(targets)) if len(targets) == 1 else None
        hard = bool(
            any(
                getattr(unit, "difficulty_is_hard_constraint", False)
                for unit in blueprint.units
            )
        )
        total = len(items)
        exact = 0
        unlabeled = 0
        for item in items:
            if item.question.origin == "generated":
                continue
            if item.question.difficulty is None:
                unlabeled += 1
            elif (
                target_difficulty is not None
                and item.question.difficulty == target_difficulty
            ):
                exact += 1
        generated = sum(1 for item in items if item.question.origin == "generated")
        web_reference = sum(
            len(unit.external_question_references)
            for unit in candidate_pool.units
        )
        unmet = (
            max(0, required_total - total)
            if required_total is not None
            else 0
        )
        parts = [f"本卷共{total}题"]
        if target_difficulty is not None:
            parts.append(f"其中难度{target_difficulty}的正式题{exact}道")
            if unlabeled:
                parts.append(f"未标注难度的正式题{unlabeled}道")
            if generated:
                parts.append(f"系统生成的补充题{generated}道")
        else:
            parts.append(f"其中正式题{total - generated}道")
            if generated:
                parts.append(f"系统生成的补充题{generated}道")
            if unlabeled:
                parts.append(f"正式题中未标注难度{unlabeled}道")
        if web_reference:
            parts.append(f"检索到网络参考题{web_reference}条，仅用于支撑出题，不直接入卷")
        if unmet:
            parts.append(f"仍有{unmet}题缺口未满足")
        notice = "；".join(parts) + "。"
        if target_difficulty is not None and generated:
            notice += "补充题没有真实难度标注，系统不会将其伪装为指定难度。"
        return PaperDifficultySourceSummary(
            target_difficulty=target_difficulty,
            difficulty_is_hard_constraint=hard,
            total_questions=total,
            exact_difficulty_count=exact,
            unlabeled_official_count=unlabeled,
            web_reference_count=web_reference,
            generated_count=generated,
            unmet_count=unmet,
            notice=notice,
        )

    @staticmethod
    def _has_complete_solution(question: QuestionDetail) -> bool:
        return bool(
            question.reference_answer.strip()
            and (question.analysis or "").strip()
        )

    @classmethod
    def _normalize_model_output(
        cls,
        raw_output: Any,
        fallback_title: str,
    ) -> dict[str, Any]:
        raw = dict(raw_output) if isinstance(raw_output, dict) else {}
        selected_items: list[dict[str, Any]] = []
        selected_source = raw.get("selected_items") or raw.get("selected_questions") or []
        for row in selected_source if isinstance(selected_source, list) else []:
            if not isinstance(row, dict):
                continue
            unit_id = cls._bounded_text(row.get("unit_id"), maximum=200)
            question_id = cls._bounded_text(row.get("question_id"), maximum=200)
            if not unit_id or not question_id:
                continue
            selected_items.append({
                "unit_id": unit_id,
                "question_id": question_id,
                "score": cls._positive_score(row.get("score")),
                "selection_rationale": cls._bounded_text(
                    row.get("selection_rationale") or row.get("reason"),
                    maximum=500,
                ) or "依据蓝图范围与候选题相关性选择。",
            })

        generated_items: list[dict[str, Any]] = []
        generated_source = raw.get("generated_items") or raw.get("generated_questions") or []
        for row in generated_source if isinstance(generated_source, list) else []:
            if not isinstance(row, dict):
                continue
            question_type = cls._bounded_text(
                row.get("question_type") or row.get("type"), maximum=100
            )
            options = cls._normalize_options(row.get("options"))
            if "选择" in question_type and len(options) < 2:
                continue
            normalized = {
                "unit_id": cls._bounded_text(row.get("unit_id"), maximum=200),
                "question_type": question_type,
                "stem": cls._bounded_text(
                    row.get("stem") or row.get("question"), maximum=2_000
                ),
                "options": options,
                "reference_answer": cls._bounded_text(
                    row.get("reference_answer") or row.get("answer"), maximum=500
                ),
                "analysis": cls._bounded_text(
                    row.get("analysis") or row.get("explanation"), maximum=2_000
                ),
                "selection_rationale": cls._bounded_text(
                    row.get("selection_rationale") or row.get("reason"), maximum=500
                ) or "用于补足蓝图单元的题目缺口。",
                "source_tier": (
                    row.get("source_tier")
                    if row.get("source_tier")
                    in {"textbook", "web_reference", "model_knowledge"}
                    else "model_knowledge"
                ),
            }
            if not all(
                normalized[key]
                for key in (
                    "unit_id",
                    "question_type",
                    "stem",
                    "reference_answer",
                    "analysis",
                )
            ):
                continue
            generated_items.append(normalized)

        coverage = raw.get("coverage_summary")
        if not isinstance(coverage, dict):
            coverage = {"summary": str(coverage)} if coverage else {}
        return {
            "title": cls._bounded_text(
                raw.get("title") or raw.get("paper_title") or fallback_title,
                maximum=300,
            ) or fallback_title[:300],
            "instructions": cls._bounded_text(
                raw.get("instructions"), maximum=2_000
            ) or "请按题目顺序作答。",
            "selected_items": selected_items,
            "generated_items": generated_items,
            "coverage_summary": coverage,
            "unresolved_constraints": cls._string_list(
                raw.get("unresolved_constraints") or raw.get("warnings")
            ),
        }

    @staticmethod
    def _bounded_text(value: Any, *, maximum: int) -> str:
        if isinstance(value, list):
            value = ", ".join(str(item) for item in value)
        return (str(value).strip() if value is not None else "")[:maximum]

    @staticmethod
    def _positive_score(value: Any) -> float | None:
        if value is None:
            return None
        try:
            parsed = float(str(value).strip().replace("分", ""))
        except (TypeError, ValueError):
            return None
        return parsed if parsed > 0 else None

    @staticmethod
    def _normalize_options(value: Any) -> list[str]:
        if isinstance(value, dict):
            rows = [f"{key}. {item}" for key, item in value.items()]
        elif isinstance(value, list):
            rows = [str(item).strip() for item in value]
        elif isinstance(value, str):
            rows = [item.strip() for item in value.splitlines()]
        else:
            rows = []
        return [item for item in rows if item][:8]

    @staticmethod
    def _string_list(value: Any) -> list[str]:
        if value is None:
            return []
        rows = value if isinstance(value, list) else [value]
        return [str(item).strip() for item in rows if str(item).strip()]

    async def _generate_remaining_gap(
        self,
        *,
        context: dict[str, Any],
        blueprint,
        candidate_pool: QuestionCandidatePool,
        current_items: list[ExamPaperItem],
        required_total: int,
        required_by_type: dict[str, int],
        skill,
    ) -> list[ExamPaperItem]:
        generated_items: list[ExamPaperItem] = []
        unit_by_id = {unit.unit_id: unit for unit in blueprint.units}
        selected_counts = {
            unit.unit_id: sum(item.unit_id == unit.unit_id for item in current_items)
            for unit in blueprint.units
        }
        existing_stems = {
            self._normalize_stem(item.question.stem)
            for item in current_items
        }
        selected_by_type = self._count_question_types(current_items)
        while len(current_items) + len(generated_items) < required_total:
            target_type = next(
                (
                    question_type
                    for question_type, required_count in required_by_type.items()
                    if selected_by_type.get(question_type, 0) < required_count
                ),
                None,
            )
            target_unit = next(
                (
                    unit
                    for unit in blueprint.units
                    if selected_counts.get(unit.unit_id, 0) < unit.required_question_count
                    and (
                        target_type is None
                        or self._matches_question_type(
                            target_type, unit.question_type_preferences
                        )
                    )
                ),
                next(
                    (
                        unit
                        for unit in blueprint.units
                        if target_type is None
                        or self._matches_question_type(
                            target_type, unit.question_type_preferences
                        )
                    ),
                    blueprint.units[0],
                ),
            )
            remaining_total = required_total - len(current_items) - len(generated_items)
            unit_gap = max(
                1,
                target_unit.required_question_count
                - selected_counts.get(target_unit.unit_id, 0),
            )
            batch_size = min(5, remaining_total, unit_gap)
            if target_type is not None:
                batch_size = min(
                    batch_size,
                    required_by_type[target_type]
                    - selected_by_type.get(target_type, 0),
                )
            added = 0
            for _attempt in range(2):
                raw = await self.chat_model.complete_json(
                    "expert_agent",
                    build_model_context(
                        context,
                        target_agent="expert_agent",
                        prompt_skill=skill,
                        payload={
                            "phase": "paper_gap_generation",
                            "unit_id": target_unit.unit_id,
                            "paper_scope": blueprint.scope_summary,
                            "knowledge_module": target_unit.knowledge_module,
                            "learning_objective": target_unit.learning_objective,
                            "retrieval_query": target_unit.retrieval_query,
                            "question_type_preferences": (
                                [target_type]
                                if target_type is not None
                                else target_unit.question_type_preferences
                            ),
                            "required_question_type": target_type,
                            "gap_count": batch_size,
                            "avoid_stems": [
                                item.question.stem
                                for item in [*current_items, *generated_items]
                            ][-20:],
                            "reference_material": [
                                evidence.content_summary[:600]
                                for pool_unit in candidate_pool.units
                                if pool_unit.unit_id == target_unit.unit_id
                                for evidence in pool_unit.external_question_references[:2]
                            ],
                            "formal_question_references": [
                                {
                                    "stem": candidate.stem,
                                    "reference_answer": candidate.reference_answer,
                                    "analysis": candidate.analysis,
                                }
                                for pool_unit in candidate_pool.units
                                if pool_unit.unit_id == target_unit.unit_id
                                for candidate in pool_unit.items[:3]
                            ],
                            "assembly_output_mode": "natural_language_document",
                            "output_contract": {
                                "assembly_document": (
                                    "自然语言缺口题原稿；须包含试卷标题，并逐题明确单元、"
                                    "题型、题干、选项、参考答案、解析和依据。"
                                )
                            },
                        },
                        permission_note=(
                            f"只补充{batch_size}道{target_unit.knowledge_module}题目；"
                            f"严格限定在{blueprint.scope_summary}与检索范围"
                            f"{target_unit.retrieval_query}内；"
                            "可参考正式题生成新变式题，但不得修改或冒用原题ID；"
                            "每题必须有题干、选项（选择题）、答案和解析。不得输出整卷或系统ID。"
                        ),
                    ),
                )
                rows: list[dict[str, Any]] = []
                if isinstance(raw, dict) and raw.get("assembly_document"):
                    candidate_catalog = [
                        {
                            "unit_id": unit.unit_id,
                            "items": [
                                {"question_id": item.question_id}
                                for item in unit.items
                            ],
                        }
                        for unit in candidate_pool.units
                    ]
                    compilation = await self.assembly_compiler.compile(
                        context,
                        assembly_document=str(raw["assembly_document"]),
                        candidate_catalog=candidate_catalog,
                    )
                    if compilation.result.status == "compiled":
                        rows = [
                            {
                                "unit_id": item.unit_id,
                                "question_type": item.question_type,
                                "stem": item.stem,
                                "options": item.options,
                                "reference_answer": item.reference_answer,
                                "analysis": item.explanation,
                                "selection_rationale": (
                                    "业务 Agent 在自然语言缺口题原稿中明确用于补足题量。"
                                ),
                                "source_tier": "model_knowledge",
                            }
                            for item in compilation.result.contract.generated_items
                        ]
                elif isinstance(raw, dict):
                    # Explicit legacy adapter for older model snapshots and tests.
                    rows = list(raw.get("generated_items", []))
                for row in rows[:batch_size]:
                    try:
                        generated = GeneratedPaperItemModelOutput.model_validate(row)
                    except ValidationError:
                        continue
                    if generated.unit_id != target_unit.unit_id:
                        continue
                    if target_unit.question_type_preferences and not self._matches_question_type(
                        generated.question_type,
                        target_unit.question_type_preferences,
                    ):
                        continue
                    actual_type = self._normalize_question_type(generated.question_type)
                    if target_type is not None and actual_type != target_type:
                        continue
                    if required_by_type and (
                        actual_type not in required_by_type
                        or selected_by_type.get(actual_type, 0)
                        >= required_by_type[actual_type]
                    ):
                        continue
                    normalized_stem = self._normalize_stem(generated.stem)
                    if not normalized_stem or normalized_stem in existing_stems:
                        continue
                    existing_stems.add(normalized_stem)
                    question = QuestionDetail(
                        question_id=f"GENERATED_{uuid4().hex}",
                        question_type=generated.question_type,
                        stem=generated.stem,
                        reference_answer=generated.reference_answer,
                        analysis=generated.analysis,
                        options=generated.options,
                        origin="generated",
                        source_tier="model_knowledge",
                        tags=[target_unit.knowledge_module],
                        source_metadata={
                            "generated_by": "expert_agent",
                            "kp_names": self._unit_kp_name_hints(
                                candidate_pool, generated.unit_id
                            ),
                        },
                        bridges=self._generated_question_bridges(
                            self._unit_kp_ids(
                                candidate_pool, target_unit.unit_id
                            ),
                            unit_id=target_unit.unit_id,
                        ),
                        retrieval=QuestionRetrievalMetadata(
                            channels=[], channel_scores={}, fusion_score=0.0
                        ),
                    )
                    generated_items.append(
                        ExamPaperItem(
                            sequence=len(current_items) + len(generated_items) + 1,
                            unit_id=generated.unit_id,
                            score=None,
                            question=question,
                            selection_rationale=generated.selection_rationale,
                        )
                    )
                    selected_counts[generated.unit_id] = (
                        selected_counts.get(generated.unit_id, 0) + 1
                    )
                    selected_by_type[actual_type] = (
                        selected_by_type.get(actual_type, 0) + 1
                    )
                    added += 1
                    if added >= batch_size:
                        break
                if added >= batch_size:
                    break
            if added == 0:
                break
        return generated_items

    @staticmethod
    def _unit_kp_ids(
        candidate_pool: QuestionCandidatePool,
        unit_id: str,
    ) -> list[str]:
        direct = next(
            (
                list(unit.resolved_kp_ids)
                for unit in candidate_pool.units
                if unit.unit_id == unit_id and unit.resolved_kp_ids
            ),
            [],
        )
        if direct:
            return list(dict.fromkeys(direct))
        return list(
            dict.fromkeys(
                kp_id
                for unit in candidate_pool.units
                for kp_id in unit.resolved_kp_ids
            )
        )

    @staticmethod
    def _generated_question_bridges(
        kp_ids: list[str],
        *,
        unit_id: str,
    ) -> list[QuestionBridge]:
        return [
            QuestionBridge(
                kp_id=kp_id,
                bridge_layer="llm",
                relation="blueprint_unit_scope",
                confidence=0.8,
                rank=index,
                evidence_chunk_uid=f"blueprint-unit:{unit_id}",
                match_method="resolved_blueprint_unit",
            )
            for index, kp_id in enumerate(kp_ids, start=1)
            if str(kp_id).strip()
        ]

    @staticmethod
    def _unit_kp_name_hints(
        candidate_pool: QuestionCandidatePool,
        unit_id: str,
    ) -> dict[str, str]:
        """Carry learner-facing KP names alongside generated bridge IDs."""

        unit = next(
            (item for item in candidate_pool.units if item.unit_id == unit_id),
            None,
        )
        if unit is None:
            return {}
        hints: dict[str, str] = {}
        for question in unit.items:
            tags = [str(value).strip() for value in question.tags if str(value).strip()]
            for index, bridge in enumerate(question.bridges):
                if bridge.kp_id in hints or not tags:
                    continue
                hints[bridge.kp_id] = tags[index] if index < len(tags) else tags[0]
        return hints

    @staticmethod
    def _compact_exam_paper_for_repair(paper: Any) -> dict[str, Any] | None:
        """Expose only the current paper fields needed for a localized rewrite."""

        if not isinstance(paper, ExamPaperDraft):
            return None
        return {
            "title": paper.title,
            "instructions": paper.instructions,
            "duration_minutes": paper.duration_minutes,
            "total_score": paper.total_score,
            "items": [
                {
                    "sequence": item.sequence,
                    "unit_id": item.unit_id,
                    "score": item.score,
                    "question_id": item.question.question_id,
                    "question_type": item.question.question_type,
                    "stem": item.question.stem,
                }
                for item in paper.items[:80]
            ],
            "answer_key": dict(paper.answer_key),
            "explanations": dict(paper.explanations),
        }

    @staticmethod
    def _normalize_stem(value: str) -> str:
        return "".join(character for character in value if character.isalnum())

    @classmethod
    def _count_question_types(
        cls, items: list[ExamPaperItem]
    ) -> dict[str, int]:
        counts: dict[str, int] = {}
        for item in items:
            question_type = cls._normalize_question_type(item.question.question_type)
            counts[question_type] = counts.get(question_type, 0) + 1
        return counts

    @staticmethod
    def _build_learner_instructions(
        question_count: int,
        duration_minutes: int | None,
        question_types: list[str] | None = None,
    ) -> str:
        duration = (
            f"，建议作答时间{duration_minutes}分钟" if duration_minutes else ""
        )
        normalized_types = {
            PaperAssemblyAgent._normalize_question_type(value)
            for value in (question_types or [])
        }
        guidance: list[str] = []
        if "单项选择题" in normalized_types:
            guidance.append("单项选择题选择一个最佳答案")
        if "多项选择题" in normalized_types:
            guidance.append("多项选择题选择所有正确答案")
        if normalized_types.intersection({"简答题", "案例分析题"}):
            guidance.append("主观题写明判断依据和关键步骤")
        suffix = "；" + "，".join(guidance) if guidance else ""
        return f"本试卷共{question_count}题{duration}。请按题目顺序作答{suffix}。"

    @classmethod
    def _recommended_duration_minutes(cls, items: list[ExamPaperItem]) -> int:
        weights = {
            "单项选择题": 1.5,
            "多项选择题": 2.5,
            "判断题": 1.0,
            "填空题": 1.5,
            "简答题": 8.0,
            "案例分析题": 15.0,
        }
        total = 0.0
        for item in items:
            raw_type = item.question.question_type.strip().replace(" ", "")
            if any(marker in raw_type for marker in ("案例", "病例")):
                total += 15.0
            else:
                total += weights.get(cls._normalize_question_type(raw_type), 5.0)
        return max(5, int((total + 4.999) // 5) * 5)

    @staticmethod
    def _normalize_question_type(value: str) -> str:
        normalized = value.strip().replace(" ", "").replace("_", "")
        if "案例" in normalized or "病例" in normalized:
            return "简答题"
        aliases = {
            "单选题": "单项选择题",
            "单项选择": "单项选择题",
            "多选题": "多项选择题",
            "多项选择": "多项选择题",
            "选择题": "选择题",
            "简答": "简答题",
            "问答": "简答题",
            "问答题": "简答题",
        }
        return aliases.get(normalized, normalized)

    @classmethod
    def _matches_question_type(cls, actual: str, preferences: list[str]) -> bool:
        actual_type = cls._normalize_question_type(actual)
        allowed = {cls._normalize_question_type(value) for value in preferences}
        if "选择题" in allowed:
            return actual_type in {"单项选择题", "多项选择题"}
        return actual_type in allowed
