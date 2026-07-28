from __future__ import annotations

import re
from typing import Any
from uuid import uuid4

from pydantic import ValidationError

from competition_app.agents.common import envelope
from competition_app.agents.paper_blueprint_compiler import PaperBlueprintCompilerAgent
from competition_app.contracts.agent_context import build_model_context
from competition_app.contracts.base import AgentEnvelope
from competition_app.contracts.paper import BlueprintUnit, PaperBlueprint
from competition_app.llm.base import ChatModel
from competition_app.llm.prompt_skills import prompt_skill_registry
from competition_app.llm.stub import StubChatModel


class PaperBlueprintAgent:
    """Expert stage one: design retrieval-ready blueprint units before retrieval."""

    def __init__(
        self,
        chat_model: ChatModel | None = None,
        blueprint_compiler: PaperBlueprintCompilerAgent | None = None,
    ) -> None:
        self.chat_model = chat_model or StubChatModel()
        self.blueprint_compiler = (
            blueprint_compiler or PaperBlueprintCompilerAgent(self.chat_model)
        )

    async def run(self, context: dict[str, Any]) -> AgentEnvelope[PaperBlueprint]:
        skill = prompt_skill_registry.load("expert_agent", "paper_blueprint")
        explicit_distribution = self._explicit_question_type_distribution(context)
        explicit_count = (
            sum(explicit_distribution.values())
            if explicit_distribution
            else self._explicit_question_count(context)
        )
        try:
            raw_output = await self.chat_model.complete_json(
                    "expert_agent",
                    build_model_context(
                        context,
                        target_agent="expert_agent",
                        prompt_skill=skill,
                        payload={
                            "phase": "paper_blueprint",
                            "blueprint_output_mode": "natural_language_document",
                            "user_request": context.get("user_request", ""),
                            "exam_constraints": context.get("exam_constraints", {}),
                            "session_time_budget_minutes": context.get("available_minutes"),
                            "learning_scope": self._requested_learning_scope(context),
                            "planning_context": self._planning_context(context),
                            "user_profile": context.get("user_profile", {}),
                            "output_contract": {
                                "blueprint_document": (
                                    "一篇完整、详细、可读的自然语言试卷蓝图原稿；"
                                    "正文必须明确标题、范围、每个单元的学习目标、检索表达、"
                                    "题型偏好、目标题数、可选分值、假设和验收条件。"
                                )
                            },
                        },
                        permission_note=(
                            "只生成详细自然语言试卷蓝图原稿和分单元检索需求；不得检索题目、选择题目、"
                            "生成试卷正文、答案、解析、系统ID或系统未提供的评级字段。"
                        ),
                    ),
                )
            blueprint_document = str(
                raw_output.get("blueprint_document")
                if isinstance(raw_output, dict)
                else raw_output
            ).strip()
            if not blueprint_document:
                raise ValueError("paper blueprint agent did not provide natural-language draft")
            compilation = await self.blueprint_compiler.compile(
                context,
                blueprint_document=blueprint_document,
            )
            if compilation.result.status != "compiled":
                details = ", ".join(
                    f"{issue.code}@{issue.field_path}"
                    for issue in compilation.result.issues
                )
                raise ValueError(
                    "paper blueprint draft could not be compiled: " + details
                )
            contract = compilation.result.contract
            normalized = {
                "title": contract.title,
                "scope_summary": contract.scope_summary,
                "duration_minutes": contract.duration_minutes,
                "total_score": contract.total_score,
                "assumptions": contract.assumptions,
                "acceptance_criteria": contract.acceptance_criteria,
                "units": [
                    unit.model_dump(mode="python", exclude={"unit_key"})
                    for unit in contract.units
                ],
            }
            normalized = self._normalize_blueprint(normalized, context)
            user_request = str(context.get("user_request") or "")
            normalized["units"] = self._constrain_units_to_explicit_coverage(
                normalized.get("units", []),
                coverage_topics=self._explicit_coverage_topics(user_request),
            )
            normalized["units"] = self._normalize_hard_count_units(
                normalized.get("units", []),
                explicit_count=explicit_count,
                user_request=user_request,
            )
            normalized["units"] = self._normalize_question_type_mix(
                normalized.get("units", []),
                explicit_types=(
                    list(explicit_distribution)
                    if explicit_distribution
                    else self._explicit_question_types(context)
                ),
            )
            units = [
                BlueprintUnit(
                    unit_id=f"UNIT_{index:02d}",
                    sequence=index,
                    candidate_limit=self._candidate_limit(
                        int(unit.get("required_question_count") or 1)
                    ),
                    **{
                        key: value
                        for key, value in unit.items()
                        if key != "candidate_limit"
                    },
                )
                for index, unit in enumerate(normalized["units"], start=1)
            ]
        except ValidationError as exc:
            raise ValueError("paper blueprint model output violates protocol") from exc
        blueprint = PaperBlueprint(
            blueprint_id=f"BLUEPRINT_{uuid4().hex}",
            title=normalized["title"],
            source_status=normalized["source_status"],
            scope_summary=normalized["scope_summary"],
            duration_minutes=normalized.get("duration_minutes"),
            total_score=normalized.get("total_score"),
            required_total_question_count=explicit_count,
            required_question_type_distribution=explicit_distribution,
            question_count_is_hard_constraint=(
                explicit_count is not None
            ),
            units=units,
            assumptions=normalized.get("assumptions", []),
            acceptance_criteria=normalized.get("acceptance_criteria", []),
        )
        return envelope(context, "expert_agent", "paper_blueprint", blueprint)

    @staticmethod
    def _candidate_limit(required_question_count: int) -> int:
        """System-owned retrieval capacity; never sourced from model prose."""
        return min(50, max(5, required_question_count * 2))

    @staticmethod
    def _explicit_question_count(context: dict[str, Any]) -> int | None:
        request = str(context.get("user_request") or "")
        match = re.search(
            r"(?:包含|共|至少|不少于|生成|出)?\s*(\d+)\s*(?:个|道)?"
            r"(?:[^，。；\n]{0,20})?题(?:目)?",
            request,
        )
        if match and int(match.group(1)) > 0:
            return int(match.group(1))
        constraints = context.get("exam_constraints", {}) or {}
        value = constraints.get("question_count")
        if isinstance(value, int) and value > 0:
            return value
        if isinstance(value, str):
            match = re.search(r"\d+", value)
            if match and int(match.group()) > 0:
                return int(match.group())
        return None

    @staticmethod
    def _explicit_duration_minutes(context: dict[str, Any]) -> int | None:
        request = str(context.get("user_request") or "")
        patterns = (
            r"(?:作答|答题|考试|测试|时长|限时|时间)[^，。；\n\d]{0,8}(\d+)\s*分钟",
            r"(\d+)\s*分钟[^，。；\n]{0,8}(?:作答|答题|考试|测试|时长|限时)",
        )
        for pattern in patterns:
            match = re.search(pattern, request)
            if match and int(match.group(1)) > 0:
                return int(match.group(1))
        constraints = context.get("exam_constraints", {}) or {}
        value = constraints.get("duration_minutes")
        if isinstance(value, int) and value > 0:
            return value
        if isinstance(value, str):
            match = re.search(r"\d+", value)
            if match and int(match.group()) > 0:
                return int(match.group())
        return None

    @staticmethod
    def _explicit_question_types(context: dict[str, Any]) -> list[str]:
        request = str(context.get("user_request") or "")
        aliases = (
            ("单项选择题", ("单项选择题", "单选题")),
            ("多项选择题", ("多项选择题", "多选题")),
            ("判断题", ("判断题",)),
            ("填空题", ("填空题",)),
            ("简答题", ("简答题", "问答题")),
            ("案例分析题", ("案例分析题", "病例分析题", "临床案例题")),
        )
        found = [
            canonical
            for canonical, names in aliases
            if any(name in request for name in names)
        ]
        if "选择题" in request and not found:
            found = ["单项选择题", "多项选择题"]
        if found:
            return found
        constraints = context.get("exam_constraints", {}) or {}
        raw_types = constraints.get("question_types") or constraints.get("question_type")
        if isinstance(raw_types, str):
            raw_types = [raw_types]
        return [str(item) for item in raw_types or [] if str(item).strip()]

    @classmethod
    def _explicit_question_type_distribution(
        cls, context: dict[str, Any]
    ) -> dict[str, int]:
        constraints = context.get("exam_constraints", {}) or {}
        raw_distribution = constraints.get("question_type_distribution") or {}
        if not isinstance(raw_distribution, dict):
            return {}
        aliases = {
            "single_choice": "单项选择题",
            "singlechoice": "单项选择题",
            "单选题": "单项选择题",
            "单项选择": "单项选择题",
            "单项选择题": "单项选择题",
            "multiple_choice": "多项选择题",
            "multiplechoice": "多项选择题",
            "多选题": "多项选择题",
            "多项选择": "多项选择题",
            "多项选择题": "多项选择题",
            "true_false": "判断题",
            "truefalse": "判断题",
            "判断题": "判断题",
            "fill_blank": "填空题",
            "fillblank": "填空题",
            "填空题": "填空题",
            "short_answer": "简答题",
            "shortanswer": "简答题",
            "简答": "简答题",
            "问答题": "简答题",
            "简答题": "简答题",
            "case_quiz": "案例分析题",
            "casequiz": "案例分析题",
            "case_analysis": "案例分析题",
            "caseanalysis": "案例分析题",
            "案例题": "案例分析题",
            "病例分析题": "案例分析题",
            "临床案例题": "案例分析题",
            "案例分析题": "案例分析题",
        }
        normalized: dict[str, int] = {}
        for raw_type, raw_count in raw_distribution.items():
            key = str(raw_type).strip().replace(" ", "").replace("-", "_").lower()
            canonical = aliases.get(key)
            if canonical is None:
                continue
            try:
                count = int(raw_count)
            except (TypeError, ValueError):
                continue
            if count > 0:
                normalized[canonical] = normalized.get(canonical, 0) + count
        return normalized

    @staticmethod
    def _explicit_coverage_topics(user_request: str) -> list[str]:
        """Extract the user's closed coverage list without inheriting stale constraints."""
        match = re.search(
            r"覆盖\s*(.+?)(?=；|;|。|\n|必须|要求|并提供|且提供|$)",
            str(user_request or ""),
        )
        if not match:
            return []
        values = [
            item.strip(" ，、和及与")
            for item in re.split(r"[、，,]|(?:和|及|与)", match.group(1))
            if item.strip(" ，、和及与")
        ]
        if len(values) < 2:
            return []
        named_anchor = next(
            (
                anchor
                for anchor in re.findall(r"[\u4e00-\u9fff]{2,12}(?:汤|散|丸|饮|方)", values[0])
                if anchor
            ),
            "",
        )
        topics: list[str] = []
        for value in values:
            topic = value
            if named_anchor and named_anchor not in topic:
                topic = f"{named_anchor}{topic}"
            if topic not in topics:
                topics.append(topic)
        return topics

    @staticmethod
    def _constrain_units_to_explicit_coverage(
        units: list[dict[str, Any]], *, coverage_topics: list[str]
    ) -> list[dict[str, Any]]:
        if not coverage_topics:
            return units

        def compact(value: Any) -> str:
            return re.sub(r"[\s，、,：:；;·和及与]", "", str(value or ""))

        normalized: list[dict[str, Any]] = []
        unused = list(units)
        for topic in coverage_topics:
            topic_key = compact(topic)
            selected_index = next(
                (
                    index
                    for index, unit in enumerate(unused)
                    if topic_key in compact(
                        f"{unit.get('knowledge_module', '')}{unit.get('retrieval_query', '')}"
                    )
                    or compact(unit.get("knowledge_module")) in topic_key
                ),
                None,
            )
            if selected_index is None:
                normalized.append(
                    {
                        "knowledge_module": topic,
                        "learning_objective": f"掌握{topic}的核心内容",
                        "retrieval_query": topic,
                        "question_type_preferences": [],
                        "required_question_count": 1,
                        "candidate_limit": 10,
                        "selection_rules": [f"只选择与{topic}直接相关的题目"],
                    }
                )
                continue
            selected = dict(unused.pop(selected_index))
            selected["knowledge_module"] = topic
            selected["retrieval_query"] = topic
            normalized.append(selected)
        return normalized

    @staticmethod
    def _normalize_question_type_mix(
        units: list[dict[str, Any]], *, explicit_types: list[str]
    ) -> list[dict[str, Any]]:
        normalized = [dict(unit) for unit in units]
        if not normalized:
            return normalized
        if explicit_types:
            for unit in normalized:
                unit["question_type_preferences"] = list(explicit_types)
            return normalized

        present = {
            str(question_type)
            for unit in normalized
            for question_type in unit.get("question_type_preferences", [])
        }
        choice_types = {"选择题", "单选题", "单项选择题", "多选题", "多项选择题"}
        if len(normalized) < 2 or (present and not present.issubset(choice_types)):
            return normalized

        if not present:
            normalized[0]["question_type_preferences"] = [
                "单项选择题", "多项选择题"
            ]
        normalized[-1]["question_type_preferences"] = ["案例分析题"]
        if len(normalized) >= 3:
            normalized[-2]["question_type_preferences"] = ["简答题"]
        return normalized

    @staticmethod
    def _requested_learning_scope(context: dict[str, Any]) -> dict[str, Any]:
        request = str(context.get("user_request") or "")
        match = re.search(r"第\s*([一二三四五六七八九十\d]+)\s*阶段", request)
        if not match:
            return {}
        numeral = match.group(1)
        chinese_numbers = {
            "一": 1, "二": 2, "三": 3, "四": 4, "五": 5,
            "六": 6, "七": 7, "八": 8, "九": 9, "十": 10,
        }
        stage_number = int(numeral) if numeral.isdigit() else chinese_numbers.get(numeral)
        if stage_number is None:
            return {}
        current_plan = context.get("current_long_term_plan") or {}
        stages = (
            current_plan.get("stages", [])
            if isinstance(current_plan, dict)
            else getattr(current_plan, "stages", [])
        )
        for stage in stages or []:
            value = stage if isinstance(stage, dict) else stage.model_dump()
            raw_stage = value.get("stage", value.get("order"))
            if raw_stage is None:
                stage_id = str(value.get("stage_id") or "")
                stage_match = re.search(r"(\d+)$", stage_id)
                raw_stage = int(stage_match.group(1)) if stage_match else None
            if isinstance(raw_stage, str) and raw_stage.isdigit():
                raw_stage = int(raw_stage)
            if raw_stage == stage_number:
                resolved = {
                    "requested_stage": stage_number,
                    "stage_name": value.get("name", ""),
                    "books": value.get("book") or value.get("books", []),
                    "goal": value.get("goal") or value.get("objective", ""),
                    "exit_evidence": value.get("exit_evidence", ""),
                    "source": "当前长期规划的结构化阶段",
                }
                return {key: item for key, item in resolved.items() if item != ""}
        content = PaperBlueprintAgent._plan_value(current_plan, "content")
        if content:
            chinese_numeral = next(
                (key for key, value in chinese_numbers.items() if value == stage_number),
                "",
            )
            stage_tokens = {
                token for token in (numeral, str(stage_number), chinese_numeral) if token
            }
            numeral_pattern = (
                r"第\s*(?:" + "|".join(re.escape(token) for token in stage_tokens) + r")\s*阶段"
            )
            path_match = re.search(
                numeral_pattern + r"(?P<text>[^；。\n]*(?:。|；)?)",
                content,
            )
            milestone_match = re.search(
                rf"(?:^|\n)\s*{stage_number}\s*[.、]\s*(?P<text>.*?)(?=\n\s*{stage_number + 1}\s*[.、]|\n【|$)",
                content,
                flags=re.S,
            )
            return {
                "requested_stage": stage_number,
                "resolution": "已从当前长期规划正文解析",
                "stage_description": (
                    path_match.group("text").strip(" ；。") if path_match else ""
                ),
                "stage_milestone": (
                    milestone_match.group("text").strip() if milestone_match else ""
                ),
                "source": "当前长期规划正文",
            }
        return {
            "requested_stage": stage_number,
            "resolution": "当前长期规划中未找到对应阶段",
        }

    @staticmethod
    def _plan_value(plan: Any, key: str, default: Any = "") -> Any:
        if isinstance(plan, dict):
            return plan.get(key, default)
        return getattr(plan, key, default)

    @classmethod
    def _planning_context(cls, context: dict[str, Any]) -> dict[str, Any]:
        """Keep model-relevant plan facts without leaking IDs or route internals."""
        long_term = context.get("current_long_term_plan") or {}
        short_term = context.get("current_short_term_plan") or {}
        learning_task = context.get("current_learning_task") or {}

        long_term_plan = {
            "content": cls._plan_value(long_term, "content"),
            "stages": cls._plan_value(long_term, "stages", []),
        }
        short_term_plan = {
            "content": cls._plan_value(short_term, "content"),
            "short_term_focus": cls._plan_value(short_term, "short_term_focus", {}),
            "textbook_selection": cls._plan_value(short_term, "textbook_selection", {}),
        }
        daily_task = {
            "task_content": cls._plan_value(learning_task, "task_content"),
            "estimated_minutes": cls._plan_value(learning_task, "estimated_minutes", None),
            "expected_output": cls._plan_value(learning_task, "expected_output"),
            "completion_criteria": cls._plan_value(learning_task, "completion_criteria"),
        }
        return {
            "long_term_plan": {
                key: value for key, value in long_term_plan.items() if value
            },
            "short_term_plan": {
                key: value for key, value in short_term_plan.items() if value
            },
            "daily_task": {
                key: value for key, value in daily_task.items() if value is not None and value != ""
            },
        }

    @staticmethod
    def _normalize_blueprint(raw_output: Any, context: dict[str, Any]) -> dict[str, Any]:
        raw = dict(raw_output) if isinstance(raw_output, dict) else {}
        constraints = context.get("exam_constraints", {}) or {}
        allowed_source_statuses = {
            "official",
            "user_provided_unverified",
            "practice_sample",
            "pending_confirmation",
        }
        constrained_source_status = constraints.get("source_status")
        source_status = (
            constrained_source_status
            if constrained_source_status in allowed_source_statuses
            else "user_provided_unverified"
        )
        units = raw.get("units") or raw.get("blueprint") or []
        normalized_units = []
        for index, item in enumerate(
            units[:20] if isinstance(units, list) else [],
            start=1,
        ):
            if not isinstance(item, dict):
                continue
            unit = dict(item)
            knowledge_module = PaperBlueprintAgent._bounded_text(
                unit.get("knowledge_module"),
                default=f"知识单元{index}",
                maximum=300,
            )
            retrieval_query = PaperBlueprintAgent._bounded_text(
                unit.get("retrieval_query")
                or unit.get("search_query")
                or knowledge_module,
                default=knowledge_module,
                maximum=300,
            )
            required_count = PaperBlueprintAgent._positive_int(
                unit.get("required_question_count"),
                default=1,
                maximum=100,
            )
            candidate_limit = PaperBlueprintAgent._positive_int(
                unit.get("candidate_limit"),
                default=max(10, required_count),
                maximum=50,
            )
            normalized_units.append({
                "knowledge_module": knowledge_module,
                "learning_objective": PaperBlueprintAgent._bounded_text(
                    unit.get("learning_objective"),
                    default="掌握该知识单元的核心内容",
                    maximum=500,
                ),
                "retrieval_query": retrieval_query,
                "question_type_preferences": PaperBlueprintAgent._string_list(
                    unit.get("question_type_preferences")
                ),
                "required_question_count": required_count,
                "score_total": PaperBlueprintAgent._positive_float(
                    unit.get(
                        "score_total",
                        unit.get("target_score", unit.get("assigned_score")),
                    )
                ),
                "candidate_limit": candidate_limit,
                "selection_rules": PaperBlueprintAgent._string_list(
                    unit.get("selection_rules") or unit.get("selection_rule")
                ),
            })
        assumptions = raw.get("assumptions", [])
        if isinstance(assumptions, dict):
            assumptions = [f"{key}：{value}" for key, value in assumptions.items()]
        assumptions = PaperBlueprintAgent._string_list(assumptions)
        model_source_status = raw.get("source_status")
        if (
            model_source_status
            and model_source_status not in allowed_source_statuses
        ):
            assumptions = [
                *assumptions,
                f"模型来源说明：{model_source_status}；正式来源状态由系统设为{source_status}。",
            ]
        acceptance = raw.get("acceptance_criteria") or raw.get("validation_checklist") or []
        acceptance = PaperBlueprintAgent._string_list(acceptance)
        duration = PaperBlueprintAgent._explicit_duration_minutes(context)
        model_duration = raw.get("duration_minutes") or raw.get("duration")
        if model_duration and duration is None:
            assumptions = [
                *assumptions,
                "模型建议的作答时长未得到用户或试卷约束确认，正式时长按实际题目工作量计算。",
            ]
        total_score = PaperBlueprintAgent._positive_float(
            raw.get("total_score") or constraints.get("total_score")
        )
        return {
            "title": PaperBlueprintAgent._bounded_text(
                raw.get("title"), default="章节模拟练习卷", maximum=300
            ),
            "source_status": source_status,
            "scope_summary": PaperBlueprintAgent._bounded_text(
                raw.get("scope_summary") or context.get("user_request"),
                default="围绕用户指定主题组卷",
                maximum=1_000,
            ),
            "duration_minutes": duration,
            "total_score": total_score,
            "units": normalized_units,
            "assumptions": assumptions,
            "acceptance_criteria": acceptance,
        }

    @staticmethod
    def _bounded_text(value: Any, *, default: str, maximum: int) -> str:
        text = str(value).strip() if value is not None else ""
        return (text or default)[:maximum]

    @staticmethod
    def _optional_bounded_text(value: Any, *, maximum: int) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        return text[:maximum] if text else None

    @staticmethod
    def _string_list(value: Any) -> list[str]:
        if value is None:
            return []
        values = value if isinstance(value, list) else [value]
        return [str(item).strip() for item in values if str(item).strip()]

    @staticmethod
    def _positive_int(value: Any, *, default: int, maximum: int) -> int:
        try:
            parsed = int(str(value).strip().replace("题", ""))
        except (TypeError, ValueError):
            parsed = default
        return min(maximum, max(1, parsed))

    @staticmethod
    def _positive_float(value: Any) -> float | None:
        if value is None:
            return None
        try:
            parsed = float(str(value).strip().replace("分", ""))
        except (TypeError, ValueError):
            return None
        return parsed if parsed > 0 else None

    @staticmethod
    def _normalize_hard_count_units(
        units: list[dict[str, Any]],
        *,
        explicit_count: int | None,
        user_request: str,
    ) -> list[dict[str, Any]]:
        if not units or explicit_count is None:
            return units
        unit_count = len(units)
        base, remainder = divmod(explicit_count, unit_count)
        choice_specialty = "选择题" in user_request
        normalized: list[dict[str, Any]] = []
        for index, source in enumerate(units):
            unit = dict(source)
            required = base + (1 if index < remainder else 0)
            unit["required_question_count"] = max(1, required)
            unit["candidate_limit"] = min(
                50,
                max(
                    int(unit.get("candidate_limit") or 1),
                    unit["required_question_count"] + 2,
                    unit["required_question_count"] * 2,
                ),
            )
            if choice_specialty:
                unit["question_type_preferences"] = [
                    "单项选择题",
                    "多项选择题",
                ]
            normalized.append(unit)
        return normalized
