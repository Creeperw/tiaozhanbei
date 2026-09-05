"""Operator-owned, disposable D1 continuation. No production code is patched.

prepare only copies data; serve waits for the browser Execute button before
making model calls. A successful 100-case run is archived before cleanup.
"""
from __future__ import annotations

import argparse
import asyncio
from contextlib import asynccontextmanager
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import sys


RUN_ID = "D1V5EVO_922d4c174f424985a078b44c79895e23"
DB_NAME = "d1_isolated_922d4c_20260905"
DB_USER = "d1_eval_922d4c"
PORT = 7861
SOURCE = Path(__file__).resolve().parents[2]
ROOT = Path.home() / ".local/share/d1-isolated-922d4c-20260905"
ARCHIVE = SOURCE / "evaluation/evolution/results/isolated-922d4c-20260905"


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     separators=(",", ":")).encode()).hexdigest()


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def source_environment(pid):
    sys.path.insert(0, str(SOURCE / "backend"))
    from competition_app.config import _load_dotenv
    process_env = {}
    if pid:
        process_env = dict(item.split("=", 1) for item in
                           Path(f"/proc/{pid}/environ").read_text().split("\0") if "=" in item)
    paths = ([Path(process_env["COMPETITION_ENV_FILE"])]
             if process_env.get("COMPETITION_ENV_FILE") else
             [SOURCE / "backend/competition_app/.env", SOURCE / "backend/competition_app/.env.local"])
    values = {}
    for path in paths:
        values.update(_load_dotenv(path))
    values.update(process_env)
    return values


def snapshot(connection, previous):
    result = {key: previous[key] for key in ("evolution_enabled", "runtime_rules_enabled")}
    tables = {key: key[6:] for key in previous if key.startswith("table:")}
    tables.update(feedback_count="evolution_feedback", signature_count="evolution_signatures",
                  rule_count="evolution_rules", rule_run_count="evolution_rule_runs")
    import pymysql
    with connection.cursor() as cursor:
        for key, table in tables.items():
            # Names come exclusively from the allowlisted production snapshot.
            if not table.replace("_", "").isalnum():
                raise ValueError("invalid snapshot table")
            try:
                cursor.execute(f"SELECT COUNT(*) FROM `{table}`")
                result[key] = int(cursor.fetchone()[0])
            except pymysql.err.ProgrammingError as exc:
                if exc.args[0] != 1146:
                    raise
                result[key] = "unavailable"
    return result


def migrate_run(original, baseline, manifest_sha):
    if original["run_id"] != RUN_ID or original["status"] != "final_interrupted":
        raise ValueError("only the approved interrupted run may migrate")
    if original["qualification"].get("passed") is not True:
        raise ValueError("qualification is not passed")
    if len(original["final"]["receipts"]) != 23:
        raise ValueError("source checkpoint changed; review required")
    if original.get("environment_migrations"):
        raise ValueError("already migrated")
    for key in ("evolution_enabled", "runtime_rules_enabled", "feedback_count",
                "signature_count", "rule_count", "rule_run_count"):
        if baseline[key] != original["production_snapshot_before"][key]:
            raise ValueError("production rule state changed")
    changed = {key: {"before": value, "after": baseline.get(key)}
               for key, value in original["production_snapshot_before"].items()
               if value != baseline.get(key)}
    if changed != {"table:conversation_sessions": {"before": 726, "after": 727}}:
        raise ValueError("unreviewed production changes; migration refused")
    result = deepcopy(original)
    result["original_production_snapshot_before"] = deepcopy(original["production_snapshot_before"])
    result["production_snapshot_before"] = deepcopy(baseline)
    result["environment_migrations"] = [{
        "type": "operator_approved_isolated_continuation", "at": datetime.now(timezone.utc).isoformat(),
        "source_port": 7860, "destination_port": PORT,
        "source_run_sha256": digest(original), "code_manifest_sha256": manifest_sha,
        "observed_source_changes": changed,
        "checkpoint_sha256": digest(original["final"]),
        "candidate_digest": original["candidate_digest"],
        "completed_receipts_reused": 23,
        "limitation": "First 23 pairs ran before environment isolation; original source snapshot differs by one session. No score or model-input change is authorized.",
    }]
    return result


