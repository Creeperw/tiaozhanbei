#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import shutil
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
DIRECTORY_NAMES = {"__pycache__", ".pytest_cache", ".ruff_cache"}
PRUNED_NAMES = {".git", "node_modules", "dist", "assets", "runtime", "vdb_store"}
EXPLICIT_GENERATED = (
    Path("frontend/llm/test-results"),
    Path("frontend/llm/coverage"),
)


def candidates() -> list[Path]:
    found: list[Path] = []
    for root, directories, _ in os.walk(REPOSITORY_ROOT, topdown=True, followlinks=False):
        current = Path(root)
        removable = [name for name in directories if name in DIRECTORY_NAMES]
        found.extend(current / name for name in removable)
        directories[:] = [
            name
            for name in directories
            if name not in DIRECTORY_NAMES and name not in PRUNED_NAMES
        ]
    found.extend(
        REPOSITORY_ROOT / relative
        for relative in EXPLICIT_GENERATED
        if (REPOSITORY_ROOT / relative).exists()
    )
    return sorted(set(found), key=lambda path: (len(path.parts), str(path)), reverse=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="Remove reproducible local caches only. Dry-run by default.")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    paths = candidates()
    for path in paths:
        print(path.relative_to(REPOSITORY_ROOT))
        if args.apply:
            shutil.rmtree(path, ignore_errors=False)
    print(f"{'removed' if args.apply else 'would remove'} {len(paths)} directories")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
