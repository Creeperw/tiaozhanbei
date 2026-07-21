"""Deterministic JSONL helpers shared by the official-exam build pipeline."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Iterable, Iterator


def iter_jsonl(path: Path) -> Iterator[dict[str, Any]]:
    """Yield non-empty JSON objects from *path* in file order."""

    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            value = line.strip()
            if not value:
                continue
            item = json.loads(value)
            if not isinstance(item, dict):
                raise ValueError(f"{path}:{line_number}: JSONL row must be an object")
            yield item


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> int:
    """Write stable UTF-8 JSONL and return the number of rows written."""

    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            if not isinstance(row, dict):
                raise TypeError(f"JSONL row must be an object, got {type(row)!r}")
            handle.write(
                json.dumps(
                    row,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
            )
            handle.write("\n")
            count += 1
    return count


def file_sha256(path: Path, block_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while block := handle.read(block_size):
            digest.update(block)
    return digest.hexdigest()


def stable_id(prefix: str, *parts: Any, length: int = 20) -> str:
    """Return a deterministic identifier from typed, separator-safe parts."""

    encoded = json.dumps(parts, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return f"{prefix}_{hashlib.sha256(encoded).hexdigest()[:length]}"


def describe_jsonl(path: Path) -> dict[str, Any]:
    return {
        "path": path.name,
        "rows": sum(1 for _ in iter_jsonl(path)),
        "bytes": path.stat().st_size,
        "sha256": file_sha256(path),
    }
