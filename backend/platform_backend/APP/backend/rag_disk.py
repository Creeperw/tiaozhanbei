"""Read-only, bounded-memory access to validated flat public indexes."""
from __future__ import annotations

from array import array
import json
from pathlib import Path
import struct
import threading

import numpy as np

from competition_app.tools.bounded_question_vector import flat_ip_topk


_SEARCH_LOCK = threading.Lock()


def flat_header(path: Path) -> tuple[int, int]:
    with path.open("rb") as handle:
        header = handle.read(45)
    if len(header) != 45:
        raise RuntimeError("incomplete FAISS flat index header")
    magic, dimension, count, _, _, trained, metric, values = struct.unpack("<4siqqq?iQ", header)
    if magic != b"IxFI" or metric != 0 or not trained or dimension <= 0 or count < 0:
        raise RuntimeError("disk retrieval requires a trained IndexFlatIP")
    if values != dimension * count or path.stat().st_size != 45 + 4 * values:
        raise RuntimeError("invalid FAISS flat index size")
    return dimension, count


def _signature(path: Path) -> tuple[int, int, int]:
    stat = path.stat()
    return stat.st_ino, stat.st_size, stat.st_mtime_ns


class DiskMetadata:
    def __init__(self, path: Path):
        self.path = path
        self.signature = _signature(path)
        self.offsets = array("Q")
        with path.open("rb") as handle:
            while True:
                offset = handle.tell()
                line = handle.readline()
                if not line:
                    break
                if line.strip():
                    self.offsets.append(offset)
        self._check()

    def _check(self):
        if _signature(self.path) != self.signature:
            raise RuntimeError("metadata changed; reload the collection")

    def __len__(self):
        return len(self.offsets)

    def __getitem__(self, position):
        self._check()
        offset = self.offsets[position]
        with self.path.open("rb") as handle:
            handle.seek(offset)
            result = json.loads(handle.readline().decode("utf-8-sig"))
        self._check()
        if not isinstance(result, dict):
            raise RuntimeError("metadata record must be an object")
        return result


class DiskFlatIndex:
    def __init__(self, path: Path):
        self.path = path
        self.signature = _signature(path)
        self.d, self.ntotal = flat_header(path)

    def _check(self):
        if _signature(self.path) != self.signature:
            raise RuntimeError("index changed; reload the collection")

    def reconstruct(self, position: int):
        self._check()
        if not 0 <= position < self.ntotal:
            raise IndexError(position)
        with self.path.open("rb") as handle:
            handle.seek(45 + int(position) * self.d * 4)
            vector = np.fromfile(handle, dtype="<f4", count=self.d)
        self._check()
        return vector

    def search(self, queries, k):
        queries = np.asarray(queries, dtype="float32")
        if queries.ndim != 2 or queries.shape[1] != self.d or k < 1:
            raise ValueError("invalid query shape or top-k")
        scores = np.full((len(queries), k), -np.inf, dtype="float32")
        positions = np.full((len(queries), k), -1, dtype="int64")
        with _SEARCH_LOCK:
            self._check()
            for row, query in enumerate(queries):
                values, ids = flat_ip_topk(self.path, query, k)
                scores[row, :len(values)] = values
                positions[row, :len(ids)] = ids
            self._check()
        return scores, positions


class DiskVectorDatabase:
    def __init__(self, index_path: str, metadata_path: str):
        self.index = DiskFlatIndex(Path(index_path))
        self.metadata = DiskMetadata(Path(metadata_path))
        if self.index.ntotal != len(self.metadata):
            raise RuntimeError("vector/metadata count mismatch")