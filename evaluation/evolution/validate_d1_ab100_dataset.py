from __future__ import annotations

"""Validate the frozen lineage and file integrity of the D1 AB100 dataset."""

import hashlib
import json

from evaluation.evolution.build_d1_ab100_dataset import (
    CSV_DATASET,
    DATASET,
    LEGACY_DATASET,
    MANIFEST,
    _load_jsonl,
    validate,
)


FROZEN_AB50_SHA256 = "e98471195ef6513806e9eace51e92fcee2e6c76d44ad18549a1c1fee4b13f531"


def _sha256(path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    legacy = _load_jsonl(LEGACY_DATASET)
    rows = _load_jsonl(DATASET)
    validate(rows, legacy)

    if _sha256(LEGACY_DATASET) != FROZEN_AB50_SHA256:
        raise ValueError("the frozen AB50 source digest changed")
    if not CSV_DATASET.is_file() or not MANIFEST.is_file():
        raise ValueError("AB100 CSV or manifest is missing")

    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    if manifest.get("case_count") != 100:
        raise ValueError("manifest case count is invalid")
    if manifest.get("paired_model_run_count") != 200:
        raise ValueError("manifest paired model run count is invalid")
    if manifest.get("lineage", {}).get("base_sha256") != FROZEN_AB50_SHA256:
        raise ValueError("manifest AB50 lineage digest is invalid")
    if manifest.get("lineage", {}).get("base_rows_unchanged") is not True:
        raise ValueError("manifest must declare unchanged AB50 base rows")
    if manifest.get("formal_environment_write_allowed") is not False:
        raise ValueError("manifest must disable formal environment writeback")

    expected_hashes = {
        DATASET.name: _sha256(DATASET),
        CSV_DATASET.name: _sha256(CSV_DATASET),
    }
    if manifest.get("files") != expected_hashes:
        raise ValueError("manifest file digests do not match generated files")

    print(
        json.dumps(
            {
                "dataset_valid": True,
                "case_count": 100,
                "base_case_count": 50,
                "extension_case_count": 50,
                "paired_model_run_count": 200,
                "dataset_sha256": expected_hashes[DATASET.name],
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
