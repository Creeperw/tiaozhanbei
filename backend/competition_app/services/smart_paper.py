from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any, Iterable

from competition_app.contracts.execution import (
    DEFAULT_PROVIDER_TIMEOUT_SECONDS,
    ExecutionPlan,
    ExecutionStep,
    model_step_timeout_seconds,
)
from competition_app.contracts.paper import ExamPaperItem


SMART_PAPER_TYPE_ALIASES = {
    "single_choice": "单项选择题",
    "singlechoice": "单项选择题",
    "单选题": "单项选择题",
    "单项选择题": "单项选择题",
    "multiple_choice": "多项选择题",
    "multiplechoice": "多项选择题",
    "多选题": "多项选择题",
    "多项选择题": "多项选择题",
    "fill_blank": "填空题",
    "fillblank": "填空题",
    "填空题": "填空题",
    "short_answer": "简答题",
    "shortanswer": "简答题",
    "简答": "简答题",
    "简答题": "简答题",
    "问答题": "简答题",
}

SMART_PAPER_TYPE_ORDER = {
    "单项选择题": 0,
    "多项选择题": 1,
    "填空题": 2,
    "简答题": 3,
}


def normalize_smart_paper_type(value: Any) -> str | None:
    key = str(value or "").strip().replace(" ", "").replace("-", "_").lower()
    return SMART_PAPER_TYPE_ALIASES.get(key)


def validate_smart_paper_constraints(constraints: Any) -> dict[str, Any]:
    """Validate the dedicated workshop request without interpreting prose.

    The result is a closed, bounded contract. Unknown question types (including
    the retired case-analysis option) fail fast instead of being silently
    converted by an LLM or inferred from user text.
    """

    if not isinstance(constraints, dict):
        raise ValueError("智能组卷参数必须是结构化对象")
    raw_distribution = constraints.get("question_type_distribution")
    if not isinstance(raw_distribution, dict) or not raw_distribution:
        raise ValueError("请至少选择一种题型")
    distribution: dict[str, int] = {}
    for raw_type, raw_count in raw_distribution.items():
        canonical = normalize_smart_paper_type(raw_type)
        if canonical is None:
            raise ValueError(f"智能组卷暂不支持题型：{raw_type}")
        if isinstance(raw_count, bool) or not isinstance(raw_count, int):
            raise ValueError("题型数量必须是整数")
        if raw_count < 0 or raw_count > 50:
            raise ValueError("单个题型数量必须在 0 至 50 之间")
        if raw_count:
            distribution[canonical] = distribution.get(canonical, 0) + raw_count
    total = sum(distribution.values())
    if not 1 <= total <= 50:
        raise ValueError("试卷总题量必须在 1 至 50 题之间")
    declared_total = constraints.get("question_count")
    if declared_total is not None and (
        isinstance(declared_total, bool)
        or not isinstance(declared_total, int)
        or declared_total != total
    ):
        raise ValueError("试卷总题量与题型分布不一致")

    answer_mode = str(constraints.get("answer_mode") or "practice").strip()
    if answer_mode not in {"practice", "test"}:
        raise ValueError("作答模式只能是练习或测试")
    duration = constraints.get("duration_minutes")
    if answer_mode == "test":
        if isinstance(duration, bool) or not isinstance(duration, int) or not 10 <= duration <= 300:
            raise ValueError("测试模式时长必须在 10 至 300 分钟之间")
    else:
        duration = None

    difficulty = constraints.get("difficulty")
    if difficulty is not None and (
        isinstance(difficulty, bool)
        or not isinstance(difficulty, int)
        or not 1 <= difficulty <= 5
    ):
        raise ValueError("题目难度必须是 1 至 5")

    paper_kind = str(constraints.get("paper_kind") or "special").strip()
    if paper_kind not in {"special", "adaptive"}:
        raise ValueError("智能组卷范围类型无效")
    topic = _bounded_plain_text(constraints.get("topic"), maximum=300)
    focus_topics = _bounded_text_list(constraints.get("focus_topics"), maximum_items=8)
    if paper_kind == "special" and not topic:
        raise ValueError("专项练必须提供练习主题")
    if paper_kind == "adaptive" and not focus_topics:
        raise ValueError("随心练暂时没有可用的薄弱知识点，请先完成练习或选择专项练")

    return {
        "question_count": total,
        "question_type_distribution": distribution,
        "question_types": list(distribution),
        "answer_mode": answer_mode,
        "duration_minutes": duration,
        "difficulty": difficulty,
        "paper_kind": paper_kind,
        "topic": topic,
        "focus_topics": focus_topics,
    }


