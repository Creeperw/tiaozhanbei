"""Read-only formal compiler evidence; no live application container."""
import json
import subprocess
import sys
from pathlib import Path

from sqlalchemy import text
from competition_app.runtime.sqlalchemy_checkpointer import SqlAlchemyCheckpointSaver

sys.path.insert(0, "/tmp")
from inspect_practice_dates import connect

execution = "EXE_9834b3b5157cf6a3a9844793ac59a11d"
learner = "USER_29d487bef1cf4845b64717c4e6c2c69b"
pid = int(subprocess.check_output(["systemctl", "show", "tiaozhanbei.service", "-p", "MainPID", "--value"]))
engine, settings = connect(pid)


def visit(value, path):
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    if isinstance(value, dict):
        for key, item in value.items():
            if key == "compiled_plan_contract":
                print(json.dumps({"path": path + "." + key, "compiled_plan_contract": item}, ensure_ascii=False, default=str))
            elif key not in {"reasoning", "raw_input", "raw_output", "messages", "system_prompt", "failure_model_trace"}:
                visit(item, path + "." + key)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            visit(item, path + f"[{index}]")


try:
    with engine.connect() as conn:
        conn.execute(text(f"USE `{settings.mysql_database}`"))
        conn.commit()
        conn.execute(text("SET TRANSACTION READ ONLY"))
        row = conn.execute(text("SELECT learner_id,thread_id,payload_json FROM workflow_run_states WHERE execution_id=:execution"), {"execution": execution}).mappings().one()
        assert row["learner_id"] == learner
        saver = SqlAlchemyCheckpointSaver(engine)
        checkpoints = conn.execute(text("SELECT * FROM langgraph_checkpoints WHERE thread_id=:thread ORDER BY checkpoint_id DESC LIMIT 1"), {"thread": row["thread_id"]}).all()
        print(json.dumps({"checkpoint_count": len(checkpoints)}))
        for checkpoint in checkpoints:
            state = saver._tuple_from_row(conn, checkpoint)
            visit(state.checkpoint["channel_values"], "checkpoint")
        for artifact in conn.execute(text("SELECT artifact_type,payload_json FROM artifacts WHERE execution_id=:execution"), {"execution": execution}).mappings():
            value = artifact["payload_json"]
            visit(json.loads(value) if isinstance(value, str) else value, artifact["artifact_type"])
        capture = json.loads(Path("/srv/tiaozhanbei/runtime/competition_app/evaluation/compiler-failure-evidence/failure-1.json").read_text())
        saved = conn.execute(text("SELECT payload_json FROM learner_exam_plan_states WHERE learner_id=:learner AND exam_track_id=:exam"), {"learner": learner, "exam": "EXAM_2025_TCM_PHYSICIAN"}).scalar_one()
        saved = json.loads(saved) if isinstance(saved, str) else saved
        plan = saved["short_term_plan"]
        document = capture["document"]
        print(json.dumps({
            "version": plan["version"], "saved_content_equals_failed_source": plan["content"] == document,
            "source_sanitized": capture["sanitization_changed_evidence"],
            "nodes": [{"index": index, "value": node, "verbatim_in_source": node in document} for index, node in enumerate(plan["short_term_learning_package"]["progression_nodes"])],
            "learning_task_is_none": saved.get("learning_task") is None,
        }, ensure_ascii=False))
        conn.rollback()
finally:
    engine.dispose()