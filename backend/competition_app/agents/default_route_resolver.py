from __future__ import annotations

from typing import Any

from competition_app.agents.common import envelope
from competition_app.contracts.agent_context import build_model_context
from competition_app.contracts.base import AgentEnvelope
from competition_app.contracts.default_route import ResolvedPlanningRoute
from competition_app.llm.base import ChatModel
from competition_app.llm.prompt_skills import prompt_skill_registry
from competition_app.llm.schemas import RouteSelectionModelOutput
from competition_app.services.default_route import DefaultRouteRepository
from competition_app.services.textbook_route import TextbookRouteRepository


class DefaultRouteResolverAgent:
    """Lets the model interpret intent while the system owns the route catalog."""

    _MIN_SELECTION_CONFIDENCE = 0.65

    _STRUCTURED_TYPE_KEYS = ("goal_type", "type")
    _STRUCTURED_NAME_KEYS = ("goal_name", "name", "title")
    _GOAL_TYPE_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
        ("professional_title", ("职称", "主管中药师")),
        ("credential", ("资格证", "资格考试", "执业医师", "执业药师", "考证", "证书")),
        ("admission", ("入学", "考研", "研究生", "招生", "录取")),
        ("research", ("课题研究", "开展研究", "研究", "课题", "论文", "文献")),
        ("course", ("课程", "学期", "方剂学", "中药学", "中医基础理论", "中医诊断学")),
        ("competency", ("能力", "技能", "胜任", "辨证论治")),
        ("habit", ("习惯", "每日", "每天", "坚持", "打卡")),
        ("literacy", ("素养", "阅读", "经典", "读书")),
    )

    def __init__(
        self,
        repository: DefaultRouteRepository,
        textbook_repository: TextbookRouteRepository | None = None,
        chat_model: ChatModel | None = None,
    ) -> None:
        self._repository = repository
        self._textbook_repository = textbook_repository
        self._chat_model = chat_model

    async def run(
        self, context: dict[str, Any]
    ) -> AgentEnvelope[ResolvedPlanningRoute]:
        inherited_resolution = self._inherited_parent_resolution(context)
        if inherited_resolution is not None:
            return envelope(
                context,
                "default_route_resolver",
                "resolved_planning_route",
                inherited_resolution,
            )
        goal_type, goal_name, uncertain = self._extract_goal(context)
        explicit_route_id = self._optional_text(context.get("route_id"))
        model_selects_route = (
            self._chat_model is not None
            and (
                context.get("plan_scope") == "long_term"
                or self._needs_inline_parent_route(context)
            )
        )
        if explicit_route_id or not model_selects_route:
            resolution = self._repository.resolve(
                goal_type=goal_type,
                goal_name=goal_name,
                explicit_route_id=explicit_route_id,
            )
        else:
            resolution = await self._resolve_with_agent(
                context, goal_type=goal_type, goal_name=goal_name
            )
        if self._textbook_repository is not None:
            request = self._optional_text(context.get("user_request")) or ""
            textbook_route = self._textbook_repository.resolve(
                exam_route_id=(
                    resolution.route_id
                    if resolution.planning_status == "approved_route"
                    else None
                ),
                goal_text=f"{goal_name} {request}".strip(),
            )
            unknowns = list(resolution.unknowns_to_confirm)
            if textbook_route.planning_status == "needs_clarification":
                unknowns.extend(textbook_route.clarification_questions)
            resolution = resolution.model_copy(
                update={
                    "textbook_route": textbook_route,
                    "unknowns_to_confirm": list(dict.fromkeys(unknowns)),
                }
            )
        if uncertain:
            resolution = self._with_uncertainty(resolution)
        return envelope(
            context,
            "default_route_resolver",
            "resolved_planning_route",
            resolution,
        )

    def _inherited_parent_resolution(
        self, context: dict[str, Any]
    ) -> ResolvedPlanningRoute | None:
        plan_scope = context.get("plan_scope")
        if plan_scope == "short_term":
            parent = context.get("current_long_term_plan") or {}
            inherited_reason = "inherited_long_term_plan"
        elif plan_scope == "daily_task":
            parent = (
                context.get("current_short_term_plan")
                or context.get("current_long_term_plan")
                or {}
            )
            inherited_reason = "inherited_short_term_plan"
        else:
            return None

        route_value = (
            parent.get("planning_route")
            if isinstance(parent, dict)
            else getattr(parent, "planning_route", None)
        )
        if not route_value:
            return None
        try:
            inherited = ResolvedPlanningRoute.model_validate(route_value)
        except ValueError:
            return None
        if inherited.planning_status != "approved_route":
            return inherited.model_copy(update={"match_reason": inherited_reason})

        approved = self._repository.get(
            str(inherited.route_id), str(inherited.route_version)
        )
        if approved is None:
            return None
        trusted = self._repository.resolve(
            goal_type=approved.goal_type,
            goal_name=approved.goal_name,
            explicit_route_id=approved.route_id,
        ).model_copy(update={"match_reason": inherited_reason})
        if self._textbook_repository is None:
            return trusted

        inherited_textbook = inherited.textbook_route
        inherited_textbook_name = (
            inherited_textbook.route.goal_name
            if inherited_textbook is not None and inherited_textbook.route is not None
            else ""
        )
        parent_content = (
            parent.get("content", "")
            if isinstance(parent, dict)
            else getattr(parent, "content", "")
        )
        textbook_route = self._textbook_repository.resolve(
            exam_route_id=trusted.route_id,
            goal_text=(
                f"{inherited_textbook_name} {parent_content} "
                f"{context.get('user_request', '')}"
            ).strip(),
        )
        return trusted.model_copy(update={"textbook_route": textbook_route})

    async def _resolve_with_agent(
        self,
        context: dict[str, Any],
        *,
        goal_type: str,
        goal_name: str,
    ) -> ResolvedPlanningRoute:
        prompt_skill = prompt_skill_registry.load(
            "default_route_resolver", "route_selection"
        )
        route_catalog = self._repository.route_selection_catalog()
        try:
            raw_output = await self._chat_model.complete_json(
                "default_route_resolver",
                build_model_context(
                    context,
                    target_agent="default_route_resolver",
                    prompt_skill=prompt_skill,
                    payload={
                        "user_request": context.get("user_request", ""),
                        "structured_goal": {
                            "goal_type": goal_type,
                            "goal_name": goal_name,
                        },
                        "route_catalog": route_catalog,
                        "selection_rules": [
                            "只能选择 route_catalog 中的 route_id。",
                            "只出现课程或学科名称，无法区分课程学习、考证、升学或其他目的时必须追问。",
                            "用户明确表示仅学习课程而非考试时，才选择对应课程路线。",
                            "用户本轮以补充、改为等方式明确修正旧目标时，以本轮明确目标为准。",
                            "目录没有覆盖目标或有多个合理候选时必须追问。",
                        ],
                        "output_schema": RouteSelectionModelOutput.model_json_schema(),
                    },
                    permission_note=(
                        "只可选择系统提供的已批准路线 ID 或提出一个必要追问；"
                        "不得生成、修改路线、教材阶段、版本、状态或前置条件。"
                    ),
                ),
            )
            decision = RouteSelectionModelOutput.model_validate(raw_output)
        except (ValueError, TypeError):
            catalog_match = self._approved_explicit_request_match(context)
            if catalog_match is not None:
                return catalog_match
            return self._clarification_resolution(
                goal_type,
                goal_name,
                "暂时无法可靠识别学习路线，请说明具体考试、升学目标、专业方向，或确认仅进行课程学习。",
            )

        if (
            decision.decision == "select"
            and decision.confidence >= self._MIN_SELECTION_CONFIDENCE
            and decision.selected_route_id
        ):
            selected = self._repository.get(decision.selected_route_id)
            if selected is not None:
                return self._repository.resolve(
                    goal_type=goal_type,
                    goal_name=goal_name,
                    explicit_route_id=selected.route_id,
                ).model_copy(update={"match_reason": "agent_selected"})

        catalog_match = self._approved_explicit_request_match(context)
        if catalog_match is not None:
            return catalog_match

        question = decision.clarification_question or (
            "当前没有可确认的已批准路线，请说明具体考试、升学目标、专业方向，"
            "或确认仅进行课程学习。"
        )
        return self._clarification_resolution(goal_type, goal_name, question)

    def _approved_explicit_request_match(
        self,
        context: dict[str, Any],
    ) -> ResolvedPlanningRoute | None:
        """Use the approved catalog when the model over-clarifies an exact intent."""
        request = self._optional_text(context.get("user_request"))
        request_type = self._classify_goal(request)
        if request is None or request_type is None or request_type == "course":
            return None
        resolution = self._repository.resolve(
            goal_type=request_type,
            goal_name=request,
        )
        if (
            resolution.planning_status != "approved_route"
            or resolution.match_reason
            not in {"canonical_name", "alias", "embedded_alias"}
        ):
            return None
        return resolution.model_copy(update={"match_reason": "agent_catalog_fallback"})

    @staticmethod
    def _needs_inline_parent_route(context: dict[str, Any]) -> bool:
        if context.get("plan_scope") != "short_term":
            return False
        parent = context.get("current_long_term_plan") or {}
        if not isinstance(parent, dict):
            return False
        return bool(parent.get("content")) and not parent.get("planning_route")

    @staticmethod
    def _clarification_resolution(
        goal_type: str, goal_name: str, question: str
    ) -> ResolvedPlanningRoute:
        return ResolvedPlanningRoute(
            goal_type=goal_type,
            goal_name=goal_name,
            planning_status="provisional",
            match_reason="agent_requires_clarification",
            unknowns_to_confirm=[question],
        )

    def _extract_goal(self, context: dict[str, Any]) -> tuple[str, str, bool]:
        user_request = self._optional_text(context.get("user_request")) or "未提供学习目标"
        structured_goal = self._first_structured_goal(context.get("user_profile"))
        if structured_goal is not None:
            goal_type, goal_name = structured_goal
            if goal_type is not None:
                return goal_type, goal_name or user_request, False

            classified_type = self._classify_goal(goal_name)
            if classified_type is None:
                classified_type = self._classify_goal(user_request)
            return classified_type or "literacy", goal_name or user_request, classified_type is None

        goal_type = self._classify_goal(user_request)
        return goal_type or "literacy", user_request, goal_type is None

    def _first_structured_goal(self, user_profile: Any) -> tuple[str | None, str | None] | None:
        if not isinstance(user_profile, dict):
            return None
        goals = user_profile.get("goals")
        candidates = [goals] if isinstance(goals, dict) else goals if isinstance(goals, list) else []
        for goal in candidates:
            if not isinstance(goal, dict):
                continue
            goal_type = self._first_text(goal, self._STRUCTURED_TYPE_KEYS)
            goal_name = self._first_text(goal, self._STRUCTURED_NAME_KEYS)
            if goal_type or goal_name:
                return goal_type, goal_name
            long_term_goal = self._optional_text(goal.get("long_term_goal"))
            if long_term_goal:
                return self._classify_goal(long_term_goal), long_term_goal
        return None

    @classmethod
    def _classify_goal(cls, text: str | None) -> str | None:
        if text is None:
            return None
        for goal_type, keywords in cls._GOAL_TYPE_KEYWORDS:
            if any(keyword in text for keyword in keywords):
                return goal_type
        return None

    @staticmethod
    def _first_text(value: dict[str, Any], keys: tuple[str, ...]) -> str | None:
        for key in keys:
            text = DefaultRouteResolverAgent._optional_text(value.get(key))
            if text:
                return text
        return None

    @staticmethod
    def _optional_text(value: Any) -> str | None:
        return value.strip() if isinstance(value, str) and value.strip() else None

    @staticmethod
    def _with_uncertainty(resolution: ResolvedPlanningRoute) -> ResolvedPlanningRoute:
        if resolution.planning_status == "approved_route":
            return resolution
        return resolution.model_copy(
            update={
                "unknowns_to_confirm": [
                    *resolution.unknowns_to_confirm,
                    "无法确定用户目标类别；已保守按 literacy 暂定，需用户确认。",
                ]
            }
        )
