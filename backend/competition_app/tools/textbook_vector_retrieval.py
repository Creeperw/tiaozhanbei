from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from competition_app.embeddings.base import EmbeddingModel


@dataclass(frozen=True)
class TextbookVectorHit:
    source_id: str
    content: str
    score: float
    source: str
    metadata: dict[str, Any]


class TextbookVectorRetriever:
    """Search the existing textbook FAISS micro-indexes with one query vector."""

    def __init__(
        self,
        embedding_model: EmbeddingModel,
        vector_store_root: Path,
        *,
        embedding_model_name: str,
        similarity_threshold: float = 0.55,
    ) -> None:
        self.embedding_model = embedding_model
        self.vector_store_root = Path(vector_store_root)
        self.embedding_model_name = embedding_model_name
        self.similarity_threshold = similarity_threshold
        self._indexes: list[tuple[Path, Any, list[dict[str, Any]]]] | None = None

    async def search(self, query: str, limit: int = 5) -> list[TextbookVectorHit]:
        vectors = await self.embedding_model.embed([query])
        if not vectors:
            raise RuntimeError("embedding model returned no query vector")
        query_vector = np.asarray(vectors, dtype="float32")
        indexes = self._load_indexes()
        if not indexes:
            raise RuntimeError(f"no textbook vector indexes found: {self.vector_store_root}")
        hits: list[TextbookVectorHit] = []
        for directory, index, metadata in indexes:
            if query_vector.shape[1] != index.d:
                raise ValueError(
                    f"embedding dimension {query_vector.shape[1]} does not match index dimension {index.d}"
                )
            faiss = self._faiss()
            normalized = query_vector.copy()
            faiss.normalize_L2(normalized)
            k = min(max(limit, 1), index.ntotal)
            scores, positions = index.search(normalized, k)
            for score, position in zip(scores[0], positions[0]):
                if position < 0 or position >= len(metadata) or float(score) < self.similarity_threshold:
                    continue
                item = metadata[position]
                content = str(item.get("content") or item.get("original", {}).get("text") or "").strip()
                if not content:
                    continue
                original = item.get("original") if isinstance(item.get("original"), dict) else {}
                chunk_id = str(original.get("chunk_id") or item.get("chunk_id") or position)
                book = str(original.get("metadata", {}).get("book") or item.get("source") or directory.name)
                hits.append(
                    TextbookVectorHit(
                        source_id=f"{book}:{chunk_id}",
                        content=content,
                        score=float(score),
                        source=str(item.get("source") or directory.name),
                        metadata={
                            "index": directory.name,
                            "embedding_model": self.embedding_model_name,
                            "record_id": item.get("record_id"),
                        },
                    )
                )
        deduplicated: dict[str, TextbookVectorHit] = {}
        for hit in sorted(hits, key=lambda item: item.score, reverse=True):
            deduplicated.setdefault(hit.source_id, hit)
        return list(deduplicated.values())[:limit]

    def _load_indexes(self) -> list[tuple[Path, Any, list[dict[str, Any]]]]:
        if self._indexes is not None:
            return self._indexes
        root = self.vector_store_root / "indexes"
        loaded: list[tuple[Path, Any, list[dict[str, Any]]]] = []
        if not root.is_dir():
            self._indexes = loaded
            return loaded
        faiss = self._faiss()
        for directory in sorted(root.iterdir()):
            if not directory.is_dir() or directory.name == "题库":
                continue
            index_path = directory / "index.faiss"
            metadata_path = directory / "metadata.jsonl"
            if not index_path.is_file() or not metadata_path.is_file():
                continue
            index = faiss.read_index(str(index_path))
            metadata = [
                json.loads(line)
                for line in metadata_path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            if len(metadata) < index.ntotal:
                raise ValueError(f"textbook vector metadata is incomplete: {directory}")
            loaded.append((directory, index, metadata))
        self._indexes = loaded
        return loaded

    @staticmethod
    def _faiss():
        try:
            import faiss
        except ImportError as exc:
            raise RuntimeError("faiss is required for textbook vector retrieval") from exc
        return faiss
