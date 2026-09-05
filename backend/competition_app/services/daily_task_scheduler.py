from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from typing import Any, Callable

from competition_app.contracts.daily_task_scheduling import (
    DailyTaskBlockedCandidate,
    DailyTaskSchedule,
    DailyTaskScheduledCandidate,
    DailyTaskScoreTrace,
)


KnowledgePointResolver = Callable[..., str | None]

_POSITIVE_WEIGHTS = {
    "mastery_gap": 0.30,
    "urgency": 0.20,
    "review_benefit": 0.15,
    "plan_alignment": 0.15,
    "difficulty_fit": 0.10,
    "autonomy_support": 0.10,
}
_TIME_WEIGHT = 0.10
_RISK_WEIGHT = 0.15
_KIND_ORDER = {
    "carryover": 0,
    "remediation": 1,
    "due_review": 2,
    "new_learning": 3,
    "maintenance": 4,
}


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def _metric(component: Any) -> float | None:
    if not isinstance(component, dict) or component.get("available") is not True:
        return None
    value = component.get("value")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return _clamp(float(value))


def _unique(values: list[str]) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))


def _candidate_id(exam_scope_id: str, kp_id: str, task_kind: str) -> str:
    digest = hashlib.sha256(
        json.dumps(
            [exam_scope_id, kp_id, task_kind],
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()[:20]
    return f"DTC_{digest}"


def _resolve(
    resolver: KnowledgePointResolver | None,
    name: str,
    chapter: str,
) -> str | None:
    if resolver is None:
        return None
    try:
        value = resolver(name, chapter)
    except TypeError:
        value = resolver(name)
    return str(value).strip() if value else None


def _path_groups(path_candidates: Any) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if not isinstance(path_candidates, dict):
        return [], []
    if isinstance(path_candidates.get("eligible"), list):
        return (
            [item for item in path_candidates["eligible"] if isinstance(item, dict)],
            [
                item
                for item in path_candidates.get("blocked", [])
                if isinstance(item, dict)
            ],
        )
    items = [item for item in path_candidates.get("items", []) if isinstance(item, dict)]
    return (
        [item for item in items if item.get("eligible") is True],
        [item for item in items if item.get("eligible") is not True],
    )


def _kind(item: dict[str, Any], components: dict[str, Any]) -> str:
    refs = " ".join(str(ref) for ref in item.get("source_refs", [])).casefold()
    action = str(item.get("recommended_action") or "").casefold()
    if action == "review" or "review:" in refs:
        return "due_review"
    mastery_gap = _metric(components.get("learning_gain"))
    if action == "practice" or "mistake" in refs or (
        mastery_gap is not None and mastery_gap >= 0.6
    ):
        return "remediation"
    if action in {"learn", "continue_stage"}:
        return "new_learning"
    return "maintenance"


def _score_trace(
    *,
    item: dict[str, Any],
    task_kind: str,
    estimated_minutes: float,
    target_minutes: float,
    model_intent: bool,
) -> DailyTaskScoreTrace:
    components = item.get("score_components")
    components = components if isinstance(components, dict) else {}
    mastery_gap = _metric(components.get("learning_gain"))
    review_benefit = _metric(components.get("retention_benefit"))
    plan_alignment = _metric(components.get("knowledge_coverage"))
    difficulty_fit = _metric(components.get("difficulty_fit"))
    autonomy_support = _metric(components.get("autonomy_support"))
    repetition = _metric(components.get("repetition_penalty")) or 0.0
    uncertainty = _metric(components.get("uncertainty_risk")) or 0.0
    urgency = {
        "carryover": 1.0,
        "due_review": 1.0,
        "remediation": 0.85,
        "new_learning": 0.65,
        "maintenance": 0.45,
    }[task_kind]
    if model_intent:
        plan_alignment = max(plan_alignment or 0.0, 1.0)
    positives = {
        "mastery_gap": mastery_gap,
        "urgency": urgency,
        "review_benefit": review_benefit,
        "plan_alignment": plan_alignment,
        "difficulty_fit": difficulty_fit,
        "autonomy_support": autonomy_support,
    }
    available = {
        key: value for key, value in positives.items() if value is not None
    }
    denominator = sum(_POSITIVE_WEIGHTS[key] for key in available)
    base = (
        sum(_POSITIVE_WEIGHTS[key] * float(value) for key, value in available.items())
        / denominator
        if denominator
        else 0.0
    )
    time_cost = _clamp(estimated_minutes / max(0.5, target_minutes))
    risk = _clamp(0.45 * repetition + 0.55 * uncertainty)
    score = _clamp(base - _TIME_WEIGHT * time_cost - _RISK_WEIGHT * risk)
    return DailyTaskScoreTrace(
        mastery_gap=mastery_gap,
        urgency=urgency,
        review_benefit=review_benefit,
        plan_alignment=plan_alignment,
        difficulty_fit=difficulty_fit,
        autonomy_support=autonomy_support,
        time_cost=time_cost,
        risk=risk,
        score=round(score, 4),
        missing_positive_components=[
            key for key, value in positives.items() if value is None
        ],
    )


@dataclass(frozen=True)
class _CandidateInput:
    name: str
    kp_id: str
    task_kind: str
    estimated_minutes: float
    model_intent: bool
    source_refs: list[str]
    evidence_refs: list[str]
    source_plan_node: str | None
    path_item: dict[str, Any]


def _max_points(target_minutes: float) -> int:
    if target_minutes <= 20:
        return 1
    if target_minutes <= 40:
        return 2
    if target_minutes <= 75:
        return 3
    return 5


def _knapsack(
    candidates: list[DailyTaskScheduledCandidate],
    *,
    capacity_minutes: float,
    max_items: int,
) -> list[DailyTaskScheduledCandidate]:
    """Deterministic 0/1 knapsack in half-minute units."""

    capacity = max(0, int(math.floor(capacity_minutes * 2 + 1e-9)))
    states: dict[tuple[int, int], tuple[float, tuple[str, ...]]] = {(0, 0): (0.0, ())}
    by_id = {item.candidate_id: item for item in candidates}
    for candidate in sorted(candidates, key=lambda item: item.candidate_id):
        cost = max(1, int(math.ceil(candidate.estimated_minutes * 2 - 1e-9)))
        snapshot = list(states.items())
        for (used, count), (score, chosen) in snapshot:
            if used + cost > capacity or count + 1 > max_items:
                continue
            key = (used + cost, count + 1)
            value = (score + candidate.score_trace.score, chosen + (candidate.candidate_id,))
            current = states.get(key)
            if current is None or value[0] > current[0] + 1e-12 or (
                abs(value[0] - current[0]) <= 1e-12 and value[1] < current[1]
            ):
                states[key] = value
    best = max(
        states.values(),
        key=lambda value: (value[0], len(value[1]), tuple(reversed(value[1]))),
    )
    return [by_id[item_id] for item_id in best[1]]


def build_daily_task_schedule(
    *,
    exam_scope_id: str,
    target_minutes: float,
    learning_chapter: str,
    intent_knowledge_points: list[str],
    review_knowledge_points: list[str] | None = None,
    knowledge_point_resolver: KnowledgePointResolver | None,
    path_candidates: Any = None,
    task_load_policy: dict[str, Any] | None = None,
    previous_schedule: DailyTaskSchedule | dict[str, Any] | None = None,
    scheduling_mode: str = "normal",
) -> DailyTaskSchedule:
    """Select a budget-safe set of formal KPs without asking an LLM to rank them."""

    target = max(1.0, min(1440.0, float(target_minutes)))
    eligible_path, blocked_path = _path_groups(path_candidates)
    intent_names = _unique([str(value or "").strip() for value in intent_knowledge_points])
    intent_set = set(intent_names)
    prerequisite_required = scheduling_mode == "prerequisite_required"
    inputs: dict[str, _CandidateInput] = {}
    blocked: dict[str, DailyTaskBlockedCandidate] = {}

    for item in eligible_path:
        components = item.get("score_components")
        components = components if isinstance(components, dict) else {}
        task_kind = _kind(item, components)
        raw_minutes = item.get("estimated_minutes")
        minutes = (
            float(raw_minutes)
            if isinstance(raw_minutes, (int, float))
            and not isinstance(raw_minutes, bool)
            and float(raw_minutes) > 0
            else 5.0
        )
        for kp in item.get("knowledge_points", []):
            if not isinstance(kp, dict):
                continue
            name = str(kp.get("name") or "").strip()
            kp_id = str(kp.get("kp_id") or "").strip()
            if prerequisite_required and name not in intent_set and kp_id not in intent_set:
                continue
            if not kp_id and name:
                kp_id = _resolve(knowledge_point_resolver, name, learning_chapter) or ""
            if not name or not kp_id:
                continue
            key = f"{kp_id}:{task_kind}"
            candidate = _CandidateInput(
                name=name,
                kp_id=kp_id,
                task_kind=task_kind,
                estimated_minutes=minutes,
                model_intent=name in intent_set or kp_id in intent_set,
                source_refs=_unique([str(ref) for ref in item.get("source_refs", [])]),
                evidence_refs=_unique([str(ref) for ref in item.get("evidence_refs", [])]),
                source_plan_node=str(item.get("candidate_id") or "") or None,
                path_item=item,
            )
            previous = inputs.get(key)
            if previous is None or float(item.get("score") or 0.0) > float(
                previous.path_item.get("score") or 0.0
            ):
                inputs[key] = candidate

    for name in intent_names:
        kp_id = _resolve(knowledge_point_resolver, name, learning_chapter)
        if not kp_id:
            candidate_id = _candidate_id(exam_scope_id, name, "new_learning")
            blocked[candidate_id] = DailyTaskBlockedCandidate(
                candidate_id=candidate_id,
                knowledge_point_name=name,
                reason="知识点无法解析为当前考试空间内的正式知识点，未进入今日任务。",
                source_refs=["compiler:daily_task_intent"],
            )
            continue
        matching_keys = [key for key in inputs if key.startswith(f"{kp_id}:")]
        if matching_keys:
            for key in matching_keys:
                value = inputs[key]
                inputs[key] = _CandidateInput(
                    **{**value.__dict__, "model_intent": True}
                )
            continue
        key = f"{kp_id}:new_learning"
        inputs[key] = _CandidateInput(
            name=name,
            kp_id=kp_id,
            task_kind="new_learning",
            estimated_minutes=min(5.0, target),
            model_intent=True,
            source_refs=["compiler:daily_task_intent"],
            evidence_refs=[f"knowledge_point:{kp_id}"],
            source_plan_node=None,
            path_item={},
        )

    allocation_hint = (task_load_policy or {}).get("allocation")
    allocation_hint = allocation_hint if isinstance(allocation_hint, dict) else {}
    schedule_standalone_reviews = bool(
        float(allocation_hint.get("review_minutes") or 0) > 0 or not intent_names
    )
    for name in (
        _unique(
            [str(value or "").strip() for value in review_knowledge_points or []]
        )
        if schedule_standalone_reviews and not prerequisite_required
        else []
    ):
        kp_id = _resolve(knowledge_point_resolver, name, learning_chapter)
        if not kp_id:
            continue
        key = f"{kp_id}:due_review"
        inputs.setdefault(
            key,
            _CandidateInput(
                name=name,
                kp_id=kp_id,
                task_kind="due_review",
                estimated_minutes=min(5.0, target),
                model_intent=name in intent_set,
                source_refs=["canonical_review_queue"],
                evidence_refs=[f"knowledge_point:{kp_id}"],
                source_plan_node=None,
                path_item={},
            ),
        )

    previous = None
    if previous_schedule:
        try:
            previous = DailyTaskSchedule.model_validate(previous_schedule)
        except (TypeError, ValueError):
            previous = None
    if (
        not prerequisite_required
        and previous is not None
        and previous.exam_scope_id == exam_scope_id
    ):
        for item in previous.deferred:
            key = f"{item.kp_id}:carryover"
            inputs.setdefault(
                key,
                _CandidateInput(
                    name=item.knowledge_point_name,
                    kp_id=item.kp_id,
                    task_kind="carryover",
                    estimated_minutes=item.estimated_minutes,
                    model_intent=False,
                    source_refs=_unique(item.source_refs + ["schedule:deferred_carryover"]),
                    evidence_refs=list(item.evidence_refs),
                    source_plan_node=item.source_plan_node,
                    path_item={},
                ),
            )

    scheduled: list[DailyTaskScheduledCandidate] = []
    for value in inputs.values():
        trace = _score_trace(
            item=value.path_item,
            task_kind=value.task_kind,
            estimated_minutes=value.estimated_minutes,
            target_minutes=target,
            model_intent=value.model_intent,
        )
        scheduled.append(
            DailyTaskScheduledCandidate(
                candidate_id=_candidate_id(exam_scope_id, value.kp_id, value.task_kind),
                knowledge_point_name=value.name,
                kp_id=value.kp_id,
                task_kind=value.task_kind,
                estimated_minutes=value.estimated_minutes,
                required=value.task_kind in {"carryover", "due_review"},
                defer_allowed=True,
                source_plan_node=value.source_plan_node,
                source_refs=value.source_refs,
                evidence_refs=value.evidence_refs,
                score_trace=trace,
                reason={
                    "carryover": "上一轮未能安排的有效任务，优先重新进入候选池。",
                    "remediation": "近期学习证据显示该知识点需要补弱训练。",
                    "due_review": "该知识点已到复习窗口，应优先安排回忆与验证。",
                    "new_learning": "该知识点与当前短期计划和今日学习意图一致。",
                    "maintenance": "用于维持当前阶段已学内容。",
                }[value.task_kind],
            )
        )

    # One knowledge point becomes one coherent task bundle.  Resource and
    # review descriptors for the same KP are evidence alternatives, not two
    # separately publishable assignments.
    best_by_kp: dict[str, DailyTaskScheduledCandidate] = {}
    for item in scheduled:
        previous_item = best_by_kp.get(item.kp_id)
        if previous_item is None or (
            item.required,
            -_KIND_ORDER[item.task_kind],
            item.score_trace.score,
            item.candidate_id,
        ) > (
            previous_item.required,
            -_KIND_ORDER[previous_item.task_kind],
            previous_item.score_trace.score,
            previous_item.candidate_id,
        ):
            best_by_kp[item.kp_id] = item
    scheduled = list(best_by_kp.values())

    # Every blocked path candidate remains observable, but it does not override
    # a formal eligible candidate for the same KP.
    eligible_kp_ids = {item.kp_id for item in scheduled}
    for item in blocked_path:
        for kp in item.get("knowledge_points", []):
            if not isinstance(kp, dict):
                continue
            name = str(kp.get("name") or "").strip()
            kp_id = str(kp.get("kp_id") or "").strip() or None
            if not name or (kp_id and kp_id in eligible_kp_ids):
                continue
            candidate_id = _candidate_id(
                exam_scope_id, kp_id or name, "maintenance"
            )
            blocked.setdefault(
                candidate_id,
                DailyTaskBlockedCandidate(
                    candidate_id=candidate_id,
                    knowledge_point_name=name,
                    kp_id=kp_id,
                    reason="；".join(
                        str(reason)
                        for reason in item.get("blocked_reasons", [])
                        if str(reason).strip()
                    )
                    or "候选未通过每日任务硬约束。",
                    source_refs=_unique(
                        [str(ref) for ref in item.get("source_refs", [])]
                    ),
                ),
            )

    allocation_raw = (task_load_policy or {}).get("allocation")
    allocation_raw = allocation_raw if isinstance(allocation_raw, dict) else {}
    allocation = {
        "new_learning_minutes": float(allocation_raw.get("new_learning_minutes") or 0),
        "review_minutes": float(allocation_raw.get("review_minutes") or 0),
        "remediation_minutes": float(allocation_raw.get("remediation_minutes") or 0),
        "buffer_minutes": float(allocation_raw.get("buffer_minutes") or 0),
    }
    allocation_total = sum(max(0.0, value) for value in allocation.values())
    if allocation_total > target:
        scale = target / allocation_total
        allocation = {key: round(max(0.0, value) * scale, 2) for key, value in allocation.items()}

    selected: list[DailyTaskScheduledCandidate] = []
    remaining = list(scheduled)
    capacity = target
    max_points = _max_points(target)
    required_kinds: list[str] = (
        ["carryover"]
        if any(item.task_kind == "carryover" for item in remaining)
        else []
    )
    if allocation.get("remediation_minutes", 0) > 0:
        required_kinds.append("remediation")
    if allocation.get("review_minutes", 0) > 0:
        required_kinds.append("due_review")
    for task_kind in required_kinds:
        options = [item for item in remaining if item.task_kind == task_kind]
        options.sort(key=lambda item: (-item.score_trace.score, item.candidate_id))
        if options and options[0].estimated_minutes <= capacity and len(selected) < max_points:
            chosen = options[0]
            selected.append(chosen)
            remaining.remove(chosen)
            capacity -= chosen.estimated_minutes

    selected.extend(
        _knapsack(
            remaining,
            capacity_minutes=capacity,
            max_items=max(0, max_points - len(selected)),
        )
    )
    selected_ids = {item.candidate_id for item in selected}
    deferred = [item for item in scheduled if item.candidate_id not in selected_ids]
    selected.sort(
        key=lambda item: (
            _KIND_ORDER[item.task_kind],
            -item.score_trace.score,
            item.candidate_id,
        )
    )
    deferred.sort(key=lambda item: (-item.score_trace.score, item.candidate_id))

    if selected:
        selected_text = "、".join(item.knowledge_point_name for item in selected)
        explanation = f"系统按当前计划、掌握缺口、到期复习和可用时间安排：{selected_text}。"
    else:
        explanation = "当前没有同时满足路线、证据、资源和时间约束的知识点任务。"
    if deferred:
        explanation += " 因今日时间或负荷限制，" + "、".join(
            item.knowledge_point_name for item in deferred[:5]
        ) + "已顺延，后续刷新时会重新校验并优先考虑。"

    state_digest = None
    if isinstance(path_candidates, dict):
        raw_digest = path_candidates.get("state_digest")
        state_digest = str(raw_digest) if raw_digest else None
    return DailyTaskSchedule(
        exam_scope_id=exam_scope_id,
        state_digest=state_digest,
        target_minutes=target,
        allocation=allocation,
        selected=selected,
        deferred=deferred,
        blocked=list(blocked.values()),
        explanation=explanation,
    )


def reconcile_daily_task_schedule(
    schedule: DailyTaskSchedule,
    *,
    materialized_kp_ids: set[str],
) -> DailyTaskSchedule:
    """Move scheduler selections rejected by the final resource gate to defer."""

    removed = [
        item for item in schedule.selected if item.kp_id not in materialized_kp_ids
    ]
    if not removed:
        return schedule
    retained = [
        item for item in schedule.selected if item.kp_id in materialized_kp_ids
    ]
    deferred = {item.candidate_id: item for item in schedule.deferred}
    for item in removed:
        deferred[item.candidate_id] = item.model_copy(
            update={
                "reason": (
                    "该任务通过知识点调度，但真实资源原子未能在今日预算内完整落地，"
                    "已由最终预算门禁顺延。"
                )
            }
        )
    return schedule.model_copy(
        update={
            "selected": retained,
            "deferred": list(deferred.values()),
            "explanation": (
                schedule.explanation
                + " 最终资源门禁又顺延了："
                + "、".join(item.knowledge_point_name for item in removed)
                + "。"
            ),
        }
    )
