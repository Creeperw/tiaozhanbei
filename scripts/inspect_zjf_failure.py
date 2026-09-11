"""Read-only failure metadata for the authorized uid6 planning run."""
import json
import subprocess
import sys
from sqlalchemy import text
sys.path.insert(0, "/tmp")
from inspect_practice_dates import connect, emit

thread = sys.argv[1]
pid = int(subprocess.check_output(["systemctl", "show", "tiaozhanbei.service", "-p", "MainPID", "--value"]))
engine, settings = connect(pid)
allowed = {"status", "error", "error_code", "error_message", "failure_code", "failure_message", "failure_reason", "message", "issues", "diagnostics", "code", "field_path", "step_id", "agent", "execution_id", "reason", "compilation_feedback", "validation_feedback", "compiler_failure_evidence"}
allowed.update({"user_request", "original_user_request", "plan_scope", "generated_scope"})
allowed.update({"audit_decision", "decision", "overall_decision", "approved"})
def visit(value, path=""):
    if isinstance(value, dict):
        if ".prerequisite_assessment.judgments[" in path:
            emit({"path": path, "judgment": {k: v for k, v in value.items() if k in {"course", "status", "source_ref"}}})
        if path.endswith("prerequisite_judgments") or ".prerequisite_judgments[" in path:
            emit({"path": path, "judgment": {k: v for k, v in value.items() if k in {"course", "status", "source_ref", "source_quote"}}})
        if "failure_model_trace[" in path and path.count(".") <= 2:
            emit({"path": path, "keys": list(value)})
        for key, child in value.items():
            if any(word in key.lower() for word in ("reasoning", "secret", "authorization", "token", "prompt", "raw_response")):
                continue
            if key in allowed and not isinstance(child, (dict, list)):
                emit({"path": path + "." + key, "value": str(child)[:1800]})
            elif isinstance(child, (dict, list)):
                visit(child, path + "." + key)
    elif isinstance(value, list):
        for i, child in enumerate(value[:30]):
            if isinstance(child, (dict, list)):
                visit(child, path + f"[{i}]")
            elif path.rsplit(".", 1)[-1] in {"issues", "diagnostics", "validation_feedback"}:
                emit({"path": path, "value": str(child)[:1800]})
try:
    with engine.connect() as conn:
        conn.execute(text("SET TRANSACTION READ ONLY"))
        row = conn.execute(text(f"SELECT * FROM `{settings.mysql_database}`.workflow_run_states WHERE thread_id=:thread AND learner_id=:learner"), {"thread": thread, "learner": "USER_29d487bef1cf4845b64717c4e6c2c69b"}).mappings().one()
        emit({"columns": list(row)})
        for key, value in row.items():
            if key.endswith("json") and isinstance(value, str):
                try:
                    visit(json.loads(value), key)
                except json.JSONDecodeError:
                    pass
            elif key in allowed:
                emit({key: str(value)[:2000]})
        conn.rollback()
finally:
    engine.dispose()