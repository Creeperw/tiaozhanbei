from __future__ import annotations

"""Capture developer-only D1 model inputs and outputs for representative cases.

These diagnostic reruns are separate from the frozen formal-effect denominator.
They never write production business state. Provider reasoning is deliberately
excluded; the export contains sanitized request messages and model responses.
"""

import argparse
import asyncio
from dataclasses import asdict
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

from competition_app.application.container import ApplicationContainer
from competition_app.config import Settings
from competition_app.evaluation.d1_formal_runner import D1FormalRunner
from competition_app.evaluation.d1_precheck_execution import (
    D1ExecutionDependencies,
    D1PrecheckExecutionService,
)
from competition_app.runtime.snapshot import _sanitize


DEFAULT_OUTPUT_DIR = Path(
    "evaluation/evolution/outputs/semantic_conflict_closure_model_io_traces"
)
DEFAULT_CASES = (
    "EVO-D1-AB50-001",
    "EVO-D1-AB50-005",
    "EVO-D1-AB50-042",
)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Capture sanitized D1 model I/O traces")
    parser.add_argument("--learner-id", required=True)
    parser.add_argument("--case-id", action="append", dest="case_ids")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    return parser.parse_args()


def _trace_payload(item: Any) -> dict[str, Any]:
    value = item.model_dump(mode="json", exclude={"reasoning_text"})
    value["reasoning_text"] = "[OMITTED_NOT_EXPORTED]"
    return _sanitize(value)


async def _run(args: argparse.Namespace) -> int:
    settings = Settings.from_env()
    if not settings.accountability_evaluation_enabled:
        raise SystemExit("ACCOUNTABILITY_EVALUATION_ENABLED must be true")
    container = ApplicationContainer.build(settings, include_backend_handoff=False)
    evaluation = container.accountability_evaluation_service
    recorder = container.model_trace_recorder
    if evaluation is None or recorder is None:
        raise SystemExit("isolated evaluation model tracing is unavailable")

    runner = D1FormalRunner()
    by_id = {case.case_id: case for case in runner.cases}
    case_ids = tuple(args.case_ids or DEFAULT_CASES)
    unknown = sorted(set(case_ids) - set(by_id))
    if unknown:
        raise SystemExit("unknown case IDs: " + ", ".join(unknown))
    service = D1PrecheckExecutionService(
        D1ExecutionDependencies(
            retrieval_tool=container.question_retrieval_tool,
            expert_agent=evaluation.expert_agent,
            audit_agent=evaluation.audit_agent,
        ),
        runner=runner,
    )
    args.output_dir.mkdir(parents=True, exist_ok=True)
    index: list[dict[str, Any]] = []
    for case_id in case_ids:
        recorder.reset()
        started_at = datetime.now(timezone.utc).isoformat()
        result = await service.execute_pair(case_id, learner_id=args.learner_id.strip())
        model_calls = [_trace_payload(item) for item in recorder.items]
        record = {
            "schema_version": "d1-model-io-trace-1.0",
            "trace_purpose": "developer_diagnostic_not_formal_effect_denominator",
            "formal_environment_write_allowed": False,
            "provider_reasoning_exported": False,
            "case": asdict(by_id[case_id]),
            "started_at": started_at,
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "model_name": container.chat_model_name,
            "model_call_count": len(model_calls),
            "model_calls": model_calls,
            "pair_result": _sanitize(result),
        }
        output = args.output_dir / f"{case_id}.model_io.json"
        output.write_text(
            json.dumps(record, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        index.append(
            {
                "case_id": case_id,
                "file": output.name,
                "model_call_count": len(model_calls),
                "validation": result.get("validation"),
                "b_audit_decision": result["arms"]["B"]["audit_decision"],
                "b_closure_allowed": result["arms"]["B"]["closure_allowed"],
            }
        )
        print(json.dumps(index[-1], ensure_ascii=False))
    (args.output_dir / "index.json").write_text(
        json.dumps(index, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return 0


def main() -> None:
    raise SystemExit(asyncio.run(_run(_parse_args())))


if __name__ == "__main__":
    main()
