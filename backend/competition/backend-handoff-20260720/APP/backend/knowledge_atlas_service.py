"""Read-only service layer for the teammate Knowledge Atlas delivery.

The large delivery remains outside Python packages and is loaded lazily.  This
module deliberately has no Flask, FAISS, embedding-model, or frontend imports,
so an absent Atlas can only degrade Atlas endpoints instead of application
startup.
"""

from __future__ import annotations

import json
import os
import re
import threading
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable

from APP.backend.knowledge_atlas_video_pipeline import active_video_release_root


PRACTITIONER_BOOKS = (
    "中医临床护理学", "中医食疗学", "中西医结合儿科学", "中西医结合内科学",
    "中西医结合外科学", "中西医结合妇产科学", "中西医结合骨伤科学", "临床中药学",
    "儿科护理学", "内科学", "各家针灸学说", "外科学", "妇产科护理学",
    "实验针灸学", "小儿推拿学", "病理学", "药理学", "诊断学", "金匮要略",
    "针灸学", "针灸治疗学",
)

ROUTES: tuple[dict[str, Any], ...] = (
    {
        "id": "textbook_14_5",
        "name": "十四五规划教材总览",
        "description": "展示十四五规划教材",
        "books": "*",
    },
    {
        "id": "tcm_assistant",
        "name": "中医执业助理医师资格考试",
        "description": "展示执业医师所用教材",
        "books": PRACTITIONER_BOOKS,
    },
    {
        "id": "postgraduate",
        "name": "考研学习路线",
        "description": "展示考研学习教科书",
        "books": "*",
    },
)

_QUESTION_FILE = Path("01_question_bank") / "formatted_questions.json"
_CHUNK_FILE = Path("03_pipeline_chunks") / "source_chunks.jsonl"
_KP_FILE = Path("04_knowledge_points") / "final_knowledge_points.json"
_IMAGE_DIR = Path("04_knowledge_points") / "images"


class AtlasUnavailableError(RuntimeError):
    """Raised only inside Atlas operations when local delivery data is absent."""


def _read_json_array(path: Path, label: str) -> list[dict[str, Any]]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError as exc:
        raise AtlasUnavailableError(f"missing Atlas {label}: {path}") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise AtlasUnavailableError(f"invalid Atlas {label}: {path}") from exc
    if not isinstance(payload, list):
        raise AtlasUnavailableError(f"Atlas {label} must be a JSON array: {path}")
    return [row for row in payload if isinstance(row, dict)]


def _question_value(row: dict[str, Any], *keys: str, default: Any = "") -> Any:
    for key in keys:
        value = row.get(key)
        if value not in (None, ""):
            return value
    return default