def prepare(pid):
    values = source_environment(pid)
    from competition_app.config import Settings
    import pymysql
    settings = Settings.from_env(values)
    if settings.mode != "live" or settings.chat_model != "deepseek-v4-flash":
        raise ValueError("source mode/model does not match")
    if settings.evolution_enabled or settings.evolution_rules_enabled:
        raise ValueError("production evolution must remain disabled")
    if ROOT.exists() or ARCHIVE.exists():
        raise ValueError("isolation/archive already exists; never overwrite")
    source_run = settings.runtime_root / "evaluation/d1-v5-evolution" / f"{RUN_ID}.json"
    original = json.loads(source_run.read_text())
    connection = pymysql.connect(host=settings.mysql_host, port=settings.mysql_port,
                                 user=settings.mysql_user, password=settings.mysql_password,
                                 database=settings.mysql_database, autocommit=True)
    baseline = snapshot(connection, original["production_snapshot_before"])
    migrate_run(original, baseline, "preflight")
    ROOT.mkdir(parents=True, mode=0o700)
    ARCHIVE.mkdir(parents=True, mode=0o700)
    os.chmod(ARCHIVE, 0o700)
    frozen = ROOT / "code"
    package = SOURCE / "backend/competition_app"
    for path in package.rglob("*"):
        relative = path.relative_to(package)
        if not path.is_file() or any(part in {"__pycache__", "tests", ".git"} for part in relative.parts):
            continue
        if path.suffix != ".py" and not (relative.parts[0] in {"data", "prompt_skills"} and path.suffix in {".json", ".jsonl", ".md", ".txt", ".yaml", ".yml"}):
            continue
        target = frozen / "backend/competition_app" / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
    dataset_relative = Path("evaluation/evolution/datasets/d1_three_stage_v5_advantage")
    shutil.copytree(SOURCE / dataset_relative, frozen / dataset_relative)
    copied_script = frozen / "evaluation/evolution/isolated_d1_runtime.py"
    shutil.copy2(Path(__file__), copied_script)
    manifest = {str(path.relative_to(frozen)): sha(path) for path in frozen.rglob("*") if path.is_file()}
    write_json(ARCHIVE / "code-manifest.json", manifest)
    write_json(ARCHIVE / "original-run.json", original)
    write_json(ARCHIVE / "migration.json", migrate_run(original, baseline, digest(manifest))["environment_migrations"][0])
    # Password remains inside a child process environment, never argv/stdout.
    command_env = {**os.environ, "MYSQL_PWD": settings.mysql_password or ""}
    common = ["--host", settings.mysql_host, "--port", str(settings.mysql_port), "--user", settings.mysql_user]
    dump_path = ROOT / "database.sql"
    with connection.cursor() as cursor:
        cursor.execute("SHOW DATABASES LIKE %s", (DB_NAME,))
        if cursor.fetchone():
            raise ValueError("temporary database exists; refusing reuse")
        cursor.execute(f"CREATE DATABASE `{DB_NAME}` CHARACTER SET utf8mb4")
    write_json(ROOT / "ownership.json", {"database": DB_NAME, "run_id": RUN_ID, "archive": str(ARCHIVE)})
    with dump_path.open("wb") as output:
        subprocess.run(["mysqldump", *common, "--single-transaction", "--quick", "--skip-lock-tables",
                        "--no-tablespaces", "--skip-triggers", settings.mysql_database],
                       env=command_env, stdout=output, check=True)
    with dump_path.open("rb") as source:
        subprocess.run(["mysql", *common, DB_NAME], env=command_env, stdin=source, check=True)
    dump_path.unlink()
    readonly_password = secrets.token_urlsafe(32)
    with connection.cursor() as cursor:
        cursor.execute("CREATE USER %s@'localhost' IDENTIFIED BY %s", (DB_USER, readonly_password))
        cursor.execute(f"GRANT SELECT ON `{DB_NAME}`.* TO %s@'localhost'", (DB_USER,))
    connection.select_db(DB_NAME)
    copied_baseline = snapshot(connection, original["production_snapshot_before"])
    if copied_baseline != baseline or json.loads(source_run.read_text()) != original:
        raise ValueError("source changed while copying")
    connection.close()
    migrated = migrate_run(original, copied_baseline, digest(manifest))
    state = ROOT / "state/evaluation"
    write_json(state / "d1-v5-evolution" / f"{RUN_ID}.json", migrated)
    discovery_id = original["discovery_run_id"]
    shutil.copytree(settings.runtime_root / "evaluation/d1-v5-discovery", state / "d1-v5-discovery")
    work = source_run.parent / RUN_ID
    if work.exists():
        shutil.copytree(work, state / "d1-v5-evolution" / RUN_ID)
    discovery_file = state / "d1-v5-discovery" / f"{discovery_id}.json"
    write_json(ARCHIVE / "source-discovery.json", json.loads(discovery_file.read_text()) if discovery_file.exists() else {
        "discovery_run_id": discovery_id, "status": "original_file_unavailable",
        "note": "Final continuation uses persisted reviewed candidate and qualification; discovery was not recreated or substituted."})
    save_config(settings, readonly_password)


