from __future__ import annotations

from datetime import datetime, timezone
import re
from typing import Any
from uuid import uuid4

from competition_app.contracts.default_route import ResolvedPlanningRoute
from competition_app.contracts.learning_plan import (
    LearningPlanProposal,
    LearningPlanResult,
    LearningTask,
    LongTermPlan,
    ShortTermPlan,
)
from competition_app.repositories.learning_plan import (
    InMemoryLearningPlanRepository,
    LearningPlanRepository,
)
from competition_app.services.default_route import DefaultRouteRepository


_REAL_PATIENT_PATTERN = re.compile(
    r"(?:真实|现实|当前|该|这位|此)(?:的)?患者"
    r"|为(?:该|这位|此|当前)?患者"
    r"|针对(?:该|这位|此|当前|真实|现实)?患者"
    r"|患者|患儿|病人|我家孩子|孩子|宝宝"
)
_UNSAFE_CLINICAL_INSTRUCTION_PATTERNS = (
    re.compile(
        r"(?:个体化|具体)诊断(?:结论|建议)?"
        r"|(?:给出|作出|确定|判断).{0,8}诊断"
        r"|(?:诊断|判定).{0,8}(?:为|属于)"
    ),
    re.compile(r"(?:开具|制定|提供|推荐|调整).{0,12}(?:个体化)?处方|个体化处方"),
    re.compile(
        r"(?:口服|服用|用药|给药|注射|煎服|开具|给予|吃|喝|调整.{0,8}(?:至|为)?)"
        r".{0,20}\d+(?:\.\d+)?\s*(?:毫克|克|mg|g|ml|毫升|片|粒|丸|次)"
    ),
    re.compile(r"(?:保证|确保|承诺).{0,12}(?:治愈|疗效|有效|康复)|疗效承诺"),
)
_ADVANCED_CLINICAL_PATTERN = re.compile(
    r"(?:高级|高阶).{0,12}(?:临床|实践技能)"
    r"|(?:临床|实践技能).{0,12}(?:高级|高阶)"
    r"|独立(?:完成|开展|承担).{0,12}(?:临床|诊疗|实践技能)"
)
_FORMAL_EVALUATION_PATTERN = re.compile(
    r"(?:导师|指导老师).{0,8}(?:监督|审核|评价|签字|考核)"
    r"|(?:监督|审核|评价|签字|考核).{0,8}(?:导师|指导老师)"
    r"|(?:导师|指导老师)指导下.{0,16}(?:临床|诊疗|实践技能|操作)"
    r"|教师评价|正式评价|正式考核|签字评价|执业资质"
)
_NEGATED_EDUCATION_PATTERN = re.compile(
    r"(?:教材|教学|反例|法规|安全边界).{0,30}(?:不得|不应|禁止|不要|不能)"
    r"|(?:不得|不应|禁止|不要|不能).{0,30}(?:诊断|处方|服药|用药|剂量|治疗)"
)
_LONG_TERM_CONTENT_SECTIONS = (
    "【最终目标】",
    "【能力路径与阶段】",
    "【阶段里程碑】",
    "【资源预算】",
    "【重规划条件】",
    "【保温底线】",
)


