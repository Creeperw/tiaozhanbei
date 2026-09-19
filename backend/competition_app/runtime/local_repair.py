from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from typing import Any, Literal

from competition_app.contracts.execution import ExecutionPlan
from competition_app.contracts.local_repair import LocalRepairPlan, RepairAction, RepairIssue
from competition_app.contracts.resource import AuditResult


PUBLISHED_AFTER_REPAIR_NOTE = (
    "已完成一轮受控返修，剩余问题不再阻断发布，已记入失败案例库。"
)


def publishable_after_bounded_repair(output: Any, decision: str | None) -> Any:
    """Normalize a post-repair audit output so it can be released.

    The product contract has exactly two terminal decisions: ``pass`` releases
    the content and ``revise`` sends it through one bounded repair round.  Once
    that round has run, running it again would rerun the same nodes and cannot
    converge, so the repaired content is released and the residual findings are
    demoted to non-blocking advice.

    Blocking findings are the exception.  A repair round is an attempt, not a
    guarantee: the content nodes may be deterministic (paper assembly selects
    from a frozen candidate pool), so the rerun can reproduce the very content
    the auditor rejected.  Rewriting ``decision`` to ``pass`` in that case
    publishes content the auditor refused, and leaves the published payload
    contradicting the audit's own verdict on the repair trace.  The paper
    blueprint's own acceptance criteria state 「审核未通过时不得发布正式试卷」,
    so a blocking finding keeps the verdict ``revise`` and the caller must not
    publish.  ``unresolved`` (protocol failure, unlocatable compiler output) is
    a red-line blocking type: an auditor that never produced a verdict is not
    evidence that the content is safe.

    Only the publication-facing payload is normalized: the audit's own verdict
    is already recorded on the repair trace and reaches the failure library
    from there.  Outputs that are already publishable are returned unchanged.
    """
    payload = getattr(output, "payload", None)
    if decision == "pass" or not isinstance(payload, AuditResult):
        return output
    if not getattr(payload, "semantic_verdict_available", True):
        # 审核模型输出不符合协议：这次审核没有形成语义结论，``decision``
        # 只反映确定性硬门禁。审核器失效不构成内容安全的证据，不能据此
        # 把内容当作“已通过审核”放行。
        return output
    blocking_findings = [
        finding
        for finding in (payload.structured_findings or [])
        if bool(getattr(finding, "blocking", False))
    ]
    if blocking_findings:
        return output
    report = str(getattr(payload, "audit_report", "") or "").strip()
    if PUBLISHED_AFTER_REPAIR_NOTE not in report:
        report = f"{report} {PUBLISHED_AFTER_REPAIR_NOTE}".strip()
    findings = [
        finding
        if str(finding).startswith("非阻断建议：")
        else f"非阻断建议：{finding}"
        for finding in (payload.findings or [])
    ]
    normalized = payload.model_copy(
        update={
            "decision": "pass",
            "audit_report": report[:8_000],
            "findings": findings,
        }
    )
    return output.model_copy(update={"payload": normalized})


