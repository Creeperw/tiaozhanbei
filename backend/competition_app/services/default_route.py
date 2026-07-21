from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from competition_app.contracts.default_route import (
    DefaultLearningRoute,
    ResolvedPlanningRoute,
)


class DefaultRouteRepository:
    """Loads application-owned routes and resolves only deterministic approved matches."""

    def __init__(
        self,
        routes: list[DefaultLearningRoute],
        aliases_by_route_id: dict[str, list[str]],
    ) -> None:
        self._routes = tuple(routes)
        self._aliases_by_route_id = aliases_by_route_id

    @classmethod
    def from_directory(cls, path: Path) -> "DefaultRouteRepository":
        routes: list[DefaultLearningRoute] = []
        aliases_by_route_id: dict[str, list[str]] = {}
        for seed_file in sorted(path.glob("*.json")):
            payload = cls._load_payload(seed_file)
            normalized_routes = cls._normalized_route_payloads(payload)
            routes.extend(DefaultLearningRoute.model_validate(item) for item in normalized_routes)
            aliases_by_route_id.update(
                {
                    route["route_id"]: route["aliases"]
                    for route in normalized_routes
                }
            )
        return cls(routes, aliases_by_route_id)

    def get(
        self, route_id: str, version: str | None = None
    ) -> DefaultLearningRoute | None:
        matches = [
            route
            for route in self._approved_routes
            if self._normalized(route.route_id) == self._normalized(route_id)
            and (version is None or str(route.route_version) == version)
        ]
        return matches[0] if len(matches) == 1 else None

    def route_selection_catalog(self) -> list[dict[str, Any]]:
        """Expose only approved route identities needed for model selection."""

        return [
            {
                "route_id": route.route_id,
                "goal_type": route.goal_type,
                "goal_name": route.goal_name,
                "aliases": list(self._aliases_by_route_id.get(route.route_id, [])),
                "planning_label": route.planning_label,
            }
            for route in self._approved_routes
        ]

    def resolve(
        self,
        *,
        goal_type: str,
        goal_name: str,
        explicit_route_id: str | None = None,
    ) -> ResolvedPlanningRoute:
        if explicit_route_id:
            route = self.get(explicit_route_id)
            if route is not None:
                return self._approved_resolution(route, "explicit_route_id")
            return self._provisional(
                goal_type, goal_name, "no_safe_match", "Explicit route ID is not an approved route."
            )

        canonical_matches = self._matches(
            lambda route: self._normalized(route.goal_name) == self._normalized(goal_name)
        )
        if len(canonical_matches) == 1:
            return self._approved_resolution(canonical_matches[0], "canonical_name")
        if len(canonical_matches) > 1:
            return self._provisional(goal_type, goal_name, "ambiguous_canonical_name", "Multiple approved canonical-name matches exist.")

        alias_matches = self._matches(
            lambda route: any(
                self._normalized(alias) == self._normalized(goal_name)
                for alias in self._aliases_by_route_id.get(route.route_id, [])
            )
        )
        if len(alias_matches) == 1:
            return self._approved_resolution(alias_matches[0], "alias")
        if len(alias_matches) > 1:
            return self._provisional(goal_type, goal_name, "ambiguous_alias", "Multiple approved alias matches exist.")

        normalized_goal_name = self._normalized(goal_name)
        embedded_alias_matches = self._matches(
            lambda route: (
                self._normalized(route.goal_type) == self._normalized(goal_type)
                and any(
                    self._normalized(alias) in normalized_goal_name
                    for alias in self._aliases_by_route_id.get(route.route_id, [])
                    if self._normalized(alias)
                )
            )
        )
        if len(embedded_alias_matches) == 1:
            return self._approved_resolution(
                embedded_alias_matches[0], "embedded_alias"
            ).model_copy(
                update={"goal_type": goal_type, "goal_name": goal_name}
            )
        if len(embedded_alias_matches) > 1:
            return self._provisional(
                goal_type,
                goal_name,
                "ambiguous_embedded_alias",
                "Multiple approved route aliases occur in the goal text.",
            )

        type_matches = self._matches(
            lambda route: self._normalized(route.goal_type) == self._normalized(goal_type)
        )
        if len(type_matches) == 1:
            if self._normalized(goal_type) != "course":
                return self._approved_resolution(type_matches[0], "unique_goal_type")
            normalized_aliases = [
                self._normalized(alias)
                for alias in self._aliases_by_route_id.get(type_matches[0].route_id, [])
            ]
            if any(alias and alias in normalized_goal_name for alias in normalized_aliases):
                return self._approved_resolution(type_matches[0], "unique_goal_type")
            return self._provisional(
                goal_type,
                goal_name,
                "no_safe_match",
                "The only route of this type does not match the requested subject.",
            )
        if len(type_matches) > 1:
            return self._provisional(goal_type, goal_name, "ambiguous_goal_type", "Multiple approved routes share this goal type.")
        return self._provisional(goal_type, goal_name, "no_safe_match", "No approved deterministic match exists.")

    @property
    def _approved_routes(self) -> tuple[DefaultLearningRoute, ...]:
        return tuple(
            route
            for route in self._routes
            if route.status == "approved" and route.route_status == "approved"
        )

    def _matches(self, predicate: Any) -> list[DefaultLearningRoute]:
        return [route for route in self._approved_routes if predicate(route)]

    @staticmethod
    def _load_payload(seed_file: Path) -> dict[str, Any]:
        with seed_file.open(encoding="utf-8") as file:
            return json.load(file)

    @staticmethod
    def _normalized_route_payloads(payload: dict[str, Any]) -> list[dict[str, Any]]:
        import_metadata = payload.get("project_import_metadata")
        runtime_checks = list(payload.get("runtime_checks", []))
        match_metadata = payload.get("route_match_metadata", {})
        governance_metadata = payload.get("route_governance_metadata", {})
        normalized_routes: list[dict[str, Any]] = []

        for route_payload in payload["routes"]:
            route = dict(route_payload)
            route_id = route["route_id"]
            governance = governance_metadata.get(route_id, {})
            phase_source_refs = governance.get("phase_source_refs", {})
            route["aliases"] = list(
                route.get("aliases", match_metadata.get(route_id, {}).get("aliases", []))
            )
            route["planning_label"] = route.get(
                "planning_label", governance.get("planning_label")
            )
            route["personalization_rules"] = list(
                route.get(
                    "personalization_rules", governance.get("personalization_rules", [])
                )
            )
            route["refresh_rule"] = route.get("refresh_rule", governance.get("refresh_rule"))
            route["runtime_checks"] = list(route.get("runtime_checks", runtime_checks))
            route["project_import_metadata"] = route.get(
                "project_import_metadata", import_metadata
            )
            route["phases"] = [
                {
                    **phase,
                    "source_refs": list(
                        phase.get(
                            "source_refs", phase_source_refs.get(phase["phase_id"], [])
                        )
                    ),
                }
                for phase in route["phases"]
            ]
            normalized_routes.append(route)
        return normalized_routes

    @staticmethod
    def _normalized(value: str) -> str:
        return " ".join(value.split()).casefold()

    @staticmethod
    def _approved_resolution(
        route: DefaultLearningRoute, match_reason: str
    ) -> ResolvedPlanningRoute:
        return ResolvedPlanningRoute(
            goal_type=route.goal_type,
            goal_name=route.goal_name,
            planning_status="approved_route",
            match_reason=match_reason,
            route_id=route.route_id,
            route_version=route.route_version,
            route_status="approved",
            planning_label=route.planning_label,
            phases=route.phases,
            sources=route.sources,
            runtime_checks=route.runtime_checks,
        )

    @staticmethod
    def _provisional(
        goal_type: str, goal_name: str, match_reason: str, unknown: str
    ) -> ResolvedPlanningRoute:
        return ResolvedPlanningRoute(
            goal_type=goal_type,
            goal_name=goal_name,
            planning_status="provisional",
            match_reason=match_reason,
            unknowns_to_confirm=[unknown],
        )
