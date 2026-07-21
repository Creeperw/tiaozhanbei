from __future__ import annotations

from typing import Any

from competition_app.contracts.profile_readiness import ProfileFieldRequirement, ProfileReadiness


_PLACEHOLDERS = (
    "未填写",
    "未选择",
    "待填写",
    "待补充",
    "待确认",
    "unknown",
    "none",
)


class ProfileReadinessService:
    """Define the minimum persisted context required before long-term planning."""

    requirements = (
        ProfileFieldRequirement(
            field="learning_goal",
            question="你这次长期学习最想达成什么目标？例如具体考试、课程或能力方向。",
            reason="长期路径必须围绕明确目标选择阶段和教材。",
        ),
        ProfileFieldRequirement(
            field="learning_background",
            question="你目前的学习基础是什么？可以直接说零基础，或列出已经学过的课程和掌握程度。",
            reason="已有基础决定从哪个阶段开始，以及哪些教材可以跳过或压缩。",
        ),
        ProfileFieldRequirement(
            field="time_constraints",
            question="你通常每周能学习几天、每天大约能投入多久？时间段不固定也可以直接说明。",
            reason="长期阶段节奏需要建立在可持续的时间预算上。",
        ),
    )

    def evaluate(self, context: dict[str, Any], plan_scope: str | None = None) -> ProfileReadiness:
        if plan_scope != "long_term":
            return ProfileReadiness(status="complete", can_proceed=True)
        profile = context.get("user_profile") or {}
        missing = [
            requirement
            for requirement in self.requirements
            if not self._is_satisfied(requirement.field, profile, context)
        ]
        if not missing:
            return ProfileReadiness(status="complete", can_proceed=True)
        # Ask one precise question at a time so a natural-language resume answer
        # can be written back to exactly one user-owned field.
        current = missing[0]
        return ProfileReadiness(
            status="incomplete",
            can_proceed=False,
            missing_fields=[item.field for item in missing],
            questions=[current.question],
            next_field=current.field,
        )

    def _is_satisfied(self, field: str, profile: dict[str, Any], context: dict[str, Any]) -> bool:
        if field == "learning_goal":
            values = (
                profile.get("learning_goal"),
                profile.get("goals"),
                (context.get("learning_target") or {}).get("exam_name"),
                self._resolved_goal(context),
                self._planned_goal(context),
            )
            return any(self._meaningful(value) for value in values)
        if field == "learning_background":
            values = (
                profile.get("learning_background"),
                profile.get("completed_courses"),
                profile.get("education"),
                profile.get("learner_group"),
                profile.get("user_group"),
            )
            return any(self._meaningful(value) for value in values)
        values = (
            profile.get("time_constraints"),
            profile.get("daily_available_minutes"),
            profile.get("weekly_available_minutes"),
        )
        return any(self._meaningful(value) for value in values)

    @staticmethod
    def _resolved_goal(context: dict[str, Any]) -> str:
        route_output = (context.get("dependency_outputs") or {}).get("route_resolution")
        payload = getattr(route_output, "payload", None)
        return str(getattr(payload, "goal_name", "") or "")

    @staticmethod
    def _planned_goal(context: dict[str, Any]) -> str:
        plan = context.get("current_long_term_plan") or {}
        route = plan.get("planning_route") or {} if isinstance(plan, dict) else {}
        return str(route.get("goal_name") or "")

    @staticmethod
    def _meaningful(value: Any) -> bool:
        if value is None or value == [] or value == {}:
            return False
        text = str(value).strip().lower()
        return bool(text) and not any(token in text for token in _PLACEHOLDERS)
