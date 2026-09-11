"""Read-only authorized planning-scope and focus evidence diagnostics."""
import json
import subprocess
import sys

from sqlalchemy import text
from competition_app.runtime.sqlalchemy_checkpointer import SqlAlchemyCheckpointSaver

sys.path.insert(0, "/tmp")
from inspect_practice_dates import connect, emit

thread = sys.argv[1]
pid = int(subprocess.check_output(["systemctl", "show", "tiaozhanbei.service", "-p", "MainPID", "--value"]))
engine, settings = connect(pid)
saver = SqlAlchemyCheckpointSaver(engine)

def visit(value, path=""):
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    if isinstance(value, dict):
        for key, item in value.items():
            if key in {"planning_request_scope", "learning_focus_status", "learning_focus_items", "learning_focus_reason"}:
                emit({"path": path + "." + key, "value": item})
            elif key in {"evidence_items", "evidence_summaries"} and isinstance(item, list):
                emit({"path": path + "." + key, "count": len(item), "items": [{k: x.get(k) for k in ("evidence_id", "source_id", "source_label", "resource_type", "content_summary")} for x in item[:12] if isinstance(x, dict)]})
            elif key not in {"user_profile", "messages", "reasoning", "raw_input", "raw_output", "sources"} and isinstance(item, (dict, list)):
                visit(item, path + "." + key)
    elif isinstance(value, list):
        for i, item in enumerate(value[:40]):
            visit(item, path + f"[{i}]")

try:
    with engine.connect() as conn:
        conn.execute(text(f"USE `{settings.mysql_database}`"))
        conn.commit()
        conn.execute(text("SET TRANSACTION READ ONLY"))
        owner = conn.execute(text("SELECT learner_id FROM workflow_run_states WHERE thread_id=:thread"), {"thread": thread}).scalar_one()
        assert owner == "USER_29d487bef1cf4845b64717c4e6c2c69b"
        rows = conn.execute(text("SELECT * FROM langgraph_checkpoints WHERE thread_id=:thread ORDER BY checkpoint_id DESC LIMIT 10"), {"thread": thread}).all()
        for row in rows:
            state = saver._tuple_from_row(conn, row)
            emit({"namespace": row.checkpoint_ns, "channel_keys": list(state.checkpoint["channel_values"])})
            visit(state.checkpoint["channel_values"])
            visit(state.pending_writes, "pending")
        if not rows:
            emit({"checkpoint": None})
            run = conn.execute(text("SELECT payload_json FROM workflow_run_states WHERE thread_id=:thread"), {"thread": thread}).scalar_one()
            payload = json.loads(run) if isinstance(run, str) else run
            emit({"run_keys": list(payload)})
            visit(payload, "run")
            messages = conn.execute(text("SELECT role, metadata_json FROM conversation_messages WHERE session_id=:session ORDER BY created_at DESC LIMIT 6"), {"session": payload.get("conversation_id") or thread}).all()
            for message in messages:
                metadata = json.loads(message.metadata_json) if isinstance(message.metadata_json, str) else message.metadata_json
                emit({"message_role": message.role, "metadata_keys": list(metadata) if isinstance(metadata, dict) else []})
                visit(metadata, "message")
            artifacts = conn.execute(text("SELECT artifact_type, payload_json FROM artifacts WHERE execution_id=:execution"), {"execution": payload["execution_id"]}).all()
            for artifact in artifacts:
                value = json.loads(artifact.payload_json) if isinstance(artifact.payload_json, str) else artifact.payload_json
                emit({"artifact_type": artifact.artifact_type, "keys": list(value) if isinstance(value, dict) else []})
                visit(value, "artifact." + artifact.artifact_type)
            for table in ("artifacts", "execution_runs", "conversation_messages"):
                columns = conn.execute(text("SELECT COLUMN_NAME FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=:db AND TABLE_NAME=:table ORDER BY ORDINAL_POSITION"), {"db": settings.mysql_database, "table": table}).scalars().all()
                emit({"table": table, "columns": columns})
        conn.rollback()
finally:
    engine.dispose()