def save_config(settings, readonly_password):
    config = {"mode": "live", "model": settings.chat_model, "base_url": settings.chat_base_url,
              "api_key": settings.llm_api_key, "api_keys": list(settings.llm_api_keys),
              "timeout": settings.llm_timeout_seconds,
              "mysql_host": settings.mysql_host, "mysql_port": settings.mysql_port,
              "readonly_password": readonly_password,
              "cleanup_user": settings.mysql_user, "cleanup_password": settings.mysql_password,
              "source_root": str(SOURCE), "archive": str(ARCHIVE)}
    write_json(ROOT / "private.json", config)
    os.chmod(ROOT / "private.json", 0o600)
    print(json.dumps({"prepared": True, "port": PORT, "completed": 23, "root": str(ROOT),
                      "archive": str(ARCHIVE), "mode": "live"}))


def finish_prepare(pid):
    values = source_environment(pid)
    from competition_app.config import Settings
    import pymysql
    settings = Settings.from_env(values)
    if (ROOT / "private.json").exists():
        raise ValueError("preparation is already complete")
    ownership = json.loads((ROOT / "ownership.json").read_text())
    if ownership != {"database": DB_NAME, "run_id": RUN_ID, "archive": str(ARCHIVE)}:
        raise ValueError("ownership mismatch")
    original = json.loads((ARCHIVE / "original-run.json").read_text())
    current = json.loads((settings.runtime_root / "evaluation/d1-v5-evolution" / f"{RUN_ID}.json").read_text())
    if current != original or settings.mode != "live" or settings.chat_model != "deepseek-v4-flash":
        raise ValueError("source checkpoint or model changed")
    connection = pymysql.connect(host=settings.mysql_host, port=settings.mysql_port,
        user=settings.mysql_user, password=settings.mysql_password, database=DB_NAME, autocommit=True)
    baseline = snapshot(connection, original["production_snapshot_before"])
    migrate_run(original, baseline, "preflight")
    # Freeze the corrected deployment helper before starting any model work.
    shutil.copy2(Path(__file__), ROOT / "code/evaluation/evolution/isolated_d1_runtime.py")
    frozen = ROOT / "code"
    manifest = {str(p.relative_to(frozen)): sha(p) for p in frozen.rglob("*") if p.is_file()}
    write_json(ARCHIVE / "code-manifest.json", manifest)
    migrated = migrate_run(original, baseline, digest(manifest))
    write_json(ARCHIVE / "migration.json", migrated["environment_migrations"][0])
    write_json(ROOT / "state/evaluation/d1-v5-evolution" / f"{RUN_ID}.json", migrated)
    write_json(ARCHIVE / "source-discovery.json", {
        "discovery_run_id": original["discovery_run_id"], "status": "original_file_unavailable",
        "note": "Final continuation uses persisted reviewed candidate and qualification; discovery was not recreated or substituted."})
    password = secrets.token_urlsafe(32)
    with connection.cursor() as cursor:
        cursor.execute("ALTER USER %s@'localhost' IDENTIFIED BY %s", (DB_USER, password))
    connection.close()
    save_config(settings, password)


