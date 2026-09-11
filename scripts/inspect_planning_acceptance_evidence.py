"""Read-only uid6 planning acceptance evidence; never build a live container."""
import json
import subprocess
import sys

from sqlalchemy import text
from competition_app.runtime.sqlalchemy_checkpointer import SqlAlchemyCheckpointSaver

sys.path.insert(0, "/tmp")
from inspect_practice_dates import connect, emit

EXECUTION = sys.argv[1] if len(sys.argv) > 1 else "EXE_34cca81211a9f7d9b5767f0c5b542c84"
pid = int(subprocess.check_output([
    "systemctl", "show", "tiaozhanbei.service", "-p", "MainPID", "--value",
]))
engine, settings = connect(pid)
FIELDS = {
    "planning_request_scope", "planning_focus_assessment", "learning_evidence",
    "task_completion_rate", "accuracy", "answer_accuracy", "correct_rate",
    "review_stability", "retry_count", "evidence_status", "evidence_source",
    "source_type", "source_label", "data_quality", "audit_report",
    "decision", "findings", "structured_findings", "evidence_items",
    "evidence_summaries", "review_performance", "performance_summary",
}
EXCLUDED = {"reasoning", "raw_input", "raw_output", "messages", "system_prompt"}


def visit(value, path):
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    if isinstance(value, dict):
        for key, item in value.items():
            if key in EXCLUDED:
                continue
            if key in FIELDS:
                emit({"path": f"{path}.{key}", "value": item})
            elif isinstance(item, (dict, list)):
                visit(item, f"{path}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            visit(item, f"{path}[{index}]")


try:
    with engine.connect() as conn:
        conn.execute(text(f"USE `{settings.mysql_database}`"))
        conn.commit()
        conn.execute(text("SET TRANSACTION READ ONLY"))
        db = settings.mysql_database
        owner = conn.execute(text(
            f"SELECT learner_id FROM `{db}`.workflow_run_states WHERE execution_id=:execution"
        ), {"execution": EXECUTION}).scalar_one()
        assert owner == "USER_29d487bef1cf4845b64717c4e6c2c69b"
        run = conn.execute(text(
            "SELECT thread_id, payload_json FROM workflow_run_states WHERE execution_id=:execution"
        ), {"execution": EXECUTION}).mappings().one()
        payload = json.loads(run["payload_json"]) if isinstance(run["payload_json"], str) else run["payload_json"]
        emit({"run_keys": list(payload)})
        emit({"run_status": {key: payload.get(key) for key in (
            "status", "message", "retryable", "error_code", "error_type", "failed_step",
        )}})
        for trace in payload.get("failure_model_trace") or []:
            if not isinstance(trace, dict) or trace.get("agent") != "diagnosis_agent":
                continue
            raw_input = trace.get("raw_input") or {}
            model_payload = raw_input.get("payload") or {}
            raw_output = trace.get("raw_output") or {}
            emit({"diagnosis_protocol_evidence": {
                "sequence": trace.get("sequence"), "full_capture": trace.get("full_capture"),
                "input_retained": bool(raw_input), "output_fields": list(raw_output),
                "source_keys": list(model_payload.get("prerequisite_sources") or {}),
                "judgments": raw_output.get("prerequisite_judgments"),
                "source_revision_error": model_payload.get("prerequisite_validation_error"),
                "error_type": trace.get("error_type"), "error_reason": trace.get("error_reason"),
                "validation_issues": trace.get("validation_issues"),
                "skill_version": (trace.get("response_diagnostics") or {}).get("skill_version"),
            }})
        visit(payload, "run")
        saver = SqlAlchemyCheckpointSaver(engine)
        checkpoints = conn.execute(text(
            "SELECT * FROM langgraph_checkpoints WHERE thread_id=:thread ORDER BY checkpoint_id DESC LIMIT 3"
        ), {"thread": run["thread_id"]}).all()
        for row in checkpoints:
            state = saver._tuple_from_row(conn, row)
            visit(state.checkpoint["channel_values"], "checkpoint")
        rows = conn.execute(text(
            f"SELECT artifact_type, payload_json FROM `{db}`.artifacts WHERE execution_id=:execution"
        ), {"execution": EXECUTION}).mappings()
        for row in rows:
            value = row["payload_json"]
            if isinstance(value, str):
                value = json.loads(value)
            emit({"artifact_type": row["artifact_type"], "keys": list(value)})
            visit(value, row["artifact_type"])
        conn.rollback()
finally:
    engine.dispose()