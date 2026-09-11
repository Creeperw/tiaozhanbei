"""Verify legacy public vectors before explicitly restoring missing manifests.

Sampling establishes measured compatibility, not historical build provenance.
No index/metadata content is changed; existing manifests are never overwritten.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path

import numpy as np
import requests

from APP.backend.rag_disk import DiskVectorDatabase


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def inspect_collection(directory, dimensions):
    database = DiskVectorDatabase(str(directory / "index.faiss"), str(directory / "metadata.jsonl"))
    index = database.index
    if index.d != dimensions or not index.ntotal:
        raise ValueError(f"invalid dimension/count: {directory.name}")
    with index.path.open("rb") as handle:
        handle.seek(45)
        for start in range(0, index.ntotal, 1024):
            count = min(1024, index.ntotal - start)
            block = np.fromfile(handle, dtype="<f4", count=count * index.d).reshape(count, index.d)
            norms = np.linalg.norm(block, axis=1)
            if not np.isfinite(block).all() or np.any(np.abs(norms - 1) > 5e-4):
                raise ValueError(f"non-normalized/non-finite vectors: {directory.name}")
    # Validate every metadata record, not just the probe positions.
    with database.metadata.path.open(encoding="utf-8-sig") as handle:
        for line in handle:
            if line.strip() and not isinstance(json.loads(line), dict):
                raise ValueError(f"invalid metadata: {directory.name}")
    samples = []
    for position in sorted({0, index.ntotal // 2, index.ntotal - 1}):
        metadata = database.metadata[position]
        text = metadata.get("content")
        if not isinstance(text, str) or not text.strip():
            raise ValueError(f"missing exact embedding text: {directory.name}:{position}")
        samples.append((position, text, index.reconstruct(position)))
    index._check()
    database.metadata._check()
    return {
        "dimensions": index.d, "vector_count": index.ntotal, "normalized": True,
        "index_sha256": sha256(index.path), "metadata_sha256": sha256(database.metadata.path),
    }, samples


def verify(index_root, *, model, dimensions, encode):
    reports = {}
    directories = sorted(p.parent for p in Path(index_root).glob("*/index.faiss"))
    if not directories:
        raise ValueError("no indexes found")
    for directory in directories:
        report, samples = inspect_collection(directory, dimensions)
        vectors = np.asarray(encode([sample[1] for sample in samples]), dtype="float32")
        if vectors.shape != (len(samples), dimensions) or not np.isfinite(vectors).all():
            raise ValueError("invalid embedding response shape/values")
        probes = []
        for (position, text, stored), current in zip(samples, vectors):
            norm = float(np.linalg.norm(current))
            if norm <= 0:
                raise ValueError("zero runtime vector")
            cosine = float(np.dot(stored / np.linalg.norm(stored), current / norm))
            if cosine < 0.999:
                raise ValueError(f"model compatibility failed: {directory.name}:{position} cosine={cosine}")
            probes.append({"position": position, "text_sha256": hashlib.sha256(text.encode()).hexdigest(), "cosine": cosine})
        reports[directory.name] = {
            "schema_version": 1, "embedding_model": model, **report,
            "verification": {"method": "full-integrity-and-sampled-runtime-reembedding", "minimum_cosine": 0.999, "samples": probes},
        }
        print(json.dumps({"verified": directory.name, "count": report["vector_count"], "min_cosine": min(p["cosine"] for p in probes)}), flush=True)
    return reports


def write_missing(index_root, reports):
    root = Path(index_root)
    # Preflight all collections before publishing any manifest.
    for name, report in reports.items():
        directory = root / name
        if (directory / "index_manifest.json").exists():
            raise ValueError(f"manifest already exists: {name}")
        if sha256(directory / "index.faiss") != report["index_sha256"] or sha256(directory / "metadata.jsonl") != report["metadata_sha256"]:
            raise ValueError(f"asset changed after verification: {name}")
    written = []
    try:
        for name, report in reports.items():
            path = root / name / "index_manifest.json"
            with path.open("x", encoding="utf-8") as handle:
                written.append(path)
                json.dump(report, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
    except Exception:
        for path in written:
            path.unlink()
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--index-root", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--model", default="Qwen/Qwen3-Embedding-4B")
    parser.add_argument("--dimensions", type=int, default=2560)
    parser.add_argument("--write-manifests", action="store_true")
    args = parser.parse_args()
    def encode(texts):
        response = requests.post(
            os.environ["EMBEDDING_API_BASE_URL"].rstrip("/") + "/embeddings",
            headers={"Authorization": "Bearer " + os.environ["EMBEDDING_API_KEY"]},
            json={"model": args.model, "input": texts}, timeout=120,
        )
        if response.status_code != 200:
            raise RuntimeError(f"Embedding HTTP {response.status_code}")
        rows = sorted(response.json()["data"], key=lambda row: row["index"])
        if [row["index"] for row in rows] != list(range(len(texts))):
            raise ValueError("invalid embedding response indexes")
        return [row["embedding"] for row in rows]
    reports = verify(args.index_root, model=args.model, dimensions=args.dimensions, encode=encode)
    with args.report.open("x", encoding="utf-8") as handle:
        json.dump(reports, handle, ensure_ascii=False, indent=2)
    if args.write_manifests:
        write_missing(args.index_root, reports)


if __name__ == "__main__":
    main()