def verify_frozen(archive):
    frozen = ROOT / "code"
    manifest = json.loads((archive / "code-manifest.json").read_text())
    for name, expected in manifest.items():
        if sha(frozen / name) != expected:
            raise ValueError(f"frozen code changed: {name}")


def repair_deployment():
    config = json.loads((ROOT / "private.json").read_text())
    archive = Path(config["archive"])
    source = Path(config["source_root"])
    path = ROOT / "state/evaluation/d1-v5-evolution" / f"{RUN_ID}.json"
    failed = json.loads(path.read_text())
    original = json.loads((archive / "original-run.json").read_text())
    new_errors = failed["final"]["technical_errors"][len(original["final"]["technical_errors"]):]
    if (failed["status"] != "final_failed" or failed["final"]["receipts"] != original["final"]["receipts"]
            or len(new_errors) != 2 or any(error["error_digest"] !=
            "8a67c7fb948490cad4deddff50f8a5f723f9a1d2558e9a4c9bc06699d7667ce1" for error in new_errors)):
        raise ValueError("not the verified pre-model deployment failure")
    if (archive / "deployment-failure.json").exists():
        raise ValueError("deployment recovery already used")
    write_json(archive / "deployment-failure.json", failed)
    write_json(archive / "code-manifest-before-deployment-repair.json",
               json.loads((archive / "code-manifest.json").read_text()))
    frozen = ROOT / "code"
    shutil.copytree(source / "backend/competition_app/prompt_skills",
                    frozen / "backend/competition_app/prompt_skills")
    shutil.copy2(Path(__file__), frozen / "evaluation/evolution/isolated_d1_runtime.py")
    manifest = {str(p.relative_to(frozen)): sha(p) for p in frozen.rglob("*")
                if p.is_file() and "__pycache__" not in p.parts}
    write_json(archive / "code-manifest.json", manifest)
    migrated = migrate_run(original, failed["production_snapshot_before"], digest(manifest))
    migrated["deployment_recoveries"] = [{"reason": "missing_prompt_skills_before_model_call",
        "failed_attempts_retained_in": "deployment-failure.json", "failed_attempt_count": 2,
        "original_completed_receipts_unchanged": True, "original_partial_arm_reused": True,
        "prompt_files_copied_verbatim": True, "at": datetime.now(timezone.utc).isoformat()}]
    write_json(path, migrated)
    write_json(archive / "migration.json", migrated["environment_migrations"][0])
    print("Deployment resources repaired; failed attempts archived; original checkpoint restored.")


def cleanup(config):
    import pymysql
    archive = Path(config["archive"])
    if not (archive / "archive-verified.json").is_file():
        raise ValueError("verified complete archive required before cleanup")
    verified = json.loads((archive / "archive-verified.json").read_text())
    for name, expected in verified["files"].items():
        if sha(archive / name) != expected:
            raise ValueError("archive checksum mismatch")
    ownership = json.loads((ROOT / "ownership.json").read_text())
    if ownership["database"] != DB_NAME or ownership["run_id"] != RUN_ID:
        raise ValueError("cleanup ownership mismatch")
    connection = pymysql.connect(host=config["mysql_host"], port=config["mysql_port"],
                                 user=config["cleanup_user"], password=config["cleanup_password"], autocommit=True)
    with connection.cursor() as cursor:
        cursor.execute(f"DROP DATABASE IF EXISTS `{DB_NAME}`")
        cursor.execute("DROP USER IF EXISTS %s@'localhost'", (DB_USER,))
    connection.close()
    shutil.rmtree(ROOT)
    write_json(archive / "cleanup.json", {"temporary_database_removed": True,
               "readonly_account_removed": True, "temporary_runtime_removed": True,
               "production_database_modified": False, "at": datetime.now(timezone.utc).isoformat()})