class KnowledgeAtlasStore:
    """Thread-safe lazy indexes for Atlas hierarchy, slices, videos and questions."""

    def __init__(
        self,
        data_root: Path | str,
        *,
        video_root: Path | str | None = None,
        enabled: bool = True,
        asset_version: str = "2026-07-18",
        contract_path: Path | str | None = None,
    ) -> None:
        self.data_root = Path(data_root).resolve()
        self.video_root = Path(video_root).resolve() if video_root else self.data_root.parent / "video"
        self.enabled = bool(enabled)
        self.asset_version = asset_version
        self.contract_path = Path(contract_path).resolve() if contract_path else None
        self._lock = threading.RLock()
        self._warm_thread: threading.Thread | None = None
        self._warm_error: str | None = None
        self._hierarchy_ready = False
        self._questions_ready = False
        self._chunks_ready = False
        self._video_signature: tuple[str, int, int] | None = None
        self.kps: dict[str, dict[str, Any]] = {}
        self.tree: dict[str, dict[str, list[dict[str, Any]]]] = {}
        self.questions: list[dict[str, Any]] = []
        self.questions_by_kp: dict[str, list[dict[str, Any]]] = defaultdict(list)
        self.chunk_offsets: dict[str, int] = {}
        self.videos_by_kp: dict[str, list[dict[str, Any]]] = defaultdict(list)

    @property
    def image_root(self) -> Path:
        return self.data_root / _IMAGE_DIR

    @property
    def video_result_root(self) -> Path:
        """Accept either the runtime/video root or full_batch_results itself."""

        if (self.video_root / "catalog.json").is_file():
            return self.video_root
        return active_video_release_root(self.video_root) / "full_batch_results"

    def _asset_errors(self) -> list[str]:
        if not self.enabled:
            return ["KNOWLEDGE_ATLAS_ENABLED=false"]
        staging_trees = list(self.data_root.parent.glob(f".{self.data_root.name}.importing-*"))
        if staging_trees:
            receipt_path = self.data_root.parent / f".{self.data_root.name}.ready.json"
            try:
                receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
            except (FileNotFoundError, OSError, json.JSONDecodeError):
                receipt = None
            if not isinstance(receipt, dict) or receipt.get("target") != self.data_root.name:
                return [
                    f"Atlas import is not ready: {staging_trees[0]} "
                    f"(missing valid {receipt_path.name})"
                ]
        required = (
            self.data_root / _KP_FILE,
            self.data_root / _QUESTION_FILE,
            self.data_root / _CHUNK_FILE,
            self.image_root,
        )
        return [f"missing {path.name}: {path}" for path in required if not path.exists()]

    def _require_available(self) -> None:
        errors = self._asset_errors()
        if errors:
            raise AtlasUnavailableError("; ".join(errors))

    def _contract(self) -> dict[str, Any]:
        candidates = [self.contract_path]
        for path in candidates:
            if path is None or not path.is_file():
                continue
            try:
                payload = json.loads(path.read_text(encoding="utf-8-sig"))
            except (OSError, json.JSONDecodeError):
                return {"path": str(path), "valid": False}
            return {"path": str(path), "valid": isinstance(payload, dict), **(payload if isinstance(payload, dict) else {})}
        return {}

    def status(self) -> dict[str, Any]:
        errors = self._asset_errors()
        warming = bool(self._warm_thread and self._warm_thread.is_alive())
        warmed = (
            self._hierarchy_ready
            and self._questions_ready
            and self._chunks_ready
            and not warming
            and self._warm_error is None
        )
        manifest: dict[str, Any] = self._contract()
        if self._hierarchy_ready:
            manifest.setdefault("knowledge_points", len(self.kps))
            manifest.setdefault("books", len(self.tree))
        if self._questions_ready:
            manifest.setdefault("questions", len(self.questions))
            manifest.setdefault("linked_questions", sum(bool(row["kp_ids"]) for row in self.questions))
        if self._video_signature is not None:
            manifest.setdefault("video_segments", sum(len(rows) for rows in self.videos_by_kp.values()))
        return {
            "enabled": self.enabled,
            "available": not errors,
            "warmed": warmed,
            "warming": warming,
            "asset_version": self.asset_version,
            "data_root": str(self.data_root),
            "video_root": str(self.video_root),
            "video_result_root": str(self.video_result_root),
            "manifest": manifest,
            "errors": errors + ([self._warm_error] if self._warm_error else []),
        }

    def catalog_datasets(self) -> list[dict[str, Any]]:
        """Describe Atlas assets as datasets without mixing them into document RAG."""

        contract = self._contract()
        expected = contract.get("validated_counts") or {}
        if not isinstance(expected, dict):
            expected = {}

        def count(name: str, fallback: int | None = None) -> int | None:
            value = expected.get(name, fallback)
            try:
                return int(value) if value is not None else None
            except (TypeError, ValueError):
                return fallback

        knowledge_count = count("knowledge_points", len(self.kps) if self._hierarchy_ready else None)
        question_count = count("questions", len(self.questions) if self._questions_ready else None)
        linked_count = count(
            "questions_with_kp_ids",
            sum(bool(row["kp_ids"]) for row in self.questions) if self._questions_ready else None,
        )
        pending_count = count(
            "questions_without_kp_ids",
            (len(self.questions) - int(linked_count or 0)) if self._questions_ready else None,
        )
        chunk_count = count("source_chunks", len(self.chunk_offsets) if self._chunks_ready else None)
        if expected.get("images") is not None:
            image_count = count("images")
        elif self.image_root.is_dir():
            image_count = sum(path.is_file() for path in self.image_root.iterdir())
        else:
            image_count = None
        segment_count = count(
            "semantic_segments",
            sum(len(rows) for rows in self.videos_by_kp.values()) if self._video_signature is not None else None,
        )
        matched_count = count("matched_segments")

        common = {"kind": "atlas_dataset", "version": self.asset_version}
        return [
            {
                **common,
                "id": "atlas_knowledge_points",
                "name": "知识点体系",
                "available": (self.data_root / _KP_FILE).is_file(),
                "count": knowledge_count,
            },
            {
                **common,
                "id": "atlas_question_bank",
                "name": "Atlas 题库",
                "available": (self.data_root / _QUESTION_FILE).is_file(),
                "count": question_count,
                "linked_count": linked_count,
                "pending_link_count": pending_count,
            },
            {
                **common,
                "id": "atlas_chunks",
                "name": "教材知识切片",
                "available": (self.data_root / _CHUNK_FILE).is_file(),
                "count": chunk_count,
            },
            {
                **common,
                "id": "atlas_images",
                "name": "教材原图",
                "available": self.image_root.is_dir(),
                "count": image_count,
            },
            {
                **common,
                "id": "atlas_exam_bridge",
                "name": "考纲与知识点映射",
                "available": any(
                    (self.data_root / name).is_dir()
                    for name in ("07_exam_bridge", "08_exam_learning_path_2025")
                ),
                "count": count("routes", 3),
            },
            {
                **common,
                "id": "atlas_videos",
                "name": "视频语义片段",
                "available": (self.video_result_root / "catalog.json").is_file(),
                "count": segment_count,
                "matched_count": matched_count,
            },
        ]

    @staticmethod
    def _kp(record: dict[str, Any]) -> dict[str, Any]:
        value = record.get("kp", record)
        return value if isinstance(value, dict) else {}

    def ensure_hierarchy(self) -> None:
        if self._hierarchy_ready:
            return
        with self._lock:
            if self._hierarchy_ready:
                return
            self._require_available()
            records = _read_json_array(self.data_root / _KP_FILE, "knowledge points")
            tree: dict[str, dict[str, list[dict[str, Any]]]] = {}
            kps: dict[str, dict[str, Any]] = {}
            for record in records:
                kp = self._kp(record)
                kp_id = str(kp.get("kp_id") or "").strip()
                if not kp_id:
                    continue
                lv1 = str(kp.get("kp_lv1") or kp.get("kp_Lv1") or "").strip() or "未分类"
                lv2 = str(kp.get("kp_lv2") or kp.get("kp_Lv2") or "").strip() or "未分类"
                kps[kp_id] = kp
                tree.setdefault(lv1, {}).setdefault(lv2, []).append(kp)
            self.tree = tree
            self.kps = kps
            self._hierarchy_ready = True

    def route(self, route_id: str) -> dict[str, Any]:
        return next((dict(item) for item in ROUTES if item["id"] == route_id), dict(ROUTES[0]))

    def route_books(self, route_id: str) -> set[str]:
        self.ensure_hierarchy()
        route = self.route(route_id)
        requested = route["books"]
        return set(self.tree) if requested == "*" else set(requested) & set(self.tree)

    def routes(self) -> list[dict[str, Any]]:
        self.ensure_hierarchy()
        output: list[dict[str, Any]] = []
        for original in ROUTES:
            route = dict(original)
            available = self.route_books(route["id"])
            requested = set(self.tree) if route["books"] == "*" else set(route["books"])
            output.append({
                "id": route["id"],
                "name": route["name"],
                "description": route["description"],
                "book_count": len(available),
                "missing_books": sorted(requested - set(self.tree)),
            })
        return output

    def nodes(
        self,
        level: int,
        *,
        lv1: str = "",
        lv2: str = "",
        route_id: str = "textbook_14_5",
    ) -> dict[str, Any]:
        self.ensure_hierarchy()
        books = self.route_books(route_id)
        stats = {
            "lv1": len(books),
            "lv2": sum(len(self.tree[name]) for name in books),
            "lv3": sum(len(items) for name in books for items in self.tree[name].values()),
        }
        if level == 1:
            rows = [
                {
                    "id": name,
                    "name": name,
                    "count": sum(len(items) for items in children.values()),
                    "children_count": len(children),
                }
                for name, children in self.tree.items()
                if name in books
            ]
        elif level == 2:
            if lv1 not in books:
                raise KeyError("该路线不包含此一级教材")
            children = self.tree.get(lv1)
            if children is None:
                raise KeyError("一级标签不存在")
            rows = [
                {
                    "id": name,
                    "name": name,
                    "count": len(items),
                    "children_count": len(items),
                    "order_index": index,
                }
                for index, (name, items) in enumerate(children.items())
            ]
        elif level == 3:
            if lv1 not in books:
                raise KeyError("该路线不包含此一级教材")
            children = self.tree.get(lv1, {}).get(lv2)
            if children is None:
                raise KeyError("二级标签不存在")
            self.ensure_questions()
            self.ensure_videos()
            rows = []
            for kp in children:
                kp_id = str(kp.get("kp_id") or "")
                question_count = len(self.questions_by_kp.get(kp_id, []))
                video_count = len(self.videos_by_kp.get(kp_id, []))
                rows.append({
                    "id": kp_id,
                    "name": str(kp.get("kp_lv3") or kp.get("kp_Lv3") or "").strip() or "未命名知识点",
                    "alias": str(kp.get("other_name") or "").strip(),
                    "count": len(kp.get("raw_content") or []),
                    "order": str(kp.get("order") or ""),
                    "question_count": question_count,
                    "video_count": video_count,
                    "has_questions": question_count > 0,
                    "has_videos": video_count > 0,
                    "node_style": (
                        "solid" if question_count and video_count else
                        "ring" if question_count else
                        "video" if video_count else
                        "dashed"
                    ),
                })
        else:
            raise ValueError("level 只能是 1、2 或 3")
        return {
            "level": level,
            "nodes": rows,
            "count": len(rows),
            "stats": stats,
            "route": self.route(route_id)["id"],
        }

    @staticmethod
    def _normalize_question(row: dict[str, Any]) -> dict[str, Any]:
        question_id = str(_question_value(row, "question_id", "题目id", "id")).strip()
        stem = str(_question_value(row, "question_content", "题目内容", "stem", "content"))
        kp_ids = row.get("kp_ids") or []
        if not isinstance(kp_ids, list):
            kp_ids = []
        options = row.get("options") or []
        if isinstance(options, dict):
            options = [{"key": str(key), "value": value} for key, value in options.items()]
        elif not isinstance(options, list):
            options = [str(options)]
        answer = _question_value(row, "answer", "题目答案", default=[])
        return {
            "question_id": question_id,
            "id": question_id,
            "question_type": str(_question_value(row, "question_type", "题型", "type", default="未分类")),
            "stem": stem,
            "content": stem,
            "options": options,
            "answer": answer,
            "explanation": str(_question_value(row, "explanation", "explaination", "题目解析", "analysis")),
            "difficulty": _question_value(row, "difficulty", default=""),
            "kp_ids": [str(value) for value in kp_ids if value not in (None, "")],
            "source": str(row.get("source") or row.get("source_file") or "knowledge_atlas"),
        }

    def ensure_questions(self) -> None:
        if self._questions_ready:
            return
        with self._lock:
            if self._questions_ready:
                return
            self._require_available()
            records = _read_json_array(self.data_root / _QUESTION_FILE, "question bank")
            questions: list[dict[str, Any]] = []
            by_kp: dict[str, list[dict[str, Any]]] = defaultdict(list)
            for original in records:
                question = self._normalize_question(original)
                if not question["question_id"]:
                    continue
                questions.append(question)
                for kp_id in question["kp_ids"]:
                    by_kp[kp_id].append(question)
            self.questions = questions
            self.questions_by_kp = by_kp
            self._questions_ready = True

    def _current_video_signature(self) -> tuple[str, int, int] | None:
        path = self.video_result_root / "catalog.json"
        if not path.is_file():
            return None
        stat = path.stat()
        return str(path.resolve()), stat.st_size, stat.st_mtime_ns

    def ensure_videos(self) -> None:
        signature = self._current_video_signature()
        if signature is None:
            self.videos_by_kp = defaultdict(list)
            self._video_signature = None
            return
        if self._video_signature == signature:
            return
        with self._lock:
            signature = self._current_video_signature()
            if self._video_signature == signature:
                return
            index: dict[str, list[dict[str, Any]]] = defaultdict(list)
            seen: dict[str, set[tuple[Any, ...]]] = defaultdict(set)
            for result_path in self.video_result_root.glob("BV*/classification_result.json"):
                try:
                    result = json.loads(result_path.read_text(encoding="utf-8-sig"))
                except (OSError, json.JSONDecodeError):
                    continue
                if not isinstance(result, dict):
                    continue
                bvid = str(result.get("bvid") or result_path.parent.name)
                for page in result.get("pages") or []:
                    if not isinstance(page, dict):
                        continue
                    for segment in page.get("segments") or []:
                        if not isinstance(segment, dict):
                            continue
                        for match in segment.get("kp_matches") or []:
                            if not isinstance(match, dict):
                                continue
                            kp_id = str(match.get("kp_id") or "")
                            if not kp_id:
                                continue
                            key = (
                                bvid,
                                page.get("page"),
                                segment.get("start_seconds"),
                                segment.get("end_seconds"),
                            )
                            if key in seen[kp_id]:
                                continue
                            seen[kp_id].add(key)
                            index[kp_id].append({
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
                            })
            for rows in index.values():
                rows.sort(key=lambda row: (row["bvid"], int(row["page"] or 0), float(row["start_seconds"] or 0)))
            self.videos_by_kp = index
            self._video_signature = signature

    def ensure_chunk_offsets(self) -> None:
        if self._chunks_ready:
            return
        with self._lock:
            if self._chunks_ready:
                return
            self._require_available()
            path = self.data_root / _CHUNK_FILE
            pattern = re.compile(br'"chunk_uid"\s*:\s*"([^"\\]+)"')
            offsets: dict[str, int] = {}
            try:
                with path.open("rb") as handle:
                    while True:
                        offset = handle.tell()
                        line = handle.readline()
                        if not line:
                            break
                        match = pattern.search(line[:2048])
                        if match:
                            offsets[match.group(1).decode("utf-8")] = offset
            except OSError as exc:
                raise AtlasUnavailableError(f"invalid Atlas chunks: {path}") from exc
            self.chunk_offsets = offsets
            self._chunks_ready = True

    def _chunk(self, uid: str) -> dict[str, Any] | None:
        offset = self.chunk_offsets.get(uid)
        if offset is None:
            return None
        path = self.data_root / _CHUNK_FILE
        try:
            with path.open("rb") as handle:
                handle.seek(offset)
                row = json.loads(handle.readline().decode("utf-8-sig"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            return None
        metadata = row.get("metadata") or {}
        return {
            "uid": uid,
            "book": row.get("book", ""),
            "heading": metadata.get("heading_path") or row.get("kp_Lv2", ""),
            "text": row.get("retrieval_text") or row.get("text", ""),
            "char_count": row.get("retrieval_char_count") or row.get("char_count", 0),
            "source_char_count": row.get("char_count", 0),
            "context_chunk_uids": row.get("retrieval_context_chunk_uids") or [],
        }

    def detail(self, kp_id: str, question_limit: int = 30) -> dict[str, Any]:
        self.ensure_hierarchy()
        kp = self.kps.get(str(kp_id))
        if kp is None:
            raise KeyError("知识点不存在")
        self.ensure_chunk_offsets()
        self.ensure_questions()
        self.ensure_videos()
        refs = [str(value) for value in (kp.get("raw_content") or []) if value]
        chunks = [chunk for uid in refs if (chunk := self._chunk(uid)) is not None]
        questions = self.questions_by_kp.get(str(kp_id), [])
        return {
            "kp": {
                "id": str(kp_id),
                "lv1": kp.get("kp_lv1") or kp.get("kp_Lv1") or "",
                "lv2": kp.get("kp_lv2") or kp.get("kp_Lv2") or "",
                "lv3": kp.get("kp_lv3") or kp.get("kp_Lv3") or "",
                "alias": kp.get("other_name", ""),
                "order": kp.get("order", ""),
                "updated_at": kp.get("updated_at", ""),
                "exam_bridges": kp.get("exam_bridges") or [],
            },
            "chunks": chunks,
            "raw_refs": refs,
            "questions": questions[: max(1, min(100, int(question_limit)))],
            "question_count": len(questions),
            "videos": self.videos_by_kp.get(str(kp_id), []),
        }

    def image_path(self, filename: str) -> Path:
        value = str(filename or "")
        if not value or value in {".", ".."} or "/" in value or "\\" in value or Path(value).name != value:
            raise ValueError("invalid image filename")
        root = self.image_root.resolve()
        path = (root / value).resolve()
        try:
            path.relative_to(root)
        except ValueError as exc:
            raise ValueError("invalid image filename") from exc
        if not path.is_file():
            raise FileNotFoundError(value)
        return path

    def warm(self) -> dict[str, Any]:
        self._warm_error = None
        try:
            self.ensure_hierarchy()
            self.ensure_chunk_offsets()
            self.ensure_questions()
            self.ensure_videos()
        except Exception as exc:
            self._warm_error = str(exc)
            raise
        return self.status()

    def _warm_worker(self) -> None:
        try:
            self.warm()
        except Exception:
            # Status exposes the scoped error; a background worker must not crash the app.
            return

    def start_warm(self) -> str:
        if self.status()["warmed"]:
            return "warm"
        with self._lock:
            if self._warm_thread and self._warm_thread.is_alive():
                return "warming"
            self._warm_thread = threading.Thread(
                target=self._warm_worker,
                name="knowledge-atlas-warm",
                daemon=True,
            )
            self._warm_thread.start()
        return "warming"

    @staticmethod
    def _route_for_track(track_id: str) -> str:
        value = str(track_id or "").lower()
        if "assistant" in value or "助理" in value:
            return "tcm_assistant"
        if "postgraduate" in value or "考研" in value:
            return "postgraduate"
        return "textbook_14_5"

    def _route_containing_book(self, preferred_route: str, book: str) -> str:
        if not book or book in self.route_books(preferred_route):
            return preferred_route
        # The all-textbooks route is the deterministic fallback required by the
        # navigation contract; only use another filtered route if it really contains
        # the resolved book.
        for candidate in ("textbook_14_5", "postgraduate", "tcm_assistant"):
            if book in self.route_books(candidate):
                return candidate
        return "textbook_14_5"

    def _context_payload(
        self,
        kp: dict[str, Any] | None,
        *,
        route_id: str,
        track_id: str,
        membership_id: str,
        match_level: str,
        notice: str | None = None,
    ) -> dict[str, Any]:
        lv1 = (kp or {}).get("kp_lv1") or (kp or {}).get("kp_Lv1") or ""
        compatible_route = self._route_containing_book(route_id, str(lv1)) if lv1 else route_id
        return {
            "resolved": kp is not None,
            "match_level": match_level,
            "route": compatible_route,
            "lv1": lv1,
            "lv2": (kp or {}).get("kp_lv2") or (kp or {}).get("kp_Lv2") or "",
            "kp_id": str((kp or {}).get("kp_id") or ""),
            "track_id": track_id,
            "membership_id": membership_id,
            **({"notice": notice} if notice else {}),
        }

    def resolve_context(
        self,
        *,
        track_id: str,
        membership_id: str,
        exam_repository: Any | None = None,
    ) -> dict[str, Any]:
        self.ensure_hierarchy()
        route_id = self._route_for_track(track_id)
        titles: list[str] = []
        if exam_repository is not None and track_id and membership_id:
            try:
                detail = exam_repository.get_membership(track_id, membership_id)
                titles = [
                    str(row.get("title") or "").strip()
                    for row in detail.get("breadcrumb") or []
                    if row.get("title")
                ]
                node_title = str((detail.get("node") or {}).get("title_normalized") or "").strip()
                if node_title:
                    titles.append(node_title)
                mapped = exam_repository.get_catalog_subtree_knowledge_points(
                    membership_id,
                    accepted_only=True,
                )
                for row in mapped.get("knowledge_points") or []:
                    kp = self.kps.get(str(row.get("kp_id") or ""))
                    if kp is not None:
                        return self._context_payload(
                            kp,
                            route_id=route_id,
                            track_id=track_id,
                            membership_id=membership_id,
                            match_level="kp",
                        )
            except (KeyError, ValueError):
                pass

        normalized_titles = [re.sub(r"^[一二三四五六七八九十\d]+[、.．节篇章\s]*", "", value) for value in titles]
        for title in reversed(normalized_titles):
            if not title:
                continue
            exact = next(
                (
                    kp for kp in self.kps.values()
                    if title in {
                        str(kp.get("kp_lv3") or kp.get("kp_Lv3") or "").strip(),
                        str(kp.get("other_name") or "").strip(),
                    }
                ),
                None,
            )
            if exact is not None:
                return self._context_payload(
                    exact,
                    route_id=route_id,
                    track_id=track_id,
                    membership_id=membership_id,
                    match_level="title",
                    notice="已按节点标题匹配到教材知识点",
                )

        for title in reversed(normalized_titles):
            if title in self.tree:
                compatible_route = self._route_containing_book(route_id, title)
                return {
                    **self._context_payload(
                        None,
                        route_id=compatible_route,
                        track_id=track_id,
                        membership_id=membership_id,
                        match_level="route",
                        notice="未找到精确知识点，已打开对应教材总览",
                    ),
                    "resolved": True,
                    "lv1": title,
                }
        return self._context_payload(
            None,
            route_id=route_id,
            track_id=track_id,
            membership_id=membership_id,
            match_level="route",
            notice="未找到精确映射，已打开学习路线总览",
        )

    def questions_for_kps(
        self,
        kp_ids: Iterable[str],
        *,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        self.ensure_questions()
        selected: dict[str, dict[str, Any]] = {}
        for kp_id in (str(value) for value in kp_ids if str(value)):
            for question in self.questions_by_kp.get(kp_id, ()):
                selected.setdefault(question["question_id"], question)
        return [
            {
                **question,
                "score": 1.0,
                "channels": ["atlas_question_bank", "kp_reverse_index"],
            }
            for question in list(selected.values())[: max(1, min(100, int(limit)))]
        ]

    def search_questions(
        self,
        query: str,
        *,
        kp_ids: Iterable[str] = (),
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        self.ensure_questions()
        requested = {str(value) for value in kp_ids if str(value)}
        terms = [value for value in re.split(r"\s+", str(query or "").strip().lower()) if value]
        results: list[dict[str, Any]] = []
        for question in self.questions:
            if requested and not requested.intersection(question["kp_ids"]):
                continue
            searchable = " ".join((
                question["stem"],
                json.dumps(question["options"], ensure_ascii=False),
                json.dumps(question["answer"], ensure_ascii=False),
                question["explanation"],
            )).lower()
            if terms and not all(term in searchable for term in terms):
                continue
            score = 1.0 if not terms else sum(searchable.count(term) for term in terms) / len(terms)
            if requested:
                score += 0.25
            results.append({
                **question,
                "score": float(score),
                "channels": ["atlas_question_bank"],
            })
        results.sort(key=lambda row: (-row["score"], row["question_id"]))
        return results[: max(1, min(100, int(limit)))]

    def reconcile_questions(self, db: Any, *, apply: bool = False) -> dict[str, Any]:
        """Reconcile by question_id without deleting Atlas-only or DB-only rows."""

        from APP.backend.database import QuestionBankItem

        self.ensure_questions()
        atlas_by_id = {row["question_id"]: row for row in self.questions}
        db_rows = db.query(QuestionBankItem).all()
        db_by_id = {str(row.question_id): row for row in db_rows}
        shared = sorted(set(atlas_by_id) & set(db_by_id))
        db_only_ids = sorted(set(db_by_id) - set(atlas_by_id))
        atlas_linked = sum(bool(row["kp_ids"]) for row in self.questions)
        changed = 0
        if apply:
            for question_id in shared:
                atlas = atlas_by_id[question_id]
                target = db_by_id[question_id]
                values = {
                    "kp_ids_json": json.dumps(atlas["kp_ids"], ensure_ascii=False),
                    "status": "active" if atlas["kp_ids"] else "pending_link",
                }
                if not str(target.source or "").strip() or target.source == "manual":
                    values["source"] = f"knowledge_atlas:{self.asset_version}"
                row_changed = False
                for key, value in values.items():
                    if getattr(target, key) != value:
                        setattr(target, key, value)
                        row_changed = True
                changed += int(row_changed)
            db.commit()
        return {
            "asset_version": self.asset_version,
            "atlas_total": len(atlas_by_id),
            "atlas_linked": atlas_linked,
            "atlas_pending_link": len(atlas_by_id) - atlas_linked,
            "db_total": len(db_by_id),
            "matched": len(shared),
            "matched_linked": sum(bool(atlas_by_id[question_id]["kp_ids"]) for question_id in shared),
            "matched_pending_link": sum(not atlas_by_id[question_id]["kp_ids"] for question_id in shared),
            "atlas_only": len(set(atlas_by_id) - set(db_by_id)),
            "db_only": len(db_only_ids),
            "db_only_by_status": dict(sorted({
                status: sum((db_by_id[question_id].status or "unknown") == status for question_id in db_only_ids)
                for status in {db_by_id[question_id].status or "unknown" for question_id in db_only_ids}
            }.items())),
            "changed": changed,
            "applied": bool(apply),
            "deleted": 0,
            "content_fields_preserved": True,
        }


class LazyKnowledgeAtlasService:
    """Create the configured store only after an Atlas endpoint is called."""

    def __init__(self) -> None:
        self._service: KnowledgeAtlasStore | None = None
        self._lock = threading.Lock()

    def _get_service(self) -> KnowledgeAtlasStore:
        if self._service is None:
            with self._lock:
                if self._service is None:
                    from APP.backend.config import (
                        KNOWLEDGE_ATLAS_ASSET_VERSION,
                        KNOWLEDGE_ATLAS_CONTRACT_PATH,
                        KNOWLEDGE_ATLAS_DATA_ROOT,
                        KNOWLEDGE_ATLAS_ENABLED,
                        KNOWLEDGE_ATLAS_VIDEO_ROOT,
                    )

                    self._service = KnowledgeAtlasStore(
                        KNOWLEDGE_ATLAS_DATA_ROOT,
                        video_root=KNOWLEDGE_ATLAS_VIDEO_ROOT,
                        enabled=KNOWLEDGE_ATLAS_ENABLED,
                        asset_version=KNOWLEDGE_ATLAS_ASSET_VERSION,
                        contract_path=KNOWLEDGE_ATLAS_CONTRACT_PATH,
                    )
        return self._service

    def __getattr__(self, name: str) -> Any:
        return getattr(self._get_service(), name)


atlas_service = LazyKnowledgeAtlasService()
