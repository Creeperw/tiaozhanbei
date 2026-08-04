from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

from APP.backend.faiss_io import read_faiss_index, write_faiss_index


ACTIVE_POINTER_FILENAME = ".question-index-active.json"
DEFAULT_QUESTION_COLLECTION = "题库"
V2_QUESTION_COLLECTION = "题库-v2"


class QuestionIndexContractError(RuntimeError):
    """Raised when an index cannot be rebuilt without violating its contract."""


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_json_object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (FileNotFoundError, OSError, json.JSONDecodeError) as exc:
        raise QuestionIndexContractError(f"invalid {label}: {path}") from exc
    if not isinstance(value, dict):
        raise QuestionIndexContractError(f"{label} must be a JSON object: {path}")
    return value


def active_question_index_name(
    index_root: str | Path,
    *,
    pointer_path: str | Path | None = None,
) -> str:
    root = Path(index_root)
    pointer = Path(pointer_path) if pointer_path else root / ACTIVE_POINTER_FILENAME
    try:
        payload = json.loads(pointer.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return DEFAULT_QUESTION_COLLECTION
    collection = payload.get("collection") if isinstance(payload, dict) else None
    if not isinstance(collection, str) or not collection or Path(collection).name != collection:
        return DEFAULT_QUESTION_COLLECTION
    target = root / collection
    required = (
        target / "index.faiss",
        target / "metadata.jsonl",
        target / "index_manifest.json",
    )
    if any(not path.is_file() for path in required):
        return DEFAULT_QUESTION_COLLECTION
    try:
        manifest = _read_json_object(target / "index_manifest.json", "index manifest")
    except QuestionIndexContractError:
        return DEFAULT_QUESTION_COLLECTION
    expected_hash = payload.get("manifest_sha256") if isinstance(payload, dict) else None
    if expected_hash and _sha256_file(target / "index_manifest.json") != expected_hash:
        return DEFAULT_QUESTION_COLLECTION
    pointer_count = payload.get("vector_count") if isinstance(payload, dict) else None
    manifest_count = manifest.get("vector_count") or manifest.get("count") or manifest.get("total")
    if pointer_count is not None and manifest_count is not None:
        try:
            counts_match = int(pointer_count) == int(manifest_count)
        except (TypeError, ValueError):
            return DEFAULT_QUESTION_COLLECTION
        if not counts_match:
            return DEFAULT_QUESTION_COLLECTION
    return collection


def _validate_question_collection(
    target: Path,
    *,
    expected_model: str | None,
    expected_dimensions: int | None,
    require_normalized: bool | None,
) -> dict[str, Any]:
    required = (target / "index.faiss", target / "metadata.jsonl", target / "index_manifest.json")
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        raise QuestionIndexContractError(
            f"question collection is incomplete: {target.name}; missing={missing}"
        )
    manifest = _read_json_object(target / "index_manifest.json", "index manifest")
    manifest_model = str(manifest.get("embedding_model") or manifest.get("model_id") or "")
    manifest_dimensions = int(manifest.get("dimensions") or 0)
    if expected_model is not None and manifest_model != expected_model:
        raise QuestionIndexContractError(
            f"question collection embedding model mismatch: expected {expected_model}, got {manifest_model}"
        )
    if expected_dimensions is not None and manifest_dimensions != int(expected_dimensions):
        raise QuestionIndexContractError(
            "question collection dimension mismatch: "
            f"expected {expected_dimensions}, got {manifest_dimensions}"
        )
    if require_normalized is True and manifest.get("normalized") is not True:
        raise QuestionIndexContractError("question collection vectors are not declared normalized")

    index = read_faiss_index(target / "index.faiss")
    metadata_count = 0
    with (target / "metadata.jsonl").open("rb") as handle:
        metadata_count = sum(1 for line in handle if line.strip())
    index_count = int(index.ntotal)
    declared_count = manifest.get("vector_count") or manifest.get("count") or manifest.get("total")
    if metadata_count != index_count:
        raise QuestionIndexContractError(
            f"question collection count mismatch: FAISS={index_count}, metadata={metadata_count}"
        )
    if declared_count is not None and int(declared_count) != index_count:
        raise QuestionIndexContractError(
            "question collection manifest count mismatch: "
            f"manifest={declared_count}, FAISS={index_count}, metadata={metadata_count}"
        )
    if manifest_dimensions and int(index.d) != manifest_dimensions:
        raise QuestionIndexContractError(
            f"question collection FAISS dimension mismatch: manifest={manifest_dimensions}, FAISS={index.d}"
        )
    if require_normalized is True and index_count:
        sample_positions = sorted({
            0,
            index_count // 4,
            index_count // 2,
            (index_count * 3) // 4,
            index_count - 1,
        })
        for position in sample_positions:
            vector = np.asarray(index.reconstruct(int(position)), dtype="float32")
            norm = float(np.linalg.norm(vector))
            if not np.isfinite(norm) or abs(norm - 1.0) > 5e-4:
                raise QuestionIndexContractError(
                    f"question collection vector is not normalized at position {position}: norm={norm}"
                )
    return {**manifest, "validated_vector_count": index_count}


def switch_active_question_index(
    *,
    index_root: str | Path,
    collection: str,
    pointer_path: str | Path | None = None,
    expected_model: str | None = None,
    expected_dimensions: int | None = None,
    require_normalized: bool | None = None,
) -> dict[str, Any]:
    root = Path(index_root)
    if not collection or Path(collection).name != collection:
        raise QuestionIndexContractError(f"invalid question collection: {collection!r}")
    target = root / collection
    manifest = _validate_question_collection(
        target,
        expected_model=expected_model,
        expected_dimensions=expected_dimensions,
        require_normalized=require_normalized,
    )
    pointer = Path(pointer_path) if pointer_path else root / ACTIVE_POINTER_FILENAME
    pointer.parent.mkdir(parents=True, exist_ok=True)
    previous = active_question_index_name(root, pointer_path=pointer)
    payload = {
        "schema_version": 1,
        "collection": collection,
        "previous_collection": previous,
        "manifest_sha256": _sha256_file(target / "index_manifest.json"),
        "activated_at": datetime.now(timezone.utc).isoformat(),
        "vector_count": manifest["validated_vector_count"],
    }
    temporary = pointer.with_name(f"{pointer.name}.tmp-{os.getpid()}")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(pointer)
    return payload


def _atlas_questions(path: Path) -> tuple[dict[str, dict[str, Any]], str]:
    atlas_sha256 = _sha256_file(path)
    try:
        records = json.loads(path.read_text(encoding="utf-8-sig"))
    except (FileNotFoundError, OSError, json.JSONDecodeError) as exc:
        raise QuestionIndexContractError(f"invalid Atlas question bank: {path}") from exc
    if not isinstance(records, list):
        raise QuestionIndexContractError(f"Atlas question bank must be a JSON array: {path}")
    by_id: dict[str, dict[str, Any]] = {}
    for record in records:
        if not isinstance(record, dict):
            continue
        question_id = str(record.get("question_id") or "").strip()
        if not question_id:
            raise QuestionIndexContractError("Atlas question is missing question_id")
        if question_id in by_id:
            raise QuestionIndexContractError(f"duplicate Atlas question_id: {question_id}")
        by_id[question_id] = record
    if not by_id:
        raise QuestionIndexContractError("Atlas question bank is empty")
    return by_id, atlas_sha256


def _question_id(metadata: dict[str, Any]) -> str:
    original = metadata.get("original")
    if not isinstance(original, dict):
        return ""
    return str(original.get("题目id") or original.get("question_id") or "").strip()


def _enrich_metadata(metadata: dict[str, Any], atlas: dict[str, Any], asset_version: str) -> dict[str, Any]:
    output = dict(metadata)
    original = dict(output.get("original") or {})
    kp_ids = [str(item).strip() for item in (atlas.get("kp_ids") or []) if str(item).strip()]
    original["kp_ids"] = kp_ids
    original["knowledge_atlas_version"] = asset_version
    output["original"] = original
    output["atlas"] = {
        "asset_version": asset_version,
        "question_id": str(atlas.get("question_id") or ""),
        "stem": str(atlas.get("question_content") or atlas.get("stem") or ""),
        "options": atlas.get("options") or [],
        "answer": atlas.get("answer") or [],
        "explanation": str(atlas.get("explanation") or ""),
        "kp_ids": kp_ids,
        "status": "active" if kp_ids else "pending_link",
        "channels": ["question_index_v2", "knowledge_atlas"],
    }
    return output


def _existing_v2_report(
    target_dir: Path,
    *,
    atlas_sha256: str,
    source_index_sha256: str,
    asset_version: str,
) -> dict[str, Any] | None:
    if not target_dir.exists():
        return None
    if (target_dir / ".building").exists():
        raise QuestionIndexContractError(f"incomplete v2 build already exists: {target_dir}")
    manifest = _read_json_object(target_dir / "index_manifest.json", "v2 index manifest")
    expected = {
        "asset_version": asset_version,
        "atlas_questions_sha256": atlas_sha256,
        "source_index_sha256": source_index_sha256,
    }
    if any(manifest.get(key) != value for key, value in expected.items()):
        raise QuestionIndexContractError(f"existing v2 target differs from requested contract: {target_dir}")
    for filename in ("index.faiss", "metadata.jsonl"):
        if not (target_dir / filename).is_file():
            raise QuestionIndexContractError(f"existing v2 target is incomplete: {target_dir / filename}")
    return {
        "vector_count": int(manifest.get("vector_count") or 0),
        "excluded_source_count": int(manifest.get("excluded_source_count") or 0),
        "linked_count": int(manifest.get("linked_count") or 0),
        "pending_link_count": int(manifest.get("pending_link_count") or 0),
        "target": str(target_dir),
        "skipped": True,
    }


def build_question_index_v2(
    *,
    source_dir: str | Path,
    atlas_questions_path: str | Path,
    target_dir: str | Path,
    active_pointer_path: str | Path,
    expected_model: str,
    expected_dimensions: int,
    asset_version: str,
    activate: bool = True,
    batch_size: int = 512,
) -> dict[str, Any]:
    """Rebuild v2 from Atlas truth while reusing compatible normalized v1 vectors."""

    source = Path(source_dir)
    target = Path(target_dir)
    atlas_path = Path(atlas_questions_path)
    pointer = Path(active_pointer_path)
    source_manifest = _read_json_object(source / "index_manifest.json", "source index manifest")
    if source_manifest.get("embedding_model") != expected_model:
        raise QuestionIndexContractError(
            f"embedding model mismatch: expected {expected_model!r}, "
            f"got {source_manifest.get('embedding_model')!r}"
        )
    if int(source_manifest.get("dimensions") or 0) != int(expected_dimensions):
        raise QuestionIndexContractError(
            f"embedding dimensions mismatch: expected {expected_dimensions}, "
            f"got {source_manifest.get('dimensions')!r}"
        )
    if source_manifest.get("normalized") is not True:
        raise QuestionIndexContractError("source vectors are not declared normalized")

    source_index_path = source / "index.faiss"
    source_metadata_path = source / "metadata.jsonl"
    if not source_index_path.is_file() or not source_metadata_path.is_file():
        raise QuestionIndexContractError(f"source question index is incomplete: {source}")
    atlas_by_id, atlas_sha256 = _atlas_questions(atlas_path)
    source_index_sha256 = _sha256_file(source_index_path)

    existing = _existing_v2_report(
        target,
        atlas_sha256=atlas_sha256,
        source_index_sha256=source_index_sha256,
        asset_version=asset_version,
    )
    if existing is not None:
        if activate:
            existing["activation"] = switch_active_question_index(
                index_root=target.parent,
                collection=target.name,
                pointer_path=pointer,
                expected_model=expected_model,
                expected_dimensions=expected_dimensions,
                require_normalized=True,
            )
        return existing

    source_index = read_faiss_index(source_index_path)
    if int(source_index.d) != int(expected_dimensions):
        raise QuestionIndexContractError(
            f"source FAISS dimension mismatch: expected {expected_dimensions}, got {source_index.d}"
        )
    try:
        import faiss
    except ImportError as exc:  # pragma: no cover - dependency boundary
        raise QuestionIndexContractError("faiss-cpu is required to rebuild question index v2") from exc
    if int(source_index.metric_type) != int(faiss.METRIC_INNER_PRODUCT):
        raise QuestionIndexContractError("source FAISS index must use inner-product similarity")

    selected_positions: list[int] = []
    selected_metadata: list[dict[str, Any]] = []
    seen: set[str] = set()
    metadata_count = 0
    with source_metadata_path.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            position = metadata_count
            try:
                metadata = json.loads(line)
            except json.JSONDecodeError as exc:
                raise QuestionIndexContractError(
                    f"invalid source metadata at non-empty row {metadata_count + 1}"
                ) from exc
            question_id = _question_id(metadata)
            if question_id in atlas_by_id:
                if question_id in seen:
                    raise QuestionIndexContractError(f"duplicate source question_id: {question_id}")
                seen.add(question_id)
                selected_positions.append(position)
                selected_metadata.append(_enrich_metadata(metadata, atlas_by_id[question_id], asset_version))
            metadata_count += 1
    if metadata_count != int(source_index.ntotal):
        raise QuestionIndexContractError(
            f"source metadata/vector count mismatch: {metadata_count} != {source_index.ntotal}"
        )
    missing = set(atlas_by_id) - seen
    if missing:
        sample = sorted(missing)[:5]
        raise QuestionIndexContractError(
            f"Atlas questions missing from source index: count={len(missing)}, sample={sample}"
        )

    target.mkdir(parents=True, exist_ok=False)
    building_marker = target / ".building"
    building_marker.write_text(
        json.dumps({"asset_version": asset_version, "atlas_questions_sha256": atlas_sha256}),
        encoding="utf-8",
    )
    index_staging = target / "index.faiss.staging"
    metadata_staging = target / "metadata.jsonl.staging"
    manifest_staging = target / "index_manifest.json.staging"

    rebuilt = faiss.IndexFlatIP(int(expected_dimensions))
    position_cursor = 0
    minimum_norm = float("inf")
    maximum_norm = 0.0
    for start in range(0, int(source_index.ntotal), max(1, int(batch_size))):
        count = min(max(1, int(batch_size)), int(source_index.ntotal) - start)
        vectors = source_index.reconstruct_n(start, count)
        local_positions: list[int] = []
        while position_cursor < len(selected_positions) and selected_positions[position_cursor] < start + count:
            local_positions.append(selected_positions[position_cursor] - start)
            position_cursor += 1
        if not local_positions:
            continue
        selected = np.asarray(vectors[local_positions], dtype="float32")
        norms = np.linalg.norm(selected, axis=1)
        minimum_norm = min(minimum_norm, float(norms.min()))
        maximum_norm = max(maximum_norm, float(norms.max()))
        if np.any(np.abs(norms - 1.0) > 5e-3):
            raise QuestionIndexContractError(
                f"source vectors violate normalized contract: min={minimum_norm}, max={maximum_norm}"
            )
        rebuilt.add(selected)
    if int(rebuilt.ntotal) != len(atlas_by_id):
        raise QuestionIndexContractError(
            f"rebuilt vector count mismatch: {rebuilt.ntotal} != {len(atlas_by_id)}"
        )

    write_faiss_index(rebuilt, index_staging)
    with metadata_staging.open("w", encoding="utf-8", newline="\n") as handle:
        for metadata in selected_metadata:
            handle.write(json.dumps(metadata, ensure_ascii=False, separators=(",", ":")) + "\n")

    linked_count = sum(bool(record.get("kp_ids")) for record in atlas_by_id.values())
    manifest = {
        "schema_version": 2,
        "collection": target.name,
        "asset_version": asset_version,
        "embedding_model": expected_model,
        "dimensions": int(expected_dimensions),
        "normalized": True,
        "metadata_format": "jsonl",
        "vector_strategy": "reuse-compatible-v1-vectors",
        "vector_count": int(rebuilt.ntotal),
        "source_vector_count": int(source_index.ntotal),
        "excluded_source_count": int(source_index.ntotal) - int(rebuilt.ntotal),
        "linked_count": linked_count,
        "pending_link_count": len(atlas_by_id) - linked_count,
        "atlas_questions_sha256": atlas_sha256,
        "source_index_sha256": source_index_sha256,
        "source_collection": source.name,
        "minimum_vector_norm": minimum_norm,
        "maximum_vector_norm": maximum_norm,
        "built_at": datetime.now(timezone.utc).isoformat(),
    }
    manifest_staging.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    validation_index = read_faiss_index(index_staging)
    if int(validation_index.d) != int(expected_dimensions) or int(validation_index.ntotal) != len(atlas_by_id):
        raise QuestionIndexContractError("staged v2 FAISS validation failed")
    with metadata_staging.open("rb") as handle:
        metadata_lines = sum(1 for line in handle if line.strip())
    if metadata_lines != int(validation_index.ntotal):
        raise QuestionIndexContractError("staged v2 metadata count validation failed")

    index_staging.replace(target / "index.faiss")
    metadata_staging.replace(target / "metadata.jsonl")
    manifest_staging.replace(target / "index_manifest.json")
    building_marker.unlink()

    report: dict[str, Any] = {
        "vector_count": int(rebuilt.ntotal),
        "excluded_source_count": int(source_index.ntotal) - int(rebuilt.ntotal),
        "linked_count": linked_count,
        "pending_link_count": len(atlas_by_id) - linked_count,
        "target": str(target),
        "skipped": False,
    }
    if activate:
        report["activation"] = switch_active_question_index(
            index_root=target.parent,
            collection=target.name,
            pointer_path=pointer,
            expected_model=expected_model,
            expected_dimensions=expected_dimensions,
            require_normalized=True,
        )
    return report
