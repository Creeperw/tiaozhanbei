from __future__ import annotations

from typing import Any

from competition_app.contracts.learning_plan import LearningPlanResult


def build_plan_progress(
    plans: LearningPlanResult | None,
    *,
    daily_task_progress: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Project plan prose, structured fields and executable acceptance gates."""

    if plans is None:
        return {
            "schema_version": "1.0",
            "long_term": None,
            "short_term": None,
            "daily_task": None,
        }

    long_plan = plans.long_term_plan
    short_plan = plans.short_term_plan
    task = plans.learning_task
    progress = daily_task_progress if isinstance(daily_task_progress, dict) else {}
    task_status = str(progress.get("status") or (task.status if task else "") or "")
    task_completed = task_status == "completed"

    return {
        "schema_version": "1.0",
        "long_term": _long_term_progress(long_plan),
        "short_term": (
            {
                "plan_id": short_plan.plan_id,
                "version": short_plan.version,
                "status": short_plan.status,
                "content": short_plan.content,
                "structured": {
                    "planning_route": _dump(short_plan.planning_route),
                    "goal_contract": _dump(short_plan.goal_contract),
                    "short_term_learning_package": _dump(
                        short_plan.short_term_learning_package
                    ),
                    "recovery_policy": _dump(short_plan.recovery_policy),
                    "recommendation_trace": _dump(short_plan.recommendation_trace),
                    "short_term_focus": _dump(short_plan.short_term_focus),
                    "textbook_selection": _dump(short_plan.textbook_selection),
                },
                "acceptance_gate": {
                    "criterion": (
                        short_plan.short_term_learning_package.completion_criteria
                        if short_plan.short_term_learning_package is not None
                        else None
                    ),
                    "status": (
                        "passed"
                        if short_plan.status == "completed"
                        else "in_progress"
                    ),
                    "automatic_pass": False,
                    "reason": (
                        "短期计划需汇总周期内任务与验收证据，单个今日任务完成不会自动通过整个短期计划。"
                    ),
                },
            }
            if short_plan is not None
            else None
        ),
        "daily_task": (
            {
                **task.model_dump(mode="json"),
                "execution_progress": progress,
                "acceptance_gate": {
                    "criterion": task.completion_criteria,
                    "status": "passed" if task_completed else "in_progress",
                    "required_item_count": len(task.items),
                    "completed_item_count": int(
                        progress.get("completed_items")
                        or (len(task.items) if task_completed else 0)
                    ),
                    "pass_rule": "all_atomic_items_completed",
                },
            }
            if task is not None
            else None
        ),
    }


def _long_term_progress(plan: Any) -> dict[str, Any] | None:
    if plan is None:
        return None
    phases = list(plan.planning_route.phases) if plan.planning_route is not None else []
    textbook_stages = []
    if (
        plan.planning_route is not None
        and plan.planning_route.textbook_route is not None
        and plan.planning_route.textbook_route.route is not None
    ):
        textbook_stages = list(plan.planning_route.textbook_route.route.stages)
    evidence = list(plan.stage_evidence)
    current_stage_number = _current_stage_number(plan)
    stage_rows: list[dict[str, Any]] = []
    authoritative_stages = textbook_stages or phases
    stage_count = max(len(plan.stages), len(authoritative_stages))
    for index in range(1, stage_count + 1):
        stage = plan.stages[index - 1] if index <= len(plan.stages) else None
        phase = phases[index - 1] if index <= len(phases) else None
        textbook_stage = (
            textbook_stages[index - 1]
            if index <= len(textbook_stages)
            else None
        )
        stage_number = stage.stage if stage is not None else index
        requirements = (
            [str(item) for item in textbook_stage.exit_evidence]
            if textbook_stage is not None
            else [str(item) for item in phase.exit_evidence]
            if phase is not None
            else (
                [str(item) for item in plan.milestones[index - 1].evidence_required]
                if index <= len(plan.milestones)
                else []
            )
        )
        verified = {
            item.requirement
            for item in evidence
            if item.stage == stage_number
        }
        indicators = [
            {
                "indicator_id": f"stage-{stage_number}-evidence-{position}",
                "description": requirement,
                "status": "satisfied" if requirement in verified else "pending",
                "evidence_refs": [
                    {
                        "source_type": item.source_type,
                        "source_id": item.source_id,
                        "verified_by": item.verified_by,
                        "verified_at": item.verified_at.isoformat(),
                    }
                    for item in evidence
                    if item.stage == stage_number
                    and item.requirement == requirement
                ],
            }
            for position, requirement in enumerate(requirements, start=1)
        ]
        passed = bool(indicators) and all(
            item["status"] == "satisfied" for item in indicators
        )
        stage_rows.append(
            {
                "stage": stage_number,
                "name": (
                    textbook_stage.name
                    if textbook_stage is not None
                    else phase.name
                    if phase is not None
                    else f"阶段{stage_number}"
                ),
                "books": (
                    list(textbook_stage.books)
                    if textbook_stage is not None
                    else list(stage.book)
                    if stage is not None
                    else list(phase.books)
                    if phase is not None
                    else []
                ),
                "goal": (
                    textbook_stage.objective
                    if textbook_stage is not None
                    else stage.goal
                    if stage is not None
                    else phase.objective
                    if phase is not None
                    else ""
                ),
                "status": (
                    "passed"
                    if passed
                    else "in_progress"
                    if stage_number == current_stage_number
                    else "previous"
                    if stage_number < current_stage_number
                    else "locked"
                ),
                "passed": passed,
                "can_advance": passed and stage_number == current_stage_number,
                "pass_rule": "all_exit_evidence_verified",
                "indicators": indicators,
            }
        )
    return {
        "plan_id": plan.plan_id,
        "version": plan.version,
        "status": plan.status,
        "content": plan.content,
        "structured": {
            "stages": [item.model_dump(mode="json") for item in plan.stages],
            "planning_route": _dump(plan.planning_route),
            "goal_contract": _dump(plan.goal_contract),
            "milestones": [item.model_dump(mode="json") for item in plan.milestones],
            "recovery_policy": _dump(plan.recovery_policy),
            "recommendation_trace": _dump(plan.recommendation_trace),
            "assumptions": list(plan.assumptions),
            "unknowns_to_confirm": list(plan.unknowns_to_confirm),
            "textbook_selection": _dump(plan.textbook_selection),
        },
        "stage_progress": stage_rows,
        "progression_policy": "阶段全部通过指标取得服务端核验证据后，才允许进入下一阶段。",
    }


def _dump(value: Any) -> Any:
    if value is None:
        return None
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    return value


def _current_stage_number(plan: Any) -> int:
    selection = plan.textbook_selection
    route = plan.planning_route
    if selection is None or route is None:
        return 1
    selected_stage_id = selection.stage_id
    textbook_route = route.textbook_route
    if textbook_route is not None and textbook_route.route is not None:
        for item in textbook_route.route.stages:
            if item.stage_id == selected_stage_id:
                return item.order
    for index, phase in enumerate(route.phases, start=1):
        if phase.phase_id == selected_stage_id:
            return index
    return 1
