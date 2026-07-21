from __future__ import annotations

import asyncio
import ast
import importlib
import json
import os
import re
import subprocess
import sys
import threading
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Literal
from uuid import uuid4

from competition_app.contracts.knowledge import (
    EvidenceItem,
    EvidencePack,
    QuestionBridge,
    QuestionDetail,
    QuestionRetrievalMetadata,
    QuestionSearchResult,
)


@dataclass(frozen=True)
class KnowledgeDeliveryPaths:
    """Paths belonging to the 2026-07-18 backend delivery.

    ``public_data`` and ``video_results`` are read-only. All mutations are kept
    below ``runtime_root`` and are scoped by the authenticated owner id.
    """

    component_root: Path
    public_data: Path
    video_results: Path
    runtime_root: Path
    public_vector_store: Path

    @classmethod
    def from_handoff_root(
        cls,
        handoff_root: Path,
        *,
        runtime_root: Path | None = None,
        public_vector_store: Path | None = None,
    ) -> "KnowledgeDeliveryPaths":
        root = Path(handoff_root).resolve()
        component = root / "知识库管理组件"
        return cls(
            component_root=component,
            public_data=component / "data" / "backend_delivery",
            video_results=root / "bilibili_video_page" / "runtime" / "full_batch_results",
            runtime_root=(runtime_root or component / "runtime").resolve(),
            public_vector_store=(public_vector_store or root.parent / "vdb_store").resolve(),
        )

    @property
    def question_runtime(self) -> Path:
        return self.runtime_root / "questions"

    @property
    def knowledge_customer_root(self) -> Path:
        return self.runtime_root / "knowledge_customers"

    @property
    def exam_customer_root(self) -> Path:
        return self.runtime_root / "exam_customers"

    def validate(self) -> None:
        required = (
            self.component_root / "retrieval" / "hybrid_question_retrieval.py",
            self.public_data / "01_question_bank" / "formatted_questions.json",
            self.public_data / "03_pipeline_chunks" / "source_chunks.jsonl",
            self.public_data / "04_knowledge_points" / "final_knowledge_points.json",
            self.public_data / "08_exam_learning_path_2025",
        )
        missing = [str(path) for path in required if not path.exists()]
        if missing:
            raise FileNotFoundError("知识库交接包不完整：" + "; ".join(missing))


def _iter_jsonl(path: Path) -> Iterable[dict[str, Any]]:
    if not path.is_file():
        return
    with path.open("r", encoding="utf-8-sig") as handle:
        for line in handle:
            if line.strip():
                row = json.loads(line)
                if isinstance(row, dict):
                    yield row


def _unwrap_kp(record: dict[str, Any]) -> dict[str, Any]:
    value = record.get("kp", record)
    return dict(value) if isinstance(value, dict) else {}


def _safe_owner(owner_id: str) -> str:
    owner = str(owner_id or "").strip()
    if not owner or not re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", owner):
        raise ValueError("owner_id 格式无效")
    return owner


