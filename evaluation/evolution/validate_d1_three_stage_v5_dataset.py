from __future__ import annotations

"""Validate frozen D1 three-stage datasets without regenerating them."""

from collections import Counter
import hashlib
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent
DATASET_DIR = ROOT / "datasets" / "d1_three_stage_v5"
MANIFEST_PATH = DATASET_DIR / "d1_rule_three_stage_v5.manifest.json"
STAGE_FILES = {
    "discovery": "d1_rule_discovery_v5.jsonl",
    "qualification": "d1_rule_qualification_v5.jsonl",
    "final_ab": "d1_rule_final_ab_v5.jsonl",
}
GOLD_FILE = "d1_rule_three_stage_v5.gold.jsonl"
FORBIDDEN_RUNTIME_FIELDS = {
    "gold_relation",
    "gold_rationale",
    "expected_rule_exposure",
    "conflict_binding",
    "repair_instruction",
    "target_rule",
}


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"invalid JSON at {path.name}:{line_number}: {exc}") from exc
            if not isinstance(value, dict):
                raise ValueError(f"row is not an object at {path.name}:{line_number}")
            rows.append(value)
    return rows


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _normalized_text(value: str) -> str:
    return "".join(value.split())


def validate() -> dict[str, Any]:
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    stages = {stage: _load_jsonl(DATASET_DIR / filename) for stage, filename in STAGE_FILES.items()}
    gold = _load_jsonl(DATASET_DIR / GOLD_FILE)

    expected_counts = {"discovery": 8, "qualification": 8, "final_ab": 100}
    counts = {stage: len(rows) for stage, rows in stages.items()}
    if counts != expected_counts:
        raise ValueError(f"stage counts mismatch: {counts}")

    runtime_rows = [row for rows in stages.values() for row in rows]
    runtime_ids = [str(row.get("case_id") or "") for row in runtime_rows]
    gold_ids = [str(row.get("case_id") or "") for row in gold]
    if "" in runtime_ids or len(runtime_ids) != len(set(runtime_ids)):
        raise ValueError("runtime case IDs are empty or duplicated")
    if len(gold_ids) != len(set(gold_ids)) or set(gold_ids) != set(runtime_ids):
        raise ValueError("gold/runtime coverage mismatch")

    prompts = [_normalized_text(str(row.get("prompt") or "")) for row in runtime_rows]
    material_pairs = [
        tuple(_normalized_text(str(material.get("text") or "")) for material in row.get("materials", []))
        for row in runtime_rows
    ]
    if "" in prompts or len(prompts) != len(set(prompts)):
        raise ValueError("prompts are empty or overlap across stages")
    if any(len(pair) != 2 or "" in pair for pair in material_pairs):
        raise ValueError("every runtime row must contain two non-empty materials")
    if len(material_pairs) != len(set(material_pairs)):
        raise ValueError("material pairs overlap across stages")

    for row in runtime_rows:
        leaked = FORBIDDEN_RUNTIME_FIELDS.intersection(row)
        if leaked:
            raise ValueError(f"runtime leak in {row['case_id']}: {sorted(leaked)}")
        if row.get("formal_environment_write_allowed") is not False:
            raise ValueError(f"writeback is not disabled in {row['case_id']}")

    final_rows = stages["final_ab"]
    final_groups = Counter(str(row.get("case_group")) for row in final_rows)
    final_orders = Counter(str(row.get("pair_order")) for row in final_rows)
    if final_groups != Counter({"target_fault": 60, "compatible_boundary": 20, "ordinary_control": 20}):
        raise ValueError(f"final groups mismatch: {dict(final_groups)}")
    if final_orders != Counter({"AB": 50, "BA": 50}):
        raise ValueError(f"pair order mismatch: {dict(final_orders)}")

    expected_hashes = manifest.get("files")
    if not isinstance(expected_hashes, dict):
        raise ValueError("manifest file hashes are missing")
    actual_hashes = {
        filename: _sha256(DATASET_DIR / filename)
        for filename in [*STAGE_FILES.values(), GOLD_FILE]
    }
    if actual_hashes != expected_hashes:
        raise ValueError("manifest hashes do not match frozen files")
    if manifest.get("runtime_gold_separated") is not True:
        raise ValueError("manifest does not declare runtime/gold separation")
    if manifest.get("formal_environment_write_allowed") is not False:
        raise ValueError("manifest does not disable formal writeback")

    return {
        "valid": True,
        "stage_counts": counts,
        "total_isolated_cases": len(runtime_rows),
        "final_groups": dict(final_groups),
        "final_pair_orders": dict(final_orders),
        "runtime_gold_separated": True,
        "cross_stage_prompt_overlap": 0,
        "cross_stage_material_pair_overlap": 0,
        "hashes_verified": len(actual_hashes),
    }


def main() -> None:
    print(json.dumps(validate(), ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
