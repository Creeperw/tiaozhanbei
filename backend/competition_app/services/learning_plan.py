from __future__ import annotations

from datetime import datetime, timedelta, timezone
import re
from typing import Any, Callable
from uuid import uuid4

from competition_app.contracts.default_route import ResolvedPlanningRoute
from competition_app.contracts.learning_plan import (
    DailyTaskItemSpec,
    LearningPlanProposal,
    LearningPlanResult,
    LearningTask,
    LongTermPlan,
    StageEvidenceRecord,
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

_DAILY_TASK_REFRESH_INTERVAL = timedelta(hours=24)

KnowledgePointResolver = Callable[..., str | None]
VideoResourceResolver = Callable[[dict[str, Any]], dict[str, Any] | None]


def materialize_daily_task_items(
    *,
    task_content: str,
    learning_chapter: str = "",
    estimated_minutes: int,
    focus_knowledge_points: list[str],
    task_blocks: list[Any] | None = None,
    knowledge_point_resolver: KnowledgePointResolver | None = None,
    video_resource_resolver: VideoResourceResolver | None = None,
) -> list[DailyTaskItemSpec]:
    """Resolve model semantics at the boundary and create executable atoms.

    Model-produced knowledge-point names and resource strings are never treated as
    formal IDs or verified video references. A caller-owned resolver must validate
    either value before an executable practice/video atom can be emitted.
    """

    semantics: list[dict[str, Any]] = []
    covered_kp_ids: set[str] = set()
    for block in task_blocks or []:
        item_type = LearningPlanService._field(block, "item_type")
        if item_type is None:
            continue
        title = str(LearningPlanService._field(block, "content") or "").strip()
        if not title:
            raise ValueError("atomic task block requires content")
        supplied_resource_ref = dict(
            LearningPlanService._field(block, "resource_ref") or {}
        )
        knowledge_point_name = str(
            LearningPlanService._field(block, "knowledge_point_name")
            or LearningPlanService._field(block, "kp_id")
            or ""
        ).strip()
        kp_id = _resolve_knowledge_point(
            knowledge_point_name,
            knowledge_point_resolver,
            learning_chapter=learning_chapter,
        )
        if item_type == "knowledge_practice":
            if not kp_id:
                item_type = "recall"
            else:
                covered_kp_ids.add(kp_id)
        if item_type == "video_section":
            resource_ref = (
                video_resource_resolver(supplied_resource_ref)
                if video_resource_resolver is not None
                else None
            )
            if not isinstance(resource_ref, dict) or not resource_ref:
                item_type = "reading"
                resource_ref = {}
            else:
                resource_ref = dict(resource_ref)
        else:
            resource_ref = supplied_resource_ref
        semantics.append(
            {
                "item_type": item_type,
                "title": title,
                "knowledge_point_name": knowledge_point_name or None,
                "kp_id": kp_id,
                "required_question_count": LearningPlanService._field(
                    block, "required_question_count"
                ),
                "resource_ref": resource_ref,
            }
        )

    resolved_focus_kp_ids: list[str] = []
    for raw_knowledge_point in focus_knowledge_points:
        knowledge_point_name = str(raw_knowledge_point).strip()
        if not knowledge_point_name:
            raise ValueError("focus knowledge points must contain non-empty names")
        kp_id = _resolve_knowledge_point(
            knowledge_point_name,
            knowledge_point_resolver,
            learning_chapter=learning_chapter,
        )
        if kp_id is None and knowledge_point_resolver is not None:
            # A live daily task contains only resources with a verifiable
            # completion path. The model label remains in the prose plan, but
            # it must not become a fake executable recall item.
            continue
        if kp_id is not None and kp_id not in resolved_focus_kp_ids:
            resolved_focus_kp_ids.append(kp_id)
        if kp_id is not None and kp_id in covered_kp_ids:
            continue
        if kp_id is not None:
            covered_kp_ids.add(kp_id)
        semantics.append(
            {
                "item_type": "knowledge_practice" if kp_id else "recall",
                "title": f"完成知识点 {knowledge_point_name} 练习",
                "knowledge_point_name": knowledge_point_name,
                "kp_id": kp_id,
                "required_question_count": 3 if kp_id else None,
                "resource_ref": {},
            }
        )

    has_video_atom = any(item["item_type"] == "video_section" for item in semantics)
    if (
        not has_video_atom
        and video_resource_resolver is not None
        and estimated_minutes >= len(semantics) + 1
    ):
        video_requests: list[dict[str, Any]] = []
        if learning_chapter.strip():
            video_requests.append(
                {
                    "learning_chapter": learning_chapter,
                    "kp_ids": list(resolved_focus_kp_ids),
                }
            )
        video_requests.extend({"kp_id": kp_id} for kp_id in resolved_focus_kp_ids)
        for request in video_requests:
            resource_ref = video_resource_resolver(request)
            if isinstance(resource_ref, dict) and resource_ref:
                semantics.insert(
                    0,
                    {
                        "item_type": "video_section",
                        "title": (
                            f"观看{learning_chapter}章节视频"
                            if learning_chapter.strip()
                            else "观看今日章节视频"
                        ),
                        "knowledge_point_name": None,
                        "kp_id": None,
                        "required_question_count": None,
                        "resource_ref": dict(resource_ref),
                    },
                )
                break

    if not semantics:
        semantics.append(
            {
                "item_type": "recall",
                "title": task_content,
                "knowledge_point_name": None,
                "kp_id": None,
                "required_question_count": None,
                "resource_ref": {},
            }
        )
    if estimated_minutes < len(semantics):
        raise ValueError("parent task budget cannot allocate one minute per atomic item")

    base_minutes, remainder = divmod(estimated_minutes, len(semantics))
    items: list[DailyTaskItemSpec] = []
    for index, semantic in enumerate(semantics, start=1):
        item_type = semantic["item_type"]
        resource_ref = semantic["resource_ref"]
        if item_type == "knowledge_practice":
            completion_policy = {"policy": "frozen_question_set"}
        elif item_type == "video_section":
            source_text = " ".join(
                str(resource_ref.get(key) or "")
                for key in ("source", "provider", "url", "bvid")
            ).casefold()
            policy = (
                "iframe_focus_and_confirmation"
                if any(token in source_text for token in ("bilibili", "b23", "youtube", "bvid"))
                or resource_ref.get("bvid")
                else "html5_coverage"
            )
            completion_policy = {"policy": policy, "coverage_threshold": 0.9}
        else:
            completion_policy = {"policy": "explicit_evidence"}
        items.append(
            DailyTaskItemSpec(
                task_item_id=f"DTI_{uuid4().hex}",
                ordinal=index,
                item_type=item_type,
                title=semantic["title"],
                estimated_minutes=base_minutes + (1 if index <= remainder else 0),
                knowledge_point_name=semantic["knowledge_point_name"],
                kp_id=semantic["kp_id"],
                required_question_count=(
                    semantic["required_question_count"] or 3
                    if item_type == "knowledge_practice"
                    else None
                ),
                resource_ref=resource_ref,
                completion_policy=completion_policy,
            )
        )
    return items


def _resolve_knowledge_point(
    knowledge_point_name: str,
    resolver: KnowledgePointResolver | None,
    *,
    learning_chapter: str = "",
) -> str | None:
    if not knowledge_point_name or resolver is None:
        return None
    try:
        resolved = resolver(knowledge_point_name, learning_chapter)
    except TypeError:
        resolved = resolver(knowledge_point_name)
    if resolved is None:
        return None
    kp_id = str(resolved).strip()
    if not kp_id:
        raise ValueError("knowledge point resolver returned an empty formal ID")
    return kp_id


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
        knowledge_point_resolver: KnowledgePointResolver | None = None,
        video_resource_resolver: VideoResourceResolver | None = None,
    ) -> None:
        self.route_repository = route_repository
        self.plan_repository = plan_repository or InMemoryLearningPlanRepository()
        self.knowledge_point_resolver = knowledge_point_resolver
        self.video_resource_resolver = video_resource_resolver

    def ensure_executable_daily_resources(
        self,
        learner_id: str,
        *,
        now: datetime | None = None,
    ) -> LearningTask | None:
        """Upgrade a legacy prose/recall task to verified video/question atoms."""

        current = self.plan_repository.get_current(learner_id)
        if current is None or current.learning_task is None:
            return None
        task = current.learning_task
        if task.status == "completed":
            return task
        has_executable_items = bool(task.items) and all(
            item.item_type in {"video_section", "knowledge_practice"}
            for item in task.items
        )
        items = (
            list(task.items)
            if has_executable_items
            else materialize_daily_task_items(
                task_content=task.task_content,
                learning_chapter=task.learning_chapter,
                estimated_minutes=task.estimated_minutes,
                focus_knowledge_points=list(task.focus_knowledge_points),
                task_blocks=[],
                knowledge_point_resolver=self.knowledge_point_resolver,
                video_resource_resolver=self.video_resource_resolver,
            )
        )
        if not items or any(
            item.item_type not in {"video_section", "knowledge_practice"}
            for item in items
        ):
            return task
        practice_items = [
            item for item in items if item.item_type == "knowledge_practice"
        ]
        video_count = sum(item.item_type == "video_section" for item in items)
        focus_points = [
            str(item.knowledge_point_name or item.kp_id or "").strip()
            for item in practice_items
            if str(item.knowledge_point_name or item.kp_id or "").strip()
        ]
        question_count = sum(
            int(item.required_question_count or 0) for item in practice_items
        )
        expected_parts = []
        if video_count:
            expected_parts.append(f"{video_count}条章节视频观看记录")
        if question_count:
            expected_parts.append(f"{question_count}道配套题提交记录")
        expected_output = "与".join(expected_parts) or task.expected_output
        completion_criteria = (
            f"完成全部{len(items)}个原子任务"
            f"（{video_count}个章节视频、{len(practice_items)}个知识点共"
            f"{question_count}道题）；以服务端记录全部完成为通过标准。"
        )
        executable_steps = "；".join(
            str(item.title or "").strip()
            for item in items
            if str(item.title or "").strip()
        )
        task_content = (
            f"今日围绕{task.learning_chapter}学习：{executable_steps}。"
            if str(task.learning_chapter or "").strip()
            else f"今日执行：{executable_steps}。"
        )
        if (
            list(task.items) == items
            and list(task.focus_knowledge_points) == focus_points
            and task.task_content == task_content
            and task.expected_output == expected_output
            and task.completion_criteria == completion_criteria
        ):
            return task
        timestamp = now or datetime.now(timezone.utc)
        updated = task.model_copy(
            update={
                "items": items,
                "focus_knowledge_points": focus_points,
                "task_content": task_content,
                "expected_output": expected_output,
                "completion_criteria": completion_criteria,
                "version": task.version + 1,
                "updated_at": timestamp,
            }
        )
        saved = self.plan_repository.save_current(
            learner_id,
            current.model_copy(update={"learning_task": updated}),
            expected_task_id=task.task_id,
            expected_task_version=task.version,
        )
        if saved:
            return updated
        latest = self.plan_repository.get_current(learner_id)
        return latest.learning_task if latest is not None else task

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
        if (
            proposal.long_term_plan_action == "update"
            and bool(proposal.long_term_plan_stages)
        ):
            self._validate_publishable_long_term_stages(
                proposal.long_term_plan_stages
            )
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
                stage_evidence=list(
                    self._field(long_source, "stage_evidence") or []
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
                learning_chapter=str(
                    self._field(task_source, "learning_chapter")
                    or proposal.task_proposal.learning_chapter
                ),
                focus_knowledge_points=list(
                    self._field(task_source, "focus_knowledge_points")
                    or proposal.task_proposal.focus_knowledge_points
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
                refresh_started_at=(
                    self._field(task_source, "refresh_started_at") or timestamp
                ),
                refresh_due_at=(
                    self._field(task_source, "refresh_due_at")
                    or timestamp + _DAILY_TASK_REFRESH_INTERVAL
                ),
                items=(
                    list(self._field(task_source, "items") or [])
                    if task_source is not None
                    else materialize_daily_task_items(
                        task_content=(
                            proposal.daily_task_content
                            or proposal.task_proposal.task_content
                        ),
                        learning_chapter=proposal.task_proposal.learning_chapter,
                        estimated_minutes=proposal.task_proposal.estimated_minutes,
                        focus_knowledge_points=list(
                            proposal.task_proposal.focus_knowledge_points
                        ),
                        task_blocks=list(
                            self._field(short_package, "task_blocks") or []
                        ),
                        knowledge_point_resolver=self.knowledge_point_resolver,
                        video_resource_resolver=self.video_resource_resolver,
                    )
                ),
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
        self._validate_publishable_long_term_stages(
            proposal.long_term_plan_stages
        )
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

    @classmethod
    def _validate_publishable_long_term_stages(cls, stages: Any) -> None:
        stage_list = list(stages or [])
        placeholder_tokens = (
            "待确认", "未确认", "unknown", "tbd", "不可发布", "路线解析失败"
        )
        if not stage_list:
            raise ValueError("long-term plan requires trusted textbook stages")
        for index, stage in enumerate(stage_list, start=1):
            books = list(cls._field(stage, "book") or [])
            if not books or any(
                any(token in str(book).lower() for token in placeholder_tokens)
                for book in books
            ):
                raise ValueError(
                    f"long-term plan stage {index} requires real route textbooks; "
                    "placeholder textbooks cannot be published"
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
        self._validate_short_term_parent_duration(long_plan, proposal)
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

    @classmethod
    def _validate_short_term_parent_duration(
        cls,
        long_plan: LongTermPlan,
        proposal: LearningPlanProposal,
    ) -> None:
        stages = list(long_plan.stages or [])
        if not stages:
            raise ValueError("short-term plan requires a long-term parent stage")
        selected_stage_id = cls._field(proposal.textbook_selection, "stage_id")
        selected_stage = next(
            (
                stage
                for stage in stages
                if selected_stage_id
                and str(cls._field(stage, "stage_id") or "") == str(selected_stage_id)
            ),
            stages[0],
        )
        parent_duration = cls._field(selected_stage, "duration_days")
        short_duration = cls._field(
            proposal.short_term_learning_package,
            "duration_days",
        )
        if (
            isinstance(parent_duration, int)
            and parent_duration > 0
            and isinstance(short_duration, int)
            and short_duration > parent_duration
        ):
            raise ValueError(
                "short-term plan duration cannot exceed its long-term parent stage: "
                f"short={short_duration}, parent={parent_duration}"
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
        package = self._field(
            current_short_term_plan, "short_term_learning_package"
        )
        task = LearningTask(
            task_id=str(self._field(task_source, "task_id") or f"TASK_{uuid4().hex}"),
            learner_id=learner_id,
            short_term_plan_id=str(short_term_plan_id),
            task_type=proposal.task_proposal.task_type,
            task_content=proposal.task_proposal.task_content,
            learning_chapter=proposal.task_proposal.learning_chapter,
            focus_knowledge_points=proposal.task_proposal.focus_knowledge_points,
            estimated_minutes=proposal.task_proposal.estimated_minutes,
            expected_output=proposal.task_proposal.expected_output,
            completion_criteria=proposal.task_proposal.completion_criteria,
            version=int(self._field(task_source, "version") or 0) + 1,
            status="pending",
            created_at=timestamp,
            updated_at=timestamp,
            refresh_started_at=timestamp,
            refresh_due_at=timestamp + _DAILY_TASK_REFRESH_INTERVAL,
            items=materialize_daily_task_items(
                task_content=proposal.task_proposal.task_content,
                learning_chapter=proposal.task_proposal.learning_chapter,
                estimated_minutes=proposal.task_proposal.estimated_minutes,
                focus_knowledge_points=list(
                    proposal.task_proposal.focus_knowledge_points
                ),
                task_blocks=list(self._field(package, "task_blocks") or []),
                knowledge_point_resolver=self.knowledge_point_resolver,
                video_resource_resolver=self.video_resource_resolver,
            ),
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
        normalized_task = self.ensure_executable_daily_resources(
            learner_id,
            now=timestamp,
        )
        return LearningPlanResult(
            learning_task=normalized_task or task,
            generated_scope="daily_task",
            invalidated_layers=[],
        )

    def record_completed_task_stage_evidence(
        self,
        learner_id: str,
        *,
        stage: int,
        requirement: str,
        task_id: str,
        now: datetime | None = None,
    ) -> LearningPlanResult:
        """Attach only a completed, server-owned task to an exact stage gate."""

        current = self.plan_repository.get_current(learner_id)
        if current is None or current.long_term_plan is None:
            raise ValueError("stage evidence requires an active long-term plan")
        task = current.learning_task
        if (
            task is None
            or task.task_id != task_id
            or task.learner_id != learner_id
            or task.status != "completed"
        ):
            raise ValueError("stage evidence source must be the learner's completed task")
        current_stage = self._current_stage_number(current.long_term_plan)
        if stage != current_stage:
            raise ValueError("stage evidence can only be recorded for the current stage")

        requirements = self._stage_requirements(current.long_term_plan, stage)
        normalized_requirement = str(requirement or "").strip()
        if normalized_requirement not in requirements:
            raise ValueError("stage evidence requirement is not part of the approved stage gate")
        declared_outputs = "\n".join(
            (
                str(task.expected_output or ""),
                str(task.completion_criteria or ""),
                str(task.task_content or ""),
            )
        )
        if self._normalize_evidence_text(normalized_requirement) not in (
            self._normalize_evidence_text(declared_outputs)
        ):
            raise ValueError(
                "completed task did not declare the requested stage evidence as an output"
            )

        records = list(current.long_term_plan.stage_evidence)
        if not any(
            item.stage == stage
            and item.requirement == normalized_requirement
            and item.source_id == task_id
            for item in records
        ):
            records.append(
                StageEvidenceRecord(
                    evidence_id=f"STAGE_EVIDENCE_{uuid4().hex}",
                    stage=stage,
                    requirement=normalized_requirement,
                    source_type="completed_daily_task",
                    source_id=task_id,
                    verified_by="daily_task_execution",
                    verified_at=now or datetime.now(timezone.utc),
                )
            )
        long_term_plan = current.long_term_plan.model_copy(
            update={"stage_evidence": records, "updated_at": now or datetime.now(timezone.utc)}
        )
        updated = current.model_copy(update={"long_term_plan": long_term_plan})
        self.plan_repository.save_current(learner_id, updated)
        return updated

    @classmethod
    def _stage_requirements(cls, plan: LongTermPlan, stage: int) -> list[str]:
        route = plan.planning_route
        textbook_route = route.textbook_route if route is not None else None
        if (
            textbook_route is not None
            and textbook_route.route is not None
            and 1 <= stage <= len(textbook_route.route.stages)
        ):
            return [
                str(value).strip()
                for value in textbook_route.route.stages[stage - 1].exit_evidence
                if str(value).strip()
            ]
        if route is not None and 1 <= stage <= len(route.phases):
            return [
                str(value).strip()
                for value in route.phases[stage - 1].exit_evidence
                if str(value).strip()
            ]
        if 1 <= stage <= len(plan.milestones):
            return [
                str(value).strip()
                for value in plan.milestones[stage - 1].evidence_required
                if str(value).strip()
            ]
        raise ValueError("unknown long-term plan stage")

    @staticmethod
    def _normalize_evidence_text(value: str) -> str:
        return re.sub(r"[\s，。；：、,.!?！？;:（）()《》“”\"']", "", str(value)).casefold()

    @staticmethod
    def _current_stage_number(plan: LongTermPlan) -> int:
        selection = plan.textbook_selection
        route = plan.planning_route
        if selection is None or route is None:
            return 1
        selected_stage_id = selection.stage_id
        textbook_route = route.textbook_route
        if textbook_route is not None and textbook_route.route is not None:
            for item in textbook_route.route.stages:
                if item.stage_id == selected_stage_id:
                    return item.order
        for index, phase in enumerate(route.phases, start=1):
            if phase.phase_id == selected_stage_id:
                return index
        return 1