def serve():
    config = json.loads((ROOT / "private.json").read_text())
    archive = Path(config["archive"])
    verify_frozen(archive)
    sys.path.insert(0, str(ROOT / "code/backend"))
    from fastapi import FastAPI, HTTPException, Request
    from fastapi.responses import HTMLResponse
    from sqlalchemy import create_engine
    from sqlalchemy.engine import URL
    import uvicorn
    from competition_app.agents.expert import ExpertAgent
    from competition_app.agents.audit import AuditAgent
    from competition_app.agents.evolution import EvolutionAgent
    from competition_app.application.container import StreamingChatModel
    from competition_app.llm.openai_compatible import OpenAICompatibleChatModel
    from competition_app.runtime.model_trace import ModelTraceRecorder
    from competition_app.evaluation.d1_semantic_judge import D1LearnerVisibleSemanticJudge
    from competition_app.evaluation.d1_experimental_candidate import D1ExperimentalCandidateCompiler
    from competition_app.evaluation.d1_v5_evolution import D1V5EvolutionSandboxService, D1V5EvolutionExecutor
    from competition_app.repositories.evolution import SqlEvolutionRepository
    trace = ModelTraceRecorder(full_capture=False)
    model = StreamingChatModel(OpenAICompatibleChatModel(
        config["base_url"], config["api_key"], config["model"],
        api_keys=tuple(config["api_keys"] or [config["api_key"]]),
        timeout_seconds=config["timeout"], total_timeout_seconds=config["timeout"]),
        model_trace_recorder=trace, stream=False)
    engine = create_engine(URL.create("mysql+pymysql", username=DB_USER,
                           password=config["readonly_password"], host=config["mysql_host"],
                           port=config["mysql_port"], database=DB_NAME), pool_pre_ping=True)
    service = D1V5EvolutionSandboxService(
        evolution_agent=EvolutionAgent(model), candidate_compiler=D1ExperimentalCandidateCompiler(model, model_name=config["model"]),
        executor=D1V5EvolutionExecutor(expert_agent=ExpertAgent(model), audit_agent=AuditAgent(model),
             semantic_judge=D1LearnerVisibleSemanticJudge(model), model_trace_recorder=trace,
             model_name=config["model"], max_repair_attempts=1, step_timeout_seconds=300,
             audit_timeout_seconds=600, judge_timeout_seconds=240, arm_timeout_seconds=2400),
        runtime_mode="live", state_root=ROOT / "state/evaluation/d1-v5-evolution",
        discovery_state_root=ROOT / "state/evaluation/d1-v5-discovery",
        production_repository=SqlEvolutionRepository(engine), production_evolution_enabled=False,
        production_rules_enabled=False)
    service._assert_production_unchanged(service._run(RUN_ID))
    job = None
    finished = False
    failure = None

    async def drive():
        nonlocal finished, failure
        try:
            while True:
                status = service.status(RUN_ID)
                if status["status"] in {"completed", "final_failed", "failed", "final_interrupted", "stale"}:
                    evidence = service.evidence(RUN_ID)
                    write_json(archive / "latest-evidence.json", evidence)
                    if status["status"] != "completed" or not evidence["final"]["integrity"]["passed"]:
                        failure = "Evaluation stopped; evidence retained; no automatic retry or destructive cleanup."
                        return
                    original = json.loads((archive / "original-run.json").read_text())
                    if len(evidence["final"]["receipts"]) != 100:
                        raise ValueError("100 completed receipts required")
                    if evidence["final"]["receipts"][:23] != original["final"]["receipts"]:
                        raise ValueError("original receipts were changed")
                    verify_frozen(archive)
                    await service.cleanup(RUN_ID)
                    write_json(archive / "final-evidence.json", service.evidence(RUN_ID))
                    final = evidence["final"]
                    write_json(archive / "metrics.json", {k: v for k, v in final.items() if k not in {"receipts", "pair_checkpoints"}})
                    write_json(archive / "archive-verified.json", {"run_id": RUN_ID, "completed": 100,
                        "files": {p.name: sha(p) for p in archive.glob("*.json") if p.name != "archive-verified.json"}})
                    finished = True
                    server.should_exit = True
                    return
                await asyncio.sleep(10)
        except Exception as exc:
            failure = type(exc).__name__
            write_json(archive / "driver-error.json", {"error_type": failure})

    @asynccontextmanager
    async def lifespan(app):
        try:
            yield
        finally:
            if job and not job.done():
                job.cancel()
                await asyncio.gather(job, return_exceptions=True)
            await service.shutdown()
            engine.dispose()

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    # Local operator page only: no business API, user login, or background dispatchers.
    @app.get("/", response_class=HTMLResponse)
    async def panel():
        return """<!doctype html><meta charset='utf-8'><title>D1 隔离续跑 7861</title>
<style>body{font:16px system-ui;max-width:950px;margin:40px auto;background:#f4f7fb;color:#172033}button{padding:12px 28px;background:#1769e0;color:white;border:0;border-radius:8px}pre{white-space:pre-wrap;background:white;padding:24px}</style>
<h1>D1 V5.1 独立 Live 评测</h1><p>7861 · deepseek-v4-flash · 原 run · 保留23条 · 独立只读数据库</p>
<p>Execute 后后台维持租约；关闭页面不会停止。成功后自动归档、停服并清理临时环境。</p>
<button id='execute'>Execute</button><pre id='result'>正在读取进度…</pre><script>
const out=document.querySelector('#result');async function refresh(){try{const r=await fetch('/status');out.textContent=JSON.stringify(await r.json(),null,2)}catch(e){out.textContent+='\\n服务已停止或连接中断；请检查归档与清理记录。'}}
document.querySelector('#execute').onclick=async()=>{document.querySelector('#execute').disabled=true;const r=await fetch('/resume',{method:'POST'});out.textContent=JSON.stringify(await r.json(),null,2)};refresh();setInterval(refresh,10000);
</script>"""

    @app.get("/status")
    async def status_endpoint():
        return {"mode": "live", "model": config["model"], "port": PORT,
                "status": service.status(RUN_ID), "driver_error": failure, "archive": str(archive)}

    # Request annotation must be resolvable despite function-local imports.
    globals()["Request"] = Request

    @app.post("/resume")
    async def resume_endpoint(request: Request):
        nonlocal job
        if request.headers.get("origin") != f"http://127.0.0.1:{PORT}":
            raise HTTPException(403, "Local browser origin required")
        if job and not job.done():
            return service.status(RUN_ID)
        verify_frozen(archive)
        result = service.resume(RUN_ID)
        job = asyncio.create_task(drive())
        return result

    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=PORT, log_level="info"))
    server.run()
    if finished:
        cleanup(config)
        print("100 cases archived; isolated database, account and runtime removed.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["prepare", "finish-prepare", "repair-deployment", "serve", "cleanup"])
    parser.add_argument("--source-pid", type=int)
    args = parser.parse_args()
    if args.action == "prepare":
        prepare(args.source_pid)
    elif args.action == "finish-prepare":
        finish_prepare(args.source_pid)
    elif args.action == "repair-deployment":
        repair_deployment()
    elif args.action == "serve":
        serve()
    else:
        cleanup(json.loads((ROOT / "private.json").read_text()))