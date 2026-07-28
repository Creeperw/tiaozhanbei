from __future__ import annotations

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
            missing_in_content = [
                book
                for book in stage.books
                if book not in contract.long_term_plan_content
            ]
            if missing_in_content:
                issues.append(
                    f"长期规划第{index + 1}阶段正文缺少具体书名："
                    + "、".join(missing_in_content)
                    + "。"
                )
            missing_in_schedule = [
                book for book in stage.books if book not in stage.schedule_summary
            ]
            if missing_in_schedule:
                issues.append(
                    f"长期规划第{index + 1}阶段详细安排缺少具体书名："
                    + "、".join(missing_in_schedule)
                    + "。"
                )
            if trusted_stages:
                trusted = trusted_stages[index]
                trusted_name = str(trusted.get("name") or "").strip()
                trusted_books = [str(item).strip() for item in trusted.get("books") or []]
                if trusted_name and stage.stage_name.strip() != trusted_name:
                    issues.append(f"长期规划第{index + 1}阶段名称与可信路线不一致。")
                if stage.books != trusted_books:
                    issues.append(f"长期规划第{index + 1}阶段书目与可信路线不一致。")
        return issues

    @staticmethod
    def _validate_short_term(
        contract: CompiledShortTermContract,
        parent: dict[str, Any],
    ) -> list[str]:
        issues: list[str] = []
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
        missing_books = [
            book
            for book in contract.selected_books
            if book not in contract.short_term_plan_content
        ]
        if missing_books:
            issues.append(
                "短期计划正文必须明确当前使用的具体书名："
                + "、".join(missing_books)
                + "。"
            )
        parent_stage_id = str(parent.get("current_stage_id") or "")
        if (
            parent_stage_id
            and contract.selected_stage_id
            and contract.selected_stage_id != parent_stage_id
        ):
            issues.append("短期计划选择的阶段与当前长期阶段不一致。")
        return issues