class DeliveryKnowledgeMapStore:
    """Framework-neutral reader for the handoff package's knowledge map schema."""

    def __init__(self, paths: KnowledgeDeliveryPaths) -> None:
        self.paths = paths
        self._lock = threading.RLock()
        self._hierarchy_ready = False
        self._questions_ready = False
        self._chunks_ready = False
        self._videos_ready = False
        self.kps: dict[str, dict[str, Any]] = {}
        self.tree: dict[str, dict[str, list[dict[str, Any]]]] = {}
        self.questions_by_kp: dict[str, list[dict[str, Any]]] = defaultdict(list)
        self.chunk_offsets: dict[str, int] = {}
        self.videos_by_kp: dict[str, list[dict[str, Any]]] = defaultdict(list)
        self.route_definitions = self._load_route_definitions()

    def _load_route_definitions(self) -> list[dict[str, Any]]:
        source_path = self.paths.component_root / "web_console" / "knowledge_map.py"
        values: dict[str, Any] = {}
        try:
            tree = ast.parse(source_path.read_text(encoding="utf-8"))
            for node in tree.body:
                if isinstance(node, (ast.Assign, ast.AnnAssign)):
                    target = node.targets[0] if isinstance(node, ast.Assign) else node.target
                    if isinstance(target, ast.Name) and target.id in {"PRACTITIONER_BOOKS", "POSTGRADUATE_BOOKS"}:
                        values[target.id] = ast.literal_eval(node.value)
        except (OSError, SyntaxError, ValueError):
            values = {}
        practitioner = list(values.get("PRACTITIONER_BOOKS") or [])
        postgraduate = list(values.get("POSTGRADUATE_BOOKS") or [])
        return [
            {"id": "textbook_14_5", "name": "十四五规划教材总览", "description": "展示十四五规划教材", "books": postgraduate},
            {"id": "tcm_assistant", "name": "中医执业助理医师资格考试", "description": "展示执业医师所用教材", "books": practitioner},
            {"id": "postgraduate", "name": "考研学习路线", "description": "展示考研学习教科书", "books": postgraduate},
        ]

    def ensure_hierarchy(self) -> None:
        if self._hierarchy_ready:
            return
        with self._lock:
            if self._hierarchy_ready:
                return
            path = self.paths.public_data / "04_knowledge_points" / "final_knowledge_points.json"
            records = json.loads(path.read_text(encoding="utf-8-sig"))
            tree: dict[str, dict[str, list[dict[str, Any]]]] = {}
            kps: dict[str, dict[str, Any]] = {}
            for original in records:
                if not isinstance(original, dict):
                    continue
                kp = _unwrap_kp(original)
                kp_id = str(kp.get("kp_id") or "").strip()
                if not kp_id:
                    continue
                lv1 = str(kp.get("kp_lv1") or "未分类").strip()
                lv2 = str(kp.get("kp_lv2") or "未分类").strip()
                kps[kp_id] = kp
                tree.setdefault(lv1, {}).setdefault(lv2, []).append(kp)
            self.kps, self.tree, self._hierarchy_ready = kps, tree, True

    def routes(self) -> list[dict[str, Any]]:
        self.ensure_hierarchy()
        available_books = set(self.tree)
        output = []
        for route in self.route_definitions:
            requested = set(route["books"])
            available = sorted(requested & available_books)
            output.append(
                {
                    "id": route["id"],
                    "name": route["name"],
                    "description": route["description"],
                    "book_count": len(available),
                    "books": available,
                    "missing_books": sorted(requested - available_books),
                }
            )
        return output

    def _route_books(self, route_id: str) -> set[str]:
        self.ensure_hierarchy()
        route = next(
            (row for row in self.route_definitions if row["id"] == route_id),
            self.route_definitions[0],
        )
        return set(route["books"]) & set(self.tree)

    def nodes(
        self,
        level: int,
        lv1: str = "",
        lv2: str = "",
        route_id: str = "textbook_14_5",
    ) -> dict[str, Any]:
        self.ensure_hierarchy()
        route_books = self._route_books(route_id)
        stats = {
            "lv1": len(route_books),
            "lv2": sum(len(self.tree[book]) for book in route_books),
            "lv3": sum(len(items) for book in route_books for items in self.tree[book].values()),
        }
        if level == 1:
            rows = [
                {"id": book, "name": book, "count": sum(map(len, children.values())), "children_count": len(children)}
                for book, children in self.tree.items()
                if book in route_books
            ]
        elif level == 2:
            if lv1 not in route_books:
                raise KeyError("一级教材不存在")
            rows = [
                {"id": name, "name": name, "count": len(items), "children_count": len(items), "order_index": index}
                for index, (name, items) in enumerate(self.tree[lv1].items())
            ]
        elif level == 3:
            if lv1 not in route_books:
                raise KeyError("该路线不包含此一级教材")
            items = self.tree.get(lv1, {}).get(lv2)
            if items is None:
                raise KeyError("二级目录不存在")
            self.ensure_questions()
            self.ensure_videos()
            rows = []
            for kp in items:
                kp_id = str(kp["kp_id"])
                rows.append(
                    {
                        "id": kp_id,
                        "name": str(kp.get("kp_lv3") or "未命名知识点"),
                        "alias": kp.get("other_name") or "",
                        "order": kp.get("order") or "",
                        "chunk_count": len(kp.get("raw_content") or []),
                        "question_count": len(self.questions_by_kp.get(kp_id, [])),
                        "video_count": len(self.videos_by_kp.get(kp_id, [])),
                    }
                )
        else:
            raise ValueError("level 只能是 1、2 或 3")
        return {"level": level, "nodes": rows, "count": len(rows), "stats": stats, "route": route_id}

    def learning_path_book_knowledge_points(
        self,
        book: str,
        offset: int = 0,
        limit: int = 100,
    ) -> dict[str, Any]:
        """Flatten a textbook into paged knowledge-point rows for plan projection."""

        self.ensure_hierarchy()
        normalized = str(book or "").strip().strip("《》")
        chapters = self.tree.get(normalized)
        if chapters is None:
            return {
                "book": normalized,
                "items": [],
                "total": 0,
                "offset": offset,
                "limit": limit,
                "route_ids": [],
            }
        rows: list[dict[str, Any]] = []
        for chapter, knowledge_points in chapters.items():
            for kp in knowledge_points:
                kp_id = str(kp.get("kp_id") or "").strip()
                if not kp_id:
                    continue
                rows.append(
                    {
                        "kp_id": kp_id,
                        "name": str(kp.get("kp_lv3") or "未命名知识点"),
                        "chapter": chapter,
                        "source_refs": [
                            str(item)
                            for item in (kp.get("source_refs") or [])
                            if str(item).strip()
                        ],
                    }
                )
        return {
            "book": normalized,
            "items": rows[offset : offset + limit],
            "total": len(rows),
            "offset": offset,
            "limit": limit,
            "route_ids": [
                str(route["id"])
                for route in self.route_definitions
                if normalized in set(route.get("books") or [])
            ],
        }

    def resolve_topic(self, query: str, limit: int = 5) -> list[dict[str, Any]]:
        self.ensure_hierarchy()
        compact = re.sub(r"\s+", "", query).lower()
        if not compact:
            return []
        primary_terms = {
            re.sub(r"\s+", "", segment.strip().split()[0]).lower(): index
            for index, segment in enumerate(re.split(r"[；;。\n]+", query))
            if segment.strip() and segment.strip().split()
        }
        query_terms = set(re.findall(r"[\u4e00-\u9fff]{2,}|[a-z0-9_]{2,}", compact))
        ranked: list[tuple[float, dict[str, Any]]] = []
        for kp in self.kps.values():
            fields = [kp.get("kp_lv3"), kp.get("other_name"), kp.get("kp_lv2"), kp.get("kp_lv1")]
            text = re.sub(r"\s+", "", " ".join(str(value or "") for value in fields)).lower()
            if not text:
                continue
            normalized_fields = [
                re.sub(r"\s+", "", str(value or "")).lower()
                for value in fields
                if str(value or "").strip()
            ]
            exact = 1.0 if compact in text else 0.0
            # A model query commonly contains several entities plus qualifiers,
            # e.g. “四君子汤……理中丸……核心区别”. Resolve every named KP instead
            # of requiring the whole generated query to equal one catalog row.
            named = max(
                (
                    (
                        1.0 - primary_terms[field] * 0.001
                        if field in primary_terms
                        else 0.90 + min(0.08, len(field) * 0.01) - index * 0.01
                    )
                    for index, field in enumerate(normalized_fields)
                    if len(field) >= 2 and field in compact
                ),
                default=0.0,
            )
            terms = set(re.findall(r"[\u4e00-\u9fff]{2,}|[a-z0-9_]{2,}", text))
            overlap = len(query_terms & terms) / max(1, len(query_terms))
            score = max(exact, named, overlap * 0.8)
            if score > 0:
                ranked.append((score, kp))
        ranked.sort(
            key=lambda item: (
                -item[0],
                0 if str(item[1].get("kp_lv1") or "") == "方剂学" else 1,
                str(item[1].get("order") or ""),
                str(item[1].get("kp_id")),
            )
        )
        output: list[dict[str, Any]] = []
        seen_names: set[str] = set()
        for score, kp in ranked:
            name = str(kp.get("kp_lv3") or kp.get("kp_lv2") or kp["kp_id"])
            normalized_name = re.sub(r"\s+", "", name).lower()
            if normalized_name in seen_names:
                continue
            seen_names.add(normalized_name)
            output.append(
                {
                    "kp_id": str(kp["kp_id"]),
                    "name": name,
                    "score": score,
                    "kp": dict(kp),
                }
            )
            if len(output) >= limit:
                break
        return output

    def ensure_questions(self) -> None:
        if self._questions_ready:
            return
        with self._lock:
            if self._questions_ready:
                return
            path = self.paths.public_data / "01_question_bank" / "formatted_questions.json"
            records = json.loads(path.read_text(encoding="utf-8-sig"))
            index: dict[str, list[dict[str, Any]]] = defaultdict(list)
            for row in records:
                if not isinstance(row, dict):
                    continue
                for kp_id in row.get("kp_ids") or []:
                    index[str(kp_id)].append(row)
            self.questions_by_kp = index
            self._questions_ready = True

    def ensure_chunk_offsets(self) -> None:
        if self._chunks_ready:
            return
        with self._lock:
            if self._chunks_ready:
                return
            path = self.paths.public_data / "03_pipeline_chunks" / "source_chunks.jsonl"
            pattern = re.compile(br'"chunk_uid"\s*:\s*"([^"\\]+)"')
            offsets: dict[str, int] = {}
            with path.open("rb") as handle:
                while True:
                    offset = handle.tell()
                    line = handle.readline()
                    if not line:
                        break
                    match = pattern.search(line[:2048])
                    if match:
                        offsets[match.group(1).decode("utf-8")] = offset
            self.chunk_offsets = offsets
            self._chunks_ready = True

    def _chunk(self, chunk_uid: str) -> dict[str, Any] | None:
        offset = self.chunk_offsets.get(chunk_uid)
        if offset is None:
            return None
        path = self.paths.public_data / "03_pipeline_chunks" / "source_chunks.jsonl"
        with path.open("rb") as handle:
            handle.seek(offset)
            row = json.loads(handle.readline().decode("utf-8-sig"))
        metadata = row.get("metadata") or {}
        return {
            "chunk_uid": chunk_uid,
            "book": row.get("book") or "",
            "kp_lv1": row.get("kp_Lv1") or row.get("kp_lv1") or "",
            "kp_lv2": row.get("kp_Lv2") or row.get("kp_lv2") or "",
            "heading": metadata.get("heading_path") or "",
            "text": row.get("text") or "",
            "retrieval_text": row.get("retrieval_text") or row.get("text") or "",
            "metadata": metadata,
        }

    def ensure_videos(self) -> None:
        if self._videos_ready:
            return
        with self._lock:
            if self._videos_ready:
                return
            index: dict[str, list[dict[str, Any]]] = defaultdict(list)
            seen: set[tuple[str, str, int, float]] = set()
            for path in self.paths.video_results.glob("BV*/classification_result.json"):
                try:
                    result = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    continue
                bvid = str(result.get("bvid") or path.parent.name)
                for page in result.get("pages") or []:
                    for segment in page.get("segments") or []:
                        for match in segment.get("kp_matches") or []:
                            kp_id = str(match.get("kp_id") or "")
                            key = (kp_id, bvid, int(page.get("page") or 0), float(segment.get("start_seconds") or 0))
                            if not kp_id or key in seen:
                                continue
                            seen.add(key)
                            index[kp_id].append(
                                {
                                    "bvid": bvid,
                                    "aid": result.get("aid"),
                                    "cid": page.get("cid"),
                                    "page": page.get("page"),
                                    "video_title": result.get("video_title") or "",
                                    "part_title": page.get("original_part_title") or "",
                                    "start_seconds": segment.get("start_seconds") or 0,
                                    "end_seconds": segment.get("end_seconds") or 0,
                                    "topic": segment.get("topic") or "知识讲解",
                                    "transcript": segment.get("transcript") or "",
                                    "match": dict(match),
                                }
                            )
            self.videos_by_kp = index
            self._videos_ready = True

    def detail(self, kp_id: str, question_limit: int = 30) -> dict[str, Any]:
        self.ensure_hierarchy()
        kp = self.kps.get(str(kp_id))
        if kp is None:
            raise KeyError("知识点不存在")
        self.ensure_chunk_offsets()
        self.ensure_questions()
        self.ensure_videos()
        refs = [str(value) for value in kp.get("raw_content") or [] if value]
        chunks = [row for uid in refs if (row := self._chunk(uid)) is not None]
        questions = self.questions_by_kp.get(str(kp_id), [])
        return {
            "kp": dict(kp),
            "chunks": chunks,
            "raw_refs": refs,
            "questions": [dict(row) for row in questions[:question_limit]],
            "question_count": len(questions),
            "videos": [dict(row) for row in self.videos_by_kp.get(str(kp_id), [])],
        }

    def warm(self) -> dict[str, int]:
        self.ensure_hierarchy()
        self.ensure_chunk_offsets()
        self.ensure_questions()
        self.ensure_videos()
        return {
            "knowledge_points": len(self.kps),
            "chunk_offsets": len(self.chunk_offsets),
            "question_links": sum(map(len, self.questions_by_kp.values())),
            "video_links": sum(map(len, self.videos_by_kp.values())),
        }


