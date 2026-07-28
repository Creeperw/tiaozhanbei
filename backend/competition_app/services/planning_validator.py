from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, Field

from competition_app.llm.schemas import ThreeLayerPlanningModelOutput


class PlanningValidationResult(BaseModel):
    valid: bool
    issues: list[str] = Field(default_factory=list)


class PlanningValidator:
    """Deliberately permissive checks for only planning-breaking defects."""

    def validate(
        self,
        output: ThreeLayerPlanningModelOutput,
        route: Any = None,
        *,
        available_minutes: int | None = None,
        user_time_constraints: str = "",
        explicit_user_request: str = "",
        evidence_status: str = "unknown",
        evidence_freshness: str = "unknown",
        long_term_action: str = "update",
        short_term_action: str = "update",
        daily_task_action: str = "update",
        confirmed_prerequisite_courses: set[str] | None = None,
        unmet_prerequisite_courses: set[str] | None = None,
        path_candidates: dict[str, Any] | None = None,
        parent_stage_duration_days: int | None = None,
    ) -> PlanningValidationResult:
        issues: list[str] = []
        actions = {
            "long": long_term_action,
            "short": short_term_action,
            "daily": daily_task_action,
        }
        self._validate_path_candidate_selection(
            output,
            path_candidates=path_candidates,
            actions=actions,
        )
        if actions["long"] == "update":
            if output.total_duration_days <= 0:
                issues.append("长期规划必须提供大于0的结构化总期限 total_duration_days。")
            stage_duration_total = sum(
                int(self._field(stage, "duration_days") or 0)
                for stage in output.long_term_plan_stages
            )
            if any(
                int(self._field(stage, "duration_days") or 0) <= 0
                for stage in output.long_term_plan_stages
            ):
                issues.append("长期规划的每个阶段都必须提供大于0的 duration_days。")
            if (
                output.total_duration_days > 0
                and stage_duration_total != output.total_duration_days
            ):
                issues.append("长期规划总期限必须等于各阶段期限之和。")
            for index, stage in enumerate(output.long_term_plan_stages, start=1):
                if not str(self._field(stage, "stage_name") or "").strip():
                    issues.append(f"长期规划第{index}阶段缺少具体阶段名称。")
                if not str(self._field(stage, "schedule_summary") or "").strip():
                    issues.append(
                        f"长期规划第{index}阶段缺少包含书名、重点、产出和验收条件的详细安排。"
                    )
        if actions["short"] == "update":
            if output.short_term_duration_days <= 0:
                issues.append("短期计划必须提供大于0的结构化周期 short_term_duration_days。")
            if len(output.short_term_progression_nodes) < 2:
                issues.append("短期计划必须至少提供两个结构化推进或验收节点。")
            if (
                isinstance(parent_stage_duration_days, int)
                and parent_stage_duration_days > 0
                and output.short_term_duration_days > parent_stage_duration_days
            ):
                issues.append(
                    "短期计划期限不能超过所属长期阶段期限："
                    f"短期{output.short_term_duration_days}天，"
                    f"父阶段{parent_stage_duration_days}天。"
                )
            if (
                str(evidence_status).casefold() != "sufficient"
                or str(evidence_freshness).casefold() in {"unknown", "stale", "expired"}
            ):
                unsupported_patterns = (
                    r"掌握度(?:为|是|达到)?\s*\d+(?:\.\d+)?%?",
                    r"\d+\s*个(?:核心)?薄弱知识点",
                    r"(?:多次|反复|高频)[^。；\n]{0,16}(?:答错|错误|错题)",
                    r"(?:零|0)正确|正确率(?:为|是|达到)?\s*0(?:\.0+)?%?",
                )
                if any(
                    re.search(pattern, output.short_term_plan_content)
                    for pattern in unsupported_patterns
                ):
                    issues.append(
                        "学习行为证据不足或新鲜度未知，短期计划不得断言精确掌握度、"
                        "错误频次或薄弱知识点总数；请改为待验证的学习重点。"
                    )
        if actions["daily"] == "update":
            if not output.learning_chapter.strip():
                issues.append("当日任务必须提供结构化 learning_chapter。")
            if not 1 <= len(output.focus_knowledge_points) <= 5:
                issues.append("当日任务必须提供1—5个结构化重点知识点名称。")

        textbook_resolution = self._field(route, "textbook_route")
        textbook_route = (
            self._field(textbook_resolution, "route")
            if self._field(textbook_resolution, "planning_status") == "resolved"
            else None
        )
        textbook_stages = list(self._field(textbook_route, "stages") or [])
        phases = textbook_stages or list(self._field(route, "phases") or [])
        structured_stages = list(output.long_term_plan_stages)
        if actions["long"] == "update":
            if not phases:
                issues.append(
                    "长期规划缺少系统可信路线阶段，禁止发布占位教材阶段。"
                )
            placeholder_tokens = (
                "待确认", "未确认", "unknown", "tbd", "不可发布", "路线解析失败"
            )
            if not structured_stages or any(
                not list(self._field(stage, "book") or [])
                or any(
                    any(token in str(book).lower() for token in placeholder_tokens)
                    for book in (self._field(stage, "book") or [])
                )
                for stage in structured_stages
            ):
                issues.append(
                    "long_term_plan_stages 必须包含可信路线中的真实教材，不得使用占位教材。"
                )
            expected_stage_numbers = list(range(1, len(structured_stages) + 1))
            if [self._field(stage, "stage") for stage in structured_stages] != expected_stage_numbers:
                issues.append("long_term_plan_stages 的长期阶段编号必须从 1 开始且连续。")
            if phases:
                if len(structured_stages) != len(phases):
                    issues.append("long_term_plan_stages 未完整对应系统可信的长期阶段。")
                else:
                    for index, (structured, trusted) in enumerate(
                        zip(structured_stages, phases), start=1
                    ):
                        trusted_books = [
                            str(book)
                            for book in (self._field(trusted, "books") or [])
                        ]
                        if not trusted_books:
                            issues.append(
                                f"系统可信路线的第{index}个长期阶段缺少明确教材，禁止发布。"
                            )
                            continue
                        structured_books = list(self._field(structured, "book") or [])
                        if len(structured_books) != len(trusted_books) or any(
                            not any(
                                self._book_matches(str(book), trusted_book)
                                for trusted_book in trusted_books
                            )
                            for book in structured_books
                        ):
                            issues.append(
                                f"long_term_plan_stages 的第{index}个长期阶段书目与系统可信路线不一致。"
                            )
                        trusted_goal = str(
                            self._field(trusted, "objective") or "完成本阶段目标"
                        )
                        if str(self._field(structured, "goal") or "").strip() != trusted_goal.strip():
                            issues.append(
                                f"long_term_plan_stages 的第{index}个长期阶段目标与系统可信路线不一致。"
                            )
        if textbook_route is not None:
            expected_route_id = str(self._field(textbook_route, "route_id") or "")
            if output.selected_textbook_route_id != expected_route_id:
                issues.append("教材路线选择与系统已解析路线不一致。")
            stages_by_id = {
                str(self._field(stage, "stage_id")): stage
                for stage in textbook_stages
            }
            selected_stage = stages_by_id.get(str(output.selected_stage_id or ""))
            if selected_stage is None:
                issues.append("模型选择了教材路线中不存在的阶段。")
            if not 1 <= len(output.selected_books) <= 2:
                issues.append("当前阶段必须选择 1—2 本主教材。")
            if not str(output.selection_reason or "").strip():
                issues.append("教材阶段选择缺少结合用户情况的理由。")
            if selected_stage is not None:
                stage_books = [
                    str(book)
                    for book in (self._field(selected_stage, "books") or [])
                ]
                prerequisite_books = [
                    f"《{self._field(rule, 'course')}》"
                    for rule in (self._field(textbook_route, "prerequisites") or [])
                    if self._field(rule, "course")
                ]
                outside_stage = [
                    book
                    for book in output.selected_books
                    if not any(
                        self._book_matches(str(book), allowed)
                        for allowed in stage_books
                    )
                ]
                if outside_stage:
                    issues.append(
                        "模型选择了不属于当前阶段的教材："
                        + "、".join(str(book) for book in outside_stage)
                        + "。"
                    )
                selected_order = int(self._field(selected_stage, "order") or 0)
                stages_by_id_for_order = {
                    str(self._field(stage, "stage_id")): stage
                    for stage in textbook_stages
                }
                confirmed = {
                    self._normalized_book_name(course)
                    for course in (confirmed_prerequisite_courses or set())
                }
                unmet = {
                    self._normalized_book_name(course)
                    for course in (unmet_prerequisite_courses or set())
                }
                missing_prerequisites = []
                declared_unmet_prerequisites = []
                for rule in self._field(textbook_route, "prerequisites") or []:
                    before_stage = stages_by_id_for_order.get(
                        str(self._field(rule, "before_stage_id") or "")
                    )
                    before_order = int(self._field(before_stage, "order") or 0)
                    course = str(self._field(rule, "course") or "")
                    normalized_course = self._normalized_book_name(course)
                    if before_order and selected_order >= before_order:
                        if normalized_course in unmet:
                            declared_unmet_prerequisites.append(course)
                        elif normalized_course not in confirmed:
                            missing_prerequisites.append(course)
                if missing_prerequisites:
                    issues.append(
                        "所选阶段的强前置尚未确认："
                        + "、".join(missing_prerequisites)
                        + "。"
                    )
                if actions["long"] == "update":
                    omitted_unmet = [
                        course
                        for course in declared_unmet_prerequisites
                        if course not in output.long_term_plan_content
                    ]
                    if omitted_unmet:
                        issues.append(
                            "用户已确认未完成的强前置课程必须纳入长期规划："
                            + "、".join(omitted_unmet)
                            + "。"
                        )

        if (
            available_minutes is not None
            and available_minutes > 0
            and daily_task_action == "update"
            and output.estimated_minutes > max(available_minutes + 10, int(available_minutes * 1.5))
        ):
            issues.append(
                f"当日任务严重超时：预计{output.estimated_minutes}分钟，预算{available_minutes}分钟。"
            )
        return PlanningValidationResult(valid=not issues, issues=issues)

    @classmethod
    def _validate_path_candidate_selection(
        cls,
        output: ThreeLayerPlanningModelOutput,
        *,
        path_candidates: dict[str, Any] | None,
        actions: dict[str, str],
    ) -> None:
        if not isinstance(path_candidates, dict):
            return
        selected_id = str(
            cls._field(output, "selected_path_candidate_id")
            or cls._field(output, "selected_candidate_id")
            or cls._field(output, "candidate_id")
            or ""
        ).strip()
        if not selected_id:
            return
        eligible = path_candidates.get("eligible")
        blocked = path_candidates.get("blocked")
        eligible = eligible if isinstance(eligible, list) else []
        blocked = blocked if isinstance(blocked, list) else []
        candidates = [
            item
            for item in [*eligible, *blocked]
            if isinstance(item, dict)
        ]
        selected = next(
            (
                item
                for item in candidates
                if str(item.get("candidate_id") or "") == selected_id
            ),
            None,
        )
        if selected is None:
            raise ValueError(f"unknown path candidate: {selected_id}")
        prerequisite_results = [
            item
            for item in selected.get("hard_constraint_results", [])
            if isinstance(item, dict)
            and item.get("key") == "prerequisite_satisfied"
        ]
        if (
            selected.get("eligible") is not True
            or selected in blocked
            or not prerequisite_results
            or any(item.get("passed") is not True for item in prerequisite_results)
        ):
            raise ValueError(f"blocked path candidate: {selected_id}")

        expected_scope = next(
            (
                scope
                for layer, scope in (
                    ("long", "long_term"),
                    ("short", "short_term"),
                    ("daily", "daily_task"),
                )
                if actions.get(layer) == "update"
            ),
            None,
        )
        if expected_scope and selected.get("scope") != expected_scope:
            raise ValueError(
                f"path candidate scope mismatch: {selected_id}"
            )

        stage = selected.get("stage")
        stage = stage if isinstance(stage, dict) else {}
        candidate_stage_id = str(
            stage.get("phase_id") or stage.get("stage_id") or ""
        )
        selected_stage_id = str(
            cls._field(output, "selected_stage_id") or ""
        )
        if (
            selected_stage_id
            and candidate_stage_id
            and selected_stage_id != candidate_stage_id
        ):
            raise ValueError(
                f"path candidate stage mismatch: {selected_id}"
            )

        candidate_books = [
            str(item.get("name") or "")
            for item in selected.get("books", [])
            if isinstance(item, dict) and str(item.get("name") or "").strip()
        ]
        selected_books = [
            str(item)
            for item in (cls._field(output, "selected_books") or [])
            if str(item).strip()
        ]
        if selected_books and (
            not candidate_books
            or any(
                not any(cls._book_matches(book, allowed) for allowed in candidate_books)
                for book in selected_books
            )
        ):
            raise ValueError(
                f"path candidate textbook mismatch: {selected_id}"
            )

        candidate_kps = {
            str(value).strip()
            for item in selected.get("knowledge_points", [])
            if isinstance(item, dict)
            for value in (item.get("kp_id"), item.get("name"))
            if str(value or "").strip()
        }
        selected_kps = {
            str(item).strip()
            for item in (cls._field(output, "focus_knowledge_points") or [])
            if str(item).strip()
        }
        if selected_kps and (
            not candidate_kps or not selected_kps.issubset(candidate_kps)
        ):
            raise ValueError(
                f"path candidate knowledge point mismatch: {selected_id}"
            )

    @staticmethod
    def _field(value: Any, name: str) -> Any:
        if isinstance(value, dict):
            return value.get(name)
        return getattr(value, name, None)

    @staticmethod
    def _book_matches(candidate: str, allowed: str) -> bool:
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

        return candidate == allowed or base_title(candidate) == base_title(allowed)

    @staticmethod
    def _normalized_book_name(value: str) -> str:
        return re.sub(
            r"\s+",
            "",
            value.strip().removeprefix("《").removesuffix("》"),
        ).casefold()

