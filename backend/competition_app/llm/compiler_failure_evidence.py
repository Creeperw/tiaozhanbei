"""Opt-in, single-run capture of formal compiler failure evidence only."""

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
from typing import Any

from competition_app.runtime.snapshot import _sanitize


CONFIG_PATH = Path("/srv/tiaozhanbei/runtime/competition_app/evaluation/compiler-failure-evidence/config.json")
MAX_BYTES = 192 * 1024
MAX_FAILURES = 4


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()


def _write_exclusive(path: Path, content: bytes) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(content)


def capture_failure(context: dict[str, Any], source: dict[str, Any], route: dict[str, Any], raw: Any, envelope: Any, *, plan_scope: str = "long_term") -> None:
    """Best effort only. No prompt, provider reasoning, or business mutation."""
    try:
        if envelope.result.status == "compiled" or not CONFIG_PATH.is_file():
            return
        config = json.loads(CONFIG_PATH.read_text())
        if config.get("enabled") is not True or context.get("learner_id") != config.get("learner_id") or not config.get("learner_id"):
            return
        if config.get("plan_scope") is not None and config["plan_scope"] != plan_scope:
            return
        expires = datetime.fromisoformat(config["expires_at"])
        if expires.tzinfo is None or datetime.now(timezone.utc) >= expires:
            return
        run = context.get("execution_id") or context.get("request_id")
        if not isinstance(run, str) or not run:
            return
        # The directory is provisioned by the operator with mode 0700. Never
        # accept a model-controlled output path or create arbitrary parents.
        root = CONFIG_PATH.parent
        claim = _digest({"run": run, "learner": context["learner_id"]}).encode()
        try:
            _write_exclusive(root / "claimed-run", claim)
        except FileExistsError:
            if (root / "claimed-run").read_bytes() != claim:
                return
        contract = raw.get("contract") if isinstance(raw, dict) else None
        formal = {}
        if isinstance(contract, dict):
            for key in ("scope", "total_duration_days", "selected_stage_id", "selected_books", "selection_mode", "selection_reason"):
                if key in contract:
                    formal[key] = contract[key]
            if plan_scope == "short_term":
                for key in ("duration_days", "progression_nodes", "expected_output", "completion_criteria"):
                    if key in contract:
                        formal[key] = contract[key]
            formal["stages"] = [
                {key: item[key] for key in ("stage_id", "duration_days", "schedule_summary", "acceptance") if key in item}
                for item in contract.get("stages", []) if isinstance(item, dict)
            ]
            formal["field_anchors"] = {
                path: [{key: entry[key] for key in ("source_field", "source_quote") if key in entry}
                       for entry in entries if isinstance(entry, dict)]
                for path, entries in (contract.get("field_anchors") or {}).items()
                if isinstance(path, str) and path.startswith("/") and isinstance(entries, list)
            }
        document = source.get("plan_document")
        evidence = {
            "version": 1, "run_digest": claim.decode(), "plan_scope": plan_scope,
            "source_digest": envelope.source_digest,
            "route_source_digest": envelope.route_source_digest,
            "document": document, "contract": formal,
            "raw_status": raw.get("status") if isinstance(raw, dict) else None,
            "issues": [item.model_dump(mode="json") for item in envelope.result.issues],
            "route": {key: route[key] for key in ("binding_mode", "route_id", "route_version", "stages") if key in route},
        }
        safe = _sanitize(evidence)
        safe["sanitization_changed_evidence"] = safe != evidence
        safe["original_evidence_digest"] = _digest(evidence)
        encoded = json.dumps(safe, ensure_ascii=False, sort_keys=True).encode()
        if len(encoded) > MAX_BYTES:
            return
        for index in range(MAX_FAILURES):
            try:
                _write_exclusive(root / f"failure-{index + 1}.json", encoded)
                return
            except FileExistsError:
                continue
    except Exception:
        # Evidence cannot replace the original failure or authorize a plan.
        return