from __future__ import annotations

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
    "unresolved",
]


class LocalRepairController:
    """Build one bounded, whitelisted repair pass without running any step."""

    _DEFAULT_TARGETS: dict[IssueType, str] = {
        "missing_evidence": "expert",
        "conflicting_evidence": "expert",
        "learner_mismatch": "expert",
        "route_or_prerequisite_error": "expert",
        "content_quality": "paper_assembly",
        "paper_blueprint_mismatch": "paper_assembly",
        "unresolved": "",
    }
    _ALLOWED_TARGETS: dict[IssueType, frozenset[str]] = {
        "missing_evidence": frozenset({"expert", "paper_assembly"}),
        "conflicting_evidence": frozenset({"expert", "paper_assembly"}),
        "learner_mismatch": frozenset({"learning_plan", "schedule", "expert", "paper_assembly"}),
        "route_or_prerequisite_error": frozenset({"learning_plan", "schedule", "expert", "paper_assembly"}),
        "content_quality": frozenset({"expert", "paper_assembly"}),
        "paper_blueprint_mismatch": frozenset({"paper_assembly"}),
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

        if not issues or any(issue.issue_type == "unresolved" for issue in issues):
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

        chains = [self._chain_for(issue, audit_step_id) for issue in issues]
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
        selected_steps, dependency_steps = self._merge_chains(resolved_chains, plan)
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
        reasons = {
            step_id: [issue.message for issue, chain in zip(issues, resolved_chains) if step_id in chain]
            for step_id in selected_steps
        }
        actions = [
            RepairAction(
                action_id=f"rerun:{step_id}",
                action_type="rerun",
                step_id=step_id,
                reason="；".join(dict.fromkeys(reasons[step_id])),
                depends_on=[f"rerun:{dependency}" for dependency in dependency_steps[step_id]],
                preserve_outputs=preserve_outputs,
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

    def _chain_for(self, issue: RepairIssue, audit_step_id: str) -> tuple[str, ...] | None:
        issue_type = issue.issue_type
        if issue_type == "unresolved":
            return None
        target = self._affected_target(issue)
        if target is None:
            return None
        if issue_type in {"missing_evidence", "conflicting_evidence"}:
            return ("knowledge", target, audit_step_id)
        if issue_type == "learner_mismatch":
            return ("diagnosis", target, audit_step_id)
        if issue_type == "route_or_prerequisite_error":
            return ("route_resolution", "diagnosis", target, audit_step_id)
        if issue_type == "content_quality":
            return (target, audit_step_id)
        if issue_type == "paper_blueprint_mismatch":
            return ("paper_blueprint", "knowledge", "paper_assembly", audit_step_id)
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
        chains: Sequence[tuple[str, ...]], plan: ExecutionPlan
    ) -> tuple[list[str], dict[str, list[str]]]:
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
                dependencies[step_id].add(dependency)
                if dependency not in dependencies:
                    dependencies[dependency] = set()
                    pending.append(dependency)

        ordered: list[str] = []
        remaining = {step_id: set(required) for step_id, required in dependencies.items()}
        while remaining:
            ready = sorted(
                (step_id for step_id, required in remaining.items() if not required),
                key=lambda step_id: (step_id == chains[0][-1], plan_order.get(step_id, len(plan_order)), step_id),
            )
            if not ready:
                return [], {}
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
        return step.agent == "audit_agent" or "audit" in (step.action or "").lower()

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
