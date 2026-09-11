"""Read-only uid6 checks; execute on server, never construct live containers."""
import hashlib
import json
import subprocess
import sys

from sqlalchemy import text

sys.path.insert(0, "/tmp")
from inspect_practice_dates import connect, emit


pid = int(subprocess.check_output(["systemctl", "show", "tiaozhanbei.service", "-p", "MainPID", "--value"]))
engine, settings = connect(pid)
learner = "USER_29d487bef1cf4845b64717c4e6c2c69b"
try:
    with engine.connect() as conn:
        conn.execute(text("SET TRANSACTION READ ONLY"))
        assert conn.execute(text("SELECT id FROM users WHERE username=:name"), {"name": "judge_tjutcm_tcm"}).scalar_one() == 6
        for table in ("question_attempts", "daily_task_items", "learning_activity_records"):
            rows = [dict(row) for row in conn.execute(text(f"SELECT * FROM `{table}` WHERE user_id=6 ORDER BY id")).mappings()]
            emit({"table": table, "count": len(rows), "sha256": hashlib.sha256(json.dumps(rows, sort_keys=True, default=str).encode()).hexdigest()})
        db = settings.mysql_database
        for table in ("learner_exam_plan_states", "learner_plan_states"):
            count = conn.execute(text(f"SELECT COUNT(*) FROM `{db}`.`{table}` WHERE learner_id=:learner"), {"learner": learner}).scalar_one()
            emit({"table": table, "count": count})
        rows = conn.execute(text(f"SELECT thread_id, execution_id, status, created_at, updated_at FROM `{db}`.workflow_run_states WHERE learner_id=:learner ORDER BY created_at DESC LIMIT 3"), {"learner": learner}).mappings().all()
        for row in rows:
            emit({"run": dict(row)})
        active = conn.execute(text(f"SELECT COUNT(*) FROM `{db}`.workflow_run_states WHERE status IN ('running', 'pending')")).scalar_one()
        emit({"active_runs_all_users": active, "pid": pid, "mode": settings.mode})
        if "--idle-required" in sys.argv:
            assert active == 0, "Do not restart while workflows are active"
        conn.rollback()
finally:
    engine.dispose()