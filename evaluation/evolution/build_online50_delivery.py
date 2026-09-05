from __future__ import annotations

import argparse
import csv
import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from competition_app.evaluation.accountability_faults import accountability_fault_specs


PRIMARY_BATCH_KEYS = (
    "__evolution_quick5_20260814",
    "__evolution_followup15_20260814",
    "__evolution_followup10_batch3_20260814",
    "__evolution_followup10_batch4_20260814",
    "__evolution_final10_batch5_20260814",
)
RETRY_BATCH_KEY = "__evolution_final6_technical_retry_20260814"


def _read_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _write_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def _ratio(numerator: int, denominator: int) -> dict[str, Any]:
    return {
        "numerator": numerator,
        "denominator": denominator,
        "rate": round(numerator / denominator, 6) if denominator else None,
        "percentage": round(numerator * 100 / denominator, 1) if denominator else None,
    }


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build(raw_path: Path, output_dir: Path) -> dict[str, Any]:
    raw = _read_json(raw_path)
    specs = accountability_fault_specs()

    primary: dict[str, dict[str, Any]] = {}
    batch_by_case: dict[str, str] = {}
    for batch_key in PRIMARY_BATCH_KEYS:
        batch = raw.get(batch_key)
        if not isinstance(batch, dict) or batch.get("status") != "completed":
            raise ValueError(f"primary batch is not completed: {batch_key}")
        for item in batch.get("results", []):
            case_id = str(item.get("case_id") or "")
            if not case_id or case_id in primary:
                raise ValueError(f"duplicate or empty primary case id: {case_id}")
            primary[case_id] = item
            batch_by_case[case_id] = batch_key

    if len(primary) != 50:
        raise ValueError(f"expected 50 primary cases, got {len(primary)}")

    retry_batch = raw.get(RETRY_BATCH_KEY)
    if not isinstance(retry_batch, dict) or retry_batch.get("status") != "completed":
        raise ValueError("technical retry batch is not completed")
    retries = {
        str(item.get("case_id") or ""): item
        for item in retry_batch.get("results", [])
    }
    if len(retries) != 6:
        raise ValueError(f"expected 6 recovered retry cases, got {len(retries)}")

    rows: list[dict[str, Any]] = []
    for sequence, case_id in enumerate(primary, start=1):
        initial = primary[case_id]
        selected = retries.get(case_id, initial)
        record = dict(selected.get("record") or {})
        spec = specs.get(case_id)
        if spec is None:
            raise ValueError(f"case missing from server-owned specs: {case_id}")
        if record.get("result_status") not in {"success", "waiting_human_review"}:
            raise ValueError(f"canonical case has no valid business terminal state: {case_id}")
        if record.get("error_type") or record.get("error_message"):
            raise ValueError(f"canonical case still has a technical error: {case_id}")

        initial_record = dict(initial.get("record") or {})
        recovered_from_technical_failure = case_id in retries
        row = {
            "sequence": sequence,
            "case_id": case_id,
            "batch_key": batch_by_case[case_id],
            "topic": spec.topic,
            "prompt": spec.prompt,
            "fault_type": spec.fault_type,
            "inject_mode": spec.inject_mode,
            "false_claim": spec.false_claim,
            "expected_owner_step_ids": list(spec.expected_owner_step_ids),
            "expected_rerun_step_ids": list(spec.expected_rerun_step_ids),
            "expected_issue_types": list(spec.expected_issue_types),
            "thread_id": record.get("thread_id") or selected.get("thread_id"),
            "http_status": selected.get("http_status"),
            "wall_seconds": selected.get("wall_seconds"),
            "duration_seconds": record.get("duration_seconds"),
            "result_status": record.get("result_status"),
            "final_audit_decision": record.get("final_audit_decision"),
            "fault_detected": record.get("fault_detected"),
            "strict_owner_correct": record.get("strict_owner_correct"),
            "rerun_chain_correct": record.get("rerun_chain_correct"),
            "repair_success": record.get("repair_success"),
            "persistent_safe_stop": record.get("persistent_safe_stop"),
            "clean_false_positive": record.get("clean_false_positive"),
            "false_claim_removed": record.get("false_claim_removed"),
            "repair_count": record.get("repair_count"),
            "predicted_owner_step_ids": record.get("predicted_owner_step_ids", []),
            "actual_rerun_step_ids": record.get("actual_rerun_step_ids", []),
            "actual_issue_types": record.get("actual_issue_types", []),
            "injections": record.get("injections", []),
            "recovered_from_technical_failure": recovered_from_technical_failure,
            "initial_technical_error_type": (
                initial_record.get("error_type") if recovered_from_technical_failure else None
            ),
            "initial_technical_error_message": (
                initial_record.get("error_message") if recovered_from_technical_failure else None
            ),
            "retry_attempt": selected.get("attempt") if recovered_from_technical_failure else None,
        }
        rows.append(row)

    fault_rows = [row for row in rows if row["fault_type"] != "none"]
    clean_rows = [row for row in rows if row["fault_type"] == "none"]
    transient_rows = [row for row in fault_rows if row["inject_mode"] == "once"]
    persistent_rows = [row for row in fault_rows if row["inject_mode"] == "persistent"]

    metrics = {
        "schema_version": "online-accountability-50-metrics-1.0",
        "evaluation_date": "2026-08-14",
        "report_date": "2026-08-15",
        "sample_counts": {
            "canonical_total": len(rows),
            "fault_cases": len(fault_rows),
            "clean_controls": len(clean_rows),
            "transient_fault_cases": len(transient_rows),
            "persistent_fault_cases": len(persistent_rows),
            "initial_technical_failures": len(retries),
            "technical_failures_after_retry": sum(
                1 for row in rows if row.get("initial_technical_error_type") and not row.get("result_status")
            ),
        },
        "primary_metrics": {
            "fault_detection": _ratio(
                sum(row["fault_detected"] is True for row in fault_rows), len(fault_rows)
            ),
            "strict_owner_accuracy": _ratio(
                sum(row["strict_owner_correct"] is True for row in fault_rows), len(fault_rows)
            ),
            "minimal_rerun_chain_accuracy": _ratio(
                sum(row["rerun_chain_correct"] is True for row in fault_rows), len(fault_rows)
            ),
            "transient_strict_repair_success": _ratio(
                sum(row["repair_success"] is True for row in transient_rows), len(transient_rows)
            ),
            "persistent_safe_stop": _ratio(
                sum(row["persistent_safe_stop"] is True for row in persistent_rows), len(persistent_rows)
            ),
            "clean_false_positive": _ratio(
                sum(row["clean_false_positive"] is True for row in clean_rows), len(clean_rows)
            ),
            "false_claim_removed": _ratio(
                sum(row["false_claim_removed"] is True for row in fault_rows), len(fault_rows)
            ),
            "safe_fault_handling": _ratio(
                sum(
                    row["false_claim_removed"] is True
                    and row["result_status"] in {"success", "waiting_human_review"}
                    for row in fault_rows
                ),
                len(fault_rows),
            ),
            "technical_success_after_retry": _ratio(len(rows), len(rows)),
        },
        "outcomes": {
            "result_status": dict(Counter(row["result_status"] for row in rows)),
            "audit_decision": dict(Counter(row["final_audit_decision"] for row in rows)),
            "fault_type": dict(Counter(row["fault_type"] for row in rows)),
        },
    }

    breakdown: dict[str, Any] = {}
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[row["fault_type"]].append(row)
    for fault_type, group in sorted(grouped.items()):
        is_control = fault_type == "none"
        breakdown[fault_type] = {
            "count": len(group),
            "success": sum(row["result_status"] == "success" for row in group),
            "waiting_human_review": sum(
                row["result_status"] == "waiting_human_review" for row in group
            ),
            "fault_detection": None
            if is_control
            else _ratio(sum(row["fault_detected"] is True for row in group), len(group)),
            "strict_owner_accuracy": None
            if is_control
            else _ratio(sum(row["strict_owner_correct"] is True for row in group), len(group)),
            "minimal_rerun_chain_accuracy": None
            if is_control
            else _ratio(sum(row["rerun_chain_correct"] is True for row in group), len(group)),
            "strict_repair_success": None
            if is_control
            else _ratio(
                sum(row["repair_success"] is True for row in group if row["inject_mode"] == "once"),
                sum(row["inject_mode"] == "once" for row in group),
            ),
            "clean_false_positive": _ratio(
                sum(row["clean_false_positive"] is True for row in group), len(group)
            )
            if is_control
            else None,
        }
    metrics["breakdown_by_fault_type"] = breakdown

    output_dir.mkdir(parents=True, exist_ok=True)
    jsonl_path = output_dir / "online50_canonical_results_20260814.jsonl"
    csv_path = output_dir / "online50_canonical_results_20260814.csv"
    metrics_path = output_dir / "online50_metrics_20260814.json"
    retries_path = output_dir / "online50_technical_retries_20260814.jsonl"
    _write_jsonl(jsonl_path, rows)
    _write_json(metrics_path, metrics)

    flat_fields = [
        "sequence",
        "case_id",
        "topic",
        "prompt",
        "fault_type",
        "inject_mode",
        "result_status",
        "final_audit_decision",
        "fault_detected",
        "strict_owner_correct",
        "rerun_chain_correct",
        "repair_success",
        "persistent_safe_stop",
        "clean_false_positive",
        "false_claim_removed",
        "repair_count",
        "duration_seconds",
        "thread_id",
        "recovered_from_technical_failure",
    ]
    with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=flat_fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)

    retry_rows: list[dict[str, Any]] = []
    for item in retry_batch.get("attempts", []):
        record = dict(item.get("record") or {})
        retry_rows.append(
            {
                "case_id": item.get("case_id"),
                "attempt": item.get("attempt"),
                "thread_id": item.get("thread_id"),
                "terminal_event": item.get("terminal_event"),
                "wall_seconds": item.get("wall_seconds"),
                "result_status": record.get("result_status"),
                "error_type": record.get("error_type"),
                "error_message": record.get("error_message"),
            }
        )
    _write_jsonl(retries_path, retry_rows)

    artifact_paths = [jsonl_path, csv_path, metrics_path, retries_path, raw_path]
    manifest = {
        "schema_version": "online-accountability-50-manifest-1.0",
        "dataset_name": "失败归因与局部返修在线50题评测",
        "evaluation_date": "2026-08-14",
        "report_date": "2026-08-15",
        "canonical_case_count": len(rows),
        "technical_retry_case_count": len(retries),
        "artifacts": [
            {
                "path": str(path),
                "bytes": path.stat().st_size,
                "sha256": _sha256(path),
            }
            for path in artifact_paths
        ],
    }
    manifest_path = output_dir / "online50_manifest_20260814.json"
    _write_json(manifest_path, manifest)
    return {"metrics": metrics, "manifest": manifest}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--raw", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    result = build(args.raw, args.output_dir)
    print(json.dumps(result["metrics"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