class KnowledgeDeliveryBackend:
    """Backend facade over every runtime capability shipped in the handoff."""

    def __init__(
        self,
        paths: KnowledgeDeliveryPaths,
        *,
        embedding_base_url: str,
        embedding_model: str,
        embedding_api_key: str | None = None,
        chat_base_url: str = "",
        chat_model: str = "",
        chat_api_key: str | None = None,
    ) -> None:
        paths.validate()
        self.paths = paths
        self.paths.runtime_root.mkdir(parents=True, exist_ok=True)
        self.paths.question_runtime.mkdir(parents=True, exist_ok=True)
        self.map = DeliveryKnowledgeMapStore(paths)
        self.embedding_base_url = embedding_base_url
        self.embedding_model = embedding_model
        self.embedding_api_key = embedding_api_key or ""
        self.chat_base_url = chat_base_url
        self.chat_model = chat_model
        self.chat_api_key = chat_api_key or ""
        self._module_lock = threading.RLock()
        self._write_lock = threading.RLock()
        self._modules: dict[str, Any] = {}
        self._official_exam_repository: Any = None

    def _module(self, name: str) -> Any:
        with self._module_lock:
            if name in self._modules:
                return self._modules[name]
            root = str(self.paths.component_root)
            if root not in sys.path:
                sys.path.insert(0, root)
            module = importlib.import_module(name)
            self._modules[name] = module
            return module

    @property
    def official_exam_repository(self) -> Any:
        if self._official_exam_repository is None:
            module = self._module("official_exam_pipeline.repository")
            self._official_exam_repository = module.OfficialExamRepository(
                self.paths.public_data / "08_exam_learning_path_2025",
                public_kp_path=self.paths.public_data / "04_knowledge_points" / "final_knowledge_points.json",
            )
        return self._official_exam_repository

    async def build_local_evidence_pack(self, query: str, limit: int = 8) -> EvidencePack:
        return await asyncio.to_thread(self._build_local_evidence_pack, query, limit)

    def _build_local_evidence_pack(self, query: str, limit: int) -> EvidencePack:
        matches = self.map.resolve_topic(query, limit=5)
        if not matches:
            raise LookupError(f"knowledge point could not be resolved for query: {query}")
        evidence: list[EvidenceItem] = []
        resolved: list[str] = []
        for match in matches:
            kp_id = str(match["kp_id"])
            detail = self.map.detail(kp_id, question_limit=0)
            resolved.append(kp_id)
            for chunk in detail["chunks"][:2]:
                summary = str(chunk.get("retrieval_text") or chunk.get("text") or "").strip()
                if not summary:
                    continue
                evidence.append(
                    EvidenceItem(
                        evidence_id=f"E_CHUNK_{chunk['chunk_uid']}",
                        source_id=str(chunk["chunk_uid"]),
                        content_summary=summary[:1800],
                        authority_level="textbook",
                        confidence=max(0.0, min(1.0, float(match["score"]))),
                        bridge_layer="strict",
                        resource_type="textbook",
                    )
                )
            for video in detail["videos"][:2]:
                page = int(video.get("page") or 1)
                start = int(float(video.get("start_seconds") or 0))
                bvid = str(video.get("bvid") or "")
                evidence.append(
                    EvidenceItem(
                        evidence_id=f"E_VIDEO_{bvid}_{page}_{start}",
                        source_id=f"{bvid}:p{page}:{start}",
                        content_summary="\n".join(
                            value for value in (
                                str(video.get("video_title") or ""),
                                str(video.get("part_title") or ""),
                                str(video.get("topic") or ""),
                                str(video.get("transcript") or "")[:900],
                            ) if value
                        ),
                        authority_level="local_video_alignment",
                        confidence=max(0.0, min(1.0, float((video.get("match") or {}).get("confidence") or 0.75))),
                        bridge_layer="video_kp_match",
                        source_url=f"https://www.bilibili.com/video/{bvid}?p={page}&t={start}",
                        resource_type="video",
                    )
                )
        if not evidence:
            raise LookupError(f"no textbook evidence found for query: {query}")
        return EvidencePack(
            evidence_pack_id=f"EP_{uuid4().hex}",
            query=str(matches[0]["name"]),
            resolved_kp_ids=list(dict.fromkeys(resolved)),
            evidence_items=evidence[:limit],
            risk_notes=["仅用于中医药教学训练，不构成诊疗建议。"],
        )

    def _sync_embedder(self) -> Any | None:
        if not self.embedding_api_key:
            return None
        module = self._module("retrieval.hybrid_question_retrieval")
        return module.OpenAICompatibleEmbedder(
            self.embedding_base_url,
            self.embedding_model,
            "SILICONFLOW_API_KEY",
            self.embedding_api_key,
        )

    async def search_questions(
        self,
        query: str,
        kp_ids: list[str] | None = None,
        limit: int = 10,
        *,
        owner_id: str | None = None,
        scope: Literal["all", "public", "user"] = "all",
    ) -> QuestionSearchResult:
        if scope not in {"all", "public", "user"}:
            raise ValueError("scope must be all, public or user")
        owner = _safe_owner(owner_id) if owner_id else None
        if scope == "user" and owner is None:
            raise ValueError("user scope requires owner_id")
        return await asyncio.to_thread(
            self._search_questions,
            query,
            kp_ids or [],
            limit,
            owner,
            scope,
        )

    def _search_questions(
        self,
        query: str,
        kp_ids: list[str],
        limit: int,
        owner_id: str | None,
        scope: str,
    ) -> QuestionSearchResult:
        module = self._module("retrieval.hybrid_question_retrieval")
        embedder = self._sync_embedder()
        raw = module.search(
            query,
            kp_ids,
            self.paths.public_data,
            self.paths.question_runtime,
            limit,
            self.paths.public_vector_store if embedder else None,
            embedder,
            owner_id,
            scope,
        )
        if owner_id:
            runtime_rows = {
                str(row.get("question_id") or ""): row
                for row in _iter_jsonl(self.paths.question_runtime / "question_events.jsonl")
                if row.get("status") == "active"
                and str(row.get("owner_id") or "") == owner_id
            }
            for item in raw.get("items") or []:
                question = item.get("question") or {}
                qid = str(question.get("question_id") or question.get("题目id") or "")
                source = runtime_rows.get(qid)
                if source:
                    question.update(
                        {
                            "options": source.get("options") or question.get("options") or [],
                            "answer": source.get("answer", question.get("answer", question.get("题目答案", ""))),
                            "analysis": source.get("analysis", question.get("analysis", question.get("题目解析", ""))),
                            "difficulty": source.get("difficulty", question.get("difficulty")),
                            "metadata": source.get("metadata") or {},
                        }
                    )
        resolved = [
            str(row.get("raw_kp_id") or row.get("kp_id") or "")
            for row in (raw.get("query") or {}).get("resolved_kps") or []
            if row.get("raw_kp_id") or row.get("kp_id")
        ]
        items = [self._question_detail(item) for item in raw.get("items") or []]
        return QuestionSearchResult(
            query=query,
            resolved_kp_ids=list(dict.fromkeys(resolved)),
            embedding_model=str(raw.get("embedding_model") or "bm25-only"),
            vector_index_path=(
                str(self.paths.public_vector_store / "indexes" / "题库" / "index.faiss")
                if embedder else ""
            ),
            items=items,
        )

    @staticmethod
    def _question_detail(item: dict[str, Any]) -> QuestionDetail:
        question = dict(item.get("question") or {})
        qid = str(question.get("question_id") or question.get("题目id") or "")
        raw_options = question.get("options") or []
        options: list[str] = []
        for option in raw_options:
            if isinstance(option, dict):
                option_id = str(option.get("option_id") or option.get("id") or "").strip()
                content = str(option.get("content") or option.get("text") or "").strip()
                options.append(f"{option_id}. {content}" if option_id else content)
            else:
                options.append(str(option))
        raw_answer = question.get("answer", question.get("题目答案", ""))
        if isinstance(raw_answer, list):
            answer = ", ".join(str(value) for value in raw_answer)
        else:
            answer = str(raw_answer or "")
        bridges: list[QuestionBridge] = []
        for rank, kp_item in enumerate(item.get("knowledge_points") or [], 1):
            kp = dict(kp_item.get("kp") or {})
            bridge = dict(kp_item.get("bridge") or {})
            raw_kp_id = str(kp.get("kp_id") or bridge.get("kp_id") or "")
            layer = str(bridge.get("bridge_layer") or "embedded_kp_ids")
            normalized_layer: Literal["strict", "llm", "similarity"] = (
                "llm" if "llm" in layer else "similarity" if "similar" in layer or "vector" in layer else "strict"
            )
            methods = bridge.get("match_method") or []
            if isinstance(methods, str):
                methods = [methods]
            bridges.append(
                QuestionBridge(
                    kp_id=raw_kp_id,
                    bridge_layer=normalized_layer,
                    relation=str(bridge.get("relation") or "related"),
                    confidence=max(0.0, min(1.0, float(bridge.get("confidence") or 1.0))),
                    rank=max(1, int(bridge.get("rank") or rank)),
                    evidence_chunk_uid=str(bridge.get("evidence_chunk_uid") or ""),
                    match_method=", ".join(map(str, methods)) or layer,
                )
            )
        retrieval = dict(item.get("retrieval") or {})
        channels = ["vector" if value == "runtime_vector" else value for value in retrieval.get("channels") or []]
        channels = [value for value in dict.fromkeys(channels) if value in {"bridge", "bm25", "vector"}]
        score = max(0.0, min(1.0, float(retrieval.get("score") or 0.0)))
        tags = [
            str((kp_item.get("kp") or {}).get("kp_lv3") or (kp_item.get("kp") or {}).get("kp_id") or "")
            for kp_item in item.get("knowledge_points") or []
        ]
        return QuestionDetail(
            question_id=qid,
            question_type=str(question.get("question_type") or question.get("题型") or "未分类"),
            stem=str(question.get("question_content") or question.get("题目内容") or question.get("stem") or ""),
            reference_answer=answer,
            analysis=str(question.get("explanation") or question.get("题目解析") or question.get("analysis") or "") or None,
            options=options,
            tags=[value for value in dict.fromkeys(tags) if value],
            source_metadata={
                "scope": question.get("scope") or "public",
                "owner_id": question.get("owner_id"),
                "difficulty": question.get("difficulty"),
                "raw_answer": raw_answer,
                "raw_options": raw_options,
                "knowledge_points": item.get("knowledge_points") or [],
                "question_exam_matches": item.get("question_exam_matches") or [],
            },
            bridges=bridges,
            retrieval=QuestionRetrievalMetadata(
                channels=channels,
                channel_scores={channel: score for channel in channels},
                fusion_score=score,
            ),
        )

    async def query_exam_knowledge(self, query: str, owner_id: str, limit: int = 10) -> dict[str, Any]:
        owner = _safe_owner(owner_id)
        module = self._module("exam_pipeline.service")
        self._patch_exam_user_kp_layout(module)
        return await asyncio.to_thread(
            module.query_exam_knowledge,
            query,
            owner,
            self.paths.public_data,
            self.paths.knowledge_customer_root,
            self.paths.exam_customer_root,
            limit,
        )

    async def ingest_exam_markdown(
        self,
        markdown: str,
        owner_id: str,
        *,
        replace: bool = True,
    ) -> dict[str, Any]:
        owner = _safe_owner(owner_id)
        upload_dir = self.paths.runtime_root / "uploads" / owner
        upload_dir.mkdir(parents=True, exist_ok=True)
        path = upload_dir / f"exam_{uuid4().hex}.md"
        path.write_text(markdown, encoding="utf-8")
        module = self._module("exam_pipeline.service")
        self._patch_exam_user_kp_layout(module)
        return await asyncio.to_thread(
            module.ingest_user_exam,
            path,
            owner,
            self.paths.public_data,
            self.paths.knowledge_customer_root,
            self.paths.exam_customer_root,
            replace,
        )

    async def ingest_exam_file(
        self,
        filename: str,
        content: bytes,
        owner_id: str,
        *,
        replace: bool = True,
        mineru_token: str = "",
    ) -> dict[str, Any]:
        owner = _safe_owner(owner_id)
        path = self._save_upload(filename, content, owner, {".pdf", ".md", ".txt"})
        if path.suffix.lower() == ".pdf":
            if not mineru_token:
                raise ValueError("PDF 考纲导入需要 MinerU Token")
            markdown = await asyncio.to_thread(self._parse_pdf_to_markdown, path, owner, mineru_token)
        else:
            markdown = path.read_text(encoding="utf-8-sig")
        return await self.ingest_exam_markdown(markdown, owner, replace=replace)

    @staticmethod
    def _patch_exam_user_kp_layout(module: Any) -> None:
        """Accept both delivery layouts present in the handoff package.

        The upload pipeline writes ``<owner>/TCM_backend_delivery/04_*`` while
        the original exam helper only checked ``<owner>/04_*``. Patching the
        helper at the integration boundary keeps the shipped matching logic and
        prevents silently dropping a user's own knowledge points.
        """

        if getattr(module, "_competition_layout_patch", False):
            return

        def load_user_kps(root: Path, owner_id: str):
            owner_root = Path(root) / owner_id
            candidates = (
                owner_root / "TCM_backend_delivery" / "04_knowledge_points" / "final_knowledge_points.json",
                owner_root / "04_knowledge_points" / "final_knowledge_points.json",
            )
            path = next((candidate for candidate in candidates if candidate.is_file()), candidates[0])
            return path, module.read_json(path, []) or []

        module.load_user_kps = load_user_kps
        module._competition_layout_patch = True

    async def ingest_question_markdown(self, markdown: str, owner_id: str) -> dict[str, Any]:
        owner = _safe_owner(owner_id)
        if not self.chat_api_key or not self.embedding_api_key:
            raise RuntimeError("题目导入需要配置聊天模型和 Embedding API Key")
        return await asyncio.to_thread(self._ingest_question_markdown, markdown, owner)

    async def ingest_question_file(
        self,
        filename: str,
        content: bytes,
        owner_id: str,
        *,
        mineru_token: str = "",
    ) -> dict[str, Any]:
        owner = _safe_owner(owner_id)
        path = self._save_upload(filename, content, owner, {".pdf", ".md", ".txt"})
        if path.suffix.lower() == ".pdf":
            if not mineru_token:
                raise ValueError("PDF 题目导入需要 MinerU Token")
            markdown = await asyncio.to_thread(self._parse_pdf_to_markdown, path, owner, mineru_token)
        else:
            markdown = path.read_text(encoding="utf-8-sig")
        return await self.ingest_question_markdown(markdown, owner)

    def _ingest_question_markdown(self, markdown: str, owner: str) -> dict[str, Any]:
        with self._write_lock:
            return self._ingest_question_markdown_locked(markdown, owner)

    def _ingest_question_markdown_locked(self, markdown: str, owner: str) -> dict[str, Any]:
        previous_chat = os.environ.get("COMPETITION_KB_CHAT_KEY")
        previous_embedding = os.environ.get("COMPETITION_KB_EMBEDDING_KEY")
        os.environ["COMPETITION_KB_CHAT_KEY"] = self.chat_api_key
        os.environ["COMPETITION_KB_EMBEDDING_KEY"] = self.embedding_api_key
        try:
            llm_module = self._module("question_pipeline.llm")
            embedding_module = self._module("question_pipeline.embedding")
            markdown_module = self._module("question_pipeline.markdown_ingest")
            audit_module = self._module("question_pipeline.audit")
            expert_module = self._module("question_pipeline.expert")
            judge_module = self._module("question_pipeline.evidence_judge")
            pipeline_module = self._module("question_pipeline.pipeline")
            chat = llm_module.OpenAICompatibleChatClient(
                self.chat_base_url, self.chat_model, "COMPETITION_KB_CHAT_KEY"
            )
            embedder = embedding_module.OpenAICompatibleEmbedder(
                self.embedding_base_url, self.embedding_model, "COMPETITION_KB_EMBEDDING_KEY"
            )
            pipeline = pipeline_module.QuestionPipeline(
                runtime_dir=self.paths.question_runtime,
                kp_file=self.paths.public_data / "04_knowledge_points" / "final_knowledge_points.json",
                base_question_file=self.paths.public_data / "01_question_bank" / "formatted_questions.json",
                embedder=embedder,
                audit_client=audit_module.LLMAuditClient(chat),
                expert_client=expert_module.LLMExpertAnswerClient(chat),
                evidence_judge=judge_module.LLMEvidenceJudgeClient(chat),
                revision_client=expert_module.LLMEvidenceReviser(chat),
                user_vector_root=self.paths.runtime_root / "user_vdb",
            )
            rows = markdown_module.LLMMarkdownExtractor(chat).extract(
                markdown, f"api://question/{uuid4().hex}", "user_upload", owner
            )
            return pipeline.ingest_many(rows)
        finally:
            if previous_chat is None:
                os.environ.pop("COMPETITION_KB_CHAT_KEY", None)
            else:
                os.environ["COMPETITION_KB_CHAT_KEY"] = previous_chat
            if previous_embedding is None:
                os.environ.pop("COMPETITION_KB_EMBEDDING_KEY", None)
            else:
                os.environ["COMPETITION_KB_EMBEDDING_KEY"] = previous_embedding

    async def ingest_knowledge_text(
        self,
        text: str,
        owner_id: str,
        *,
        title: str = "用户资料",
        apply: bool = True,
    ) -> dict[str, Any]:
        owner = _safe_owner(owner_id)
        if not self.chat_api_key or not self.embedding_api_key:
            raise RuntimeError("知识导入需要配置聊天模型和 Embedding API Key")
        return await asyncio.to_thread(self._ingest_knowledge_text, text, owner, title, apply)

    async def ingest_knowledge_file(
        self,
        filename: str,
        content: bytes,
        owner_id: str,
        *,
        title: str = "用户资料",
        apply: bool = True,
        mineru_token: str = "",
    ) -> dict[str, Any]:
        owner = _safe_owner(owner_id)
        path = self._save_upload(
            filename,
            content,
            owner,
            {".pdf", ".md", ".txt", ".png", ".jpg", ".jpeg", ".webp"},
        )
        if path.suffix.lower() in {".pdf", ".png", ".jpg", ".jpeg", ".webp"} and not mineru_token:
            raise ValueError("PDF/图片知识导入需要 MinerU Token")
        if not self.chat_api_key or not self.embedding_api_key:
            raise RuntimeError("知识导入需要配置聊天模型和 Embedding API Key")
        return await asyncio.to_thread(
            self._ingest_knowledge_text,
            "",
            owner,
            title,
            apply,
            path,
            mineru_token,
        )

    def _save_upload(
        self,
        filename: str,
        content: bytes,
        owner: str,
        allowed: set[str],
    ) -> Path:
        safe_name = Path(str(filename or "")).name
        suffix = Path(safe_name).suffix.lower()
        if not safe_name or suffix not in allowed:
            raise ValueError("不支持的文件类型")
        if not content:
            raise ValueError("上传文件为空")
        upload_dir = self.paths.runtime_root / "uploads" / owner
        upload_dir.mkdir(parents=True, exist_ok=True)
        path = upload_dir / f"{uuid4().hex}_{safe_name}"
        path.write_bytes(content)
        return path

    def _parse_pdf_to_markdown(self, path: Path, owner: str, mineru_token: str) -> str:
        output_dir = self.paths.runtime_root / "pdf_runs" / owner / uuid4().hex
        command = [
            sys.executable,
            str(self.paths.component_root / "knowledge_upload_pipeline" / "parse_question_pdf.py"),
            "--config",
            str(self.paths.component_root / "knowledge_upload_pipeline" / "pipeline_config.json"),
            "--output-dir",
            str(output_dir),
            "--pdf",
            str(path),
        ]
        env = os.environ.copy()
        env["MINERU_TOKEN"] = mineru_token
        completed = subprocess.run(
            command,
            cwd=self.paths.component_root / "knowledge_upload_pipeline",
            env=env,
            capture_output=True,
            text=True,
            timeout=24 * 60 * 60,
            check=False,
        )
        if completed.returncode != 0:
            message = (completed.stderr or completed.stdout or "PDF 解析失败").strip()
            raise RuntimeError(message[-4000:])
        markdown_files = sorted(output_dir.rglob("*.md"))
        if not markdown_files:
            raise RuntimeError("MinerU 未生成 Markdown")
        return "\n\n".join(path.read_text(encoding="utf-8-sig") for path in markdown_files)

    def _ingest_knowledge_text(
        self,
        text: str,
        owner: str,
        title: str,
        apply: bool,
        source_path: Path | None = None,
        mineru_token: str = "",
    ) -> dict[str, Any]:
        run_dir = self.paths.runtime_root / "knowledge_runs" / owner / uuid4().hex
        run_dir.mkdir(parents=True, exist_ok=True)
        config = json.loads(
            (self.paths.component_root / "knowledge_upload_pipeline" / "pipeline_config.json").read_text(encoding="utf-8")
        )
        config.setdefault("llm", {}).update(
            {
                "base_url": self.chat_base_url,
                "extract_model": self.chat_model,
                "standardize_model": self.chat_model,
                "chat_model": self.chat_model,
                "api_key": "",
                "api_key_env": "COMPETITION_KB_CHAT_KEY",
            }
        )
        config.setdefault("embedding", {}).update(
            {
                "base_url": self.embedding_base_url,
                "model": self.embedding_model,
                "api_key": "",
                "api_key_env": "COMPETITION_KB_EMBEDDING_KEY",
            }
        )
        config.setdefault("question_retrieval", {}).update(
            {
                "base_url": self.embedding_base_url,
                "model": self.embedding_model,
                "api_key": "",
                "api_key_env": "COMPETITION_KB_EMBEDDING_KEY",
                "index_dir": str(self.paths.public_vector_store / "indexes" / "题库"),
            }
        )
        config.setdefault("paths", {}).update(
            {
                "backend_template_dir": str(self.paths.public_data),
                "question_file": str(self.paths.public_data / "01_question_bank" / "formatted_questions.json"),
            }
        )
        config.setdefault("ingestion", {})["customer_delivery_root"] = str(self.paths.knowledge_customer_root)
        config_path = run_dir / "pipeline_config.json"
        config_path.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
        command = [
            sys.executable,
            str(self.paths.component_root / "knowledge_upload_pipeline" / "ingest_content.py"),
            "--config",
            str(config_path),
            "--reference-delivery",
            str(self.paths.public_data),
            "--customer-id",
            owner,
            "--customer-root",
            str(self.paths.knowledge_customer_root),
            "--run-dir",
            str(run_dir),
            "--title",
            title,
        ]
        if source_path is not None:
            command.extend(["--file", str(source_path)])
        else:
            command.extend(["--text", text])
        if mineru_token:
            command.extend(["--mineru-token", mineru_token])
        if apply:
            command.append("--apply")
        env = os.environ.copy()
        env["COMPETITION_KB_CHAT_KEY"] = self.chat_api_key
        env["COMPETITION_KB_EMBEDDING_KEY"] = self.embedding_api_key
        if mineru_token:
            env["MINERU_TOKEN"] = mineru_token
        completed = subprocess.run(
            command,
            cwd=self.paths.component_root / "knowledge_upload_pipeline",
            env=env,
            capture_output=True,
            text=True,
            timeout=24 * 60 * 60,
            check=False,
        )
        if completed.returncode != 0:
            message = (completed.stderr or completed.stdout or "知识导入失败").strip()
            raise RuntimeError(message[-4000:])
        result_path = run_dir / "result.json"
        if not result_path.is_file():
            raise RuntimeError("知识导入未生成 result.json")
        result = json.loads(result_path.read_text(encoding="utf-8"))
        default_runtime = (self.paths.component_root / "runtime").resolve()
        if apply and self.paths.runtime_root.resolve() != default_runtime:
            delivery = Path(
                result.get("customer_delivery")
                or result.get("delivery")
                or self.paths.knowledge_customer_root / owner / "TCM_backend_delivery"
            )
            vector_module = self._module("retrieval.user_vector_store")
            embedder = self._sync_embedder()
            if embedder is not None:
                result["vector_index"] = vector_module.sync_collection(
                    vector_module.collection_dir(
                        self.paths.runtime_root / "user_vdb", owner, "知识点"
                    ),
                    vector_module.knowledge_point_records(delivery, owner),
                    embedder.name,
                    embedder.embed_many,
                    owner_id=owner,
                    collection="知识点",
                )
                result_path.write_text(
                    json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
                )
        return result

    def list_exam_tracks(self) -> list[dict[str, Any]]:
        return self.official_exam_repository.list_tracks()

    def exam_stage_graph(self, track_id: str) -> dict[str, Any]:
        return self.official_exam_repository.get_track_stage_graph(track_id)

    def exam_track_catalog(self, track_id: str) -> list[dict[str, Any]]:
        return self.official_exam_repository.get_track_catalog(track_id)

    def exam_stage_requirements(self, stage_id: str, offset: int = 0, limit: int = 100) -> list[dict[str, Any]]:
        return self.official_exam_repository.get_stage_requirements(stage_id, offset=offset, limit=limit)

    def exam_requirement_matches(self, node_id: str, include_candidates: bool = True) -> dict[str, Any]:
        return self.official_exam_repository.get_requirement_matches(node_id, include_candidates=include_candidates)

    def exam_catalog_knowledge_points(self, catalog_node_id: str) -> dict[str, Any]:
        return self.official_exam_repository.get_catalog_subtree_knowledge_points(catalog_node_id)

    def kp_exam_matches(self, kp_id: str) -> list[dict[str, Any]]:
        return self.official_exam_repository.get_kp_exam_matches(kp_id)

    def exam_review_queue(
        self,
        *,
        track_id: str | None = None,
        mapping_status: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        if limit < 1 or limit > 1000:
            raise ValueError("limit must be between 1 and 1000")
        rows = self.official_exam_repository.iter_review_queue(
            track_id=track_id,
            mapping_status=mapping_status,
        )
        output: list[dict[str, Any]] = []
        for row in rows or ():
            output.append(row)
            if len(output) >= limit:
                break
        return output

    def exam_validation_summary(self) -> dict[str, Any]:
        return self.official_exam_repository.get_validation_summary()
