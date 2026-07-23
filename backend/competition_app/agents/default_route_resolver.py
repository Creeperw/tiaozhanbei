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
        (
            "credential",
            (
                "资格证",
                "资格考试",
                "执业医师",
                "执业药师",
                "考证",
                "证书",
                "规定学历",
                "中医（专长）医师",
                "传统医学师承",
                "确有专长",
            ),
        ),
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
        trusted_target_resolution = self._trusted_learning_target_resolution(context)
        if trusted_target_resolution is not None:
            return envelope(
                context,
                "default_route_resolver",
                "resolved_planning_route",
                trusted_target_resolution,
            )
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
        clarified_request = (
            None
            if explicit_route_id
            else self._clarified_explicit_request_resolution(
                context, fallback_goal_type=goal_type
            )
        )
        ambiguous_request = (
            None
            if explicit_route_id or clarified_request is not None
            else self._ambiguous_explicit_request_resolution(
                context, fallback_goal_type=goal_type
            )
        )
        model_selects_route = (
            self._chat_model is not None
            and (
                context.get("plan_scope") == "long_term"
                or self._needs_inline_parent_route(context)
            )
        )
        if clarified_request is not None:
            resolution = clarified_request
        elif ambiguous_request is not None:
            resolution = ambiguous_request
        elif explicit_route_id or not model_selects_route:
            resolution = self._repository.resolve(
                goal_type=goal_type,
                goal_name=goal_name,
                explicit_route_id=explicit_route_id,
            )
        else:
            resolution = await self._resolve_with_agent(
                context, goal_type=goal_type, goal_name=goal_name
            )
        if (
            self._textbook_repository is not None
            and ambiguous_request is None
            and resolution.match_reason != "agent_requires_clarification"
        ):
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
            if textbook_route.planning_status in {
                "needs_clarification",
                "unmatched",
            }:
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
        if plan_scope == "long_term":
            # A generic request such as “结合我的状态制定长期规划” should
            # continue the user's approved goal instead of asking them to repeat
            # it. An explicit goal in this turn still takes precedence.
            if (
                self._classify_goal(self._optional_text(context.get("user_request")))
                or self._has_explicit_target_change(context)
            ):
                return None
            parent = (
                context.get("current_long_term_plan")
                or context.get("current_short_term_plan")
                or {}
            )
            inherited_reason = "inherited_current_plan"
        elif plan_scope == "short_term":
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
            if plan_scope == "long_term":
                return self._clarification_resolution(
                    inherited.goal_type,
                    inherited.goal_name,
                    (
                        "现有长期规划尚未绑定已批准的学习路线，不能继续沿用临时教材。"
                        "请明确具体考试、升学或课程目标；如为中医类别执业医师，"
                        "请确认报考路径。"
                    ),
                )
            return inherited.model_copy(update={"match_reason": inherited_reason})

        # Plans created before competing-route clarification was enforced may
        # persist one selected route while their goal text still names two or
        # more mutually exclusive routes. Never perpetuate that contaminated
        # choice during a generic replan. A unique resume answer can repair it.
        inherited_goal = self._repository.resolve(
            goal_type=inherited.goal_type,
            goal_name=inherited.goal_name,
        )
        if inherited_goal.match_reason in {
            "ambiguous_canonical_name",
            "ambiguous_alias",
            "ambiguous_embedded_alias",
        }:
            answer = self._optional_text(context.get("latest_resume_answer"))
            answer_resolution = (
                self._repository.resolve(
                    goal_type=self._classify_goal(answer) or inherited.goal_type,
                    goal_name=answer,
                )
                if answer is not None
                else None
            )
            if (
                answer_resolution is None
                or answer_resolution.planning_status != "approved_route"
                or answer_resolution.match_reason
                not in {"canonical_name", "alias", "embedded_alias"}
            ):
                return self._clarification_resolution(
                    inherited.goal_type,
                    inherited.goal_name,
                    (
                        "现有长期规划来自一次多选报考路径，不能继续沿用。"
                        "请只确认一项：规定学历路径、中医（专长）医师资格考核，"
                        "或传统医学师承/确有专长人员考核。"
                    ),
                )
            inherited = answer_resolution
            inherited_reason = "clarification_answer"

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
        # The inherited textbook route was already selected and persisted by
        # the system. Revalidate it against the approved exam-route binding,
        # but never reinterpret free-form plan prose: a plan can legitimately
        # mention “中西医结合” without changing the user's exam identity.
        textbook_route = self._textbook_repository.resolve(
            exam_route_id=trusted.route_id,
            goal_text=inherited_textbook_name,
        )
        return trusted.model_copy(update={"textbook_route": textbook_route})

    def _trusted_learning_target_resolution(
        self, context: dict[str, Any]
    ) -> ResolvedPlanningRoute | None:
        """Resolve a persisted explicit target before asking the user again."""

        if (
            self._classify_goal(self._optional_text(context.get("user_request")))
            or self._has_explicit_target_change(context)
        ):
            return None
        target = context.get("learning_target")
        if not isinstance(target, dict) or not target.get("is_active", True):
            return None
        goal_name = self._optional_text(target.get("exam_name"))
        target_type = self._optional_text(target.get("target_type"))
        if goal_name is None:
            return None
        goal_type = {
            "certification": "credential",
            "graduate_entrance_exam": "admission",
        }.get(
            target_type or "",
            target_type or self._classify_goal(goal_name) or "learning",
        )
        trusted = self._repository.resolve(
            goal_type=goal_type,
            goal_name=goal_name,
        )
        if trusted.planning_status != "approved_route":
            return None
        trusted = trusted.model_copy(update={"match_reason": "active_learning_target"})
        if self._textbook_repository is None:
            return trusted
        textbook_route = self._textbook_repository.resolve(
            exam_route_id=trusted.route_id,
            goal_text=f"{goal_name} {context.get('user_request', '')}".strip(),
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
                        "learner_context": self._route_relevant_learner_context(context),
                        "route_catalog": route_catalog,
                        "selection_rules": [
                            "只能选择 route_catalog 中的 route_id。",
                            "只出现课程或学科名称，无法区分课程学习、考证、升学或其他目的时必须追问。",
                            "用户明确表示仅学习课程而非考试时，才选择对应课程路线。",
                            "用户本轮以补充、改为等方式明确修正旧目标时，以本轮明确目标为准。",
                            "目录没有覆盖目标或有多个合理候选时必须追问。",
                            "用户的专业或资格背景与规定学历路径明显不一致，且未明确报考途径时必须追问。",
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
                if (
                    selected.route_id == "tcm_physician_standard_degree"
                    and self._requires_physician_path_clarification(context)
                ):
                    return self._clarification_resolution(
                        goal_type,
                        goal_name,
                        decision.clarification_question
                        or self._physician_path_fallback_question(context),
                    )
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
        if self._requires_physician_path_clarification(context):
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

    @classmethod
    def _requires_physician_path_clarification(cls, context: dict[str, Any]) -> bool:
        request = cls._optional_text(context.get("user_request")) or ""
        profile = context.get("user_profile") if isinstance(context.get("user_profile"), dict) else {}
        goal = " ".join(
            str(profile.get(key) or "")
            for key in ("learning_goal", "goals", "learning_goals")
        )
        combined = f"{request} {goal}"
        if "中医" not in combined or "执业医师" not in combined:
            return False
        if any(marker in combined for marker in ("规定学历", "中医（专长）", "中医(专长)", "师承", "确有专长")):
            return False
        background = " ".join(
            str(profile.get(key) or "")
            for key in ("learning_background", "education", "user_major_or_profession", "learner_group")
        ).strip()
        if "专业" not in background:
            return False
        medical_markers = ("中医", "中西医", "医学", "针灸推拿")
        return not any(marker in background for marker in medical_markers)

    @staticmethod
    def _route_relevant_learner_context(context: dict[str, Any]) -> dict[str, Any]:
        profile = context.get("user_profile") if isinstance(context.get("user_profile"), dict) else {}
        return {
            key: profile.get(key)
            for key in ("learning_goal", "learning_background", "education", "user_major_or_profession", "learner_group")
            if profile.get(key) not in (None, "", [], {})
        }

    @staticmethod
    def _physician_path_fallback_question(context: dict[str, Any]) -> str:
        profile = context.get("user_profile") if isinstance(context.get("user_profile"), dict) else {}
        background = str(profile.get("learning_background") or "当前专业背景").strip()
        return (
            f"你提到自己是{background}。为了匹配正确路线，请说明计划通过规定学历、"
            "中医（专长）医师考核，还是传统医学师承/确有专长途径报考？"
        )

    def _ambiguous_explicit_request_resolution(
        self,
        context: dict[str, Any],
        *,
        fallback_goal_type: str,
    ) -> ResolvedPlanningRoute | None:
        """Stop before model selection when one turn names competing routes."""
        request = self._optional_text(context.get("user_request"))
        if request is None:
            return None
        request_goal_type = self._classify_goal(request) or fallback_goal_type
        candidate = self._repository.resolve(
            goal_type=request_goal_type,
            goal_name=request,
        )
        if candidate.match_reason not in {
            "ambiguous_canonical_name",
            "ambiguous_alias",
            "ambiguous_embedded_alias",
        }:
            return None
        return self._clarification_resolution(
            request_goal_type,
            request,
            (
                "你同时选择了多个不同的报考路径，它们不能合并为同一条长期规划。"
                "请只确认一项：规定学历路径、中医（专长）医师资格考核，"
                "或传统医学师承/确有专长人员考核。"
            ),
        )

    def _clarified_explicit_request_resolution(
        self,
        context: dict[str, Any],
        *,
        fallback_goal_type: str,
    ) -> ResolvedPlanningRoute | None:
        """Use a unique resume answer to settle a previously ambiguous request."""
        request = self._optional_text(context.get("user_request"))
        answer = self._optional_text(context.get("latest_resume_answer"))
        if request is None or answer is None:
            return None
        request_goal_type = self._classify_goal(request) or fallback_goal_type
        previous = self._repository.resolve(
            goal_type=request_goal_type,
            goal_name=request,
        )
        if previous.match_reason not in {
            "ambiguous_canonical_name",
            "ambiguous_alias",
            "ambiguous_embedded_alias",
        }:
            return None
        answer_goal_type = self._classify_goal(answer) or request_goal_type
        selected = self._repository.resolve(
            goal_type=answer_goal_type,
            goal_name=answer,
        )
        if (
            selected.planning_status != "approved_route"
            or selected.match_reason
            not in {"canonical_name", "alias", "embedded_alias"}
        ):
            return None
        return selected.model_copy(update={"match_reason": "clarification_answer"})

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
        if self._has_explicit_target_change(context):
            goal_type = self._classify_goal(user_request)
            if (
                goal_type == "credential"
                and "执业医师" in user_request
                and not any(
                    category in user_request
                    for category in ("中医", "中西医", "临床", "口腔", "公共卫生")
                )
                and structured_goal is not None
                and structured_goal[0] == "credential"
                and "执业医师" in str(structured_goal[1] or "")
            ):
                return "credential", str(structured_goal[1]), False
            return goal_type or "learning", user_request, goal_type is None
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

    @classmethod
    def _has_explicit_target_change(cls, context: dict[str, Any]) -> bool:
        if (
            context.get("plan_scope") != "long_term"
        ):
            return False
        request = cls._optional_text(context.get("user_request")) or ""
        target_markers = (
            "我想学习",
            "我要学习",
            "我想学",
            "我要学",
            "我想考",
            "我要考",
            "目标改为",
            "改成学习",
            "改学",
            "转向",
            "报考",
        )
        return any(marker in request for marker in target_markers)

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
        learning_goal = self._optional_text(user_profile.get("learning_goal"))
        if learning_goal:
            return self._classify_goal(learning_goal), learning_goal
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
