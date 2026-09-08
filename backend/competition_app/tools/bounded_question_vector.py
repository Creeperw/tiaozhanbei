"""Bounded-memory exact search of the shipped FAISS IndexFlatIP question bank.

FAISS's flat-index read_index loads the entire vector array; IO_FLAG_MMAP does
not make IndexFlatIP disk backed. Read its validated little-endian flat format
in bounded blocks instead. Other formats fail explicitly, never get guessed.
"""
from __future__ import annotations

import json
import struct
from pathlib import Path

import numpy as np


def flat_ip_topk(path: Path, vector: np.ndarray, top_k: int, *, block_rows: int = 1024):
    if top_k < 1 or block_rows < 1:
        raise ValueError("top_k and block_rows must be positive")
    query = np.asarray(vector, dtype=np.float32).reshape(-1)
    if not np.isfinite(query).all():
        raise ValueError("query vector must be finite")
    with Path(path).open("rb") as handle:
        header = handle.read(45)
        if len(header) != 45:
            raise RuntimeError("incomplete FAISS flat index header")
        magic, dimension, count, _, _, trained, metric, values = struct.unpack("<4siqqq?iQ", header)
        if magic != b"IxFI" or metric != 0 or not trained or dimension <= 0 or count < 0:
            raise RuntimeError("bounded question search requires a trained IndexFlatIP")
        if values != count * dimension or Path(path).stat().st_size != 45 + values * 4:
            raise RuntimeError("invalid FAISS flat index size")
        if query.size != dimension:
            raise ValueError(f"embedding dimension {query.size} does not match index dimension {dimension}")
        best_scores = np.empty(0, dtype=np.float32)
        best_ids = np.empty(0, dtype=np.int64)
        for start in range(0, count, block_rows):
            rows = min(block_rows, count - start)
            block = np.fromfile(handle, dtype="<f4", count=rows * dimension)
            if block.size != rows * dimension:
                raise RuntimeError("FAISS index changed or was truncated during search")
            scores = block.reshape(rows, dimension) @ query
            if not np.isfinite(scores).all():
                raise RuntimeError("FAISS index contains non-finite vectors")
            ids = np.arange(start, start + rows, dtype=np.int64)
            scores = np.concatenate((best_scores, scores))
            ids = np.concatenate((best_ids, ids))
            # FAISS retains the first IDs at a tied cutoff, then emits ties
            # in descending ID order from its result heap.
            order = np.lexsort((ids, -scores))[:top_k]
            best_scores, best_ids = scores[order], ids[order]
        order = np.lexsort((-best_ids, -best_scores))
        best_scores, best_ids = best_scores[order], best_ids[order]
        return best_scores, best_ids


def vector_question_hits(vdb_dir, query, embedder, top_k, query_vector=None):
    directory = Path(vdb_dir) / "indexes" / "题库"
    vector = query_vector if query_vector is not None else embedder.embed(query)
    scores, positions = flat_ip_topk(directory / "index.faiss", vector, top_k)
    wanted = set(map(int, positions))
    if not wanted:
        return []
    metadata = {}
    with (directory / "metadata.jsonl").open(encoding="utf-8-sig") as handle:
        position = 0
        for line in handle:
            if not line.strip():
                continue
            if position in wanted:
                metadata[position] = json.loads(line)
                if len(metadata) == len(wanted):
                    break
            position += 1
    if set(metadata) != wanted:
        raise RuntimeError("question vector metadata is incomplete")
    result = []
    for score, position in zip(scores, positions):
        row = metadata[int(position)]
        original = row.get("original") or {}
        qid = str(original.get("题目id") or original.get("question_id") or row.get("question_id") or "")
        if qid:
            result.append((qid, float(score)))
    return result