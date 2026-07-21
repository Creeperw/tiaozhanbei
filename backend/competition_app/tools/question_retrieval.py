from __future__ import annotations

import json
import math
import re
from collections import Counter
from pathlib import Path

import faiss
import numpy as np

from competition_app.contracts.knowledge import (
    QuestionDetail,
    QuestionRetrievalMetadata,
    QuestionSearchResult,
)
from competition_app.embeddings.base import EmbeddingModel
from competition_app.tools.knowledge_repository import KnowledgeRepository


class QuestionVectorIndexError(RuntimeError):
    pass


def _tokens(text: str) -> list[str]:
    normalized = re.sub(r"\s+", "", text.lower())
    chinese = [normalized[index:index + 2] for index in range(max(0, len(normalized) - 1)) if "\u4e00" <= normalized[index] <= "\u9fff"]
    return chinese + re.findall(r"[a-z0-9_]{2,}", normalized)


class _BM25:
    def __init__(self, corpus: list[list[str]]) -> None:
        self._frequencies = [Counter(row) for row in corpus]
        self._lengths = [len(row) for row in corpus]
        self._average = sum(self._lengths) / len(self._lengths) if self._lengths else 0.0
        document_frequency: Counter[str] = Counter()
        for row in self._frequencies:
            document_frequency.update(row)
        count = len(corpus)
        self._idf = {term: math.log(1 + (count - value + 0.5) / (value + 0.5)) for term, value in document_frequency.items()}

    def scores(self, query: list[str]) -> list[float]:
        values = [0.0] * len(self._frequencies)
        if not self._average:
            return values
        for term in set(query):
            if term not in self._idf:
                continue
            for index, frequency in enumerate(self._frequencies):
                tf = frequency.get(term, 0)
                denominator = tf + 1.2 * (1 - 0.75 + 0.75 * self._lengths[index] / self._average)
                values[index] += self._idf[term] * tf * 2.2 / max(denominator, 1e-9)
        return values


