"""Read-only scope, parent and audit evidence for uid6 plan acceptance."""
import hashlib
import json
import subprocess
import sys
from sqlalchemy import text
sys.path.insert(0, "/tmp")
from inspect_practice_dates import connect, emit
pid = int(subprocess.check_output(["systemctl", "show", "tiaozhanbei.service", "-p", "MainPID", "--value"]))
engine, settings = connect(pid)
try:
    with engine.connect() as conn:
        conn.execute(text("SET TRANSACTION READ ONLY"))
        rows = conn.execute(text(f"SELECT * FROM `{settings.mysql_database}`.learner_exam_plan_states WHERE learner_id=:learner"), {"learner": "USER_29d487bef1cf4845b64717c4e6c2c69b"}).mappings().all()
        for row in rows:
            emit({"columns": list(row), "exam_track_id": row.get("exam_track_id")})
            for key, value in row.items():
                if key.endswith("json") and isinstance(value, str):
                    data = json.loads(value)
                    emit({"payload_keys": list(data)})
                    for layer in ("long_term_plan", "short_term_plan", "learning_task"):
                        plan = data.get(layer)
                        emit({"layer": layer, "plan": {k: plan.get(k) for k in ("plan_id", "long_term_plan_id", "learner_id", "version", "status", "textbook_selection", "duration_days", "parent_plan_id", "parent_plan_version")} if isinstance(plan, dict) else plan})
                        if isinstance(plan, dict):
                            if layer == "long_term_plan":
                                from competition_app.agents.diagnosis import DiagnosisAgent
                                from competition_app.services.prerequisite_policy import all_prerequisite_courses
                                route = plan.get("planning_route") or {}
                                trusted = DiagnosisAgent._trusted_route_context(route)
                                emit({"parent_route_keys": list(route), "validator_courses": all_prerequisite_courses(route), "model_requirements": (trusted.get("textbook_route") or {}).get("route", {}).get("prerequisites", [])})
                            emit({"layer": layer, "keys": list(plan), "sha256": hashlib.sha256(json.dumps(plan, sort_keys=True, ensure_ascii=False).encode()).hexdigest()})
                            if layer == "short_term_plan":
                                emit({"short_term_content": plan.get("content")})
                                emit({"short_term_contracts": {k: plan.get(k) for k in ("goal_contract", "short_term_focus", "short_term_learning_package")}})
        conn.rollback()
finally:
    engine.dispose()