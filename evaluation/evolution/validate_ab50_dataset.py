from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path


DATASET = Path(__file__).resolve().parent / "datasets" / "evolution_effect_ab50_20260813.jsonl"
CSV_DATASET = DATASET.with_suffix(".csv")
MANIFEST = DATASET.with_suffix(".manifest.json")


def main() -> None:
    rows = [json.loads(line) for line in DATASET.read_text(encoding="utf-8").splitlines() if line]
    assert len(rows) == 50
    assert len({row["case_id"] for row in rows}) == 50
    normalized_prompts = [re.sub(r"\s+", "", row["prompt"]) for row in rows]
    assert len(set(normalized_prompts)) == 50
    assert [row["case_id"] for row in rows] == [f"EVO-AB50-{i:03d}" for i in range(1, 51)]

    groups = Counter(row["case_group"] for row in rows)
    assert groups == {
        "target_fault": 30,
        "non_regression_control": 15,
        "targeting_negative_control": 5,
    }
    operators = Counter(row["fault_injection"]["operator"] for row in rows)
    for name in (
        "stale_removed_reference", "near_match_decoy", "legacy_summary_unknown_id",
        "untrusted_evidence_instruction", "sparse_pack_overrequest",
        "conflicting_source_alias", "none_rich_control", "none_sparse_control",
        "valid_id_format_control", "none_targeting_control",
    ):
        assert operators[name] == 5, (name, operators[name])

    natural_target_prompts = rows[:45]
    forbidden_hints = ("不得编造", "不要编造", "只能使用本轮", "证据包", "evidence_id")
    for row in natural_target_prompts:
        assert not any(hint in row["prompt"] for hint in forbidden_hints), row["case_id"]
        assert row["target_rule"] == "require_evidence_ids_from_current_pack"
        assert row["execution_mode"] == "paired_frozen_expert_context"

    assert sum(row["pair_order"] == "AB" for row in rows) == 25
    assert sum(row["pair_order"] == "BA" for row in rows) == 25
    assert CSV_DATASET.is_file()
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    assert manifest["case_count"] == 50
    assert manifest["paired_model_run_count"] == 100
    assert manifest["formal_environment_write_allowed"] is False
    print("dataset valid")
    print("cases=50 paired_model_runs=100")
    print("groups", dict(groups))
    print("operators", dict(sorted(operators.items())))


if __name__ == "__main__":
    main()