IssueType = Literal[
    "factual_error",
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
    # Diagnosis authors and revises a learning plan, and
    # AuditIssueResolver.resolve_responsibility assigns every plan-local finding
    # to it whatever label the prose Compiler attached.  This whitelist must
    # admit Diagnosis for those labels as well: a plan-local factual_error or
    # content_quality finding arrives with owner_step_id="diagnosis", and
    # rejecting it here silently fell through to the Expert/PaperAssembly
    # default, whose steps do not exist in a planning DAG, so an actionable
    # revise was escalated to human review without any repair running.
    # Diagnosis deliberately stays out of _DEFAULT_TARGETS: only a
    # system-attributed owner selects it, so resource and paper workflows keep
    # their existing defaults.
    _ALLOWED_TARGETS: dict[IssueType, frozenset[str]] = {
        "missing_evidence": frozenset({"knowledge", "expert", "paper_assembly", "diagnosis"}),
        "conflicting_evidence": frozenset({"knowledge", "expert", "paper_assembly", "diagnosis"}),
        "factual_error": frozenset({"expert", "paper_assembly", "diagnosis"}),
        "learner_mismatch": frozenset({"learning_plan", "schedule", "expert", "paper_assembly", "diagnosis"}),
        "route_or_prerequisite_error": frozenset({"learning_plan", "schedule", "expert", "paper_assembly", "diagnosis"}),
        "content_quality": frozenset({"expert", "paper_assembly", "diagnosis"}),
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
        execution_id = self._execution_id(outputs, plan.plan_id)
        repair_id = f"repair:{execution_id}:{audit_step_id}"
        issues = list(structured_findings) if structured_findings else self._classify(audit_findings)
        if not issues:
            # 审核给出非 pass 结论却没有可定位的问题（模型判 reject 但未逐条
            # 列出，或编译器未能定位）。产品约定只有发布与返修两种终态，不能
            # 停在等待人工：构造兜底问题重跑内容生产节点，既不放弃发布，也不
            # 放行未经审核的内容。
            fallback = self._fallback_issue(plan)
            if fallback is not None:
                issues = [fallback]
        repaired = self._build_plan(
            plan=plan,
            audit_step_id=audit_step_id,
            issues=issues,
            outputs=outputs,
            repair_id=repair_id,
            execution_id=execution_id,
        )
        if repaired is not None:
            return repaired
        # 结构性失败：送来的问题都无法绑定到可返修节点（定位目录与 DAG 不匹配、
        # 返修链合并失败等）。用兜底内容节点重跑一次，而不是停在等待人工。
        fallback = self._fallback_issue(plan)
        if fallback is not None:
            repaired = self._build_plan(
                plan=plan,
                audit_step_id=audit_step_id,
                issues=[fallback],
                outputs=outputs,
                repair_id=repair_id,
                execution_id=execution_id,
            )
            if repaired is not None:
                return repaired
        return self._human_review_plan(
            repair_id=repair_id,
            execution_id=execution_id,
            audit_step_id=audit_step_id,
            issues=issues,
        )

    def _build_plan(
        self,
        *,
        plan: ExecutionPlan,
        audit_step_id: str,
        issues: list[RepairIssue],
        outputs: Mapping[str, Any],
        repair_id: str,
        execution_id: str,
    ) -> LocalRepairPlan | None:
        """Build one bounded repair plan, or return None when none is safe."""
        if not issues:
            return None
        try:
            plan.validate_dag()
        except ValueError:
            return None

        steps_by_id = {step.step_id: step for step in plan.steps}
        if (
            audit_step_id not in steps_by_id
            or not self._is_audit_step(steps_by_id[audit_step_id])
        ):
            return None

        chains = [
            self._chain_for(issue, audit_step_id, step_ids=frozenset(steps_by_id))
            for issue in issues
        ]
        if any(chain is None for chain in chains):
            return None
        resolved_chains = [chain for chain in chains if chain is not None]
        if not set().union(*resolved_chains).issubset(steps_by_id):
            return None
        merged = self._merge_chains(
            resolved_chains,
            plan,
            available_outputs=frozenset(outputs),
        )
        if merged is None:
            return None
        selected_steps, dependency_steps = merged
        if audit_step_id not in selected_steps or not selected_steps or not selected_steps[-1] == audit_step_id:
            return None
        if not set(selected_steps).issubset(steps_by_id):
            return None

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
                previous_content_digest=self._content_digest(outputs.get(step_id)),
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
            classified.append(
                RepairIssue(
                    issue_id=f"legacy-{len(classified) + 1}",
                    issue_type="unresolved",
                    message=message,
                    owner_step_id=None,
                )
            )
        return classified

    # 内容生产节点的兜底优先级：资源审核重跑 Expert，试卷重跑装配，计划重跑
    # Diagnosis。审核机制故障或问题缺少定位时用它整篇重新生成后再送审。
    _FALLBACK_TARGETS = ("expert", "paper_assembly", "diagnosis")

    # 整卷级问题重新取题时必须一并重跑的上游节点。试卷装配是确定性选择：
    # 候选身份绑定、题量配额和排序都由系统掌握，同一候选池必然产出同一份
    # 试卷（除重新生成的 paper_draft_id 外每个字段都相同）。只重跑装配节点
    # 的返修因此是恒等变换——线上实测返修前后 40 道题的题干逐字一致。要让
    # 返修真的改变产物，必须让装配拿到一批新的候选。
    _RETRIEVAL_UPSTREAM_STEPS = ("paper_blueprint", "question_pool")

    def _fallback_target(self, step_ids: frozenset[str]) -> str | None:
        return next(
            (
                step_id
                for step_id in self._FALLBACK_TARGETS
                if step_id in step_ids
            ),
            None,
        )

    def _whole_paper_chain(
        self,
        step_ids: frozenset[str],
        audit_step_id: str,
    ) -> tuple[str, ...]:
        """整卷级问题的返修链：从取题开始重跑，再装配、再送审。

        上游节点不存在时（例如只装配、不取题的旧 DAG）退回只重跑装配，
        保持既有行为不变。
        """
        upstream = tuple(
            step_id
            for step_id in self._RETRIEVAL_UPSTREAM_STEPS
            if step_id in step_ids
        )
        return (*upstream, "paper_assembly", audit_step_id)

    def _fallback_issue(self, plan: ExecutionPlan) -> RepairIssue | None:
        """Build one synthetic, locatable issue for the content-producing node.

        Used when the audit concluded non-pass without a locatable issue, or
        when no supplied issue can be bound to a repairable node.  The product
        contract admits only publish and repair, so the workflow reruns the
        content node instead of stopping for human review.  ``content_quality``
        is the one issue type whose allowed targets cover all three content
        nodes, so the issue stays bindable in resource, paper and plan DAGs.
        """
        target = self._fallback_target(
            frozenset(step.step_id for step in plan.steps)
        )
        if target is None:
            return None
        return RepairIssue(
            issue_id="REPAIR_FALLBACK",
            issue_type="content_quality",
            message="审核未形成可定位的问题，兜底重跑内容生产节点后再次送审。",
            severity="medium",
            origin="deterministic",
            blocking=True,
            affected_step_ids=[target],
            owner_step_id=target,
        )

    def _chain_for(
        self,
        issue: RepairIssue,
        audit_step_id: str,
        *,
        step_ids: frozenset[str],
    ) -> tuple[str, ...] | None:
        issue_type = issue.issue_type
        if issue_type in {"safety_violation", "unresolved"}:
            # 安全越界必须由内容节点重写；审核机制故障（协议解析失败、编译器
            # 无法定位）没有可用的定位信息，只能整篇重新生成后再送审。
            # 产品约定只有发布与返修两种终态，两者都不能停在等待人工。
            fallback = self._fallback_target(step_ids)
            if fallback is None:
                return None
            if issue_type == "unresolved" and fallback == "paper_assembly":
                # 审核机制故障落在整卷上：这类问题没有可定位的单题，装配
                # 节点重跑只会逐字复现原卷。必须重新取题，返修才有可能
                # 真的改变产物。
                return self._whole_paper_chain(step_ids, audit_step_id)
            return (fallback, audit_step_id)
        target = self._affected_target(issue)
        if target is None:
            return None
        if target == "diagnosis":
            # The finding is owned by Diagnosis, so it is local to the plan
            # prose.  A planning DAG has no EvidencePack, Expert or
            # PaperAssembly branch to rerun, so the chain must stay inside the
            # planning steps; a route mismatch additionally needs the frozen
            # route decision re-read before the plan is rewritten.
            prefix = (
                ("route_resolution",)
                if issue_type == "route_or_prerequisite_error"
                and "route_resolution" in step_ids
                else ()
            )
            return (*prefix, "diagnosis", audit_step_id)
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
            return self._whole_paper_chain(step_ids, audit_step_id)
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

    # 每次运行都会重新生成的标识与时间戳：完整 uuid、长十六进制串（``EP_``、
    # ``PAPER_DRAFT_`` 之类的 id 都是 32 位十六进制）、ISO 时间。内容相同的
    # 两次运行也会得到不同的值，比对前必须抹平。
    _VOLATILE_VALUE_PATTERN = re.compile(
        r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
        r"|[0-9a-f]{16,}"
        r"|\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?"
    )

    @classmethod
    def _content_digest(cls, value: Any) -> str | None:
        """摘要产物内容，忽略每次运行都会重新生成的标识符与时间戳。

        ``_output_digest`` 把 ``paper_draft_id`` 这类字段也算进去，所以即使
        返修逐字复现了原产物，摘要依然会变——线上实测返修前后的 40 道题题干
        完全一致，而 ``before_digest`` 与 ``after_digest`` 不同，差值只来自
        重新生成的 ``paper_draft_id``。返修是否真的改变了内容必须能被判定，
        否则“已完成返修”只是一个不成立的记录。
        """
        if value is None:
            return None
        if hasattr(value, "model_dump"):
            value = value.model_dump(mode="json")
        try:
            raw = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
        except TypeError:
            raw = str(value)
        normalized = cls._VOLATILE_VALUE_PATTERN.sub("<volatile>", raw)
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()

    @staticmethod
    def _human_review_plan(
        *,
        repair_id: str,
        execution_id: str,
        audit_step_id: str,
        issues: list[RepairIssue],
    ) -> LocalRepairPlan:
        """Last-resort plan for a DAG with no rerunnable content node.

        Reached only when the plan has no ``expert`` / ``paper_assembly`` /
        ``diagnosis`` step, so no bounded repair can be built.  This is a
        structural system fault, not a content verdict: callers report it as a
        failed run rather than a product-level review state.  The contract
        keeps the ``needs_human_review`` literal for persisted-plan backward
        compatibility.
        """
        return LocalRepairPlan(
            repair_id=repair_id,
            execution_id=execution_id,
            trigger_step_id=audit_step_id,
            issues=issues,
            actions=[],
            status="needs_human_review",
        )
