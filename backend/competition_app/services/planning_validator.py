from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, Field

from competition_app.llm.schemas import ThreeLayerPlanningModelOutput
from competition_app.services.prerequisite_policy import normalize_course_name
from competition_app.services.planning_prerequisites import missing_execution_prerequisites


class PlanningValidationResult(BaseModel):
    valid: bool
    issues: list[str] = Field(default_factory=list)
    diagnostics: list[dict[str, str]] = Field(default_factory=list)


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
        active_scope: str | None = None,
        force_prerequisite_daily_task: bool = False,
        required_prerequisite_courses: set[str] | None = None,
        temporary_focus_overlay: dict[str, Any] | None = None,
        completed_textbooks: set[str] | None = None,
    ) -> PlanningValidationResult:
        issues: list[str] = []
        diagnostics: list[dict[str, str]] = []
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
                diagnostics.append({"code": "duration_sum_mismatch", "field_path": "/total_duration_days"})
            for index, stage in enumerate(output.long_term_plan_stages, start=1):
                if not str(self._field(stage, "stage_name") or "").strip():
                    issues.append(f"长期规划第{index}阶段缺少具体阶段名称。")
                if not str(self._field(stage, "schedule_summary") or "").strip():
                    issues.append(
                        f"长期规划第{index}阶段缺少包含书名、重点、产出和验收条件的详细安排。"
                    )
        # A change at an upper layer invalidates lower-layer versions, but it
        # does not mean those lower layers must be regenerated in the same
        # response.  Diagnosis may therefore validate only the layer the
        # current request is materialising.  Direct callers that do not pass
        # active_scope retain the historical strict three-layer contract.
        validate_short = actions["short"] == "update" and (
            active_scope is None or active_scope in {"short_term", "full"}
        )
        temporary_focus_issues = (
            self._temporary_focus_overlay_issues(
                output,
                temporary_focus_overlay,
                long_term_action=long_term_action,
                short_term_action=short_term_action,
                active_scope=active_scope,
            )
            if temporary_focus_overlay is not None
            else []
        )
        temporary_preview_authorized = bool(
            temporary_focus_overlay is not None
            and not temporary_focus_issues
        )
        issues.extend(temporary_focus_issues)
        validate_daily = actions["daily"] == "update" and (
            active_scope is None or active_scope in {"daily_task", "full"}
        )
        if validate_short:
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
            # Audit compares complete claims (including negation and examples)
            # with learning evidence; prose regexes cannot establish assertions.
        if validate_daily:
            if not output.learning_chapter.strip():
                issues.append("当日任务必须提供结构化 learning_chapter。")
            if not 1 <= len(output.focus_knowledge_points) <= 5:
                issues.append("当日任务必须提供1—5个结构化重点知识点名称。")
            if force_prerequisite_daily_task:
                # The existing Compiler checks full prose against system-owned
                # allowed/authorized prerequisite courses. A title mention is
                # neither proof of training nor permission to execute a course.
                if not str(output.expected_output or "").strip():
                    issues.append(
                        "daily_task_prerequisite_required："
                        "前置课程每日任务必须提供 expected_output。"
                    )
                if not str(output.completion_criteria or "").strip():
                    issues.append(
                        "daily_task_prerequisite_required："
                        "前置课程每日任务必须提供 completion_criteria。"
                    )

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
            if not structured_stages or any(
                not list(self._field(stage, "book") or [])
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
                    diagnostics.append({"code": "route_stage_missing", "field_path": "/stages"})
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
                            diagnostics.append({"code": "route_books_mismatch", "field_path": "/stages/*/books"})
                        trusted_goal = str(
                            self._field(trusted, "objective") or "完成本阶段目标"
                        )
                        if str(self._field(structured, "goal") or "").strip() != trusted_goal.strip():
                            issues.append(
                                f"long_term_plan_stages 的第{index}个长期阶段目标与系统可信路线不一致。"
                            )
                            diagnostics.append({"code": "route_goal_mismatch", "field_path": "/stages/*/goal"})
        validate_textbook_selection = active_scope is None or active_scope in {
            "long_term",
            "short_term",
            "full",
        }
        if textbook_route is not None and validate_textbook_selection:
            completed = {self._normalized_book_name(book) for book in (completed_textbooks or set())}
            if output.selection_mode not in {"review", "diagnostic"} and any(
                self._normalized_book_name(book) in completed for book in output.selected_books
            ):
                issues.append("已记录整本完成的教材不得重新作为新学任务；如需复习或诊断，必须明确学习用途，完成记录不等于掌握或通过测验。")
                diagnostics.append({"code": "completed_book_new_learning", "field_path": "/selection_mode"})
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
                # 前置课程教材允许作为当前阶段的短期计划教材：
                # 长期规划可能把前置训练（如进入 stage-2 前的《中医诊断学》）
                # 安排在当前阶段的前若干天内，此时所选教材不属于该阶段正式书目。
                selected_stage_id_value = (
                    selected_stage.get("stage_id")
                    if isinstance(selected_stage, dict)
                    else getattr(selected_stage, "stage_id", None)
                )
                allowed_books = list(stage_books) + [
                    f"《{self._field(rule, 'course')}》"
                    for rule in (self._field(textbook_route, "prerequisites") or [])
                    if self._field(rule, "course")
                    and str(self._field(rule, "before_stage_id") or "")
                    == str(selected_stage_id_value or "")
                ]
                outside_stage = [
                    book
                    for book in output.selected_books
                    if not any(
                        self._book_matches(str(book), allowed)
                        for allowed in allowed_books
                    )
                ]
                if outside_stage:
                    issues.append(
                        "模型选择了不属于当前阶段的教材："
                        + "、".join(str(book) for book in outside_stage)
                        + "。"
                    )
                confirmed = {
                    self._normalized_book_name(course)
                    for course in (confirmed_prerequisite_courses or set())
                }
                unmet = {
                    self._normalized_book_name(course)
                    for course in (unmet_prerequisite_courses or set())
                }
                missing_prerequisites = (
                    [] if temporary_preview_authorized else
                    missing_execution_prerequisites(
                        textbook_route, str(output.selected_stage_id or ""),
                        list(output.selected_books), confirmed - unmet,
                    )
                )
                if missing_prerequisites:
                    diagnostics.append({"code": "prerequisite_unconfirmed", "field_path": "/selected_books"})
                    issues.append(
                        "所选阶段的强前置尚未确认："
                        + "、".join(missing_prerequisites)
                        + "。"
                    )
                # Audit independently judges whether the prose schedules
                # unmet prerequisites. The execution gate above remains hard.

        if (
            available_minutes is not None
            and available_minutes > 0
            and validate_daily
            and output.estimated_minutes > max(available_minutes + 10, int(available_minutes * 1.5))
        ):
            issues.append(
                f"当日任务严重超时：预计{output.estimated_minutes}分钟，预算{available_minutes}分钟。"
            )
        if len(issues) > len(diagnostics):
            diagnostics.append({"code": "planning_rule_rejected", "field_path": "/"})
        return PlanningValidationResult(valid=not issues, issues=issues, diagnostics=diagnostics[:20])

    @classmethod
    def _temporary_focus_overlay_issues(
        cls,
        output: ThreeLayerPlanningModelOutput,
        overlay: dict[str, Any],
        *,
        long_term_action: str,
        short_term_action: str,
        active_scope: str | None,
    ) -> list[str]:
        issues: list[str] = []
        names = [
            str(item).strip()
            for item in overlay.get("focus_names") or []
            if str(item).strip()
        ]
        books = [
            str(item).strip()
            for item in overlay.get("focus_books") or []
            if str(item).strip()
        ]
        if (
            overlay.get("mode") != "temporary_cross_stage"
            or overlay.get("prerequisite_mode") != "introductory_preview"
            or long_term_action != "reuse"
            or short_term_action != "update"
            or active_scope != "short_term"
        ):
            issues.append("临时跨阶段专题只能用于复用长期规划的短期入门预习。")
        if not names or len(names) != len(set(names)):
            issues.append("临时跨阶段专题必须包含非空且不重复的证据焦点。")
        if (
            str(output.selected_textbook_route_id or "")
            != str(overlay.get("route_id") or "")
            or str(output.selected_stage_id or "")
            != str(overlay.get("focus_stage_id") or "")
        ):
            issues.append("短期计划选择的路线或阶段与系统专题授权不一致。")
        if (
            len(output.selected_books) != len(books)
            or any(
                not cls._book_matches(actual, expected)
                for actual, expected in zip(output.selected_books, books)
            )
        ):
            issues.append("短期计划教材与系统专题授权不一致。")
        # Audit checks prose coverage and negation against this same overlay.
        return issues

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
        return normalize_course_name(value)