def _all_text(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [text for item in value.values() for text in _all_text(item)]
    if isinstance(value, (list, tuple)):
        return [text for item in value for text in _all_text(item)]
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        return _all_text(model_dump())
    return []


def validate_medical_education_safety(proposal: LearningPlanProposal) -> None:
    """Reject only real-patient instructions that cross the teaching boundary."""

    safety_surface = {
        "long_term_plan_content": proposal.long_term_plan_content,
        "short_term_plan_content": proposal.short_term_plan_content,
        "goal_contract": proposal.goal_contract,
        "milestones": proposal.milestones,
        "short_term_learning_package": proposal.short_term_learning_package,
        "recovery_policy": proposal.recovery_policy,
        "task_proposal": proposal.task_proposal,
    }
    for text in _all_text(safety_surface):
        for clause in re.split(r"[。！？；;\n]+|(?:，|,)?(?:但|但是|然而|不过)", text):
            if (
                not _NEGATED_EDUCATION_PATTERN.search(clause)
                and _REAL_PATIENT_PATTERN.search(clause)
                and any(
                    pattern.search(clause)
                    for pattern in _UNSAFE_CLINICAL_INSTRUCTION_PATTERNS
                )
            ):
                raise ValueError(
                    "medical education safety boundary forbids real-patient diagnosis, "
                    "prescription, medication dose, or efficacy instructions"
                )


def _validate_route(
    route: ResolvedPlanningRoute | None,
    repository: DefaultRouteRepository | None,
    assumptions: list[str],
    unknowns_to_confirm: list[str],
) -> None:
    if route is None:
        return
    if route.planning_status not in {"approved_route", "provisional"}:
        raise ValueError(
            "planning route status must be 'approved_route' or 'provisional'"
        )
    if route.planning_status == "approved_route":
        if not route.route_id or route.route_version is None:
            raise ValueError("approved plan requires an approved route ID and version")
        if route.route_status != "approved":
            raise ValueError("approved plan route_status must be approved")
        approved = (
            repository.get(route.route_id, str(route.route_version))
            if repository is not None
            else None
        )
        if approved is None:
            raise ValueError("approved route ID/version could not be resolved")
        if approved.status != "approved" or approved.route_status != "approved":
            raise ValueError("approved plan route must have status approved")
        return
    if route.route_id is not None or route.route_version is not None or route.route_status is not None:
        raise ValueError(
            "provisional plan must not reference an approved, candidate, or unknown route"
        )
    if not any(item.strip() for item in [*assumptions, *unknowns_to_confirm]):
        raise ValueError("provisional plan requires assumptions or unknowns_to_confirm")


def _validate_milestones(goal_contract: Any, milestones: list[Any]) -> None:
    for milestone in milestones:
        milestone_id = LearningPlanService._field(milestone, "milestone_id") or "unknown"
        evidence = LearningPlanService._field(milestone, "evidence_required") or []
        if not any(isinstance(item, str) and item.strip() for item in evidence):
            raise ValueError(
                f"milestone {milestone_id} requires observable exit or acceptance evidence"
            )
        milestone_text = "\n".join(_all_text(milestone))
        if (
            _ADVANCED_CLINICAL_PATTERN.search(milestone_text)
            and not _FORMAL_EVALUATION_PATTERN.search(milestone_text)
        ):
            raise ValueError(
                "advanced clinical capability requires mentor or formal evaluation boundary"
            )

    goal_text = "\n".join(_all_text(goal_contract))
    if (
        _ADVANCED_CLINICAL_PATTERN.search(goal_text)
        and not _FORMAL_EVALUATION_PATTERN.search(goal_text)
    ):
        raise ValueError(
            "advanced clinical capability requires mentor or formal evaluation boundary"
        )


def _validate_budget(
    proposal: LearningPlanProposal,
    short_term_package: Any,
    available_minutes: int | None,
) -> None:
    if available_minutes is None:
        return
    if (
        isinstance(available_minutes, bool)
        or not isinstance(available_minutes, int)
        or available_minutes <= 0
    ):
        raise ValueError("available_minutes must be a positive integer")
    if proposal.task_proposal.estimated_minutes > available_minutes:
        raise ValueError("current learning task exceeds available_minutes")
    if short_term_package is None:
        return

    task_blocks = LearningPlanService._field(short_term_package, "task_blocks") or []
    structured_block_minutes = [
        LearningPlanService._field(block, "estimated_minutes")
        for block in task_blocks
        if LearningPlanService._field(block, "estimated_minutes") is not None
    ]
    if structured_block_minutes and len(structured_block_minutes) != len(task_blocks):
        raise ValueError(
            "short-term task_blocks must be all structured or all legacy strings"
        )
    task_minutes = (
        sum(int(minutes) for minutes in structured_block_minutes)
        if structured_block_minutes
        else proposal.task_proposal.estimated_minutes
    )
    auxiliary_minutes = sum(
        int(LearningPlanService._field(short_term_package, field_name) or 0)
        for field_name in ("review_minutes", "maintenance_minutes", "buffer_minutes")
    )
    if task_minutes + auxiliary_minutes > available_minutes:
        raise ValueError("short-term structured total exceeds available_minutes")


def _validate_current_task_matches_package(
    proposal: LearningPlanProposal,
    short_term_package: Any,
) -> None:
    if short_term_package is None:
        return
    task_text = "".join(proposal.task_proposal.task_content.split()).casefold()
    task_blocks = LearningPlanService._field(short_term_package, "task_blocks") or []
    block_texts = [
        str(
            LearningPlanService._field(block, "content")
            if LearningPlanService._field(block, "content") is not None
            else block
        )
        for block in task_blocks
    ]
    normalized_blocks = ["".join(text.split()).casefold() for text in block_texts]
    if normalized_blocks and not any(
        block in task_text or task_text in block
        for block in normalized_blocks
        if block
    ):
        raise ValueError("current learning task must match a short-term task block")


def _validate_maintenance_and_recovery(
    priority_mode: str,
    short_term_package: Any,
    recovery_policy: Any,
) -> None:
    if priority_mode == "temporary_focus":
        if short_term_package is None:
            raise ValueError(
                "temporary focus requires maintenance_plan or maintenance_unavailable_reason"
            )
        maintenance = LearningPlanService._field(short_term_package, "maintenance_plan")
        unavailable = LearningPlanService._field(
            short_term_package, "maintenance_unavailable_reason"
        )
        if not any(isinstance(item, str) and item.strip() for item in (maintenance, unavailable)):
            raise ValueError(
                "temporary focus requires maintenance_plan or maintenance_unavailable_reason"
            )
    if priority_mode in {"temporary_focus", "recovery"} and recovery_policy is None:
        raise ValueError("temporary focus or recovery mode requires recovery_policy")
    _validate_recovery_policy(recovery_policy)


def _validate_recovery_policy(recovery_policy: Any) -> None:
    if recovery_policy is None:
        return
    triggers = LearningPlanService._field(recovery_policy, "trigger_conditions") or []
    actions = LearningPlanService._field(recovery_policy, "recovery_actions") or []
    if not all(isinstance(item, str) and item.strip() for item in triggers) or not all(
        isinstance(item, str) and item.strip() for item in actions
    ):
        raise ValueError(
            "recovery_policy requires non-empty trigger conditions and recovery actions"
        )


def _validate_formal_route_consistency(
    long_route: ResolvedPlanningRoute | None,
    short_route: ResolvedPlanningRoute | None,
) -> None:
    long_is_approved = (
        long_route is not None and long_route.planning_status == "approved_route"
    )
    short_is_approved = (
        short_route is not None and short_route.planning_status == "approved_route"
    )
    if long_is_approved or short_is_approved:
        if not (long_is_approved and short_is_approved):
            raise ValueError(
                "long-term and short-term plans must use the same approved route ID/version"
            )
        if (
            long_route.route_id != short_route.route_id
            or long_route.route_version != short_route.route_version
        ):
            raise ValueError(
                "long-term and short-term plans must use the same approved route ID/version"
            )


class LearningPlanService:
    """Turn diagnosis proposals into system-owned, executable plan records.

    Persistence is provided through a repository so the same service can use
    MySQL in the application and an isolated in-memory store in unit tests.
    """

    def __init__(
        self,
        route_repository: DefaultRouteRepository | None = None,
        plan_repository: LearningPlanRepository | None = None,
    ) -> None:
        self.route_repository = route_repository
        self.plan_repository = plan_repository or InMemoryLearningPlanRepository()

    def materialize(
        self,
        learner_id: str,
        proposal: LearningPlanProposal,
        *,
        now: datetime | None = None,
        current_long_term_plan: dict[str, Any] | None = None,
        current_short_term_plan: dict[str, Any] | None = None,
        available_minutes: int | None = None,
    ) -> LearningPlanResult:
        if not learner_id:
            raise ValueError("learner_id is required")
        validate_medical_education_safety(proposal)
        timestamp = now or datetime.now(timezone.utc)
        previous = self.plan_repository.get_current(learner_id)

        long_source = self._reusable_plan(
            proposal.long_term_plan_action, current_long_term_plan, previous, "long"
        )
        short_source = self._reusable_plan(
            proposal.short_term_plan_action, current_short_term_plan, previous, "short"
        )
        long_term_plan_id = str(
            self._field(long_source, "plan_id")
            or self._field(self._field(previous, "long_term_plan"), "plan_id")
            or f"LP_LONG_{uuid4().hex}"
        )
        short_term_plan_id = str(
            self._field(short_source, "plan_id")
            or self._field(self._field(previous, "short_term_plan"), "plan_id")
            or f"LP_SHORT_{uuid4().hex}"
        )
        task_source = (
            previous.learning_task
            if proposal.daily_task_action == "reuse"
            and previous is not None
            and previous.learning_task is not None
            else None
        )
        task_id = str(
            self._field(task_source, "task_id")
            or self._field(self._field(previous, "learning_task"), "task_id")
            or f"TASK_{uuid4().hex}"
        )
        long_version = int(
            self._field(long_source, "version")
            or (int(self._field(self._field(previous, "long_term_plan"), "version") or 0) + 1)
        )
        short_version = int(
            self._field(short_source, "version")
            or (int(self._field(self._field(previous, "short_term_plan"), "version") or 0) + 1)
        )
        task_version = int(
            self._field(task_source, "version")
            or (int(self._field(self._field(previous, "learning_task"), "version") or 0) + 1)
        )
        created_at = (
            self._field(self._field(previous, "long_term_plan"), "created_at")
            or timestamp
        )

        long_route = self._structured_field(
            long_source, "planning_route", proposal.planning_route
        )
        long_goal = self._structured_field(
            long_source, "goal_contract", proposal.goal_contract
        )
        long_milestones = list(
            self._structured_field(long_source, "milestones", proposal.milestones) or []
        )
        long_recovery = self._structured_field(
            long_source, "recovery_policy", proposal.recovery_policy
        )
        assumptions = list(
            self._structured_field(long_source, "assumptions", proposal.assumptions) or []
        )
        unknowns = list(
            self._structured_field(
                long_source, "unknowns_to_confirm", proposal.unknowns_to_confirm
            )
            or []
        )
        short_route = self._structured_field(
            short_source, "planning_route", proposal.planning_route
        )
        short_goal = self._structured_field(
            short_source, "goal_contract", proposal.goal_contract
        )
        short_package = self._structured_field(
            short_source,
            "short_term_learning_package",
            proposal.short_term_learning_package,
        )
        short_recovery = self._structured_field(
            short_source, "recovery_policy", proposal.recovery_policy
        )
        long_trace = self._structured_field(
            long_source, "recommendation_trace", proposal.recommendation_trace
        )
        short_trace = self._structured_field(
            short_source, "recommendation_trace", proposal.recommendation_trace
        )
        short_focus = self._structured_field(
            short_source, "short_term_focus", proposal.short_term_focus
        )
        long_textbook_selection = self._structured_field(
            long_source, "textbook_selection", proposal.textbook_selection
        )
        short_textbook_selection = self._structured_field(
            short_source, "textbook_selection", proposal.textbook_selection
        )

        if proposal.planning_route is None and any(
            (
                proposal.goal_contract is not None,
                bool(proposal.milestones),
                proposal.short_term_learning_package is not None,
                proposal.recovery_policy is not None,
                proposal.recommendation_trace is not None,
                bool(proposal.assumptions),
                bool(proposal.unknowns_to_confirm),
            )
        ):
            raise ValueError(
                "structured plan requires an approved_route or provisional planning route"
            )
        _validate_route(long_route, self.route_repository, assumptions, unknowns)
        short_assumptions = list(self._field(short_route, "assumptions") or assumptions)
        short_unknowns = list(
            self._field(short_route, "unknowns_to_confirm") or unknowns
        )
        _validate_route(
            short_route, self.route_repository, short_assumptions, short_unknowns
        )
        _validate_formal_route_consistency(long_route, short_route)
        _validate_milestones(long_goal, long_milestones)
        _validate_budget(proposal, short_package, available_minutes)
        _validate_current_task_matches_package(proposal, short_package)
        _validate_recovery_policy(long_recovery)
        _validate_maintenance_and_recovery(
            proposal.priority_mode, short_package, short_recovery
        )

        result = LearningPlanResult(
            long_term_plan=LongTermPlan(
                plan_id=long_term_plan_id,
                learner_id=learner_id,
                content=str(self._field(long_source, "content") or proposal.long_term_plan_content),
                version=long_version,
                status=str(self._field(long_source, "status") or "active"),
                created_at=created_at,
                updated_at=timestamp,
                stages=(
                    list(self._field(long_source, "stages") or [])
                    or proposal.long_term_plan_stages
                ),
                planning_route=long_route,
                goal_contract=long_goal,
                milestones=long_milestones,
                recovery_policy=long_recovery,
                recommendation_trace=long_trace,
                assumptions=assumptions,
                unknowns_to_confirm=unknowns,
                textbook_selection=long_textbook_selection,
            ),
            short_term_plan=ShortTermPlan(
                plan_id=short_term_plan_id,
                learner_id=learner_id,
                long_term_plan_id=long_term_plan_id,
                content=str(self._field(short_source, "content") or proposal.short_term_plan_content),
                version=short_version,
                status=str(self._field(short_source, "status") or "active"),
                created_at=(
                    self._field(self._field(previous, "short_term_plan"), "created_at")
                    or timestamp
                ),
                updated_at=timestamp,
                planning_route=short_route,
                goal_contract=short_goal,
                short_term_learning_package=short_package,
                recovery_policy=short_recovery,
                recommendation_trace=short_trace,
                short_term_focus=short_focus,
                textbook_selection=short_textbook_selection,
            ),
            learning_task=LearningTask(
                task_id=task_id,
                learner_id=learner_id,
                short_term_plan_id=short_term_plan_id,
                task_type=str(
                    self._field(task_source, "task_type")
                    or proposal.task_proposal.task_type
                ),
                task_content=str(
                    self._field(task_source, "task_content")
                    or proposal.daily_task_content
                    or proposal.task_proposal.task_content
                ),
                estimated_minutes=int(
                    self._field(task_source, "estimated_minutes")
                    or proposal.task_proposal.estimated_minutes
                ),
                expected_output=str(
                    self._field(task_source, "expected_output")
                    or proposal.task_proposal.expected_output
                ),
                completion_criteria=str(
                    self._field(task_source, "completion_criteria")
                    or proposal.task_proposal.completion_criteria
                ),
                version=task_version,
                status=str(self._field(task_source, "status") or "pending"),
                created_at=(
                    self._field(self._field(previous, "learning_task"), "created_at")
                    or timestamp
                ),
                updated_at=timestamp,
            ),
        )
        self.plan_repository.save_current(learner_id, result)
        return result

    @staticmethod
    def _field(value: Any, name: str) -> Any:
        if isinstance(value, dict):
            return value.get(name)
        return getattr(value, name, None)

    @classmethod
    def _structured_field(cls, source: Any, name: str, fallback: Any) -> Any:
        if source is None:
            return fallback
        value = cls._field(source, name)
        if value is None:
            return fallback
        if name == "planning_route" and isinstance(value, dict):
            return ResolvedPlanningRoute.model_validate(value)
        return value

    @classmethod
    def _reusable_plan(
        cls,
        action: str,
        supplied: dict[str, Any] | None,
        previous: LearningPlanResult | None,
        plan_kind: str,
    ) -> Any | None:
        if action != "reuse":
            return None
        if supplied and cls._field(supplied, "content"):
            return supplied
        if previous is None:
            return None
        return previous.long_term_plan if plan_kind == "long" else previous.short_term_plan

    def get_current(self, learner_id: str) -> LearningPlanResult | None:
        return self.plan_repository.get_current(learner_id)

    @classmethod
    def is_importable_long_term_parent(cls, supplied: dict[str, Any]) -> bool:
        content = cls._field(supplied, "content")
        status = cls._field(supplied, "status")
        return (
            isinstance(content, str)
            and bool(content.strip())
            and not cls._field(supplied, "plan_id")
            and status in (None, "active")
            and all(section in content for section in _LONG_TERM_CONTENT_SECTIONS)
        )

    def import_long_term_parent(
        self,
        learner_id: str,
        supplied: dict[str, Any],
        proposal: LearningPlanProposal,
        *,
        now: datetime | None = None,
    ) -> LongTermPlan:
        if not learner_id:
            raise ValueError("learner_id is required")
        if self.plan_repository.get_current(learner_id) is not None:
            raise ValueError("cannot import a long-term parent over an existing plan")
        if not self.is_importable_long_term_parent(supplied):
            raise ValueError("inline long-term parent is incomplete")

        content = str(self._field(supplied, "content")).strip()
        safety_proposal = proposal.model_copy(
            update={"long_term_plan_content": content}
        )
        validate_medical_education_safety(safety_proposal)
        route = proposal.planning_route
        _validate_route(
            route,
            self.route_repository,
            proposal.assumptions,
            proposal.unknowns_to_confirm,
        )
        _validate_milestones(proposal.goal_contract, proposal.milestones)
        _validate_recovery_policy(proposal.recovery_policy)
        timestamp = now or datetime.now(timezone.utc)
        plan = LongTermPlan(
            plan_id=f"LP_LONG_{uuid4().hex}",
            learner_id=learner_id,
            content=content,
            version=1,
            status="active",
            created_at=timestamp,
            updated_at=timestamp,
            stages=proposal.long_term_plan_stages,
            planning_route=route,
            goal_contract=proposal.goal_contract,
            milestones=proposal.milestones,
            recovery_policy=proposal.recovery_policy,
            recommendation_trace=proposal.recommendation_trace,
            assumptions=proposal.assumptions,
            unknowns_to_confirm=proposal.unknowns_to_confirm,
            textbook_selection=proposal.textbook_selection,
        )
        self.plan_repository.save_current(
            learner_id, LearningPlanResult(long_term_plan=plan)
        )
        return plan

    def materialize_long_term(
        self,
        learner_id: str,
        proposal: LearningPlanProposal,
        *,
        now: datetime | None = None,
    ) -> LearningPlanResult:
        if not learner_id:
            raise ValueError("learner_id is required")
        validate_medical_education_safety(proposal)
        timestamp = now or datetime.now(timezone.utc)
        previous = self.plan_repository.get_current(learner_id)
        previous_long = previous.long_term_plan if previous is not None else None
        route = proposal.planning_route
        _validate_route(
            route,
            self.route_repository,
            proposal.assumptions,
            proposal.unknowns_to_confirm,
        )
        _validate_milestones(proposal.goal_contract, proposal.milestones)
        _validate_recovery_policy(proposal.recovery_policy)
        plan = LongTermPlan(
            plan_id=str(self._field(previous_long, "plan_id") or f"LP_LONG_{uuid4().hex}"),
            learner_id=learner_id,
            content=proposal.long_term_plan_content,
            version=int(self._field(previous_long, "version") or 0) + 1,
            status="active",
            created_at=self._field(previous_long, "created_at") or timestamp,
            updated_at=timestamp,
            stages=proposal.long_term_plan_stages,
            planning_route=route,
            goal_contract=proposal.goal_contract,
            milestones=proposal.milestones,
            recovery_policy=proposal.recovery_policy,
            recommendation_trace=proposal.recommendation_trace,
            assumptions=proposal.assumptions,
            unknowns_to_confirm=proposal.unknowns_to_confirm,
            textbook_selection=proposal.textbook_selection,
        )
        stored = LearningPlanResult(long_term_plan=plan)
        self.plan_repository.save_current(
            learner_id,
            stored,
            invalidated_layers=["short_term", "daily_task"],
        )
        return LearningPlanResult(
            long_term_plan=plan,
            generated_scope="long_term",
            invalidated_layers=["short_term", "daily_task"],
        )

    def materialize_short_term(
        self,
        learner_id: str,
        proposal: LearningPlanProposal,
        *,
        current_long_term_plan: dict[str, Any],
        now: datetime | None = None,
    ) -> LearningPlanResult:
        if not learner_id:
            raise ValueError("learner_id is required")
        validate_medical_education_safety(proposal)
        timestamp = now or datetime.now(timezone.utc)
        long_plan = LongTermPlan.model_validate(current_long_term_plan)
        previous = self.plan_repository.get_current(learner_id)
        previous_short = previous.short_term_plan if previous is not None else None
        route = proposal.planning_route
        _validate_route(
            route,
            self.route_repository,
            proposal.assumptions,
            proposal.unknowns_to_confirm,
        )
        _validate_formal_route_consistency(long_plan.planning_route, route)
        _validate_maintenance_and_recovery(
            proposal.priority_mode,
            proposal.short_term_learning_package,
            proposal.recovery_policy,
        )
        plan = ShortTermPlan(
            plan_id=str(self._field(previous_short, "plan_id") or f"LP_SHORT_{uuid4().hex}"),
            learner_id=learner_id,
            long_term_plan_id=long_plan.plan_id,
            content=proposal.short_term_plan_content,
            version=int(self._field(previous_short, "version") or 0) + 1,
            status="active",
            created_at=self._field(previous_short, "created_at") or timestamp,
            updated_at=timestamp,
            planning_route=route,
            goal_contract=proposal.goal_contract,
            short_term_learning_package=proposal.short_term_learning_package,
            recovery_policy=proposal.recovery_policy,
            recommendation_trace=proposal.recommendation_trace,
            short_term_focus=proposal.short_term_focus,
            textbook_selection=proposal.textbook_selection,
        )
        stored = LearningPlanResult(long_term_plan=long_plan, short_term_plan=plan)
        self.plan_repository.save_current(
            learner_id, stored, invalidated_layers=["daily_task"]
        )
        return LearningPlanResult(
            short_term_plan=plan,
            generated_scope="short_term",
            invalidated_layers=["daily_task"],
        )

    def is_current_parent(
        self,
        learner_id: str,
        supplied: dict[str, Any],
        plan_kind: str,
    ) -> bool:
        if self._field(supplied, "learner_id") != learner_id:
            return False
        if self._field(supplied, "status") != "active":
            return False
        version = self._field(supplied, "version")
        if isinstance(version, bool) or not isinstance(version, int) or version < 1:
            return False
        if not self._field(supplied, "content") or not self._field(supplied, "plan_id"):
            return False
        previous = self.plan_repository.get_current(learner_id)
        if previous is None:
            return True
        expected = (
            previous.long_term_plan if plan_kind == "long" else previous.short_term_plan
        )
        if expected is None:
            return False
        return (
            self._field(supplied, "plan_id") == self._field(expected, "plan_id")
            and self._field(supplied, "version") == self._field(expected, "version")
        )

    def retain_generated_scope(
        self,
        learner_id: str,
        materialized: LearningPlanResult,
        plan_scope: str,
    ) -> LearningPlanResult:
        if plan_scope == "long_term":
            stored = LearningPlanResult(long_term_plan=materialized.long_term_plan)
            self.plan_repository.save_current(
                learner_id,
                stored,
                invalidated_layers=["short_term", "daily_task"],
            )
            return LearningPlanResult(
                long_term_plan=materialized.long_term_plan,
                generated_scope="long_term",
                invalidated_layers=["short_term", "daily_task"],
            )
        if plan_scope == "short_term":
            stored = LearningPlanResult(
                long_term_plan=materialized.long_term_plan,
                short_term_plan=materialized.short_term_plan,
            )
            self.plan_repository.save_current(
                learner_id, stored, invalidated_layers=["daily_task"]
            )
            return LearningPlanResult(
                short_term_plan=materialized.short_term_plan,
                generated_scope="short_term",
                invalidated_layers=["daily_task"],
            )
        return materialized

    def materialize_daily_task(
        self,
        learner_id: str,
        proposal: LearningPlanProposal,
        *,
        current_short_term_plan: dict[str, Any],
        current_long_term_plan: dict[str, Any] | None = None,
        current_learning_task: dict[str, Any] | None = None,
        now: datetime | None = None,
    ) -> LearningPlanResult:
        if not learner_id:
            raise ValueError("learner_id is required")
        validate_medical_education_safety(proposal)
        short_term_plan_id = self._field(current_short_term_plan, "plan_id")
        if not short_term_plan_id:
            raise ValueError("daily task requires an existing short-term plan ID")
        timestamp = now or datetime.now(timezone.utc)
        previous = self.plan_repository.get_current(learner_id)
        task_source = current_learning_task or (
            previous.learning_task if previous is not None else None
        )
        task = LearningTask(
            task_id=str(self._field(task_source, "task_id") or f"TASK_{uuid4().hex}"),
            learner_id=learner_id,
            short_term_plan_id=str(short_term_plan_id),
            task_type=proposal.task_proposal.task_type,
            task_content=proposal.task_proposal.task_content,
            estimated_minutes=proposal.task_proposal.estimated_minutes,
            expected_output=proposal.task_proposal.expected_output,
            completion_criteria=proposal.task_proposal.completion_criteria,
            version=int(self._field(task_source, "version") or 0) + 1,
            status="pending",
            created_at=self._field(task_source, "created_at") or timestamp,
            updated_at=timestamp,
        )
        if previous is not None:
            stored = previous.model_copy(update={"learning_task": task})
        else:
            try:
                long_plan = (
                    LongTermPlan.model_validate(current_long_term_plan)
                    if current_long_term_plan
                    else None
                )
                short_plan = ShortTermPlan.model_validate(current_short_term_plan)
            except (TypeError, ValueError):
                long_plan = None
                short_plan = None
            stored = LearningPlanResult(
                long_term_plan=long_plan,
                short_term_plan=short_plan,
                learning_task=task,
            )
        self.plan_repository.save_current(learner_id, stored)
        return LearningPlanResult(
            learning_task=task,
            generated_scope="daily_task",
            invalidated_layers=[],
        )
