from __future__ import annotations

"""Validate D1 AB100 V2 structure, construct contract and frozen hashes."""

import hashlib
import json

from evaluation.evolution.build_d1_ab100_v2_dataset import (
    CSV_DATASET,
    DATASET,
    MANIFEST,
    REVIEW_DATASET,
    _load_jsonl,
    validate,
)


def _sha256(path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    rows = _load_jsonl(DATASET)
    validate(rows)
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    expected_files = {
        DATASET.name: _sha256(DATASET),
        CSV_DATASET.name: _sha256(CSV_DATASET),
        REVIEW_DATASET.name: _sha256(REVIEW_DATASET),
    }
    if manifest.get("files") != expected_files:
        raise ValueError("manifest hashes do not match frozen files")
    if manifest.get("formal_environment_write_allowed") is not False:
        raise ValueError("formal writeback must remain disabled")
    if manifest.get("gold_relations") != {
        "contradiction": 60,
        "not_applicable": 30,
        "compatible": 10,
    }:
        raise ValueError("unexpected gold relation distribution")
    if manifest.get("rule_exposure_scope") != {"exposed": 70, "not_exposed": 30}:
        raise ValueError("unexpected rule exposure scope")
    print(json.dumps({
        "dataset_valid": True,
        "construct_contract_valid": True,
        "case_count": len(rows),
        "paired_model_run_count": len(rows) * 2,
        "target_conflicts": 60,
        "compatible_boundary_controls": 10,
        "scope_controls": 30,
        "dataset_sha256": expected_files[DATASET.name],
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
