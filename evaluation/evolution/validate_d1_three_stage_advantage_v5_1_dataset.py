from __future__ import annotations

"""Validate the frozen D1 V5.1 advantage dataset without regenerating it."""

from collections import Counter
import hashlib
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent
DATASET_DIR = ROOT / "datasets" / "d1_three_stage_v5_advantage"
MANIFEST_PATH = DATASET_DIR / "d1_rule_three_stage_advantage_v5_1.manifest.json"
STAGE_FILES = {
    "discovery": "d1_rule_discovery_advantage_v5_1.jsonl",
    "qualification": "d1_rule_qualification_advantage_v5_1.jsonl",
    "final_ab": "d1_rule_final_ab_advantage_v5_1.jsonl",
}
GOLD_FILE = "d1_rule_three_stage_advantage_v5_1.gold.jsonl"
FORBIDDEN_RUNTIME_FIELDS = {
    "gold_relation",
    "gold_rationale",
    "expected_rule_exposure",
    "target_rule",
    "conflict_binding",
    "repair_instruction",
    "selection_basis",
}


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(
        path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid JSON at {path.name}:{line_number}") from exc
        if not isinstance(value, dict):
            raise ValueError(f"non-object row at {path.name}:{line_number}")
        rows.append(value)
    return rows


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _normalized(value: Any) -> str:
    return "".join(str(value or "").split())


def validate() -> dict[str, Any]:
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    if manifest.get("dataset_id") != "d1_three_stage_v5_advantage":
        raise ValueError("unexpected dataset identity")
    if manifest.get("version") != "5.1.0":
        raise ValueError("unexpected dataset version")
    if manifest.get("case_selection_completed_before_execution") is not True:
        raise ValueError("case selection is not pre-registered")
    if manifest.get("result_based_case_removal_allowed") is not False:
        raise ValueError("manifest permits result-based case removal")
    if manifest.get("qualification_observed_effect_used_for_selection") is not False:
        raise ValueError("qualification effect may influence selection")
    if manifest.get("runtime_gold_separated") is not True:
        raise ValueError("runtime and Gold are not declared separate")
    if manifest.get("formal_environment_write_allowed") is not False:
        raise ValueError("manifest permits formal writeback")
    if manifest.get("judge_independence") is not False:
        raise ValueError("manifest incorrectly claims an independent judge")

    stages = {
        stage: _load_jsonl(DATASET_DIR / filename)
        for stage, filename in STAGE_FILES.items()
    }
    gold = _load_jsonl(DATASET_DIR / GOLD_FILE)
    counts = {stage: len(rows) for stage, rows in stages.items()}
    if counts != {"discovery": 8, "qualification": 8, "final_ab": 100}:
        raise ValueError(f"stage counts mismatch: {counts}")

    runtime = [row for rows in stages.values() for row in rows]
    ids = [str(row.get("case_id") or "") for row in runtime]
    if "" in ids or len(ids) != len(set(ids)):
        raise ValueError("runtime case IDs are empty or duplicated")
    gold_ids = [str(row.get("case_id") or "") for row in gold]
    if len(gold_ids) != len(set(gold_ids)) or set(gold_ids) != set(ids):
        raise ValueError("Gold/runtime coverage mismatch")

    prompts = [_normalized(row.get("prompt")) for row in runtime]
    pairs = [
        tuple(_normalized(item.get("text")) for item in row.get("materials") or [])
        for row in runtime
    ]
    if "" in prompts or len(prompts) != len(set(prompts)):
        raise ValueError("prompt overlap detected")
    if any(len(pair) != 2 or "" in pair for pair in pairs):
        raise ValueError("runtime material pair is incomplete")
    if len(pairs) != len(set(pairs)):
        raise ValueError("material pair overlap detected")

    for row in runtime:
        leaked = FORBIDDEN_RUNTIME_FIELDS.intersection(row)
        if leaked:
            raise ValueError(f"runtime leak in {row['case_id']}: {sorted(leaked)}")
        if row.get("formal_environment_write_allowed") is not False:
            raise ValueError(f"writeback enabled in {row['case_id']}")

    final_groups = Counter(row.get("case_group") for row in stages["final_ab"])
    final_orders = Counter(row.get("pair_order") for row in stages["final_ab"])
    if final_groups != Counter(
        {"target_fault": 60, "compatible_boundary": 20, "ordinary_control": 20}
    ):
        raise ValueError(f"final group mismatch: {final_groups}")
    if final_orders != Counter({"AB": 50, "BA": 50}):
        raise ValueError(f"pair-order mismatch: {final_orders}")

    relations = Counter(row.get("gold_relation") for row in gold)
    if relations != Counter(
        {"contradiction": 76, "compatible": 20, "not_applicable": 20}
    ):
        raise ValueError(f"Gold distribution mismatch: {relations}")
    if any(
        row.get("source_status")
        != "synthetic_evaluation_fixture_pending_independent_human_review"
        for row in gold
    ):
        raise ValueError("Gold review status is overstated")

    expected_hashes = manifest.get("files")
    actual_hashes = {
        name: _sha256(DATASET_DIR / name)
        for name in [*STAGE_FILES.values(), GOLD_FILE]
    }
    if expected_hashes != actual_hashes:
        raise ValueError("frozen file hashes do not match manifest")

    return {
        "valid": True,
        "dataset_id": manifest["dataset_id"],
        "version": manifest["version"],
        "stage_counts": counts,
        "final_groups": dict(final_groups),
        "final_pair_orders": dict(final_orders),
        "runtime_gold_separated": True,
        "case_selection_completed_before_execution": True,
        "result_based_case_removal_allowed": False,
        "judge_independence": False,
        "hashes_verified": len(actual_hashes),
    }


def main() -> None:
    print(json.dumps(validate(), ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
