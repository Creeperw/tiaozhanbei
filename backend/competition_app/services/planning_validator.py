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
        long_term_action: str = "update",
        short_term_action: str = "update",
        daily_task_action: str = "update",
        confirmed_prerequisite_courses: set[str] | None = None,
        unmet_prerequisite_courses: set[str] | None = None,
        path_candidates: dict[str, Any] | None = None,
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
        if actions["long"] == "update" and not self._has_groups(
            output.long_term_plan_content,
            (
                ("目标契约", "最终目标"),
                ("长期阶段", "阶段路径", "能力路径", "能力路径与阶段"),
                ("重规划", "调整触发"),
            ),
        ):
            issues.append("长期计划缺少目标、阶段路径或重规划触发器等核心区域。")
        if actions["short"] == "update" and not self._has_groups(
            output.short_term_plan_content,
            (
                ("周期目标", "当前主目标"),
                ("本周期任务", "任务表", "具体任务", "任务块"),
            ),
        ):
            issues.append("短期计划缺少周期目标或任务安排等核心区域。")
        if actions["short"] == "update":
            cycle_markers = set(
                re.findall(
                    r"第[一二12]周|周初|周中|周末|前半周?|后半周?|"
                    r"本周|本周期|首个节点|下个节点|第二个节点|周期末|"
                    r"第?[一二12](?:个)?节点|"
                    r"第一阶段|第二阶段|阶段[一二12]|"
                    r"前段|中段|验收段|"
                    r"第?\d+\s*[-—至~]\s*\d+天|前\d+天|后\d+天",
                    output.short_term_plan_content,
                )
            )
            if len(cycle_markers) < 2:
                issues.append("短期计划必须覆盖完整周期，并至少给出两个推进或验收节点。")
            if re.search(r"今晚|今天|今日|明早", output.short_term_plan_content):
                issues.append("短期计划混入了今日任务；具体当日动作应只写入今日任务。")
            if re.search(
                r"(?:[一二两三四五六七八九十\d]+天后|明天|后天|"
                r"每(?:满|隔)?[一二两三四五六七八九十\d]+天)",
                output.short_term_plan_content,
            ):
                issues.append("短期计划不得自行指定系统调度日期。")
            if re.search(
                r"(?:系统.{0,30}(?:调度|安排)|(?:调度|安排).{0,30}系统)",
                output.short_term_plan_content,
            ):
                issues.append("短期计划不得包含系统调度占位语；复习时间由复习队列独立管理。")
            if re.search(
                r"(?:每次|每个)[^。；\n]{0,30}(?:开始前|结束前|开始时|结束时|结束后)",
                output.short_term_plan_content,
            ):
                issues.append(
                    "短期计划不得自行安排每次学习前后的固定复习时点；只描述复习对象、形式与验收。"
                )
            weekly_days_match = re.search(
                r"每周[^\d]{0,12}(\d+)\s*天",
                str(user_time_constraints or ""),
            )
            if weekly_days_match and int(weekly_days_match.group(1)) < 7:
                if re.search(r"每日|每天", output.short_term_plan_content):
                    issues.append(
                        "短期计划不得把用户的每周学习天数改写为每日任务；请按学习日安排。"
                    )
                if re.search(
                    r"第\s*\d+\s*[-—至~]\s*\d+\s*天",
                    output.short_term_plan_content,
                ):
                    issues.append(
                        "短期计划应按学习日或周期节点推进，不得把非连续学习安排写成连续自然日。"
                    )
        if (
            actions["daily"] == "update"
            and len(output.daily_task_content.strip()) < 30
        ):
            issues.append("当日任务缺少今日目标或今日任务等核心区域。")

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
                selected_stage_mentions = self._planned_book_mentions(
                    "\n".join(
                        content
                        for content, action in (
                            (output.short_term_plan_content, actions["short"]),
                            (output.daily_task_content, actions["daily"]),
                        )
                        if action == "update"
                    )
                )
                outside_selected_stage = sorted(
                    book
                    for book in selected_stage_mentions
                    if not any(
                        self._book_matches(book, allowed)
                        for allowed in [*stage_books, *prerequisite_books]
                    )
                )
                if outside_selected_stage:
                    issues.append(
                        "短期计划或今日任务使用了所选阶段外教材："
                        + "、".join(outside_selected_stage)
                        + "。"
                    )
        allowed_books = {
            str(book)
            for phase in phases
            for book in (self._field(phase, "books") or [])
            if str(book).strip()
        }
        if textbook_route is not None:
            allowed_books.update(
                f"《{self._field(rule, 'course')}》"
                for rule in (self._field(textbook_route, "prerequisites") or [])
                if str(self._field(rule, "course") or "").strip()
            )
        if allowed_books:
            checked = []
            if actions["long"] == "update":
                checked.append(output.long_term_plan_content)
            if actions["short"] == "update":
                checked.append(output.short_term_plan_content)
            if actions["daily"] == "update":
                checked.append(output.daily_task_content)
            mentioned = self._planned_book_mentions("\n".join(checked))
            outside = sorted(
                book
                for book in mentioned
                if not any(
                    self._book_matches(book, allowed) for allowed in allowed_books
                )
            )
            if outside:
                issues.append("计划使用了默认路线外教材：" + "、".join(outside) + "。")

        if (
            available_minutes is not None
            and available_minutes > 0
            and daily_task_action == "update"
            and output.estimated_minutes > max(available_minutes + 10, int(available_minutes * 1.5))
        ):
            issues.append(
                f"当日任务严重超时：预计{output.estimated_minutes}分钟，预算{available_minutes}分钟。"
            )
        if (
            actions["short"] == "update"
            and actions["daily"] == "update"
            and not self._meaningfully_related(
                output.short_term_plan_content, output.daily_task_content
            )
        ):
            issues.append("当日任务与短期计划完全失配。")
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
    def _planned_book_mentions(content: str) -> set[str]:
        """Find books used as study material, not titles cited as factual sources."""
        mentions: set[str] = set()
        for match in re.finditer(r"《[^》]{1,80}》", content):
            prefix = content[max(0, match.start() - 24):match.start()]
            if re.search(r"(?:出处|出自|首见|源自|载于|记载)(?:为|于)?\s*$", prefix):
                continue
            if re.search(
                r"(?:学习|阅读|研读|主学|选用|使用|改用|核对|复习|教材)"
                r"[^。；;！？!?\n]{0,20}$",
                prefix,
            ):
                mentions.add(match.group())
        return mentions

    @staticmethod
    def _has_groups(content: str, groups: tuple[tuple[str, ...], ...]) -> bool:
        return all(any(marker in content for marker in group) for group in groups)

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

    @staticmethod
    def _meaningfully_related(short_term: str, daily: str) -> bool:
        ignored = set("今日目标当前周期短期任务计划分步动作时间分配客观完成标准每日本周未来进行完成学习复习" )

        def ngrams(text: str) -> set[str]:
            compact = "".join(
                char
                for char in re.sub(r"[#*`\s\d\W_]", "", text)
                if char not in ignored
            )
            return {compact[index : index + 2] for index in range(max(0, len(compact) - 1))}

        left, right = ngrams(short_term), ngrams(daily)
        return bool(left and right and left.intersection(right))
