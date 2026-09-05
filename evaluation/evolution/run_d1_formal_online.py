from __future__ import annotations

"""Run a frozen D1 formal A/B evaluation in one isolated worker.

This command never calls the production review use case and never writes formal
conversation, plan, failure-case, or evolution-promotion state. It only uses
retrieval + Expert + Audit through D1PrecheckExecutionService and appends
receipts to an evaluation output file.
"""

import argparse
import asyncio
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

from competition_app.application.container import ApplicationContainer
from competition_app.config import Settings
from competition_app.evaluation.d1_ab100_runner import (
    D1AB100Runner,
    validate_d1_ab100_cases,
)
from competition_app.evaluation.d1_ab100_v2_runner import (
    D1AB100V2Runner,
    validate_d1_ab100_v2_cases,
)
from competition_app.evaluation.d1_formal_runner import D1FormalRunner, validate_d1_formal_cases
from competition_app.evaluation.d1_precheck_execution import (
    D1ExecutionDependencies,
    D1PrecheckExecutionService,
)

DEFAULT_OUTPUT = Path("evaluation/evolution/outputs/semantic_conflict_closure_ab50_results.jsonl")
AB100_DEFAULT_OUTPUT = Path(
    "evaluation/evolution/outputs/semantic_conflict_closure_ab100_results.jsonl"
)
AB100_V2_DEFAULT_OUTPUT = Path(
    "evaluation/evolution/outputs/semantic_conflict_closure_ab100_v2_results.jsonl"
)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run D1 formal paired online evaluation")
    parser.add_argument(
        "--dataset",
        choices=("ab50", "ab100", "ab100-v2"),
        default="ab50",
        help="Frozen dataset contract to run (default: ab50)",
    )
    parser.add_argument("--learner-id", help="Dedicated evaluation learner ID; required without --dry-run")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--case-id", action="append", help="Run only the selected case(s); repeatable")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--resume", action="store_true", help="Skip case IDs already present in output")
    parser.add_argument(
        "--max-attempts",
        type=int,
        default=3,
        help="Maximum whole-pair attempts for a technical failure (default: 3)",
    )
    parser.add_argument(
        "--retry-backoff-seconds",
        type=float,
        default=5.0,
        help="Base delay between whole-pair technical retries (default: 5)",
    )
    parser.add_argument(
        "--technical-errors-output",
        type=Path,
        default=None,
        help="Sanitized technical-attempt log; defaults beside --output",
    )
    parser.add_argument("--dry-run", action="store_true", help="Validate dataset and dependencies without model calls")
    return parser.parse_args()


def _existing_ids(path: Path) -> set[str]:
    if not path.is_file():
        return set()
    ids: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict) and value.get("case_id"):
            ids.add(str(value["case_id"]))
    return ids


def _technical_errors_path(output: Path) -> Path:
    return output.with_name(f"{output.stem}_technical_errors.jsonl")