def build_smart_paper_execution_plan(
    *,
    memory_required: bool = True,
    provider_timeout_seconds: float | None = None,
) -> ExecutionPlan:
    """Return the fixed workshop pipeline while bypassing only Planner.

    The form has already established the paper-generation intent, so another
    semantic routing call would only add latency and routing risk.  Memory and
    Diagnosis are still business dependencies: Memory maintains the bounded
    learner context and Diagnosis reads the current exam workspace's mastery,
    review and learning evidence before retrieval scope is compiled.

    When conversation compression is unnecessary, Memory and Diagnosis may run
    in parallel.  The blueprint remains a barrier over both outputs so question
    retrieval never starts before the learner context has been inspected.
    """

    provider_timeout = provider_timeout_seconds or DEFAULT_PROVIDER_TIMEOUT_SECONDS
    model_timeout = model_step_timeout_seconds(provider_timeout)
    steps = [
        ExecutionStep(
            step_id="memory",
            agent="memory_agent",
            action="prepare_bounded_learner_context",
            timeout_seconds=1800.0,
        ),
        ExecutionStep(
            step_id="diagnosis",
            agent="diagnosis_agent",
            action="query_learner_data",
            depends_on=["memory"] if memory_required else [],
            timeout_seconds=1800.0,
        ),
        ExecutionStep(
            step_id="paper_blueprint",
            agent="paper_blueprint_agent",
            action="create_structured_blueprint",
            depends_on=["memory", "diagnosis"],
            timeout_seconds=60.0,
        ),
        ExecutionStep(
            step_id="question_pool",
            agent="knowledge_base_agent",
            action="retrieve_questions_by_blueprint",
            depends_on=["paper_blueprint"],
            timeout_seconds=1800.0,
        ),
        ExecutionStep(
            step_id="paper_assembly",
            agent="paper_assembly_agent",
            action="assemble_exam_paper",
            depends_on=["paper_blueprint", "question_pool", "diagnosis"],
            timeout_seconds=1800.0,
        ),
        ExecutionStep(
            step_id="audit",
            agent="audit_agent",
            action="review_exam_paper",
            depends_on=["paper_blueprint", "question_pool", "paper_assembly"],
            timeout_seconds=1800.0,
        ),
    ]
    steps = [
        step.model_copy(
            update={"timeout_seconds": max(step.timeout_seconds, model_timeout)}
        )
        if step.requires_provider_deadline
        else step
        for step in steps
    ]
    plan = ExecutionPlan(
        plan_id="PLAN_WORKSHOP_SMART_PAPER_V2",
        task_type="paper_generation",
        steps=steps,
        provider_timeout_seconds=provider_timeout,
    )
    plan.validate_deadlines()
    return plan


