from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel

from competition_app.contracts.knowledge import EvidenceItem


@dataclass(frozen=True)
class KnowledgeAssetPaths:
    knowledge_points: Path
    kp_chunk_links: Path
    source_chunks: Path

    @classmethod
    def from_delivery_root(cls, root: Path) -> "KnowledgeAssetPaths":
        return cls(
            knowledge_points=root / "04_knowledge_points" / "final_knowledge_points.json",
            kp_chunk_links=root / "05_bridge" / "kp_chunk_links.jsonl",
            source_chunks=root / "03_pipeline_chunks" / "source_chunks.jsonl",
        )


class KnowledgePointMatch(BaseModel):
    kp_id: str
    name: str
    level1: str = ""
    level2: str = ""
    score: float


class KnowledgeAssetRepository:
    def __init__(self, paths: KnowledgeAssetPaths) -> None:
        self.paths = paths
        self._knowledge_points: list[dict[str, object]] | None = None
        self._links_by_kp: dict[str, list[dict[str, object]]] | None = None
        self._kp_ids_by_chunk: dict[str, list[str]] | None = None
        self._chunks_by_uid: dict[str, dict[str, object]] | None = None

    def resolve_topic(self, query: str, limit: int = 5) -> list[KnowledgePointMatch]:
        normalized = self._normalize(query)
        matches: list[KnowledgePointMatch] = []
        for item in self._load_knowledge_points():
            name = str(item.get("kp_Lv3_standard", "")).strip()
            aliases = str(item.get("kp_Lv3_others", "")).strip()
            candidates = [name, *[part.strip() for part in aliases.split("|") if part.strip()]]
            candidate_scores = [self._text_score(normalized, self._normalize(value)) for value in candidates]
            score = max(candidate_scores, default=0.0)
            if score <= 0:
                continue
            matches.append(
                KnowledgePointMatch(
                    kp_id=str(item["kp_id"]),
                    name=name,
                    level1=str(item.get("kp_Lv1", "")),
                    level2=str(item.get("kp_Lv2", "")),
                    score=score,
                )
            )
        return sorted(matches, key=lambda item: (-item.score, item.kp_id))[:limit]

    def get_chunk_evidence(self, kp_id: str, limit: int = 5) -> list[EvidenceItem]:
        links = self._load_links().get(kp_id, [])
        chunks = self._load_chunks()
        evidence: list[EvidenceItem] = []
        for index, link in enumerate(links[:limit], start=1):
            chunk_uid = str(link.get("chunk_uid", ""))
            chunk = chunks.get(chunk_uid)
            if not chunk:
                continue
            bridge_layer = str(link.get("bridge_layer") or "strict")
            confidence = 0.95 if bridge_layer == "strict" else 0.65
            evidence.append(
                EvidenceItem(
                    evidence_id=f"E_{kp_id}_{index}",
                    source_id=chunk_uid,
                    content_summary=self._teaching_excerpt(str(chunk.get("text", ""))),
                    authority_level="textbook",
                    confidence=confidence,
                    bridge_layer=bridge_layer,
                )
            )
        return evidence

    def kp_ids_for_chunks(self, chunk_uids: list[str]) -> list[str]:
        if self._kp_ids_by_chunk is None:
            by_chunk: dict[str, set[str]] = {}
            for kp_id, links in self._load_links().items():
                for link in links:
                    chunk_uid = str(link.get("chunk_uid", ""))
                    if chunk_uid:
                        by_chunk.setdefault(chunk_uid, set()).add(kp_id)
            self._kp_ids_by_chunk = {
                chunk_uid: sorted(kp_ids) for chunk_uid, kp_ids in by_chunk.items()
            }
        ordered: list[str] = []
        for chunk_uid in chunk_uids:
            for kp_id in self._kp_ids_by_chunk.get(chunk_uid, []):
                if kp_id not in ordered:
                    ordered.append(kp_id)
        return ordered

    def _load_knowledge_points(self) -> list[dict[str, object]]:
        if self._knowledge_points is None:
            with self.paths.knowledge_points.open("r", encoding="utf-8") as handle:
                value = json.load(handle)
            if not isinstance(value, list):
                raise ValueError("knowledge point file must contain a JSON array")
            normalized: list[dict[str, object]] = []
            for raw_item in value:
                item = raw_item.get("kp", raw_item) if isinstance(raw_item, dict) else raw_item
                if not isinstance(item, dict):
                    continue
                normalized.append({
                    **item,
                    "kp_Lv1": item.get("kp_Lv1") or item.get("kp_lv1", ""),
                    "kp_Lv2": item.get("kp_Lv2") or item.get("kp_lv2", ""),
                    "kp_Lv3_standard": (
                        item.get("kp_Lv3_standard") or item.get("kp_lv3", "")
                    ),
                    "kp_Lv3_others": (
                        item.get("kp_Lv3_others") or item.get("other_name", "")
                    ),
                })
            self._knowledge_points = normalized
        return self._knowledge_points

    def _load_links(self) -> dict[str, list[dict[str, object]]]:
        if self._links_by_kp is None:
            links: dict[str, list[dict[str, object]]] = {}
            for item in self._iter_jsonl(self.paths.kp_chunk_links):
                links.setdefault(str(item["kp_id"]), []).append(item)
            self._links_by_kp = links
        return self._links_by_kp

    def _load_chunks(self) -> dict[str, dict[str, object]]:
        if self._chunks_by_uid is None:
            self._chunks_by_uid = {
                str(item["chunk_uid"]): item for item in self._iter_jsonl(self.paths.source_chunks)
            }
        return self._chunks_by_uid

    @staticmethod
    def _iter_jsonl(path: Path):
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                if line.strip():
                    yield json.loads(line)

    @staticmethod
    def _normalize(value: str) -> str:
        return "".join(value.lower().split())

    @staticmethod
    def _teaching_excerpt(value: str, max_length: int = 360) -> str:
        """Return the lead teaching passage from a potentially mixed-source chunk.

        Source chunks can combine core curriculum text with historical or
        cross-disciplinary extensions. The evidence pack used for a short
        learning resource should expose the leading teaching content rather
        than an entire unbounded chunk; provenance remains available through
        ``source_id`` for review.
        """
        normalized = " ".join(value.split()).strip()
        if len(normalized) <= max_length:
            return normalized
        excerpt = normalized[:max_length]
        last_stop = max(excerpt.rfind("。"), excerpt.rfind("！"), excerpt.rfind("？"))
        if last_stop >= 0:
            return excerpt[: last_stop + 1]
        return excerpt + "…"

    @staticmethod
    def _text_score(query: str, candidate: str) -> float:
        if not candidate:
            return 0.0
        if candidate == query:
            return 1.0
        if candidate in query:
            return 0.95
        if query in candidate:
            return 0.85
        return 0.0
