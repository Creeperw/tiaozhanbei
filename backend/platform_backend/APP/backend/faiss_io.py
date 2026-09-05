from __future__ import annotations

from pathlib import Path
from typing import Any


def _faiss():
    import faiss

    return faiss


def read_faiss_index(path: str | Path) -> Any:
    """Read a FAISS index through Python IO so Windows Unicode paths work."""

    index_path = Path(path)
    faiss = _faiss()
    with index_path.open("rb") as handle:
        reader = faiss.PyCallbackIOReader(handle.read)
        reader.name = str(index_path)
        return faiss.read_index(reader)


def write_faiss_index(index: Any, path: str | Path) -> None:
    """Write a FAISS index through Python IO so Windows Unicode paths work."""

    index_path = Path(path)
    index_path.parent.mkdir(parents=True, exist_ok=True)
    faiss = _faiss()
    with index_path.open("wb") as handle:
        writer = faiss.PyCallbackIOWriter(handle.write)
        writer.name = str(index_path)
        faiss.write_index(index, writer)