def deterministic_candidate_rank(
    question: Any,
    *,
    target_difficulty: int | None,
    weak_kp_ids: set[str] | None = None,
) -> tuple[Any, ...]:
    """Rank already-admitted candidates using only system-owned metadata."""

    raw_difficulty = getattr(question, "difficulty", None)
    difficulty_tier = 0
    if target_difficulty is not None:
        if raw_difficulty == target_difficulty:
            difficulty_tier = 0
        elif raw_difficulty is None:
            difficulty_tier = 1
        else:
            difficulty_tier = 2
    source_tier = str(getattr(question, "source_tier", "") or "")
    official = int(
        source_tier in {"textbook", "official_exam", "official_book", "official"}
    )
    bridge_ids = {
        str(getattr(bridge, "kp_id", "") or "")
        for bridge in list(getattr(question, "bridges", []) or [])
    }
    weak_match = len(bridge_ids & (weak_kp_ids or set()))
    retrieval = getattr(question, "retrieval", None)
    semantic_status = str(
        getattr(retrieval, "semantic_status", "not_evaluated") or "not_evaluated"
    )
    semantic_tier = {
        "eligible": 0,
        "shadow": 1,
        "not_evaluated": 1,
        "uncertain": 2,
        "rejected": 3,
    }.get(semantic_status, 2)
    semantic_score = getattr(retrieval, "semantic_score", None)
    semantic_score_key = -float(semantic_score) if semantic_score is not None else 0.0
    fusion_score = float(getattr(retrieval, "fusion_score", 0.0) or 0.0)
    stable_id = str(getattr(question, "question_id", "") or "")
    return (
        difficulty_tier,
        -official,
        -weak_match,
        semantic_tier,
        semantic_score_key,
        -fusion_score,
        stable_id,
    )


@dataclass(frozen=True)
class PaperOrderingResult:
    items: list[ExamPaperItem]
    strategy: str
    preserved_positions: int
    reordered_items: int