def _append_jsonl(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as file:
        file.write(json.dumps(value, ensure_ascii=False) + "\n")
        file.flush()


def _technical_error_record(
    *,
    case_id: str,
    attempt: int,
    max_attempts: int,
    error: Exception,
) -> dict[str, Any]:
    """Return a sanitized error receipt without prompts, responses, or secrets."""
    return {
        "schema_version": "d1-formal-technical-error-1.0",
        "case_id": case_id,
        "attempt": attempt,
        "max_attempts": max_attempts,
        "error_type": type(error).__name__,
        "will_retry": attempt < max_attempts,
        "occurred_at": datetime.now(timezone.utc).isoformat(),
    }


def _compact_result(result: dict[str, Any]) -> dict[str, Any]:
    """Keep the receipt useful while avoiding accidental model-trace export."""
    arms = {}
    for arm, value in (result.get("arms") or {}).items():
        arms[arm] = {
            key: value.get(key)
            for key in (
                "rule_exposed",
                "exposed_target_agent",
                "target_failure",
                "closure_allowed",
                "initial_target_failure",
                "final_target_failure",
                "first_audit_decision",
                "final_audit_decision",
                "repair_count",
                "repair_attempt_count",
                "repair_exhausted",
                "max_repair_attempts",
                "release_allowed",
                "audit_decision",
                "same_conflict_pair_resolved",
                "new_unsupported_claims",
                "actual_rerun_step_ids",
                "pack_digest",
            )
        }
    return {
        "schema_version": "d1-formal-result-1.1",
        "case_id": result.get("case_id"),
        "pair_order": result.get("pair_order"),
        "formal_environment_write_allowed": result.get("formal_environment_write_allowed"),
        "retrieval_pack_digest": result.get("retrieval_pack_digest"),
        "conflict_binding": result.get("conflict_binding"),
        "validation": result.get("validation"),
        "arms": arms,
    }


async def _run(args: argparse.Namespace) -> int:
    if args.max_attempts < 1:
        raise SystemExit("--max-attempts must be at least 1")
    if args.retry_backoff_seconds < 0:
        raise SystemExit("--retry-backoff-seconds cannot be negative")
    if args.dataset == "ab100-v2":
        runner = D1AB100V2Runner()
        summary = validate_d1_ab100_v2_cases(runner.cases)
        if args.output == DEFAULT_OUTPUT:
            args.output = AB100_V2_DEFAULT_OUTPUT
    elif args.dataset == "ab100":
        runner = D1AB100Runner()
        summary = validate_d1_ab100_cases(runner.cases)
        # Keep old evidence immutable.  Selecting AB100 without an explicit
        # output path must never append new receipts to the completed AB50 file.
        if args.output == DEFAULT_OUTPUT:
            args.output = AB100_DEFAULT_OUTPUT
    else:
        runner = D1FormalRunner()
        summary = validate_d1_formal_cases(runner.cases)
    selected = runner.cases
    if args.case_id:
        wanted = set(args.case_id)
        unknown = wanted - {case.case_id for case in selected}
        if unknown:
            raise SystemExit(f"unknown case ID(s): {', '.join(sorted(unknown))}")
        selected = [case for case in selected if case.case_id in wanted]
    selected = selected[args.offset :]
    if args.limit is not None:
        selected = selected[: args.limit]
    if args.resume:
        existing = _existing_ids(args.output)
        selected = [case for case in selected if case.case_id not in existing]

    print(json.dumps({"dataset": summary, "selected_cases": len(selected), "dry_run": args.dry_run}, ensure_ascii=False))
    if args.dry_run:
        return 0
    if not args.learner_id or not args.learner_id.strip():
        raise SystemExit("--learner-id is required unless --dry-run is used")

    settings = Settings.from_env()
    if not settings.accountability_evaluation_enabled:
        raise SystemExit("ACCOUNTABILITY_EVALUATION_ENABLED must be true for formal evaluation")
    container = ApplicationContainer.build(settings, include_backend_handoff=False)
    evaluation = container.accountability_evaluation_service
    if evaluation is None:
        raise SystemExit("isolated accountability evaluation service is unavailable")
    service = D1PrecheckExecutionService(
        D1ExecutionDependencies(
            retrieval_tool=container.question_retrieval_tool,
            expert_agent=evaluation.expert_agent,
            audit_agent=evaluation.audit_agent,
        ),
        runner=runner,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    technical_errors_output = args.technical_errors_output or _technical_errors_path(args.output)
    exhausted_cases: list[str] = []
    for case in selected:
        for attempt in range(1, args.max_attempts + 1):
            started_at = datetime.now(timezone.utc).isoformat()
            try:
                # A technical retry always reruns the whole pair. A partial arm
                # is never persisted or combined with a later attempt.
                result = await service.execute_pair(case.case_id, learner_id=args.learner_id.strip())
            except Exception as exc:  # noqa: BLE001 - isolated runner boundary
                error_record = _technical_error_record(
                    case_id=case.case_id,
                    attempt=attempt,
                    max_attempts=args.max_attempts,
                    error=exc,
                )
                _append_jsonl(technical_errors_output, error_record)
                print(json.dumps({"technical_error": error_record}, ensure_ascii=False))
                if attempt >= args.max_attempts:
                    exhausted_cases.append(case.case_id)
                    break
                await asyncio.sleep(args.retry_backoff_seconds * attempt)
                continue
            record = _compact_result(result)
            record["attempt"] = attempt
            record["started_at"] = started_at
            record["finished_at"] = datetime.now(timezone.utc).isoformat()
            _append_jsonl(args.output, record)
            print(
                json.dumps(
                    {
                        "case_id": case.case_id,
                        "attempt": attempt,
                        "validation": record["validation"],
                        "arms": record["arms"],
                    },
                    ensure_ascii=False,
                )
            )
            break
    if exhausted_cases:
        print(json.dumps({"exhausted_case_ids": exhausted_cases}, ensure_ascii=False))
        return 2
    return 0


def main() -> None:
    raise SystemExit(asyncio.run(_run(_parse_args())))


if __name__ == "__main__":
    main()
