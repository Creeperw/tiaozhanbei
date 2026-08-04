"""Read-only in-memory repository for official exam delivery JSONL."""

from __future__ import annotations

import json
import threading
from collections import defaultdict
from copy import deepcopy
from pathlib import Path
from typing import Any, Iterable

from .jsonl_io import iter_jsonl


class OfficialExamRepository:
    """Serve indexed reads while keeping large detail/review files lazy."""

    STAGE_FILES = (
        "exam_tracks",
        "exam_stage_nodes",
        "exam_stage_edges",
        "exam_stage_stats",
    )
    STRUCTURE_FILES = (
        "syllabus_nodes",
        "track_node_memberships",
    )
    STATUS_FILES = (
        "requirement_mapping_status",
    )
    CATALOG_FILES = ("exam_catalog_nodes",)
    MATCH_FILES = (
        "node_kp_matches",
        "kp_exam_matches",
    )
    DETAIL_FILES = STRUCTURE_FILES + STATUS_FILES + CATALOG_FILES + MATCH_FILES
    REQUIRED = STAGE_FILES + DETAIL_FILES
    HEAVY_OPTIONAL = (
        "mapping_review_queue",
        "unmapped_requirements",
        "source_issues",
        "deterministic_review_sample",
    )

    def __init__(self, data_dir: Path, *, public_kp_path: Path | None = None):
        self.data_dir = Path(data_dir).resolve()
        if not self.data_dir.is_dir():
            raise FileNotFoundError(f"official exam delivery directory not found: {self.data_dir}")
        self.public_kp_path = (
            Path(public_kp_path).resolve()
            if public_kp_path is not None
            else (
                self.data_dir.parent
                / "04_knowledge_points"
                / "final_knowledge_points.json"
            ).resolve()
        )
        self._load_lock = threading.RLock()
        self._rows: dict[str, list[dict[str, Any]]] = {}
        for name in self.REQUIRED:
            path = self.data_dir / f"{name}.jsonl"
            if not path.is_file():
                raise FileNotFoundError(f"missing required artifact: {path}")
        for name in self.STAGE_FILES:
            self._rows[name] = list(iter_jsonl(self.data_dir / f"{name}.jsonl"))
        for optional in ("validation_report", "manifest"):
            path = self.data_dir / f"{optional}.jsonl"
            self._rows[optional] = list(iter_jsonl(path)) if path.is_file() else []
        self._details_loaded = False
        self._statuses_loaded = False
        self._catalog_loaded = False
        self._matches_loaded = False
        self._public_kps_loaded = False
        self._build_stage_indexes()

    @staticmethod
    def _copies(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
        return [dict(row) for row in rows]

    @property
    def details_loaded(self) -> bool:
        return self._details_loaded

    @property
    def catalog_loaded(self) -> bool:
        return self._catalog_loaded

    @property
    def matches_loaded(self) -> bool:
        return self._matches_loaded

    @property
    def public_kps_loaded(self) -> bool:
        return self._public_kps_loaded

    def _build_stage_indexes(self) -> None:
        self.tracks = {row["track_id"]: row for row in self._rows["exam_tracks"]}
        self.stages = {row["stage_id"]: row for row in self._rows["exam_stage_nodes"]}
        self.stage_stats = {
            row["stage_id"]: row for row in self._rows["exam_stage_stats"]
        }
        self._stage_children: dict[tuple[str, str | None], list[dict[str, Any]]] = defaultdict(list)
        for row in self._rows["exam_stage_nodes"]:
            self._stage_children[(row["track_id"], row.get("parent_stage_id"))].append(row)
        for rows in self._stage_children.values():
            rows.sort(key=lambda row: (row.get("sort_index", 0), row["stage_id"]))

    def _ensure_details(self) -> None:
        if self._details_loaded:
            return
        with self._load_lock:
            if self._details_loaded:
                return
            for name in self.STRUCTURE_FILES:
                self._rows[name] = list(iter_jsonl(self.data_dir / f"{name}.jsonl"))
            self._ensure_statuses()
            self.nodes = {row["node_id"]: row for row in self._rows["syllabus_nodes"]}
            self.memberships = {
                row["membership_id"]: row for row in self._rows["track_node_memberships"]
            }

            self._membership_children: dict[tuple[str, str | None], list[dict[str, Any]]] = defaultdict(list)
            for row in self._rows["track_node_memberships"]:
                key = (row["track_id"], row.get("parent_membership_id"))
                self._membership_children[key].append(row)
            for rows in self._membership_children.values():
                rows.sort(key=lambda row: (row.get("sort_index", 0), row["membership_id"]))

            self._details_loaded = True

    def _ensure_statuses(self) -> None:
        if self._statuses_loaded:
            return
        with self._load_lock:
            if self._statuses_loaded:
                return
            for name in self.STATUS_FILES:
                self._rows[name] = list(iter_jsonl(self.data_dir / f"{name}.jsonl"))
            self.mapping_status = {
                row["node_id"]: row for row in self._rows["requirement_mapping_status"]
            }
            self._statuses_loaded = True

    def _ensure_catalog(self) -> None:
        if self._catalog_loaded:
            return
        with self._load_lock:
            if self._catalog_loaded:
                return
            self._ensure_statuses()
            rows = list(iter_jsonl(self.data_dir / "exam_catalog_nodes.jsonl"))
            self._rows["exam_catalog_nodes"] = rows
            self.catalog_nodes: dict[str, dict[str, Any]] = {}
            self._catalog_children: dict[str | None, list[dict[str, Any]]] = defaultdict(list)
            grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
            for original in rows:
                row = dict(original)
                if row.get("node_type") == "requirement":
                    status = self.mapping_status.get(str(row.get("canonical_node_id")), {})
                    row["mapping_status"] = status.get("mapping_status", "unmapped")
                    row["accepted_count"] = int(status.get("accepted_count") or 0)
                    row["candidate_count"] = int(status.get("candidate_count") or 0)
                self.catalog_nodes[row["catalog_node_id"]] = row
                self._catalog_children[row.get("parent_id")].append(row)
                grouped[row["track_id"]].append(row)
            for catalog_rows in self._catalog_children.values():
                catalog_rows.sort(
                    key=lambda row: (row.get("node_order", 0), row["catalog_node_id"])
                )
            for catalog_rows in grouped.values():
                catalog_rows.sort(
                    key=lambda row: (
                        row.get("node_order", 0),
                        row.get("level", 0),
                        row["catalog_node_id"],
                    )
                )
            self._catalog_by_track = dict(grouped)
            self._catalog_loaded = True

    def _ensure_matches(self) -> None:
        if self._matches_loaded:
            return
        with self._load_lock:
            if self._matches_loaded:
                return
            self._ensure_details()
            for name in self.MATCH_FILES:
                self._rows[name] = list(iter_jsonl(self.data_dir / f"{name}.jsonl"))

            self._matches_by_node: dict[str, list[dict[str, Any]]] = defaultdict(list)
            for row in self._rows["node_kp_matches"]:
                self._matches_by_node[row["node_id"]].append(row)
            for rows in self._matches_by_node.values():
                rows.sort(
                    key=lambda row: (
                        0 if row.get("decision") == "accepted" else 1,
                        row.get("rank", 0),
                        row.get("kp_id", ""),
                    )
                )

            self._kp_exam: dict[str, list[dict[str, Any]]] = defaultdict(list)
            for row in self._rows["kp_exam_matches"]:
                self._kp_exam[row["kp_id"]].append(row)
            self._subtree_kp_cache: dict[str, dict[str, Any]] = {}
            self._matches_loaded = True

    def list_tracks(self) -> list[dict[str, Any]]:
        return self._copies(self._rows["exam_tracks"])

    def get_track(self, track_id: str) -> dict[str, Any]:
        try:
            return dict(self.tracks[track_id])
        except KeyError as exc:
            raise KeyError(f"unknown track_id: {track_id}") from exc

    def get_membership_children(
        self,
        track_id: str,
        parent_membership_id: str | None = None,
    ) -> list[dict[str, Any]]:
        self.get_track(track_id)
        self._ensure_details()
        if parent_membership_id is not None:
            parent = self.memberships.get(parent_membership_id)
            if parent is None or parent.get("track_id") != track_id:
                raise KeyError(
                    f"membership {parent_membership_id} does not belong to track {track_id}"
                )
        rows = self._membership_children.get((track_id, parent_membership_id), [])
        result = []
        for membership in rows:
            node = self.nodes.get(membership["node_id"], {})
            child_count = len(
                self._membership_children.get((track_id, membership["membership_id"]), [])
            )
            result.append(
                {
                    **dict(membership),
                    "node": dict(node),
                    "title": (
                        node.get("title_normalized")
                        or node.get("title_raw")
                        or membership["membership_id"]
                    ),
                    "child_count": child_count,
                }
            )
        return result

    def get_membership(self, track_id: str, membership_id: str) -> dict[str, Any]:
        self.get_track(track_id)
        self._ensure_details()
        membership = self.memberships.get(membership_id)
        if membership is None or membership.get("track_id") != track_id:
            raise KeyError(f"membership {membership_id} does not belong to track {track_id}")
        breadcrumb = []
        current = membership
        while current is not None:
            node = self.nodes.get(current["node_id"], {})
            breadcrumb.append(
                {
                    **dict(current),
                    "title": (
                        node.get("title_normalized")
                        or node.get("title_raw")
                        or current["membership_id"]
                    ),
                }
            )
            parent_id = current.get("parent_membership_id")
            current = self.memberships.get(parent_id) if parent_id else None
        breadcrumb.reverse()
        return {
            "membership": dict(membership),
            "node": dict(self.nodes.get(membership["node_id"], {})),
            "breadcrumb": breadcrumb,
            "child_count": len(
                self._membership_children.get((track_id, membership_id), [])
            ),
        }

    def get_track_catalog(self, track_id: str) -> list[dict[str, Any]]:
        """Return the complete, ordered catalog without loading KP match rows."""

        self.get_track(track_id)
        self._ensure_catalog()
        return self._copies(self._catalog_by_track.get(track_id, []))

    def get_stage(self, stage_id: str) -> dict[str, Any]:
        try:
            row = dict(self.stages[stage_id])
        except KeyError as exc:
            raise KeyError(f"unknown stage_id: {stage_id}") from exc
        row["stats"] = dict(self.stage_stats.get(stage_id, {}))
        return row

    def get_stage_children(
        self,
        track_id: str,
        parent_stage_id: str | None = None,
    ) -> list[dict[str, Any]]:
        if track_id not in self.tracks:
            raise KeyError(f"unknown track_id: {track_id}")
        if parent_stage_id:
            parent = self.stages.get(parent_stage_id)
            if parent is None or parent["track_id"] != track_id:
                raise KeyError(f"stage {parent_stage_id} does not belong to track {track_id}")
        result: list[dict[str, Any]] = []
        for child in self._stage_children.get((track_id, parent_stage_id), []):
            row = dict(child)
            row["stats"] = dict(self.stage_stats.get(child["stage_id"], {}))
            result.append(row)
        return result

    def get_track_stage_graph(self, track_id: str) -> dict[str, Any]:
        """Return the complete, lightweight stage graph for one exam track."""

        track = self.get_track(track_id)
        nodes: list[dict[str, Any]] = []
        for stage in self._rows["exam_stage_nodes"]:
            if stage["track_id"] != track_id:
                continue
            row = dict(stage)
            row["stats"] = dict(self.stage_stats.get(stage["stage_id"], {}))
            nodes.append(row)
        nodes.sort(
            key=lambda row: (
                row.get("depth", 0),
                row.get("sort_index", 0),
                row["stage_id"],
            )
        )
        edges = [
            dict(row)
            for row in self._rows["exam_stage_edges"]
            if row["track_id"] == track_id
        ]
        edges.sort(
            key=lambda row: (
                row.get("edge_type", ""),
                row.get("from_stage_id", ""),
                row.get("to_stage_id", ""),
                row.get("edge_id", ""),
            )
        )
        return {"track": track, "nodes": nodes, "edges": edges}

    def _ensure_public_kps(self) -> None:
        if self._public_kps_loaded:
            return
        with self._load_lock:
            if self._public_kps_loaded:
                return
            if not self.public_kp_path.is_file():
                self._public_kps = {}
                self._public_kps_loaded = True
                return
            try:
                with self.public_kp_path.open("r", encoding="utf-8-sig") as handle:
                    payload = json.load(handle)
            except (OSError, json.JSONDecodeError) as exc:
                raise RuntimeError("public knowledge-point data is unavailable") from exc
            if not isinstance(payload, list):
                raise RuntimeError("public knowledge-point data must be a JSON array")
            public_kps: dict[str, dict[str, Any]] = {}
            for original in payload:
                if not isinstance(original, dict):
                    continue
                kp = original.get("kp", original)
                if not isinstance(kp, dict):
                    continue
                kp_id = str(kp.get("kp_id") or "").strip()
                if kp_id:
                    public_kps[kp_id] = kp
            self._public_kps = public_kps
            self._public_kps_loaded = True

    @staticmethod
    def _kp_level(kp: dict[str, Any], level: int) -> str:
        return str(
            kp.get(f"kp_lv{level}")
            or kp.get(f"kp_Lv{level}")
            or kp.get(f"kp_LV{level}")
            or ""
        ).strip()

    def _enrich_match(self, original: dict[str, Any]) -> dict[str, Any]:
        row = dict(original)
        levels = [str(row.get(f"kp_lv{level}") or "").strip() for level in (1, 2, 3)]
        if not all(levels):
            self._ensure_public_kps()
            public = self._public_kps.get(str(row.get("kp_id") or ""), {})
            levels = [
                value or self._kp_level(public, level)
                for level, value in zip((1, 2, 3), levels)
            ]
        for level, value in zip((1, 2, 3), levels):
            row[f"kp_lv{level}"] = value
        row["name"] = levels[2] or levels[1] or levels[0] or str(row.get("kp_id") or "")
        row["path"] = [value for value in levels if value]
        row["scores"] = dict(row.get("score_components") or row.get("scores") or {})
        return row

    def get_requirement_matches(
        self,
        node_id: str,
        *,
        include_candidates: bool = True,
    ) -> dict[str, Any]:
        self._ensure_statuses()
        if node_id not in self.mapping_status:
            raise KeyError(f"unknown requirement node_id: {node_id}")
        self._ensure_matches()
        node = self.nodes.get(node_id)
        if node is None or not node.get("is_requirement"):
            raise KeyError(f"unknown requirement node_id: {node_id}")
        matches = self._matches_by_node.get(node_id, [])
        if not include_candidates:
            matches = [row for row in matches if row.get("decision") == "accepted"]
        return {
            "requirement": dict(node),
            "mapping_status": dict(self.mapping_status.get(node_id, {})),
            "matches": [self._enrich_match(row) for row in matches],
        }

    def get_catalog_subtree_knowledge_points(
        self,
        catalog_node_id: str,
        *,
        accepted_only: bool = True,
    ) -> dict[str, Any]:
        """Return unique KPs mapped anywhere below one catalog node in one read."""

        self._ensure_catalog()
        root = self.catalog_nodes.get(catalog_node_id)
        if root is None:
            raise KeyError(f"unknown catalog_node_id: {catalog_node_id}")
        self._ensure_matches()
        cache_key = f"{catalog_node_id}:{int(accepted_only)}"
        cached = self._subtree_kp_cache.get(cache_key)
        if cached is not None:
            return deepcopy(cached)

        pending = [catalog_node_id]
        requirements: list[dict[str, Any]] = []
        while pending:
            parent_id = pending.pop()
            for child in reversed(self._catalog_children.get(parent_id, [])):
                if child.get("node_type") == "requirement":
                    requirements.append(child)
                else:
                    pending.append(child["catalog_node_id"])
        if root.get("node_type") == "requirement":
            requirements.insert(0, root)
        requirements.sort(
            key=lambda row: (row.get("node_order", 0), row["catalog_node_id"])
        )

        grouped: dict[str, dict[str, Any]] = {}
        mapping_count = 0
        for requirement in requirements:
            node_id = str(requirement.get("canonical_node_id") or "")
            requirement_node = self.nodes.get(node_id, {})
            for original_match in self._matches_by_node.get(node_id, []):
                if accepted_only and original_match.get("decision") != "accepted":
                    continue
                match = self._enrich_match(original_match)
                kp_id = str(match.get("kp_id") or "").strip()
                if not kp_id:
                    continue
                mapping_count += 1
                source = {
                    "catalog_node_id": requirement["catalog_node_id"],
                    "requirement_node_id": node_id,
                    "requirement_title": (
                        requirement.get("title_normalized")
                        or requirement.get("title")
                        or requirement_node.get("title_normalized")
                        or requirement_node.get("title_raw")
                        or ""
                    ),
                    "requirement_path": list(requirement.get("path") or []),
                    "decision": match.get("decision", "candidate"),
                    "rank": match.get("rank"),
                    "confidence": match.get("confidence"),
                    "method": list(match.get("method") or []),
                    "relation": match.get("relation"),
                    "reason": match.get("reason"),
                    "scores": dict(match.get("scores") or {}),
                }
                item = grouped.setdefault(
                    kp_id,
                    {
                        "kp_id": kp_id,
                        "name": match.get("name") or kp_id,
                        "path": list(match.get("path") or []),
                        "decision": "candidate",
                        "accepted_count": 0,
                        "candidate_count": 0,
                        "best_match": match,
                        "requirements": [],
                    },
                )
                item["requirements"].append(source)
                decision = str(match.get("decision") or "candidate")
                if decision == "accepted":
                    item["decision"] = "accepted"
                    item["accepted_count"] += 1
                else:
                    item["candidate_count"] += 1
                current = item["best_match"]
                current_key = (
                    0 if current.get("decision") == "accepted" else 1,
                    current.get("rank") or 1_000_000,
                    -(float(current.get("confidence") or 0)),
                )
                match_key = (
                    0 if decision == "accepted" else 1,
                    match.get("rank") or 1_000_000,
                    -(float(match.get("confidence") or 0)),
                )
                if match_key < current_key:
                    item["best_match"] = match

        knowledge_points = list(grouped.values())
        knowledge_points.sort(
            key=lambda item: (
                0 if item["decision"] == "accepted" else 1,
                item["name"],
                item["kp_id"],
            )
        )
        payload = {
            "catalog_node": dict(root),
            "requirement_count": len(requirements),
            "mapping_count": mapping_count,
            "knowledge_points": knowledge_points,
            "total": len(knowledge_points),
        }
        self._subtree_kp_cache[cache_key] = payload
        return deepcopy(payload)

    def get_stage_requirements(
        self,
        stage_id: str,
        *,
        offset: int = 0,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        self._ensure_details()
        if offset < 0 or limit < 0:
            raise ValueError("offset and limit must be non-negative")
        stage = self.stages.get(stage_id)
        if stage is None:
            raise KeyError(f"unknown stage_id: {stage_id}")
        root_membership = stage["membership_id"]
        pending = [root_membership]
        requirements: list[dict[str, Any]] = []
        while pending:
            parent = pending.pop()
            children = self._membership_children.get((stage["track_id"], parent), [])
            for child in reversed(children):
                if child.get("is_requirement"):
                    node = self.nodes[child["node_id"]]
                    requirements.append(
                        {
                            "membership": dict(child),
                            "requirement": dict(node),
                            "mapping_status": dict(self.mapping_status.get(child["node_id"], {})),
                        }
                    )
                else:
                    pending.append(child["membership_id"])
        requirements.sort(
            key=lambda row: (
                row["membership"].get("sort_index", 0),
                row["membership"]["membership_id"],
            )
        )
        return requirements[offset : offset + limit]

    def get_kp_exam_matches(self, kp_id: str) -> list[dict[str, Any]]:
        self._ensure_matches()
        return self._copies(self._kp_exam.get(str(kp_id), []))

    def iter_review_queue(
        self,
        *,
        track_id: str | None = None,
        mapping_status: str | None = None,
    ) -> Iterable[dict[str, Any]]:
        """Stream the large review queue instead of loading it at startup."""

        path = self.data_dir / "mapping_review_queue.jsonl"
        if not path.is_file():
            return
        for row in iter_jsonl(path):
            if track_id is not None and track_id not in (row.get("track_ids") or []):
                continue
            if mapping_status is not None and row.get("mapping_status") != mapping_status:
                continue
            yield dict(row)

    def get_validation_summary(self) -> dict[str, Any]:
        for row in self._rows["validation_report"]:
            if row.get("record_type") == "validation_summary":
                return dict(row)
        return {}
