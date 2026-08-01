# -*- coding: utf-8 -*-
"""考纲要求 -> 知识库/题库 的批量向量匹配。

复用现有 FAISS 向量库（题库 + 教材 chunk），对考纲每条考核要求做批量
embedding 与向量检索，输出可展示的匹配证据（命中教材片段、相关题目）。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import faiss
import numpy as np

from competition_app.embeddings.base import EmbeddingModel

# 与"中医内科学（中级）"考试科目相关的教材索引关键词。
_RELEVANT_BOOK_KEYWORDS = (
    "中医内科", "内科学", "中医学基础", "中医基础", "中医诊断",
    "中药", "方剂", "针灸", "内经", "伤寒", "金匮", "温病",
    "诊断学", "中医各家", "传染病",
)
_TEXTBOOK_MIN_SCORE = 0.45
_TEXTBOOK_MATCH_SCORE = 0.72
_TEXTBOOK_STRONG_SCORE = 0.78
_QUESTION_MIN_SCORE = 0.45
_QUESTION_MATCH_SCORE = 0.75
_QUESTION_STRONG_SCORE = 0.80
_EMBED_BATCH = 64


def _query_text(requirement: dict[str, Any], structured: dict[str, Any]) -> str:
    return " ".join(filter(None, [
        str(structured.get("subject") or ""),
        str(requirement.get("section_title") or ""),
        str(requirement.get("title") or ""),
        str(requirement.get("details") or "")[:120],
    ]))


def _snippet(value: Any, length: int = 120) -> str:
    return str(value or "").strip()[:length]


class SyllabusVectorMatcher:
    """批量向量检索器：一次 embedding 全部考纲要求，再对相关向量索引批量检索。"""

    def __init__(
        self,
        embedding_model: EmbeddingModel,
        *,
        embedding_model_name: str,
        vector_store_root: Path,
    ) -> None:
        self.embedding_model = embedding_model
        self.embedding_model_name = embedding_model_name
        self.vector_store_root = Path(vector_store_root)
        self._textbook_indexes: list[tuple[str, Any, list[dict[str, Any]]]] | None = None
        self._question_index: tuple[Any, list[dict[str, Any]]] | None = None

    async def embed_queries(self, queries: list[str]) -> np.ndarray:
        vectors: list[list[float]] = []
        for offset in range(0, len(queries), _EMBED_BATCH):
            batch = queries[offset:offset + _EMBED_BATCH]
            vectors.extend(await self.embedding_model.embed(batch))
        matrix = np.asarray(vectors, dtype="float32").reshape(len(queries), -1)
        normalized = matrix.copy()
        faiss.normalize_L2(normalized)
        return normalized

    def _load_textbook_indexes(self) -> list[tuple[str, Any, list[dict[str, Any]]]]:
        if self._textbook_indexes is not None:
            return self._textbook_indexes
        root = self.vector_store_root / "indexes"
        loaded: list[tuple[str, Any, list[dict[str, Any]]]] = []
        if root.is_dir():
            for directory in sorted(root.iterdir()):
                if not directory.is_dir() or directory.name == "题库":
                    continue
                name = directory.name
                if not any(keyword in name for keyword in _RELEVANT_BOOK_KEYWORDS):
                    continue
                index_path = directory / "index.faiss"
                metadata_path = directory / "metadata.jsonl"
                if not index_path.is_file() or not metadata_path.is_file():
                    continue
                try:
                    # faiss.read_index 在本机打不开含中文路径的文件（Windows 窄字符 fopen），
                    # 改为 Python 读字节后反序列化。
                    index = faiss.deserialize_index(
                        np.frombuffer(index_path.read_bytes(), dtype="uint8")
                    )
                    metadata = [
                        json.loads(line)
                        for line in metadata_path.read_text(encoding="utf-8").splitlines()
                        if line.strip()
                    ]
                except Exception:
                    continue
                if len(metadata) < index.ntotal:
                    continue
                loaded.append((name, index, metadata))
        self._textbook_indexes = loaded
        return loaded

    def _load_question_index(self) -> tuple[Any, list[dict[str, Any]]]:
        if self._question_index is not None:
            return self._question_index
        root = self.vector_store_root / "indexes" / "题库"
        index_path = root / "index.faiss"
        metadata_path = root / "metadata.jsonl"
        index: Any = None
        metadata: list[dict[str, Any]] = []
        if index_path.is_file() and metadata_path.is_file():
            try:
                index = faiss.deserialize_index(
                    np.frombuffer(index_path.read_bytes(), dtype="uint8")
                )
                metadata = [
                    json.loads(line)
                    for line in metadata_path.read_text(encoding="utf-8").splitlines()
                    if line.strip()
                ]
            except Exception:
                index = None
                metadata = []
        self._question_index = (index, metadata)
        return self._question_index

    async def match(
        self,
        requirements: list[dict[str, Any]],
        structured: dict[str, Any],
    ) -> list[dict[str, Any]]:
        queries = [_query_text(requirement, structured) for requirement in requirements]
        if not queries:
            return []
        normalized = await self.embed_queries(queries)
        count = len(queries)

        textbook_hits: list[list[dict[str, Any]]] = [[] for _ in range(count)]
        for book, index, metadata in self._load_textbook_indexes():
            if normalized.shape[1] != index.d:
                continue
            scores, positions = index.search(normalized, 3)
            for row in range(count):
                for score, position in zip(scores[row], positions[row]):
                    if position < 0 or position >= len(metadata):
                        continue
                    if float(score) < _TEXTBOOK_MIN_SCORE:
                        continue
                    item = metadata[int(position)]
                    original = item.get("original") if isinstance(item.get("original"), dict) else {}
                    meta = original.get("metadata") if isinstance(original.get("metadata"), dict) else {}
                    content = str(item.get("content") or original.get("text") or "").strip()
                    heading = str(meta.get("heading_path") or " / ".join(
                        str(value) for value in (original.get("heading_path") or [])
                    ) or "")
                    textbook_hits[row].append({
                        "book": str(meta.get("book") or book),
                        "heading": heading,
                        "kp_lv2": str(meta.get("kp_Lv2") or ""),
                        "snippet": _snippet(content, 140),
                        "score": round(float(score), 4),
                    })
        for row in range(count):
            textbook_hits[row].sort(key=lambda hit: hit["score"], reverse=True)
            textbook_hits[row] = textbook_hits[row][:2]

        question_hits: list[list[dict[str, Any]]] = [[] for _ in range(count)]
        question_index, question_metadata = self._load_question_index()
        if question_index is not None and normalized.shape[1] == question_index.d:
            scores, positions = question_index.search(normalized, 8)
            for row in range(count):
                for score, position in zip(scores[row], positions[row]):
                    if position < 0 or position >= len(question_metadata):
                        continue
                    if float(score) < _QUESTION_MIN_SCORE:
                        continue
                    item = question_metadata[int(position)]
                    original = item.get("original") if isinstance(item.get("original"), dict) else {}
                    question_hits[row].append({
                        "question_id": str(original.get("题目id") or ""),
                        "stem": _snippet(original.get("题目内容") or item.get("content"), 110),
                        "type": str(original.get("题型") or ""),
                        "source": str(original.get("题目大来源") or ""),
                        "score": round(float(score), 4),
                    })
        for row in range(count):
            question_hits[row].sort(key=lambda hit: hit["score"], reverse=True)
            question_hits[row] = question_hits[row][:3]

        output: list[dict[str, Any]] = []
        for row, requirement in enumerate(requirements):
            textbooks = textbook_hits[row]
            questions = question_hits[row]
            top_textbook = textbooks[0]["score"] if textbooks else 0.0
            top_question = questions[0]["score"] if questions else 0.0
            channels: list[str] = []
            if textbooks:
                channels.append("vector_textbook")
            if questions:
                channels.append("vector_question")
            strong = bool(
                (textbooks and top_textbook >= _TEXTBOOK_STRONG_SCORE)
                or (questions and top_question >= _QUESTION_STRONG_SCORE)
            )
            matched = strong or bool(
                (textbooks and top_textbook >= _TEXTBOOK_MATCH_SCORE)
                or (questions and top_question >= _QUESTION_MATCH_SCORE)
            )
            has_evidence = bool(textbooks or questions)
            if strong:
                match_grade = "strong"
            elif matched:
                match_grade = "medium"
            elif has_evidence:
                match_grade = "weak"
            else:
                match_grade = "unmatched"
            kp_name = None
            if textbooks:
                kp_name = textbooks[0].get("kp_lv2") or textbooks[0].get("heading") or None
            output.append({
                "requirement_id": requirement.get("requirement_id"),
                "match_status": "matched" if (strong or matched) else "unmatched",
                "match_grade": match_grade,
                "kp_id": None,
                "kp_name": kp_name,
                "confidence": round(min(1.0, max(top_textbook, top_question)), 4),
                "match_source": ",".join(channels) or None,
                "channels": channels,
                "textbook_evidence": textbooks,
                "question_evidence": questions,
                "question_count": len(questions),
            })
        return output