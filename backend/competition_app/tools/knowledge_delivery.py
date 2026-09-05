from __future__ import annotations

import asyncio
import ast
import hashlib
import importlib
import json
import math
import os
import re
import shutil
import subprocess
import sys
import threading
import unicodedata
import urllib.request
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
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
from competition_app.contracts.difficulty import parse_difficulty
from competition_app.services.knowledge_recognition_review import (
    KnowledgeRecognitionReportReader,
)
from competition_app.legacy_asset_compat import (
    knowledge_component_root,
    knowledge_video_root,
)
from competition_app.tools.video_segment_index import VideoSegmentIndex
from competition_app.tools.question_channel_reservation import reserve_raw_question_items


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
    def from_release_root(
        cls,
        release_root: Path,
        *,
        runtime_root: Path | None = None,
        public_vector_store: Path | None = None,
    ) -> "KnowledgeDeliveryPaths":
        root = Path(release_root).resolve()
        component = knowledge_component_root(root)
        return cls(
            component_root=component,
            public_data=component / "data" / "backend_delivery",
            video_results=knowledge_video_root(root) / "full_batch_results",
            runtime_root=(runtime_root or component / "runtime").resolve(),
            public_vector_store=(public_vector_store or root.parent / "vdb_store").resolve(),
        )

    @classmethod
    def from_handoff_root(
        cls,
        handoff_root: Path,
        *,
        runtime_root: Path | None = None,
        public_vector_store: Path | None = None,
    ) -> "KnowledgeDeliveryPaths":
        """Compatibility alias for deployments created before release roots."""

        return cls.from_release_root(
            handoff_root,
            runtime_root=runtime_root,
            public_vector_store=public_vector_store,
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


def _normalize_match_text(text: str) -> str:
    """NFKC 全角→半角并移除空白与常见标点，用于知识点名/查询的宽容匹配。

    解决 “热（火）邪” vs “热(火)邪”、“肺、脾、肾” vs “肺脾肾” 等字面差异。
    保留汉字、字母、数字与下划线。
    """
    return re.sub(
        r"[\s，。；;、,（）()《》〈〉「」『』“”‘’：:！!？?—…\-]",
        "",
        unicodedata.normalize("NFKC", str(text)),
    ).lower()


def _subseq_ratio(needle: str, haystack: str) -> float:
    """needle 字符按序出现在 haystack 中的比例（贪心子序列）。

    用于字段与查询仅相差插入词/修饰词的情形，如 kp“肺脾肾在津液代谢中的作用”
    对查询“肺脾肾在津液代谢中的综合调节作用”。返回 0.0~1.0。
    """
    if not needle or not haystack:
        return 0.0
    matched = 0
    for char in haystack:
        if matched < len(needle) and char == needle[matched]:
            matched += 1
    return matched / len(needle)


def clean_book_name(book: str) -> str:
    """Return the display name of a textbook, stripping pipeline suffixes.

    Source chunks carry ``book`` values such as ``中医临床护理学_clean``;
    the display label should read ``中医临床护理学``.
    """
    name = str(book or "").strip()
    for suffix in ("_clean", "_cleaned", "_v2", "_final"):
        if name.endswith(suffix):
            name = name[: -len(suffix)].rstrip("_").strip()
            break
    return name


def format_source_label(book: str, chapter: str = "", heading: str = "") -> str:
    """Build the deterministic citation label for a textbook chunk.

    Prefers the finest granularity available: heading path > section > book
    alone.  The label is display-only; it never embeds a chunk id.
    """
    book_name = clean_book_name(book)
    detail = str(chapter or "").strip() or str(heading or "").strip()
    if detail:
        return f"《{book_name}》· {detail}"
    return f"《{book_name}》"


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
        self._web_questions_ready = False
        self.kps: dict[str, dict[str, Any]] = {}
        self.tree: dict[str, dict[str, list[dict[str, Any]]]] = {}
        self._kp_search_entries_cache: list[tuple[dict[str, Any], str, list[str], set[str], str, list[str]]] | None = None
        self._kp_chunk_name_cache: dict[str, int] = {}
        self._kp_group_identical_cache: dict[tuple[str, ...], bool] = {}
        self.questions_by_kp: dict[str, list[dict[str, Any]]] = defaultdict(list)
        self.chunk_offsets: dict[str, int] = {}
        self.videos_by_kp: dict[str, list[dict[str, Any]]] = defaultdict(list)
        # 网络搜索补充题库：以归一化知识点名为键，持久化于 runtime 目录；
        # 供知识库缺失/题量不足的知识点在物化时兜底，并作为复习测验的题源。
        self.web_questions_by_kp: dict[str, list[dict[str, Any]]] = defaultdict(list)
        self._web_stem_signatures: set[str] = set()
        self._public_stem_signatures: set[str] = set()
        self._targeted_video_index = VideoSegmentIndex(
            self.paths.video_results,
            self.paths.runtime_root / "indexes" / "video_segments.sqlite3",
        )
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

    def _kp_search_entries(self) -> list[tuple[dict[str, Any], str, list[str], set[str], str, list[str]]]:
        self.ensure_hierarchy()
        if self._kp_search_entries_cache is None:
            entries: list[tuple[dict[str, Any], str, list[str], set[str], str, list[str]]] = []
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
                terms = set(re.findall(r"[\u4e00-\u9fff]{2,}|[a-z0-9_]{2,}", text))
                # 归一化字段按顿号/分号/逗号拆分为独立候选（如 other_name 常含
                # 多个别名），提升部分词命中的可能。注意：须先按分隔符拆分再
                # 归一化，否则分隔符已被 _normalize_match_text 删除无法拆分。
                norm_fields: list[str] = []
                for value in normalized_fields:
                    for part in re.split(r"[；;、,]+", str(value)):
                        normed = _normalize_match_text(part)
                        if len(normed) >= 2:
                            norm_fields.append(normed)
                entries.append((kp, text, normalized_fields, terms, _normalize_match_text(text), norm_fields))
            self._kp_search_entries_cache = entries
        return self._kp_search_entries_cache

    def _kp_chunk_name_score(self, kp: dict[str, Any]) -> int:
        """同名重复知识点择优：统计 kp 名称/别名在其绑定切片文本中的出现次数。

        内容错位的重复条目（绑定切片讲的是别的证候，如 004085 绑定“燥邪犯肺证”
        切片）得分低，会被绑定切片自洽的正确条目（004132）淘汰。
        """
        kp_id = str(kp.get("kp_id") or "")
        if not kp_id:
            return 0
        cached = self._kp_chunk_name_cache.get(kp_id)
        if cached is not None:
            return cached
        names = [
            _normalize_match_text(value)
            for value in (kp.get("kp_lv3"), kp.get("other_name"))
            if str(value or "").strip()
        ]
        if not names:
            self._kp_chunk_name_cache[kp_id] = 0
            return 0
        self.ensure_chunk_offsets()
        hits = 0
        for uid in kp.get("raw_content") or []:
            chunk = self._chunk(str(uid))
            if not chunk:
                continue
            text = _normalize_match_text(str(chunk.get("text") or chunk.get("content") or ""))
            if any(name and name in text for name in names):
                hits += 1
        self._kp_chunk_name_cache[kp_id] = hits
        return hits

    def _kp_group_identical(self, members: list[tuple[float, dict[str, Any]]]) -> bool:
        """同名组内各条目绑定切片文本是否完全一致（真重复残留判定）。

        结果按 kp_id 组合缓存；首次调用会触发切片偏移索引扫描（进程内一次）。
        """
        ids = tuple(sorted(str(member[1].get("kp_id") or "") for member in members))
        if not ids:
            return False
        cached = self._kp_group_identical_cache.get(ids)
        if cached is not None:
            return cached
        self.ensure_chunk_offsets()
        texts: list[str] = []
        for _, kp in members:
            parts = []
            for uid in kp.get("raw_content") or []:
                chunk = self._chunk(str(uid))
                if chunk:
                    parts.append(str(chunk.get("text") or ""))
            texts.append("".join(parts))
        nonempty = [text for text in texts if text.strip()]
        result = bool(nonempty) and all(text == nonempty[0] for text in nonempty)
        self._kp_group_identical_cache[ids] = result
        return result

    def resolve_topic(self, query: str, limit: int = 5) -> list[dict[str, Any]]:
        self.ensure_hierarchy()
        compact = re.sub(r"\s+", "", query).lower()
        if not compact:
            return []
        # 全角/半角与标点归一化，缓解 “热（火）邪” vs “热(火)邪” 类字面差异。
        compact_norm = _normalize_match_text(compact)
        primary_terms = {
            _normalize_match_text(segment.strip().split()[0]): index
            for index, segment in enumerate(re.split(r"[；;。\n]+", query))
            if segment.strip() and segment.strip().split()
        }
        query_terms = set(re.findall(r"[\u4e00-\u9fff]{2,}|[a-z0-9_]{2,}", compact))
        ranked: list[tuple[float, dict[str, Any]]] = []
        for kp, text, normalized_fields, terms, norm_text, norm_fields in self._kp_search_entries():
            exact = 1.0 if compact_norm and compact_norm in norm_text else 0.0
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
                    for index, field in enumerate(norm_fields)
                    if len(field) >= 2 and field in compact_norm
                ),
                default=0.0,
            )
            # 子序列匹配：字段与查询仅相差插入词（如“肺脾肾…中的作用” vs
            # “肺脾肾…综合调节作用”）时，子串匹配失效；若字段按序基本完整
            # 出现在查询中则给部分分。
            subseq = 0.0
            for field in norm_fields:
                if len(field) >= 4 and field[:2] in compact_norm:
                    ratio = _subseq_ratio(field, compact_norm)
                    if ratio >= 0.8:
                        subseq = max(subseq, 0.80 + 0.15 * ratio)
            overlap = len(query_terms & terms) / max(1, len(query_terms))
            score = max(exact, named, subseq, overlap * 0.8)
            if score > 0:
                ranked.append((score, kp))
        # 同名知识点处理：数据层已清理内容完全一致的真重复（保留 order 大者，
        # 删除映射见 tests/services/_kp_remap.json）。剩余同名条目多为“同名
        # 异义”——不同教材/版本的不同内容（如各学派“引经报使”），全部保留
        # 避免误删；若数据中仍有内容完全一致的残留重复，则只保留一条。
        best: dict[tuple[str, str, str], list[tuple[float, dict[str, Any]]]] = defaultdict(list)
        for score, kp in ranked:
            key = (
                str(kp.get("kp_lv1") or ""),
                str(kp.get("kp_lv2") or ""),
                str(kp.get("kp_lv3") or kp.get("kp_lv2") or kp["kp_id"]),
            )
            best[key].append((score, kp))
        collapsed: list[tuple[float, dict[str, Any]]] = []
        for key, members in best.items():
            if len(members) == 1 or not self._kp_group_identical(members):
                collapsed.extend(members)
                continue
            # 内容完全一致：只保留评分最高的一条（同分取切片名命中度高者）
            collapsed.append(max(members, key=lambda item: (item[0], self._kp_chunk_name_score(item[1]))))
        ranked = sorted(
            collapsed,
            key=lambda item: (
                -item[0],
                0 if str(item[1].get("kp_lv1") or "") == "方剂学" else 1,
                str(item[1].get("order") or ""),
                str(item[1].get("kp_id")),
            ),
        )
        output: list[dict[str, Any]] = []
        # 同名限流：跨教材同名条目（如各教材的“同病异治”共 10 个）会挤占名额，
        # 同名（kp_lv3）最多保留前 3 个高分条目，给不同名的正确知识点留位置。
        name_slots: dict[str, int] = defaultdict(int)
        for score, kp in ranked:
            name = str(kp.get("kp_lv3") or kp.get("kp_lv2") or kp["kp_id"])
            if name_slots[name] >= 3:
                continue
            name_slots[name] += 1
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
            self._public_stem_signatures = {
                signature
                for row in records
                for signature in (self._normalized_stem(row.get("question_content") or row.get("题目内容") or row.get("stem") or ""),)
                if signature
            }
            self._questions_ready = True

    @property
    def web_question_runtime(self) -> Path:
        """Runtime path for web-ingested questions (shared, not owner-scoped)."""
        return self.paths.question_runtime / "web_ingested" / "questions.jsonl"

    @staticmethod
    def _normalized_stem(stem: Any) -> str:
        """Normalize a question stem for duplicate detection."""
        return unicodedata.normalize(
            "NFKC", str(stem or "").casefold().replace("\n", "").replace(" ", "")
        ).strip()

    def ensure_web_questions(self) -> None:
        if self._web_questions_ready:
            return
        with self._lock:
            if self._web_questions_ready:
                return
            index: dict[str, list[dict[str, Any]]] = defaultdict(list)
            signatures: set[str] = set()
            for row in _iter_jsonl(self.web_question_runtime):
                if not isinstance(row, dict):
                    continue
                kp_name = str(row.get("kp_name") or "").strip()
                question = row.get("question")
                if not kp_name or not isinstance(question, dict):
                    continue
                key = self._normalized_learning_label(kp_name)
                if not key:
                    continue
                index[key].append(question)
                stem = question.get("stem") or question.get("题干") or ""
                signature = self._normalized_stem(stem)
                if signature:
                    signatures.add(signature)
            self.web_questions_by_kp = index
            self._web_stem_signatures = signatures
            self._web_questions_ready = True

    def register_web_questions(
        self,
        knowledge_point_name: str,
        questions: list[dict[str, Any]],
    ) -> int:
        """Persist cleaned web questions for one knowledge point and refresh the
        in-memory index. Duplicates against the public bank and previously
        ingested web questions are skipped; the number of new questions is
        returned.
        """
        kp_name = str(knowledge_point_name or "").strip()
        if not kp_name or not questions:
            return 0
        self.ensure_questions()
        self.ensure_web_questions()
        with self._lock:
            key = self._normalized_learning_label(kp_name)
            if not key:
                return 0
            deduplicated: list[dict[str, Any]] = []
            for question in questions:
                if not isinstance(question, dict):
                    continue
                stem = question.get("stem") or question.get("题干") or ""
                if not str(stem).strip():
                    continue
                signature = self._normalized_stem(stem)
                if not signature or signature in self._public_stem_signatures:
                    continue
                if signature in self._web_stem_signatures:
                    continue
                self._web_stem_signatures.add(signature)
                deduplicated.append(dict(question))
            if not deduplicated:
                return 0
            self.web_question_runtime.parent.mkdir(parents=True, exist_ok=True)
            with self.web_question_runtime.open("a", encoding="utf-8") as handle:
                for question in deduplicated:
                    handle.write(
                        json.dumps(
                            {
                                "kp_name": kp_name,
                                "question": question,
                                "ingested_at": datetime.now(timezone.utc).isoformat(),
                                "origin": "web_search",
                            },
                            ensure_ascii=False,
                        )
                        + "\n"
                    )
            self.web_questions_by_kp[key].extend(
                dict(question) for question in deduplicated
            )
            return len(deduplicated)

    def resolve_web_question_bundle(
        self,
        knowledge_point_name: str,
        *,
        required_question_count: int = 3,
    ) -> dict[str, Any] | None:
        """Bind a knowledge point that is missing from the public atlas to the
        cleaned web-ingested question bank. The returned bundle uses the same
        trusted-atlas shape as :meth:`resolve_executable_bundle` so callers can
        register it through the executable task store (stable ``WEBQ_`` question
        ids keep the registration idempotent).
        """
        name = str(knowledge_point_name or "").strip()
        if not name or required_question_count <= 0:
            return None
        self.ensure_web_questions()
        key = self._normalized_learning_label(name)
        if not key:
            return None
        questions = self.web_questions_by_kp.get(key, [])
        if len(questions) < required_question_count:
            return None
        kp_id = f"WEB_{hashlib.sha1(key.encode('utf-8')).hexdigest()[:12]}"
        normalized_questions: list[dict[str, Any]] = []
        for row in questions[:required_question_count]:
            stem = str(row.get("stem") or row.get("题干") or "").strip()
            if not stem:
                continue
            question_id = (
                f"WEBQ_{hashlib.sha1(f'{key}|{stem}'.encode('utf-8')).hexdigest()[:16]}"
            )
            normalized_questions.append(
                {
                    "question_id": question_id,
                    "question_content": stem,
                    "question_type": str(
                        row.get("question_type") or row.get("题型") or "未分类"
                    ),
                    "options": row.get("options") or [],
                    "answer": row.get("answer") or row.get("答案") or "",
                    "explanation": row.get("analysis") or row.get("解析") or "",
                    "kp_ids": [kp_id],
                    "source_ref": str(row.get("source_ref") or f"web://{name}"),
                    "source_urls": list(row.get("source_urls") or []),
                    "origin": "web_search",
                }
            )
        if len(normalized_questions) < required_question_count:
            return None
        return {
            "source": "knowledge_atlas",
            "kp": {
                "kp_id": kp_id,
                "kp_lv3": name,
                "kp_lv2": "",
                "kp_lv1": "",
            },
            "kp_id": kp_id,
            "knowledge_point_name": name,
            "questions": normalized_questions,
            "question_count": len(questions),
            "video_count": 0,
        }

    @staticmethod
    def _normalized_learning_label(value: Any, *, simplified: bool = False) -> str:
        text = unicodedata.normalize("NFKC", str(value or "")).casefold()
        text = re.sub(r"[\s，。；、,:：;（）()《》“”\"'·—_-]+", "", text)
        if simplified:
            text = text.replace("相互", "").replace("的", "")
        return text

    def resolve_executable_bundle(
        self,
        knowledge_point_name: str,
        *,
        required_question_count: int = 3,
        preferred_scope: str = "",
    ) -> dict[str, Any] | None:
        """Bind a natural model label to trusted atlas data for execution."""

        query = self._normalized_learning_label(knowledge_point_name)
        simplified_query = self._normalized_learning_label(
            knowledge_point_name, simplified=True
        )
        if not query or required_question_count <= 0:
            return None
        self.ensure_hierarchy()
        self.ensure_questions()
        self.ensure_videos()
        normalized_scope = self._normalized_learning_label(preferred_scope)
        preferred_books = {
            book
            for book in self.tree
            if self._normalized_learning_label(book)
            and self._normalized_learning_label(book) in normalized_scope
        }

        ranked: list[tuple[int, int, int, str, dict[str, Any]]] = []
        for kp_id, kp in self.kps.items():
            if preferred_books and str(kp.get("kp_lv1") or "") not in preferred_books:
                continue
            questions = self.questions_by_kp.get(kp_id, [])
            if len(questions) < required_question_count:
                continue
            aliases = [
                str(kp.get("kp_lv3") or ""),
                *re.split(r"[；;、]", str(kp.get("other_name") or "")),
            ]
            labels = [
                (
                    self._normalized_learning_label(label),
                    self._normalized_learning_label(label, simplified=True),
                )
                for label in aliases
                if str(label).strip()
            ]
            score = 0
            for label, simplified_label in labels:
                if query == label:
                    score = max(score, 1000)
                elif simplified_query and simplified_query == simplified_label:
                    score = max(score, 900)
                elif min(len(query), len(label)) >= 4 and (
                    query in label or label in query
                ):
                    score = max(score, 700 + min(len(query), len(label)))
                elif (
                    min(len(simplified_query), len(simplified_label)) >= 4
                    and (
                        simplified_query in simplified_label
                        or simplified_label in simplified_query
                    )
                ):
                    score = max(
                        score,
                        600 + min(len(simplified_query), len(simplified_label)),
                    )
            if score <= 0:
                continue
            kp_scope = self._normalized_learning_label(
                f"{kp.get('kp_lv1') or ''}{kp.get('kp_lv2') or ''}"
            )
            if normalized_scope and self._normalized_learning_label(
                kp.get("kp_lv1") or ""
            ) in normalized_scope:
                score += 2000
            elif normalized_scope and min(len(normalized_scope), len(kp_scope)) >= 4 and (
                normalized_scope in kp_scope or kp_scope in normalized_scope
            ):
                score += 1000
            ranked.append(
                (
                    score,
                    min(len(questions), 99),
                    min(len(self.videos_by_kp.get(kp_id, [])), 99),
                    str(kp.get("order") or ""),
                    kp,
                )
            )
        if not ranked:
            return None
        ranked.sort(key=lambda item: (-item[0], -item[2], -item[1], item[3]))
        kp = dict(ranked[0][4])
        kp_id = str(kp["kp_id"])
        from competition_app.services.retrieval_fusion import reciprocal_rank_fusion

        questions_by_id = {
            str(row.get("question_id") or row.get("题目id")): dict(row)
            for row in self.questions_by_kp.get(kp_id, [])
            if row.get("question_id") or row.get("题目id")
        }
        bridge_hits = [(question_id, 1.0) for question_id in questions_by_id]
        fusion = reciprocal_rank_fusion({"bridge": bridge_hits})
        ranked_questions = [
            questions_by_id[question_id]
            for question_id in sorted(
                questions_by_id,
                key=lambda question_id: (-fusion[question_id].score, question_id),
            )
        ]
        if len(ranked_questions) < required_question_count:
            return None
        return {
            "source": "knowledge_atlas",
            "kp": kp,
            "kp_id": kp_id,
            "knowledge_point_name": str(kp.get("kp_lv3") or kp_id),
            "questions": ranked_questions[:required_question_count],
            "question_count": len(self.questions_by_kp[kp_id]),
            "video_count": len(self.videos_by_kp.get(kp_id, [])),
        }

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

    def resolve_trusted_video_resource(
        self,
        supplied_resource_ref: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Return only a canonical segment present in the published video index."""

        if not isinstance(supplied_resource_ref, dict):
            return None
        requested_kp_id = str(supplied_resource_ref.get("kp_id") or "").strip()
        requested_chapter = str(
            supplied_resource_ref.get("learning_chapter")
            or supplied_resource_ref.get("chapter")
            or ""
        ).strip()
        if not requested_kp_id and requested_chapter:
            self.ensure_hierarchy()
            self.ensure_videos()
            normalized_scope = self._normalized_learning_label(requested_chapter)
            ranked: list[tuple[int, str, str, dict[str, Any]]] = []
            for kp_id, rows in self.videos_by_kp.items():
                kp = self.kps.get(kp_id) or {}
                book = self._normalized_learning_label(kp.get("kp_lv1") or "")
                chapter = self._normalized_learning_label(kp.get("kp_lv2") or "")
                topic = self._normalized_learning_label(kp.get("kp_lv3") or "")
                score = 0
                if book and book in normalized_scope:
                    score += 4
                if chapter and (
                    chapter in normalized_scope or normalized_scope in chapter
                ):
                    score += 8
                if topic and topic in normalized_scope:
                    score += 2
                if score >= 8 and rows:
                    ranked.append(
                        (
                            score,
                            str(kp.get("order") or ""),
                            kp_id,
                            sorted(
                                rows,
                                key=lambda row: (
                                    str(row.get("bvid") or ""),
                                    int(row.get("page") or 0),
                                    float(row.get("start_seconds") or 0),
                                ),
                            )[0],
                        )
                    )
            if not ranked:
                # 小节标题唯一匹配增强：查询命中且仅命中一个已发布视频
                # 知识点的小节标题（kp_lv3）时，允许解析该视频，避免本应
                # 出现的视频资源因缺少章节上下文而丢失。
                topic_matches: list[tuple[str, dict[str, Any]]] = []
                for kp_id, rows in self.videos_by_kp.items():
                    kp = self.kps.get(kp_id) or {}
                    topic = self._normalized_learning_label(kp.get("kp_lv3") or "")
                    if not topic or not rows:
                        continue
                    if topic in normalized_scope or normalized_scope in topic:
                        topic_matches.append(
                            (
                                kp_id,
                                sorted(
                                    rows,
                                    key=lambda row: (
                                        str(row.get("bvid") or ""),
                                        int(row.get("page") or 0),
                                        float(row.get("start_seconds") or 0),
                                    ),
                                )[0],
                            )
                        )
                if len(topic_matches) == 1:
                    matched_topic_kp_id, row = topic_matches[0]
                    matched_topic_kp = self.kps.get(matched_topic_kp_id) or {}
                    ranked.append(
                        (
                            8,
                            str(matched_topic_kp.get("order") or ""),
                            matched_topic_kp_id,
                            row,
                        )
                    )
            if ranked:
                ranked.sort(key=lambda item: (-item[0], item[1], item[2]))
                row = ranked[0][3]
                supplied_resource_ref = {
                    "provider": "bilibili",
                    "bvid": row.get("bvid"),
                    "page": row.get("page"),
                    "start_seconds": row.get("start_seconds"),
                    "end_seconds": row.get("end_seconds"),
                }
        if requested_kp_id:
            self.ensure_videos()
            rows = sorted(
                self.videos_by_kp.get(requested_kp_id, []),
                key=lambda row: (
                    str(row.get("bvid") or ""),
                    int(row.get("page") or 0),
                    float(row.get("start_seconds") or 0),
                    float(row.get("end_seconds") or 0),
                ),
            )
            if not rows:
                return None
            supplied_resource_ref = {
                "provider": "bilibili",
                "bvid": rows[0].get("bvid"),
                "page": rows[0].get("page"),
                "start_seconds": rows[0].get("start_seconds"),
                "end_seconds": rows[0].get("end_seconds"),
            }
        provider = str(supplied_resource_ref.get("provider") or "bilibili").casefold()
        if provider not in {"bilibili", "b23"}:
            return None
        bvid = str(supplied_resource_ref.get("bvid") or "").strip()
        try:
            page = int(supplied_resource_ref.get("page"))
            start = float(supplied_resource_ref.get("start_seconds"))
            end = float(supplied_resource_ref.get("end_seconds"))
        except (TypeError, ValueError):
            return None
        if not bvid or page <= 0 or not all(map(math.isfinite, (start, end))) or end <= start:
            return None

        self.ensure_hierarchy()
        self.ensure_videos()
        matched_kp_id = ""
        matches = []
        for kp_id, rows in self.videos_by_kp.items():
            for row in rows:
                if (
                    str(row.get("bvid") or "") == bvid
                    and int(row.get("page") or 0) == page
                    and math.isclose(float(row.get("start_seconds") or 0), start, abs_tol=1e-6)
                    and math.isclose(float(row.get("end_seconds") or 0), end, abs_tol=1e-6)
                ):
                    matches.append(row)
                    if not matched_kp_id:
                        matched_kp_id = kp_id
        canonical = {
            (
                str(row.get("bvid") or ""),
                int(row.get("page") or 0),
                float(row.get("start_seconds") or 0),
                float(row.get("end_seconds") or 0),
            ): row
            for row in matches
        }
        if len(canonical) != 1:
            return None
        row = next(iter(canonical.values()))
        kp_context = self.kps.get(matched_kp_id) or {}
        start_seconds = float(row["start_seconds"])
        end_seconds = float(row["end_seconds"])
        return {
            "source": "knowledge_atlas",
            "provider": "bilibili",
            "bvid": str(row["bvid"]),
            "aid": row.get("aid"),
            "cid": row.get("cid"),
            "page": int(row["page"]),
            "start_seconds": start_seconds,
            "end_seconds": end_seconds,
            "duration_seconds": round(end_seconds - start_seconds),
            "video_title": str(row.get("video_title") or ""),
            "part_title": str(row.get("part_title") or ""),
            "topic": str(row.get("topic") or "知识讲解"),
            "kp_id": matched_kp_id or None,
            "book": str(kp_context.get("kp_lv1") or "").strip() or None,
            "chapter": str(kp_context.get("kp_lv2") or "").strip() or None,
            "section": str(kp_context.get("kp_lv3") or "").strip() or None,
        }

    def targeted_videos(self, kp_id: str) -> list[dict[str, Any]]:
        """Return published video rows without materializing the full catalog."""

        return self._targeted_video_index.videos_for_kp(kp_id)

    def detail(
        self,
        kp_id: str,
        question_limit: int = 30,
        *,
        include_questions: bool = True,
        include_videos: bool = True,
    ) -> dict[str, Any]:
        self.ensure_hierarchy()
        kp = self.kps.get(str(kp_id))
        if kp is None:
            raise KeyError("知识点不存在")
        self.ensure_chunk_offsets()
        if include_questions:
            self.ensure_questions()
        if include_videos:
            self.ensure_videos()
        refs = [str(value) for value in kp.get("raw_content") or [] if value]
        chunks = [row for uid in refs if (row := self._chunk(uid)) is not None]
        questions = self.questions_by_kp.get(str(kp_id), []) if include_questions else []
        return {
            "kp": dict(kp),
            "chunks": chunks,
            "raw_refs": refs,
            "questions": [dict(row) for row in questions[:question_limit]],
            "question_count": len(questions),
            "videos": (
                [dict(row) for row in self.videos_by_kp.get(str(kp_id), [])]
                if include_videos
                else []
            ),
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


_BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)
_urllib_ua_installed = False
_urllib_ua_lock = threading.Lock()


def _install_browser_user_agent() -> None:
    """为 urllib 全局安装带浏览器 UA 的 opener（幂等）。

    部分 OpenAI 兼容网关（如 opencode.ai）通过 Cloudflare 校验 User-Agent，
    默认的 Python-urllib UA 会被 403 拦截。组件包（question_pipeline）内部
    使用 urllib.request.urlopen，这里统一安装带 UA 的全局 opener。
    """

    global _urllib_ua_installed
    if _urllib_ua_installed:
        return
    with _urllib_ua_lock:
        if _urllib_ua_installed:
            return
        opener = urllib.request.build_opener()
        opener.addheaders = [("User-Agent", _BROWSER_USER_AGENT)]
        urllib.request.install_opener(opener)
        _urllib_ua_installed = True


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
        mineru_token: str | None = None,
    ) -> None:
        paths.validate()
        _install_browser_user_agent()
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
        self.mineru_token = mineru_token or ""
        self._module_lock = threading.RLock()
        self._write_lock = threading.RLock()
        self._modules: dict[str, Any] = {}
        self._official_exam_repository: Any = None
        self.recognition_reports = KnowledgeRecognitionReportReader(self.paths.runtime_root)

    def list_recognition_reports(
        self, owner_id: str, *, offset: int = 0, limit: int = 20
    ) -> dict[str, Any]:
        return self.recognition_reports.list_reports(
            _safe_owner(owner_id), offset=offset, limit=limit
        )

    def get_recognition_report(
        self, owner_id: str, report_id: str
    ) -> dict[str, Any]:
        return self.recognition_reports.get_report(_safe_owner(owner_id), report_id)

    def _mineru_token(self, override: str = "") -> str:
        return str(override or self.mineru_token).strip()

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
        # 解析候选放宽到 10 个：描述性查询下正确知识点常被同名/通用知识点挤出
        # 前 5 位（如“比较法”0.93 排在 8 个“同病异治”0.94 之后），证据条数仍由
        # 外层 limit 截断，多解析的 kp 不会带来额外证据噪声。
        matches = self.map.resolve_topic(query, limit=10)
        if not matches:
            raise LookupError(f"knowledge point could not be resolved for query: {query}")
        evidence: list[EvidenceItem] = []
        resolved: list[str] = []
        resolved_names: dict[str, str] = {}
        for match in matches:
            kp_id = str(match["kp_id"])
            detail = self.map.detail(
                kp_id,
                question_limit=0,
                include_questions=False,
                include_videos=False,
            )
            targeted_videos = self.map.targeted_videos(kp_id)
            resolved.append(kp_id)
            resolved_names[kp_id] = str(match.get("name") or kp_id)
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
                        source_label=format_source_label(
                            str(chunk.get("book") or ""),
                            chapter=str(chunk.get("kp_lv2") or chunk.get("kp_lv1") or ""),
                            heading=str(chunk.get("heading") or ""),
                        ),
                    )
                )
            for video in targeted_videos[:2]:
                page = int(video.get("page") or 1)
                start = int(float(video.get("start_seconds") or 0))
                bvid = str(video.get("bvid") or "")
                video_title = str(video.get("video_title") or "").strip()
                part_title = str(video.get("part_title") or "").strip()
                evidence.append(
                    EvidenceItem(
                        evidence_id=f"E_VIDEO_{bvid}_{page}_{start}",
                        source_id=f"{bvid}:p{page}:{start}",
                        content_summary="\n".join(
                            value for value in (
                                video_title,
                                part_title,
                                str(video.get("topic") or ""),
                                str(video.get("transcript") or "")[:900],
                            ) if value
                        ),
                        authority_level="local_video_alignment",
                        confidence=max(0.0, min(1.0, float((video.get("match") or {}).get("confidence") or 0.75))),
                        bridge_layer="video_kp_match",
                        source_url=f"https://www.bilibili.com/video/{bvid}?p={page}&t={start}",
                        resource_type="video",
                        source_label=(
                            f"视频《{video_title}》· {part_title}"
                            if video_title and part_title and part_title != video_title
                            else f"视频《{video_title}》"
                            if video_title
                            else "视频来源"
                        ),
                    )
                )
        if not evidence:
            raise LookupError(f"no textbook evidence found for query: {query}")
        return EvidencePack(
            evidence_pack_id=f"EP_{uuid4().hex}",
            query=str(matches[0]["name"]),
            resolved_kp_ids=list(dict.fromkeys(resolved)),
            resolved_kp_names=resolved_names,
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
            "EMBEDDING_API_KEY",
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
        difficulty: int | None = None,
        difficulty_min: int | None = None,
        difficulty_max: int | None = None,
    ) -> QuestionSearchResult:
        if scope not in {"all", "public", "user"}:
            raise ValueError("scope must be all, public or user")
        if difficulty is not None and (difficulty_min is not None or difficulty_max is not None):
            raise ValueError("difficulty cannot be combined with difficulty_min/difficulty_max")
        if (
            difficulty_min is not None
            and difficulty_max is not None
            and difficulty_min > difficulty_max
        ):
            raise ValueError("difficulty_min must not exceed difficulty_max")
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
            difficulty,
            difficulty_min,
            difficulty_max,
        )

    def _search_questions(
        self,
        query: str,
        kp_ids: list[str],
        limit: int,
        owner_id: str | None,
        scope: str,
        difficulty: int | None = None,
        difficulty_min: int | None = None,
        difficulty_max: int | None = None,
    ) -> QuestionSearchResult:
        module = self._module("retrieval.hybrid_question_retrieval")
        embedder = self._sync_embedder()
        candidate_limit = max(limit * 4, 20)
        try:
            raw = module.search(
                query,
                kp_ids,
                self.paths.public_data,
                self.paths.question_runtime,
                candidate_limit,
                self.paths.public_vector_store if embedder else None,
                embedder,
                owner_id,
                scope,
            )
        except (FileNotFoundError, OSError, RuntimeError) as exc:
            # 向量索引缺失/不可读或 embedder 失败时降级为 BM25/Bridge 检索，
            # 让组卷链路继续；结果仍只来自正式题库候选。
            if embedder is None:
                raise
            raw = module.search(
                query,
                kp_ids,
                self.paths.public_data,
                self.paths.question_runtime,
                candidate_limit,
                None,
                None,
                owner_id,
                scope,
            )
            raw["embedding_model"] = None
            raw["vector_degraded"] = True
            raw["vector_error"] = str(exc)[:300]
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
                            "metadata": source.get("metadata") or {},
                        }
                    )
                    if "difficulty" in source:
                        question["difficulty"] = source.get("difficulty")
                    if "difficulty_source" in source:
                        question["difficulty_source"] = source.get("difficulty_source")
        raw_items = raw.get("items") or []
        if difficulty is not None or difficulty_min is not None or difficulty_max is not None:
            raw_items = [
                item
                for item in raw_items
                if self._question_difficulty_matches(
                    parse_difficulty(((item.get("question") or {}).get("difficulty"))),
                    level=difficulty,
                    minimum=difficulty_min,
                    maximum=difficulty_max,
                )
            ]
        raw_items = reserve_raw_question_items(raw_items, limit=limit)
        resolved = [
            str(row.get("raw_kp_id") or row.get("kp_id") or "")
            for row in (raw.get("query") or {}).get("resolved_kps") or []
            if row.get("raw_kp_id") or row.get("kp_id")
        ]
        items = [self._question_detail(item) for item in raw_items]
        return QuestionSearchResult(
            query=query,
            resolved_kp_ids=list(dict.fromkeys(resolved)),
            embedding_model=str(raw.get("embedding_model") or "bm25-only"),
            vector_index_path=(
                str(self.paths.public_vector_store / "indexes" / "题库" / "index.faiss")
                if embedder else ""
            ),
            items=items,
            fusion_strategy=(
                "rrf_v1" if raw.get("fusion_strategy") == "rrf_v1" else "legacy_max"
            ),
            vector_degraded=bool(raw.get("vector_degraded")),
        )

    @staticmethod
    def _question_difficulty_matches(
        difficulty: int | None,
        *,
        level: int | None,
        minimum: int | None,
        maximum: int | None,
    ) -> bool:
        """难度匹配：仅真实标注参与严格匹配；无标注题永不冒充指定难度。"""
        if level is None and minimum is None and maximum is None:
            return True
        if difficulty is None:
            return False
        if level is not None:
            return difficulty == level
        low = minimum if minimum is not None else 1
        high = maximum if maximum is not None else 5
        return low <= difficulty <= high

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
        raw_channel_scores = dict(retrieval.get("channel_scores") or {})
        channel_scores: dict[str, float] = {}
        for channel, value in raw_channel_scores.items():
            normalized_channel = "vector" if channel == "runtime_vector" else str(channel)
            if normalized_channel in {"bridge", "bm25", "vector"}:
                channel_scores[normalized_channel] = max(
                    channel_scores.get(normalized_channel, float("-inf")),
                    float(value),
                )
        if not channel_scores:
            channel_scores = {channel: score for channel in channels}
        raw_channel_ranks = dict(retrieval.get("channel_ranks") or {})
        channel_ranks: dict[str, int] = {}
        for channel, value in raw_channel_ranks.items():
            normalized_channel = "vector" if channel == "runtime_vector" else str(channel)
            if normalized_channel in {"bridge", "bm25", "vector"}:
                channel_ranks[normalized_channel] = min(
                    channel_ranks.get(normalized_channel, 1_000_000),
                    max(1, int(value)),
                )
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
            difficulty=parse_difficulty(question.get("difficulty")),
            difficulty_source=str(question.get("difficulty_source") or "") or None,
            options=options,
            tags=[value for value in dict.fromkeys(tags) if value],
            source_metadata={
                "scope": question.get("scope") or "public",
                "owner_id": question.get("owner_id"),
                "raw_answer": raw_answer,
                "raw_options": raw_options,
                "knowledge_points": item.get("knowledge_points") or [],
                "question_exam_matches": item.get("question_exam_matches") or [],
            },
            bridges=bridges,
            retrieval=QuestionRetrievalMetadata(
                channels=channels,
                channel_scores=channel_scores,
                channel_ranks=channel_ranks,
                fusion_score=score,
                fusion_strategy=(
                    "rrf_v1"
                    if retrieval.get("fusion_strategy") == "rrf_v1"
                    else "legacy_max"
                ),
                legacy_max_score=(
                    float(retrieval["legacy_max_score"])
                    if retrieval.get("legacy_max_score") is not None
                    else None
                ),
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
        mineru_token = self._mineru_token(mineru_token)
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
        mineru_token = self._mineru_token(mineru_token)
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
        mineru_token = self._mineru_token(mineru_token)
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
        delivery = Path(
            result.get("customer_delivery")
            or result.get("delivery")
            or self.paths.knowledge_customer_root / owner / "TCM_backend_delivery"
        )
        if apply:
            result["chapter_hierarchy"] = self._sync_user_chapter_hierarchy(
                run_dir=run_dir,
                delivery=delivery,
                ingestion_id=str(result.get("ingestion_id") or run_dir.name),
            )
            result["recognition_review"] = self.recognition_reports.create_snapshot(
                owner, run_dir, delivery
            )

        default_runtime = (self.paths.component_root / "runtime").resolve()
        if apply and self.paths.runtime_root.resolve() != default_runtime:
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

    def _sync_user_chapter_hierarchy(
        self,
        *,
        run_dir: Path,
        delivery: Path,
        ingestion_id: str,
    ) -> dict[str, Any]:
        """Generate the same chapter mapping used by the public textbooks."""

        chunks_path = delivery / "03_pipeline_chunks" / "source_chunks.jsonl"
        normalized_books = run_dir / "normalized_books"
        if not chunks_path.is_file():
            raise RuntimeError("用户教材导入后缺少 source_chunks.jsonl")
        if not normalized_books.is_dir():
            raise RuntimeError("用户教材导入后缺少标准化 Markdown")

        source_root = delivery / "09_ingestion" / "source_markdown"
        source_root.mkdir(parents=True, exist_ok=True)
        shutil.copytree(normalized_books, source_root, dirs_exist_ok=True)

        chapter_script = (
            Path(__file__).resolve().parents[2]
            / "competition"
            / "knowledge_atlas_chapters"
            / "2026-07-22"
            / "chapter_hierarchy.py"
        )
        if not chapter_script.is_file():
            raise RuntimeError(f"章节映射脚本不存在：{chapter_script}")

        output_dir = delivery / "03_pipeline_chunks"
        completed = subprocess.run(
            [
                sys.executable,
                str(chapter_script),
                "--chunks",
                str(chunks_path),
                "--markdown-root",
                str(source_root),
                "--output-dir",
                str(output_dir),
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30 * 60,
            check=False,
        )
        if completed.returncode != 0:
            message = (completed.stderr or completed.stdout or "章节映射生成失败").strip()
            raise RuntimeError(message[-4000:])

        report_path = output_dir / "chapter_hierarchy_report.json"
        if not report_path.is_file():
            raise RuntimeError("章节映射未生成 chapter_hierarchy_report.json")
        report = json.loads(report_path.read_text(encoding="utf-8"))
        report.update({
            "ingestion_id": ingestion_id,
            "chapter_nodes_path": str(output_dir / "chapter_nodes.jsonl"),
            "chunk_chapter_links_path": str(output_dir / "chunk_chapter_links.jsonl"),
            "source_markdown_root": str(source_root),
        })
        return report

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
