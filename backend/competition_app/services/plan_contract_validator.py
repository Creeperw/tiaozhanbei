from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, Field

from competition_app.contracts.plan_compilation import (
    CompiledDailyTaskContract,
    CompiledLongTermContract,
    CompiledShortTermContract,
)


class PlanContractValidationResult(BaseModel):
    valid: bool
    issues: list[str] = Field(default_factory=list)


class PlanContractValidator:
    """Deterministic hierarchical validation for compiled plan semantics."""

    def validate(
        self,
        contract: CompiledLongTermContract
        | CompiledShortTermContract
        | CompiledDailyTaskContract,
        *,
        trusted_route: dict[str, Any] | None = None,
        parent_plan_constraints: dict[str, Any] | None = None,
    ) -> PlanContractValidationResult:
        issues: list[str] = []
        if isinstance(contract, CompiledLongTermContract):
            issues.extend(self._validate_long_term(contract, trusted_route or {}))
        elif isinstance(contract, CompiledShortTermContract):
            issues.extend(
                self._validate_short_term(contract, parent_plan_constraints or {})
            )
        return PlanContractValidationResult(valid=not issues, issues=issues)

    @staticmethod
    def _validate_long_term(
        contract: CompiledLongTermContract,
        trusted_route: dict[str, Any],
    ) -> list[str]:
        issues: list[str] = []
        if sum(stage.duration_days for stage in contract.stages) != contract.total_duration_days:
            issues.append("长期规划总期限必须等于各阶段期限之和。")
        trusted_stages = list(trusted_route.get("stages") or trusted_route.get("phases") or [])
        if trusted_stages and len(trusted_stages) != len(contract.stages):
            issues.append("长期规划阶段数量与系统可信路线不一致。")
            return issues
        for index, stage in enumerate(contract.stages):
            if not stage.stage_name.strip():
                issues.append(f"长期规划第{index + 1}阶段缺少阶段名称。")
            if not stage.books:
                issues.append(f"长期规划第{index + 1}阶段缺少具体书名。")
            if not stage.schedule_summary.strip():
                issues.append(f"长期规划第{index + 1}阶段缺少详细安排。")
            # Contract membership is structural; Audit judges whether the
            # complete prose actually schedules these books, including negation.
            if trusted_stages:
                trusted = trusted_stages[index]
                trusted_name = str(trusted.get("name") or "").strip()
                trusted_books = [str(item).strip() for item in trusted.get("books") or []]
                if trusted_name and stage.stage_name.strip() != trusted_name:
                    issues.append(f"长期规划第{index + 1}阶段名称与可信路线不一致。")
                if len(stage.books) != len(trusted_books) or any(
                    not PlanContractValidator._book_matches(actual, trusted)
                    for actual, trusted in zip(stage.books, trusted_books)
                ):
                    issues.append(f"长期规划第{index + 1}阶段书目与可信路线不一致。")
        return issues

    @staticmethod
    def _book_matches(candidate: str, trusted: str) -> bool:
        """Compare semantic book titles without weakening route membership.

        Compiler extraction may preserve or omit Chinese title marks.  Those
        typography-only differences must not turn a route-valid plan into a
        deterministic audit failure.  Keep the same narrow title equivalence
        used by the planning validator; stage order and cardinality are still
        checked separately above.
        """

        def base_title(value: str) -> str:
            title = value.strip().removeprefix("《").removesuffix("》")
            title = re.sub(r"[（(][^）)]*[）)]", "", title)
            title = re.sub(r"\s+", "", title)
            title = title.removesuffix("选读")
            return {
                "伤寒论": "伤寒",
                "金匮要略": "金匮",
                "温病学": "温病",
            }.get(title, title)

        return candidate == trusted or base_title(candidate) == base_title(trusted)

    @staticmethod
    def _validate_short_term(
        contract: CompiledShortTermContract,
        parent: dict[str, Any],
    ) -> list[str]:
        issues: list[str] = []
        overlay = parent.get("temporary_focus_overlay")
        overlay = overlay if isinstance(overlay, dict) else None
        parent_duration = parent.get("current_stage_duration_days")
        if isinstance(parent_duration, int) and parent_duration > 0:
            if contract.duration_days > parent_duration:
                issues.append(
                    "短期计划期限不能超过所属长期阶段期限："
                    f"短期{contract.duration_days}天，父阶段{parent_duration}天。"
                )
        if len(contract.progression_nodes) < 2:
            issues.append("短期计划至少需要两个推进或验收节点。")
        if not contract.selected_books:
            issues.append("短期计划必须明确当前使用的具体书名。")
        parent_stage_id = str(parent.get("current_stage_id") or "")
        cross_stage_authorized = PlanContractValidator._cross_stage_overlay_matches(
            contract,
            parent_stage_id=parent_stage_id,
            overlay=overlay,
        )
        if (
            parent_stage_id
            and contract.selected_stage_id
            and contract.selected_stage_id != parent_stage_id
            and not cross_stage_authorized
        ):
            issues.append("短期计划选择的阶段与当前长期阶段不一致。")
        if overlay is not None and not cross_stage_authorized:
            issues.append("短期计划与系统授权的临时跨阶段专题不一致。")
        return issues

    @classmethod
    def _cross_stage_overlay_matches(
        cls,
        contract: CompiledShortTermContract,
        *,
        parent_stage_id: str,
        overlay: dict[str, Any] | None,
    ) -> bool:
        if not overlay:
            return False
        focus_names = [
            str(item).strip()
            for item in overlay.get("focus_names") or []
            if str(item).strip()
        ]
        focus_books = [
            str(item).strip()
            for item in overlay.get("focus_books") or []
            if str(item).strip()
        ]
        return bool(
            overlay.get("mode") == "temporary_cross_stage"
            and overlay.get("prerequisite_mode") == "introductory_preview"
            and parent_stage_id
            and str(overlay.get("progression_stage_id") or "")
            == parent_stage_id
            and str(overlay.get("focus_stage_id") or "")
            == str(contract.selected_stage_id or "")
            and focus_names
            and len(focus_names) == len(set(focus_names))
            and focus_books
            and len(focus_books) == len(contract.selected_books)
            and all(
                cls._book_matches(actual, expected)
                for actual, expected in zip(contract.selected_books, focus_books)
            )
        )

