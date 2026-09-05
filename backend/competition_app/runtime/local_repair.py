from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from typing import Any, Literal

from competition_app.contracts.execution import ExecutionPlan
from competition_app.contracts.local_repair import LocalRepairPlan, RepairAction, RepairIssue


IssueType = Literal[
    "missing_evidence",
    "conflicting_evidence",
    "learner_mismatch",
    "route_or_prerequisite_error",
    "content_quality",
    "paper_blueprint_mismatch",
    "plan_quality",
    "plan_contract_invalid",
    "plan_parent_constraint",
    "question_pool_insufficient",
    "paper_item_invalid",
    "answer_or_explanation_invalid",
    "safety_violation",
    "unresolved",
]


class LocalRepairController:
    """Build one bounded, whitelisted repair pass without running any step."""

    _APPROVED_REPAIR_STEP_IDS = frozenset(
        {
            "memory",
            "knowledge",
            "route_resolution",
            "diagnosis",
            "learning_plan",
            "schedule",
            "expert",
            "paper_blueprint",
            "question_pool",
            "paper_assembly",
        }
    )
    _APPROVED_AUDIT_ACTIONS = frozenset({"audit", "review_exam_paper"})
    _DEFAULT_TARGETS: dict[IssueType, str] = {
        "missing_evidence": "expert",
        "conflicting_evidence": "expert",
        "factual_error": "expert",
        "learner_mismatch": "expert",
        "route_or_prerequisite_error": "expert",
        "content_quality": "paper_assembly",
        "paper_blueprint_mismatch": "paper_assembly",
        "plan_quality": "diagnosis",
        "plan_contract_invalid": "diagnosis",
        "plan_parent_constraint": "diagnosis",
        "question_pool_insufficient": "paper_assembly",
        "paper_item_invalid": "paper_assembly",
        "answer_or_explanation_invalid": "paper_assembly",
        "safety_violation": "",
        "unresolved": "",
    }
    _ALLOWED_TARGETS: dict[IssueType, frozenset[str]] = {
        "missing_evidence": frozenset({"knowledge", "expert", "paper_assembly"}),
        "conflicting_evidence": frozenset({"knowledge", "expert", "paper_assembly"}),
        "factual_error": frozenset({"expert", "paper_assembly"}),
        "learner_mismatch": frozenset({"learning_plan", "schedule", "expert", "paper_assembly"}),
        "route_or_prerequisite_error": frozenset({"learning_plan", "schedule", "expert", "paper_assembly"}),
        "content_quality": frozenset({"expert", "paper_assembly"}),
        "paper_blueprint_mismatch": frozenset({"paper_assembly"}),
        "plan_quality": frozenset({"diagnosis"}),
        "plan_contract_invalid": frozenset({"diagnosis"}),
        "plan_parent_constraint": frozenset({"diagnosis"}),
        "question_pool_insufficient": frozenset({"paper_assembly"}),
        "paper_item_invalid": frozenset({"paper_assembly"}),
        "answer_or_explanation_invalid": frozenset({"paper_assembly"}),
        "safety_violation": frozenset(),
        "unresolved": frozenset(),
    }

    def plan_repair(
        self,
        *,
        plan: ExecutionPlan,
        audit_step_id: str,
        audit_findings: Sequence[str],
        outputs: Mapping[str, Any],
        structured_findings: Sequence[RepairIssue] | None = None,
    ) -> LocalRepairPlan:
        """Return a deterministic repair plan, or fail closed for unsafe input."""
        issues = list(structured_findings) if structured_findings else self._classify(audit_findings)
        execution_id = self._execution_id(outputs, plan.plan_id)
        repair_id = f"repair:{execution_id}:{audit_step_id}"

        try:
            plan.validate_dag()
        except ValueError:
            return self._human_review_plan(
                repair_id=repair_id,
                execution_id=execution_id,
                audit_step_id=audit_step_id,
                issues=issues,
            )

        if not issues or any(
            issue.issue_type in {"unresolved", "safety_violation"}
            for issue in issues
        ):
            return self._human_review_plan(
                repair_id=repair_id,
                execution_id=execution_id,
                audit_step_id=audit_step_id,
                issues=issues,
            )

        steps_by_id = {step.step_id: step for step in plan.steps}
        if (
            audit_step_id not in steps_by_id
            or not self._is_audit_step(steps_by_id[audit_step_id])
        ):
            return self._human_review_plan(
                repair_id=repair_id,
                execution_id=execution_id,
                audit_step_id=audit_step_id,
                issues=issues,
            )

        chains = [
            self._chain_for(issue, audit_step_id, step_ids=frozenset(steps_by_id))
            for issue in issues
        ]
        if any(chain is None for chain in chains):
            return self._human_review_plan(
                repair_id=repair_id,
                execution_id=execution_id,
                audit_step_id=audit_step_id,
                issues=issues,
            )
        resolved_chains = [chain for chain in chains if chain is not None]
        if not set().union(*resolved_chains).issubset(steps_by_id):
            return self._human_review_plan(
                repair_id=repair_id,
                execution_id=execution_id,
                audit_step_id=audit_step_id,
                issues=issues,
            )
        merged = self._merge_chains(
            resolved_chains,
            plan,
            available_outputs=frozenset(outputs),
        )
        if merged is None:
            return self._human_review_plan(
                repair_id=repair_id,
                execution_id=execution_id,
                audit_step_id=audit_step_id,
                issues=issues,
            )
        selected_steps, dependency_steps = merged
        if audit_step_id not in selected_steps or not selected_steps or not selected_steps[-1] == audit_step_id:
            return self._human_review_plan(
                repair_id=repair_id,
                execution_id=execution_id,
                audit_step_id=audit_step_id,
                issues=issues,
            )
        if not set(selected_steps).issubset(steps_by_id):
            return self._human_review_plan(
                repair_id=repair_id,
                execution_id=execution_id,
                audit_step_id=audit_step_id,
                issues=issues,
            )

        preserve_outputs = sorted(set(outputs) - set(selected_steps))
        issues_by_step = {
            step_id: [
                issue
                for issue, chain in zip(issues, resolved_chains)
                if step_id in chain
            ]
            for step_id in selected_steps
        }
        actions = [
            RepairAction(
                action_id=f"rerun:{step_id}",
                action_type="rerun",
                operation=self._repair_operation(
                    step_id, issues_by_step[step_id], audit_step_id=audit_step_id
                ),
                step_id=step_id,
                reason="；".join(
                    dict.fromkeys(issue.message for issue in issues_by_step[step_id])
                ),
                depends_on=[f"rerun:{dependency}" for dependency in dependency_steps[step_id]],
                preserve_outputs=preserve_outputs,
                issue_ids=list(
                    dict.fromkeys(issue.issue_id for issue in issues_by_step[step_id])
                ),
                locations=self._locations_for(issues_by_step[step_id]),
                scope_unit_ids=self._scope_ids(
                    issues_by_step[step_id], "paper:unit:"
                ),
                scope_question_ids=self._question_scope_ids(
                    issues_by_step[step_id]
                ),
                scope_field_paths=self._field_scope_paths(
                    issues_by_step[step_id]
                ),
                repair_instruction=self._repair_instruction(
                    step_id, issues_by_step[step_id], audit_step_id=audit_step_id
                ),
                previous_output_digest=self._output_digest(outputs.get(step_id)),
            )
            for step_id in selected_steps
        ]
        return LocalRepairPlan(
            repair_id=repair_id,
            execution_id=execution_id,
            trigger_step_id=audit_step_id,
            issues=issues,
            actions=actions,
            status="planned",
        )

    @staticmethod
    def _repair_operation(
        step_id: str,
        issues: Sequence[RepairIssue],
        *,
        audit_step_id: str,
    ) -> str:
        if step_id == audit_step_id:
            return "reaudit"
        location_types = {
            location.location_type
            for issue in issues
            for location in issue.locations
        }
        issue_types = {issue.issue_type for issue in issues}
        if location_types == {"explanation"}:
            return "repair_explanation"
        if location_types and location_types.issubset({"answer_key", "explanation"}):
            return "repair_answer"
        if "question" in location_types:
            return "replace_question"
        if "question_pool_insufficient" in issue_types or "unit" in location_types:
            return "fill_unit_gap"
        if step_id == "paper_assembly" and issue_types == {"paper_blueprint_mismatch"}:
            return "recompute_summary"
        return "rerun_step"

    @staticmethod
    def _scope_ids(issues: Sequence[RepairIssue], prefix: str) -> list[str]:
        return list(
            dict.fromkeys(
                location.location_key.removeprefix(prefix)
                for issue in issues
                for location in issue.locations
                if location.location_key.startswith(prefix)
            )
        )

    @staticmethod
    def _question_scope_ids(issues: Sequence[RepairIssue]) -> list[str]:
        prefixes = ("paper:question:", "paper:answer:", "paper:explanation:")
        return list(
            dict.fromkeys(
                location.location_key.removeprefix(prefix)
                for issue in issues
                for location in issue.locations
                for prefix in prefixes
                if location.location_key.startswith(prefix)
            )
        )

    @staticmethod
    def _field_scope_paths(issues: Sequence[RepairIssue]) -> list[str]:
        paths: list[str] = []
        for issue in issues:
            for location in issue.locations:
                key = location.location_key
                if key.startswith("paper:answer:"):
                    paths.append(f"answer_key.{key.removeprefix('paper:answer:')}")
                elif key.startswith("paper:explanation:"):
                    paths.append(
                        f"explanations.{key.removeprefix('paper:explanation:')}"
                    )
        return list(dict.fromkeys(paths))

    def _classify(self, findings: Sequence[str]) -> list[RepairIssue]:
        classified: list[RepairIssue] = []
        seen: set[str] = set()
        for finding in findings:
            message = str(finding).strip()
            if not message or message in seen:
                continue
            seen.add(message)
            issue_type, owner_step_id = self._classify_message(message)
            classified.append(
                RepairIssue(
                    issue_id=f"legacy-{len(classified) + 1}",
                    issue_type=issue_type,
                    message=message,
                    owner_step_id=owner_step_id,
                )
            )
        return classified

    @staticmethod
    def _classify_message(message: str) -> tuple[IssueType, str | None]:
        if any(keyword in message for keyword in ("蓝图", "偏离蓝图", "成卷")):
            return "paper_blueprint_mismatch", "paper_assembly"
        if any(keyword in message for keyword in ("冲突", "矛盾")) and "证据" in message:
            return "conflicting_evidence", "expert"
        if "证据" in message and any(keyword in message for keyword in ("缺少", "缺失", "没有", "不足")):
            return "missing_evidence", "expert"
        if any(keyword in message for keyword in ("掌握状态", "学情", "学习者", "用户掌握")):
            return "learner_mismatch", "expert"
        if any(keyword in message for keyword in ("前置", "先修", "路由", "路线", "路径")):
            return "route_or_prerequisite_error", "expert"
        if any(keyword in message for keyword in ("表达不清", "题目内容", "题干", "内容质量")):
            return "content_quality", "paper_assembly"
        return "unresolved", None

    def _chain_for(
        self,
        issue: RepairIssue,
        audit_step_id: str,
        *,
        step_ids: frozenset[str],
    ) -> tuple[str, ...] | None:
        issue_type = issue.issue_type
        if issue_type == "unresolved":
            return None
        target = self._affected_target(issue)
        if target is None:
            return None
        if issue_type in {"missing_evidence", "conflicting_evidence", "factual_error"}:
            if target == "knowledge":
                if "expert" not in step_ids:
                    return None
                return ("knowledge", "expert", audit_step_id)
            # A system-located Expert fault does not require refreshing a
            # healthy EvidencePack.  Legacy findings have no origin and keep
            # the conservative upstream rerun for backward compatibility.
            if target == "expert" and issue.origin_step_id == "expert":
                return ("expert", audit_step_id)
            evidence_step_id = (
                "question_pool"
                if target == "paper_assembly" and "question_pool" in step_ids
                else "knowledge"
            )
            return (evidence_step_id, target, audit_step_id)
        if issue_type == "learner_mismatch":
            return ("diagnosis", target, audit_step_id)
        if issue_type == "route_or_prerequisite_error":
            return ("route_resolution", "diagnosis", target, audit_step_id)
        if issue_type == "content_quality":
            return (target, audit_step_id)
        if issue_type == "paper_blueprint_mismatch":
            # A mismatch already bound to one or more concrete questions is a
            # selection fault, not a blueprint-authoring fault. Reuse the
            # frozen blueprint and candidate pool, replace only those items,
            # then re-audit the complete paper. Whole-paper/unit-level scope
            # still keeps the conservative upstream refresh path.
            if any(
                location.location_type == "question"
                for location in issue.locations
            ):
                return ("paper_assembly", audit_step_id)
            return (
                "paper_blueprint",
                "question_pool",
                "paper_assembly",
                audit_step_id,
            )
        if issue_type in {
            "plan_quality",
            "plan_contract_invalid",
            "plan_parent_constraint",
        }:
            return ("diagnosis", audit_step_id)
        if issue_type == "question_pool_insufficient":
            return ("question_pool", "paper_assembly", audit_step_id)
        if issue_type in {"paper_item_invalid", "answer_or_explanation_invalid"}:
            return ("paper_assembly", audit_step_id)
        return None

    def _affected_target(self, issue: RepairIssue) -> str | None:
        candidates = [*issue.affected_step_ids, issue.owner_step_id, self._DEFAULT_TARGETS[issue.issue_type]]
        allowed = self._ALLOWED_TARGETS[issue.issue_type]
        for candidate in candidates:
            if candidate in allowed:
                return candidate
        return None

    @staticmethod
    def _merge_chains(
        chains: Sequence[tuple[str, ...]],
        plan: ExecutionPlan,
        *,
        available_outputs: frozenset[str],
    ) -> tuple[list[str], dict[str, list[str]]] | None:
        plan_order = {step.step_id: index for index, step in enumerate(plan.steps)}
        steps_by_id = {step.step_id: step for step in plan.steps}
        dependencies: dict[str, set[str]] = {}
        for chain in chains:
            for step_id in chain:
                dependencies.setdefault(step_id, set())
            for dependency, step_id in zip(chain, chain[1:]):
                dependencies[step_id].add(dependency)

        pending = list(dependencies)
        while pending:
            step_id = pending.pop()
            for dependency in steps_by_id[step_id].depends_on:
                if dependency in available_outputs and dependency not in dependencies:
                    continue
                dependencies[step_id].add(dependency)
                if dependency not in dependencies:
                    dependencies[dependency] = set()
                    pending.append(dependency)

        permitted_steps = LocalRepairController._APPROVED_REPAIR_STEP_IDS | {
            chain[-1] for chain in chains
        }
        if not set(dependencies).issubset(permitted_steps):
            return None

        ordered: list[str] = []
        remaining = {step_id: set(required) for step_id, required in dependencies.items()}
        while remaining:
            ready = sorted(
                (step_id for step_id, required in remaining.items() if not required),
                key=lambda step_id: (step_id == chains[0][-1], plan_order.get(step_id, len(plan_order)), step_id),
            )
            if not ready:
                return None
            for step_id in ready:
                ordered.append(step_id)
                del remaining[step_id]
            for required in remaining.values():
                required.difference_update(ready)

        direct_dependencies = {
            step_id: sorted(dependencies[step_id], key=lambda item: ordered.index(item))
            for step_id in ordered
        }
        return ordered, direct_dependencies

    @staticmethod
    def _is_audit_step(step: Any) -> bool:
        return (
            step.agent == "audit_agent"
            or (step.action or "").lower()
            in LocalRepairController._APPROVED_AUDIT_ACTIONS
        )

    @staticmethod
    def _execution_id(outputs: Mapping[str, Any], fallback: str) -> str:
        for output in outputs.values():
            execution_id = getattr(output, "execution_id", None)
            if execution_id:
                return str(execution_id)
            if isinstance(output, Mapping) and output.get("execution_id"):
                return str(output["execution_id"])
        return fallback

    @staticmethod
    def _locations_for(issues: Sequence[RepairIssue]):
        locations = []
        seen: set[str] = set()
        for issue in issues:
            for location in issue.locations:
                if location.location_key in seen:
                    continue
                seen.add(location.location_key)
                locations.append(location)
        return locations[:8]

    @staticmethod
    def _repair_instruction(
        step_id: str,
        issues: Sequence[RepairIssue],
        *,
        audit_step_id: str,
    ) -> str:
        if step_id == audit_step_id:
            return (
                "请对返修后的完整产物重新执行全部确定性门禁和语义审核；"
                "不得沿用上一轮通过结论。"
            )
        details = []
        for issue in issues:
            labels = "、".join(
                location.display_label for location in issue.locations
            ) or "当前产物"
            details.append(f"{labels}：{issue.message}")
        return (
            "只修正以下已定位问题，保留其他已经通过的内容和有效依赖，不扩大修改范围："
            + "；".join(dict.fromkeys(details))
        )[:4_000]

    @staticmethod
    def _output_digest(value: Any) -> str | None:
        if value is None:
            return None
        if hasattr(value, "model_dump"):
            value = value.model_dump(mode="json")
        try:
            raw = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
        except TypeError:
            raw = str(value)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    @staticmethod
    def _human_review_plan(
        *,
        repair_id: str,
        execution_id: str,
        audit_step_id: str,
        issues: list[RepairIssue],
    ) -> LocalRepairPlan:
        return LocalRepairPlan(
            repair_id=repair_id,
            execution_id=execution_id,
            trigger_step_id=audit_step_id,
            issues=issues,
            actions=[],
            status="needs_human_review",
        )
