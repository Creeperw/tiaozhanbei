from __future__ import annotations

import hashlib
import re
from collections.abc import Callable
from typing import Any

from competition_app.contracts.learning_path import (
    LearningPathNavigation,
    LearningPathNode,
    LearningPathPage,
    LearningPathPlanRef,
)
from competition_app.contracts.learning_plan import LongTermPlan, LongTermPlanStage


BookKnowledgeLoader = Callable[[str, int, int], dict[str, Any]]


def _clean_book(value: str) -> str:
    return str(value or "").strip().strip("《》")


def _stable_segment(value: str) -> str:
    readable = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff_-]+", "-", value).strip("-")
    digest = hashlib.sha1(value.encode("utf-8")).hexdigest()[:10]
    return f"{readable[:24]}-{digest}" if readable else digest


class LearningPathProjectionService:
    """Project a persisted long-term plan into a stable navigable hierarchy."""

    def __init__(self, book_knowledge_loader: BookKnowledgeLoader | None = None) -> None:
        self.book_knowledge_loader = book_knowledge_loader

    def page(
        self,
        *,
        learner_id: str,
        plan: LongTermPlan,
        parent_id: str | None = None,
        mastery_rows: list[dict[str, Any]] | None = None,
        offset: int = 0,
        limit: int = 100,
    ) -> LearningPathPage:
        stages = sorted(plan.stages, key=lambda item: item.stage)
        route_id, route_version, route_stage_ids, route_stage_names, source_refs = self._route_metadata(plan)
        plan_ref = LearningPathPlanRef(
            plan_id=plan.plan_id,
            plan_version=plan.version,
            route_id=route_id,
            route_version=route_version,
        )
        stage_nodes = self._stage_nodes(
            plan, stages, route_stage_ids, route_stage_names, source_refs
        )
        current_node_id = next(
            (node.node_id for node in stage_nodes if node.status == "in_progress"),
            stage_nodes[0].node_id if stage_nodes else None,
        )
        if not parent_id:
            return self._paginate(
                learner_id=learner_id,
                plan_ref=plan_ref,
                parent_id=None,
                parent_type=None,
                current_node_id=current_node_id,
                nodes=stage_nodes,
                offset=offset,
                limit=limit,
            )

        stage_index = self._stage_index(parent_id, stage_nodes)
        if stage_index is not None:
            nodes = self._book_nodes(
                plan,
                stages[stage_index],
                stage_nodes[stage_index],
                route_id,
                source_refs,
            )
            return self._paginate(
                learner_id=learner_id,
                plan_ref=plan_ref,
                parent_id=parent_id,
                parent_type="stage",
                current_node_id=next(
                    (node.node_id for node in nodes if node.status == "in_progress"),
                    nodes[0].node_id if nodes else None,
                ),
                nodes=nodes,
                offset=offset,
                limit=limit,
            )

        book_match = self._find_book(parent_id, plan, stages, stage_nodes, route_id, source_refs)
        if book_match is None:
            raise KeyError("learning path parent node does not exist")
        book_node, book_name = book_match
        nodes = self._knowledge_nodes(
            book_node=book_node,
            book_name=book_name,
            route_id=route_id,
            mastery_rows=mastery_rows or [],
            offset=offset,
            limit=limit,
        )
        total = nodes.pop("total")
        items = nodes.pop("items")
        return LearningPathPage(
            learner_id=learner_id,
            plan_ref=plan_ref,
            parent_id=parent_id,
            parent_type="book",
            current_node_id=next(
                (node.node_id for node in items if node.status == "in_progress"),
                items[0].node_id if items else None,
            ),
            nodes=items,
            offset=offset,
            limit=limit,
            total=total,
            has_more=offset + len(items) < total,
        )

    @staticmethod
    def _route_metadata(
        plan: LongTermPlan,
    ) -> tuple[str | None, int | None, list[str], list[str], list[str]]:
        planning_route = plan.planning_route
        if planning_route is None:
            return None, None, [], [], []
        textbook = planning_route.textbook_route
        if textbook is None:
            return planning_route.route_id, planning_route.route_version, [], [], []
        route = textbook.route
        if route is None:
            return planning_route.route_id, planning_route.route_version, [], [], []
        source_refs = sorted(
            {
                ref
                for stage in route.stages
                for ref in stage.source_refs
                if str(ref).strip()
            }
        )
        return (
            route.route_id,
            route.route_version,
            [stage.stage_id for stage in route.stages],
            [stage.name for stage in route.stages],
            source_refs,
        )

    @staticmethod
    def _stage_nodes(
        plan: LongTermPlan,
        stages: list[LongTermPlanStage],
        route_stage_ids: list[str],
        route_stage_names: list[str],
        source_refs: list[str],
    ) -> list[LearningPathNode]:
        nodes: list[LearningPathNode] = []
        for index, stage in enumerate(stages):
            stage_id = (
                route_stage_ids[index]
                if index < len(route_stage_ids)
                else f"stage-{stage.stage}"
            )
            node_id = f"plan:{plan.plan_id}:stage:{stage_id}"
            active_stage_id = (
                plan.textbook_selection.stage_id if plan.textbook_selection is not None else None
            )
            active_index = (
                route_stage_ids.index(active_stage_id)
                if active_stage_id in route_stage_ids
                else 0
            )
            status = (
                "completed" if index < active_index
                else "in_progress" if index == active_index
                else "locked"
            )
            nodes.append(
                LearningPathNode(
                    node_id=node_id,
                    node_type="stage",
                    title=(
                        route_stage_names[index]
                        if index < len(route_stage_names)
                        else f"第{stage.stage}阶段"
                    ),
                    order=stage.stage,
                    status=status,
                    has_children=bool(stage.book),
                    child_count=len(stage.book),
                    description=stage.goal,
                    source_refs=source_refs,
                    navigation=LearningPathNavigation(action="expand", parent_id=node_id),
                )
            )
        return nodes

    def _book_nodes(
        self,
        plan: LongTermPlan,
        stage: LongTermPlanStage,
        stage_node: LearningPathNode,
        route_id: str | None,
        source_refs: list[str],
    ) -> list[LearningPathNode]:
        nodes: list[LearningPathNode] = []
        for index, raw_book in enumerate(stage.book):
            book = _clean_book(raw_book)
            node_id = f"{stage_node.node_id}:book:{_stable_segment(book)}"
            child_count = 0
            route_candidates: list[str] = []
            if self.book_knowledge_loader is not None:
                try:
                    summary = self.book_knowledge_loader(book, 0, 1)
                    child_count = int(summary.get("total") or 0)
                    route_candidates = [str(item) for item in summary.get("route_ids") or []]
                except (KeyError, OSError, TypeError, ValueError):
                    child_count = 0
            nodes.append(
                LearningPathNode(
                    node_id=node_id,
                    node_type="book",
                    parent_id=stage_node.node_id,
                    title=f"《{book}》",
                    order=index + 1,
                    status="in_progress" if stage_node.status == "in_progress" and index == 0 else "locked",
                    has_children=child_count > 0,
                    child_count=child_count,
                    description=f"服务于本阶段目标：{stage.goal}",
                    source_refs=source_refs,
                    navigation=LearningPathNavigation(
                        action="open_knowledge_atlas" if child_count else "expand",
                        parent_id=node_id,
                        route_id=self._atlas_route_id(route_id, route_candidates),
                        book=book,
                    ),
                )
            )
        return nodes

    def _knowledge_nodes(
        self,
        *,
        book_node: LearningPathNode,
        book_name: str,
        route_id: str | None,
        mastery_rows: list[dict[str, Any]],
        offset: int,
        limit: int,
    ) -> dict[str, Any]:
        if self.book_knowledge_loader is None:
            return {"items": [], "total": 0}
        payload = self.book_knowledge_loader(book_name, offset, limit)
        mastery_by_kp = {
            str(row.get("kp_id")): float(row.get("mastery") or 0.0)
            for row in mastery_rows
            if row.get("kp_id")
        }
        items: list[LearningPathNode] = []
        for index, row in enumerate(payload.get("items") or []):
            kp_id = str(row.get("kp_id") or row.get("id") or "").strip()
            if not kp_id:
                continue
            mastery = mastery_by_kp.get(kp_id)
            status = (
                "completed" if mastery is not None and mastery >= 0.8
                else "in_progress" if mastery is not None and mastery > 0
                else "unassessed"
            )
            items.append(
                LearningPathNode(
                    node_id=f"{book_node.node_id}:kp:{kp_id}",
                    node_type="knowledge_point",
                    parent_id=book_node.node_id,
                    title=str(row.get("name") or row.get("kp_lv3") or kp_id),
                    order=offset + index + 1,
                    status=status,
                    progress=mastery or 0.0,
                    mastery=mastery,
                    has_children=False,
                    child_count=0,
                    description=str(row.get("chapter") or row.get("kp_lv2") or "") or None,
                    source_refs=[str(item) for item in row.get("source_refs") or []],
                    navigation=LearningPathNavigation(
                        action="open_knowledge_point",
                        route_id=book_node.navigation.route_id
                        or self._atlas_route_id(route_id),
                        book=book_name,
                        chapter=str(row.get("chapter") or row.get("kp_lv2") or "") or None,
                        kp_id=kp_id,
                    ),
                )
            )
        return {"items": items, "total": int(payload.get("total") or len(items))}

    @staticmethod
    def _atlas_route_id(
        route_id: str | None,
        route_candidates: list[str] | None = None,
    ) -> str:
        normalized = str(route_id or "").lower()
        candidates = route_candidates or []
        preferred = "textbook_14_5"
        if "postgraduate" in normalized or "graduate" in normalized:
            preferred = "postgraduate"
        elif "physician" in normalized or "practitioner" in normalized:
            preferred = "tcm_assistant"
        if not candidates or preferred in candidates:
            return preferred
        if "textbook_14_5" in candidates:
            return "textbook_14_5"
        return candidates[0]

    def _find_book(
        self,
        parent_id: str,
        plan: LongTermPlan,
        stages: list[LongTermPlanStage],
        stage_nodes: list[LearningPathNode],
        route_id: str | None,
        source_refs: list[str],
    ) -> tuple[LearningPathNode, str] | None:
        for index, stage in enumerate(stages):
            for raw_book, node in zip(
                stage.book,
                self._book_nodes(plan, stage, stage_nodes[index], route_id, source_refs),
            ):
                if node.node_id == parent_id:
                    return node, _clean_book(raw_book)
        return None

    @staticmethod
    def _stage_index(parent_id: str, stage_nodes: list[LearningPathNode]) -> int | None:
        return next((index for index, node in enumerate(stage_nodes) if node.node_id == parent_id), None)

    @staticmethod
    def _paginate(
        *,
        learner_id: str,
        plan_ref: LearningPathPlanRef,
        parent_id: str | None,
        parent_type: str | None,
        current_node_id: str | None,
        nodes: list[LearningPathNode],
        offset: int,
        limit: int,
    ) -> LearningPathPage:
        selected = nodes[offset : offset + limit]
        return LearningPathPage(
            learner_id=learner_id,
            plan_ref=plan_ref,
            parent_id=parent_id,
            parent_type=parent_type,
            current_node_id=current_node_id,
            nodes=selected,
            offset=offset,
            limit=limit,
            total=len(nodes),
            has_more=offset + len(selected) < len(nodes),
        )
