"""Semantic retrieval for the active Atlas question index.

Document RAG intentionally returns chunks.  This service is the only runtime
adapter that turns question-index hits into the complete question contract.
"""

from __future__ import annotations

from typing import Any, Iterable

import numpy as np

from APP.backend.question_index_v2_service import active_question_index_name
from APP.backend.rag_core import RAGUnavailableError, rag_service
from APP.backend.rag_text import Config


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _string_list(value: Any) -> list[str]:
    if isinstance(value, (list, tuple, set)):
        return [_text(item) for item in value if _text(item)]
    return [_text(value)] if _text(value) else []


class QuestionIndexSearchService:
    def __init__(
        self,
        *,
        rag: Any | None = None,
        active_collection: str | None = None,
    ) -> None:
        self.rag = rag if rag is not None else rag_service
        self._active_collection = active_collection

    def _collection_name(self) -> str:
        if self._active_collection:
            return self._active_collection
        return active_question_index_name(Config.PUBLIC_INDEX_DIR)

    def status(self) -> dict[str, Any]:
        state = _text(getattr(self.rag, "embedding_state", "unavailable")) or "unavailable"
        refresh = getattr(self.rag, "ensure_active_question_db", None)
        refresh_error = None
        if state == "ready" and callable(refresh):
            try:
                refresh()
            except RAGUnavailableError as exc:
                refresh_error = exc.message
        collection = self._collection_name()
        database = getattr(self.rag, "dbs", {}).get(collection)
        available = (
            state == "ready"
            and getattr(self.rag, "model", None) is not None
            and database is not None
            and getattr(database, "index", None) is not None
        )
        return {
            "state": state if available or state != "ready" else "unavailable",
            "error": refresh_error or getattr(self.rag, "embedding_error", None),
            "collection": collection,
            "available": available,
            "count": int(getattr(getattr(database, "index", None), "ntotal", 0) or 0),
        }

    def _require_ready(self) -> tuple[str, Any]:
        state = _text(getattr(self.rag, "embedding_state", "unavailable")) or "unavailable"
        model = getattr(self.rag, "model", None)
        if state != "ready" or model is None:
            raise RAGUnavailableError(
                state=state,
                message=_text(getattr(self.rag, "embedding_error", ""))
                or "question embedding model is unavailable",
            )
        refresh = getattr(self.rag, "ensure_active_question_db", None)
        if callable(refresh):
            refresh()
        collection = self._collection_name()
        database = getattr(self.rag, "dbs", {}).get(collection)
        index = getattr(database, "index", None) if database is not None else None
        if database is None or index is None:
            raise RAGUnavailableError(
                state="unavailable",
                message=f"active question index is not loaded: {collection}",
            )
        if int(getattr(index, "ntotal", 0) or 0) != len(getattr(database, "metadata", [])):
            raise RAGUnavailableError(
                state="unavailable",
                message=f"active question index metadata count mismatch: {collection}",
            )
        return collection, database

    def search(
        self,
        query: str,
        *,
        kp_ids: Iterable[str] = (),
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        query = _text(query)
        if not query:
            raise ValueError("semantic question search requires a non-blank query")
        _, database = self._require_ready()
        targets = {_text(value) for value in kp_ids if _text(value)}
        requested_limit = max(1, min(100, int(limit)))

        vector = np.asarray(
            self.rag.model.encode([query], convert_to_numpy=True),
            dtype="float32",
        )
        if vector.ndim != 2 or vector.shape[0] != 1:
            raise RAGUnavailableError(
                state="unavailable",
                message=f"embedding model returned invalid query shape: {vector.shape}",
            )
        expected_dimension = int(getattr(database.index, "d", vector.shape[1]))
        if int(vector.shape[1]) != expected_dimension:
            raise RAGUnavailableError(
                state="unavailable",
                message=(
                    "query embedding dimension mismatch: "
                    f"expected {expected_dimension}, got {vector.shape[1]}"
                ),
            )
        norms = np.linalg.norm(vector, axis=1, keepdims=True)
        if not np.all(np.isfinite(norms)) or np.any(norms <= 0):
            raise RAGUnavailableError(
                state="unavailable",
                message="embedding model returned a zero or non-finite query vector",
            )
        vector = vector / norms

        multiplier = 20 if targets else 4
        search_limit = min(
            int(database.index.ntotal),
            max(requested_limit, requested_limit * multiplier),
        )
        scores, indices = database.index.search(vector, search_limit)
        results: list[dict[str, Any]] = []
        for score, index_position in zip(scores[0], indices[0]):
            position = int(index_position)
            if position < 0 or position >= len(database.metadata):
                continue
            metadata = database.metadata[position]
            atlas = metadata.get("atlas") if isinstance(metadata, dict) else None
            if not isinstance(atlas, dict):
                continue
            question_id = _text(atlas.get("question_id"))
            kp_values = _string_list(atlas.get("kp_ids"))
            if not question_id or (targets and not targets.intersection(kp_values)):
                continue
            channels = list(dict.fromkeys([
                *_string_list(atlas.get("channels")),
                "semantic_search",
            ]))
            original = metadata.get("original") if isinstance(metadata.get("original"), dict) else {}
            results.append({
                "question_id": question_id,
                "stem": _text(atlas.get("stem")),
                "options": atlas.get("options") or [],
                "answer": atlas.get("answer") if atlas.get("answer") is not None else [],
                "explanation": _text(atlas.get("explanation")),
                "kp_ids": kp_values,
                "question_type": _text(atlas.get("question_type") or original.get("question_type")),
                "difficulty": float(atlas.get("difficulty") or original.get("difficulty") or 0.0),
                "status": _text(atlas.get("status")) or ("active" if kp_values else "pending_link"),
                "score": float(score),
                "channels": channels,
            })
            if len(results) >= requested_limit:
                break
        return results


question_index_search_service = QuestionIndexSearchService()