class QuestionHybridRetriever:
    def __init__(self, repository: KnowledgeRepository, embedding_model: EmbeddingModel, *, embedding_model_name: str, vector_store_root: Path) -> None:
        self.repository = repository
        self.embedding_model = embedding_model
        self.embedding_model_name = embedding_model_name
        self.vector_store_root = vector_store_root

    async def search(self, query: str, resolved_kp_ids: list[str], limit: int) -> QuestionSearchResult:
        if limit < 1:
            raise ValueError("limit must be positive")
        hits: dict[str, dict[str, float | set[str]]] = {}
        for kp_id in resolved_kp_ids:
            for question_id in self.repository.question_ids_for_kp(kp_id):
                entry = hits.setdefault(question_id, {"channels": set(), "bridge": 0.0, "bm25": 0.0, "vector": 0.0})
                entry["channels"].add("bridge")
                entry["bridge"] = 1.0
        candidate_ids = sorted(hits)
        if candidate_ids:
            scores = _BM25([
                _tokens(str(
                    self.repository.get_question(question_id).get("question_content")
                    or self.repository.get_question(question_id).get("题目内容", "")
                ))
                for question_id in candidate_ids
            ]).scores(_tokens(query))
            maximum = max(scores, default=0.0)
            for question_id, score in zip(candidate_ids, scores):
                if score > 0:
                    entry = hits[question_id]
                    entry["channels"].add("bm25")
                    entry["bm25"] = score / maximum * 0.75
        try:
            vector_hits = await self._vector_hits(query, max(limit * 4, 20))
        except QuestionVectorIndexError:
            raise
        except (RuntimeError, ValueError):
            # Vector search is an enhancement. Bridge mappings and BM25 remain
            # valid retrieval channels when the embedding API/index is down.
            if not hits:
                raise
            vector_hits = []
        for question_id, score in vector_hits:
            try:
                self.repository.get_question(question_id)
            except KeyError:
                continue
            entry = hits.setdefault(question_id, {"channels": set(), "bridge": 0.0, "bm25": 0.0, "vector": 0.0})
            entry["channels"].add("vector")
            entry["vector"] = score * 0.85
        items = [self._detail(question_id, entry) for question_id, entry in hits.items()]
        items.sort(key=self._sort_key)
        index_path = self._index_path()
        return QuestionSearchResult(query=query, resolved_kp_ids=resolved_kp_ids, embedding_model=self.embedding_model_name, vector_index_path=str(index_path), items=items[:limit])

    async def _vector_hits(self, query: str, limit: int) -> list[tuple[str, float]]:
        index_path = self._index_path()
        metadata_path = index_path.with_name("metadata.jsonl")
        if not index_path.exists() or not metadata_path.exists():
            raise QuestionVectorIndexError("missing question vector index or metadata")
        try:
            index = faiss.read_index(str(index_path))
        except Exception as exc:
            raise QuestionVectorIndexError("unreadable question vector index") from exc
        vectors = await self.embedding_model.embed([query])
        vector = np.asarray(vectors[0], dtype="float32").reshape(1, -1)
        if vector.shape[1] != index.d:
            raise QuestionVectorIndexError(f"dimension mismatch: index={index.d}, query={vector.shape[1]}")
        faiss.normalize_L2(vector)
        scores, positions = index.search(vector, min(limit, index.ntotal))
        metadata = [json.loads(line) for line in metadata_path.read_text(encoding="utf-8").splitlines() if line.strip()]
        result: list[tuple[str, float]] = []
        for score, position in zip(scores[0], positions[0]):
            if position < 0 or position >= len(metadata):
                continue
            question_id = str(metadata[int(position)].get("original", {}).get("题目id", ""))
            if question_id:
                result.append((question_id, float(score)))
        return result

    def _detail(self, question_id: str, entry: dict[str, float | set[str]]) -> QuestionDetail:
        row = self.repository.get_question(question_id)
        bridges = self.repository.bridges_for_question(question_id)
        channels = [name for name in ("bridge", "bm25", "vector") if name in entry["channels"]]
        scores = {name: float(entry[name]) for name in channels}
        return QuestionDetail(
            question_id=question_id,
            question_type=str(row.get("question_type") or row.get("题型", "")),
            stem=str(row.get("question_content") or row.get("题目内容", "")),
            reference_answer=str(row.get("answer") or row.get("题目答案", "")),
            analysis=(
                str(row.get("explanation") or row.get("题目答案解析"))
                if row.get("explanation") is not None or row.get("题目答案解析") is not None
                else None
            ),
            options=[str(option) for option in (row.get("options") or [])],
            tags=list(row.get("kp_ids") or row.get("标签", [])),
            source_metadata={
                "major_source": row.get("题目大来源"),
                "chapter_source": row.get("题目章节来源"),
                "question_number": row.get("题号"),
                "source": row.get("source", {}),
                "options": row.get("options", []),
            },
            bridges=bridges,
            retrieval=QuestionRetrievalMetadata(
                channels=channels,
                channel_scores=scores,
                fusion_score=max(scores.values(), default=0.0),
            ),
        )

    def _sort_key(self, item: QuestionDetail):
        layer = {"strict": 0, "llm": 1, "similarity": 2}
        relation = {"primary": 0, "candidate": 1, "propagated_candidate": 2}
        best = min(item.bridges, key=lambda bridge: (layer[bridge.bridge_layer], relation.get(bridge.relation, 3), -bridge.confidence, bridge.rank), default=None)
        return (-item.retrieval.fusion_score, layer.get(best.bridge_layer, 3) if best else 3, relation.get(best.relation, 3) if best else 3, -(best.confidence if best else 0), best.rank if best else 999999, item.question_id)

    def _index_path(self) -> Path:
        return self.vector_store_root / "indexes" / "题库" / "index.faiss"