class DeterministicPaperOrderer:
    """Stable, bounded ordering with type sections and mild interleaving.

    The orderer never asks a model to choose IDs. It uses only validated item
    fields and a bounded beam search. During localized repair, protected item
    positions are retained and replacements fill the remaining positions.
    """

    def __init__(self, *, beam_width: int = 24) -> None:
        self.beam_width = max(4, min(int(beam_width), 64))

    def order(
        self,
        items: Iterable[ExamPaperItem],
        *,
        answer_mode: str = "practice",
        preserve_positions: dict[str, int] | None = None,
    ) -> PaperOrderingResult:
        source = list(items)
        if len(source) < 2:
            normalized = [
                item.model_copy(update={"sequence": index})
                for index, item in enumerate(source, start=1)
            ]
            return PaperOrderingResult(normalized, "type_sections_beam_v1", 0, 0)

        by_type: dict[str, list[ExamPaperItem]] = {}
        for item in source:
            question_type = normalize_smart_paper_type(item.question.question_type)
            canonical = question_type or str(item.question.question_type)
            by_type.setdefault(canonical, []).append(item)

        ordered: list[ExamPaperItem] = []
        for question_type in sorted(
            by_type,
            key=lambda value: (SMART_PAPER_TYPE_ORDER.get(value, 99), value),
        ):
            ordered.extend(self._order_section(by_type[question_type], answer_mode))

        preserved = 0
        if preserve_positions:
            ordered, preserved = self._restore_positions(
                ordered,
                preserve_positions=preserve_positions,
            )
        old_positions = {
            item.question.question_id: index
            for index, item in enumerate(source, start=1)
        }
        normalized = [
            item.model_copy(update={"sequence": index})
            for index, item in enumerate(ordered, start=1)
        ]
        moved = sum(
            old_positions.get(item.question.question_id) != index
            for index, item in enumerate(normalized, start=1)
        )
        return PaperOrderingResult(
            normalized,
            "type_sections_beam_v1",
            preserved,
            moved,
        )

    def _order_section(
        self, items: list[ExamPaperItem], answer_mode: str
    ) -> list[ExamPaperItem]:
        if len(items) < 2:
            return list(items)
        stable = sorted(items, key=lambda item: item.question.question_id)
        beams: list[tuple[float, tuple[str, ...], list[ExamPaperItem]]] = [
            (0.0, tuple(), [])
        ]
        for _ in range(len(stable)):
            expanded: list[tuple[float, tuple[str, ...], list[ExamPaperItem]]] = []
            for cost, ids, path in beams:
                used = set(ids)
                for candidate in stable:
                    question_id = candidate.question.question_id
                    if question_id in used:
                        continue
                    next_path = [*path, candidate]
                    expanded.append(
                        (
                            cost + self._incremental_cost(next_path, answer_mode),
                            (*ids, question_id),
                            next_path,
                        )
                    )
            expanded.sort(key=lambda state: (state[0], state[1]))
            beams = expanded[: self.beam_width]
        return beams[0][2] if beams else stable

    @classmethod
    def _incremental_cost(
        cls, path: list[ExamPaperItem], answer_mode: str
    ) -> float:
        current = path[-1]
        previous = path[-2] if len(path) > 1 else None
        difficulty_cost = cls._difficulty_cost(path, answer_mode)
        if previous is None:
            return difficulty_cost
        kp_cost = cls._kp_overlap(previous, current)
        unit_cost = 1.0 if previous.unit_id == current.unit_id else 0.0
        stem_cost = cls._bigram_jaccard(
            previous.question.stem, current.question.stem
        )
        answer_cost = cls._answer_run_cost(path)
        if answer_mode == "test":
            return (
                0.40 * difficulty_cost
                + 0.25 * kp_cost
                + 0.20 * stem_cost
                + 0.10 * unit_cost
                + 0.05 * answer_cost
            )
        return (
            0.30 * difficulty_cost
            + 0.35 * kp_cost
            + 0.20 * stem_cost
            + 0.10 * unit_cost
            + 0.05 * answer_cost
        )

    @staticmethod
    def _difficulty_cost(path: list[ExamPaperItem], answer_mode: str) -> float:
        values = [
            getattr(item.question, "difficulty", None)
            for item in path
        ]
        values = [value for value in values if isinstance(value, int)]
        if len(values) < 2:
            return 0.0
        previous, current = values[-2:]
        if answer_mode == "practice":
            return max(0.0, float(previous - current) / 4.0)
        target = 1.0 + 4.0 * (len(path) - 1) / max(1, len(path))
        return min(1.0, abs(current - target) / 4.0)

    @staticmethod
    def _kp_overlap(left: ExamPaperItem, right: ExamPaperItem) -> float:
        left_ids = {bridge.kp_id for bridge in left.question.bridges}
        right_ids = {bridge.kp_id for bridge in right.question.bridges}
        if not left_ids or not right_ids:
            return 0.0
        return len(left_ids & right_ids) / len(left_ids | right_ids)

    @staticmethod
    def _bigram_jaccard(left: str, right: str) -> float:
        def grams(value: str) -> set[str]:
            compact = re.sub(r"\s+", "", str(value or ""))
            return {compact[index : index + 2] for index in range(len(compact) - 1)}

        left_grams, right_grams = grams(left), grams(right)
        if not left_grams or not right_grams:
            return 0.0
        return len(left_grams & right_grams) / len(left_grams | right_grams)

    @staticmethod
    def _answer_run_cost(path: list[ExamPaperItem]) -> float:
        if len(path) < 3:
            return 0.0
        answers = [
            str(item.question.reference_answer or "").strip().upper()[:1]
            for item in path[-3:]
        ]
        return 1.0 if answers[0] and len(set(answers)) == 1 else 0.0

    @staticmethod
    def _restore_positions(
        ordered: list[ExamPaperItem], *, preserve_positions: dict[str, int]
    ) -> tuple[list[ExamPaperItem], int]:
        slots: list[ExamPaperItem | None] = [None] * len(ordered)
        remaining: list[ExamPaperItem] = []
        preserved = 0
        for item in ordered:
            position = preserve_positions.get(item.question.question_id)
            if isinstance(position, int) and 1 <= position <= len(slots):
                index = position - 1
                if slots[index] is None:
                    slots[index] = item
                    preserved += 1
                    continue
            remaining.append(item)
        iterator = iter(remaining)
        restored = [slot if slot is not None else next(iterator) for slot in slots]
        return restored, preserved


def _bounded_plain_text(value: Any, *, maximum: int) -> str:
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", str(value or ""))
    return re.sub(r"\s+", " ", text).strip()[:maximum]


def _bounded_text_list(value: Any, *, maximum_items: int) -> list[str]:
    values = value if isinstance(value, list) else []
    result: list[str] = []
    for item in values[:maximum_items]:
        text = _bounded_plain_text(item, maximum=120)
        if text and text not in result:
            result.append(text)
    return result


def stable_scope_digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]
