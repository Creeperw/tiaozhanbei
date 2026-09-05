import importlib.util
from pathlib import Path
from copy import deepcopy

import pytest

spec = importlib.util.spec_from_file_location(
    "isolated_d1_runtime", Path(__file__).with_name("isolated_d1_runtime.py")
)
runtime = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runtime)


def source_run():
    return {
        "run_id": runtime.RUN_ID, "status": "final_interrupted",
        "qualification": {"passed": True}, "candidate_digest": "frozen-candidate",
        "final": {"receipts": [{"case_id": str(i)} for i in range(23)],
                  "pair_checkpoints": {"24": {"A": {"value": "retained"}}}},
        "production_snapshot_before": {
            "evolution_enabled": False, "runtime_rules_enabled": False,
            "feedback_count": 0, "signature_count": 0, "rule_count": 0,
            "rule_run_count": 0, "table:conversation_sessions": 726,
        },
    }


def test_migration_preserves_inputs_original_snapshot_and_partial_arms():
    original = source_run()
    before = deepcopy(original)
    baseline = {**original["production_snapshot_before"], "table:conversation_sessions": 727}
    migrated = runtime.migrate_run(original, baseline, "manifest-hash")
    assert original == before
    assert migrated["final"] == original["final"]
    assert migrated["original_production_snapshot_before"] == original["production_snapshot_before"]
    assert migrated["production_snapshot_before"] == baseline
    assert migrated["environment_migrations"][0]["checkpoint_sha256"] == runtime.digest(original["final"])


@pytest.mark.parametrize("change", [{"table:conversation_sessions": 728}, {"rule_count": 1}])
def test_migration_rejects_unapproved_source_changes(change):
    original = source_run()
    baseline = {**original["production_snapshot_before"], "table:conversation_sessions": 727, **change}
    with pytest.raises(ValueError):
        runtime.migrate_run(original, baseline, "hash")


def test_migration_rejects_duplicate_transfer():
    original = source_run()
    baseline = {**original["production_snapshot_before"], "table:conversation_sessions": 727}
    migrated = runtime.migrate_run(original, baseline, "hash")
    with pytest.raises(ValueError, match="already migrated"):
        runtime.migrate_run(migrated, baseline, "hash")