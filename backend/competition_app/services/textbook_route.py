from __future__ import annotations

import json
from pathlib import Path

from competition_app.contracts.textbook_route import (
    ResolvedTextbookRoute,
    TextbookLearningRoute,
    TextbookRouteBinding,
    TextbookRouteSource,
)


class TextbookRouteRepository:
    """Loads the application-owned textbook catalog and resolves safe bindings."""

    def __init__(
        self,
        routes: list[TextbookLearningRoute],
        bindings: list[TextbookRouteBinding],
        sources: list[TextbookRouteSource],
    ) -> None:
        self.routes = tuple(routes)
        self.bindings = tuple(bindings)
        self.sources = tuple(sources)
        self._routes_by_id = {route.route_id: route for route in self.routes}
        if len(self._routes_by_id) != len(self.routes):
            raise ValueError("duplicate textbook route ID")
        unknown = {
            binding.textbook_route_id
            for binding in self.bindings
            if binding.textbook_route_id not in self._routes_by_id
        }
        if unknown:
            raise ValueError("binding references unknown textbook route: " + ", ".join(sorted(unknown)))

    @classmethod
    def from_file(cls, path: Path) -> "TextbookRouteRepository":
        with path.open(encoding="utf-8") as file:
            payload = json.load(file)
        return cls(
            routes=[TextbookLearningRoute.model_validate(item) for item in payload["routes"]],
            bindings=[TextbookRouteBinding.model_validate(item) for item in payload["bindings"]],
            sources=[TextbookRouteSource.model_validate(item) for item in payload["sources"]],
        )

    def resolve(
        self, *, exam_route_id: str | None, goal_text: str
    ) -> ResolvedTextbookRoute:
        normalized_goal = self._normalized(goal_text)
        if exam_route_id:
            candidates = [
                binding
                for binding in self.bindings
                if self._normalized(binding.exam_route_id)
                == self._normalized(exam_route_id)
            ]
            keyword_matches = [
                binding
                for binding in candidates
                if binding.keywords
                and any(self._normalized(word) in normalized_goal for word in binding.keywords)
            ]
            if len(keyword_matches) == 1:
                return self._resolved(keyword_matches[0], "exam_route_and_specialty")
            if len(keyword_matches) > 1:
                return self._clarification(None, "ambiguous_specialty")
            defaults = [binding for binding in candidates if binding.default]
            if len(defaults) == 1:
                return self._resolved(defaults[0], "exam_route_default")
            if candidates:
                return self._clarification(None, "missing_specialty")

        alias_matches = [
            route
            for route in self.routes
            if any(
                self._normalized(alias) in normalized_goal
                for alias in (route.goal_name, *route.aliases)
                if self._normalized(alias)
            )
        ]
        if len(alias_matches) == 1:
            return self._clarification(alias_matches[0], "missing_exam_identity")
        if len(alias_matches) > 1:
            return self._clarification(None, "ambiguous_textbook_route")
        return ResolvedTextbookRoute(
            planning_status="unmatched",
            match_reason="no_textbook_route_match",
            clarification_questions=["请说明要参加的具体考试或要学习的专业方向。"],
        )

    def _resolved(
        self, binding: TextbookRouteBinding, reason: str
    ) -> ResolvedTextbookRoute:
        return ResolvedTextbookRoute(
            planning_status="resolved",
            match_reason=reason,
            route=self._routes_by_id[binding.textbook_route_id],
        )

    @staticmethod
    def _clarification(
        route: TextbookLearningRoute | None, reason: str
    ) -> ResolvedTextbookRoute:
        return ResolvedTextbookRoute(
            planning_status="needs_clarification",
            match_reason=reason,
            route=route,
            clarification_questions=[
                "请说明具体考试名称；如果只是系统学习，也请明确这是课程学习而不是资格考试。"
            ],
        )

    @staticmethod
    def _normalized(value: str) -> str:
        return "".join(value.split()).casefold()

