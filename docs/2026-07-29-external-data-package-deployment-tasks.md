# External Data Package Deployment Task Breakdown

> **For Codex:** REQUIRED SUB-SKILL: Use executing-plan skill to execute this task breakdown task-by-task.

**Goal:** Connect the 2026-07-29 external knowledge-data package to the local MySQL-backed application without copying its multi-gigabyte immutable assets into the repository.

**Approach:** Verify the supplied package against the tracked 2026-07-18 Atlas contract first. Configure the application to reference the immutable package by absolute path, retain all generated/user-owned runtime output beneath `backend/competition_app/runtime`, initialize the two MySQL schemas, and validate the running API after restart. Do not migrate the package's legacy SQLite file because it is not the main application's authentication contract.

**Skills:** @task-breakdown

**Tech Details:** FastAPI, MySQL 8.4 Docker container, `competition_app` and `competition_frontend` schemas, FAISS indexes, JSON/JSONL Atlas delivery package, PowerShell on Windows.

**Completion status (2026-07-29):** Tasks 1–5 completed. The supplied package is mounted read-only and has been read successfully by the knowledge service. The formal-content importer now also accepts the package's current English-schema JSON files and safely derives question-to-knowledge-point links from `kp_ids`; the initial MySQL import completed successfully. No legacy SQLite data was imported.

---

### Task 1: Verify the supplied immutable package

**Files:**

- Read: `backend/competition/backend-handoff-20260720/APP/backend/knowledge_atlas_contracts/2026-07-18/manifest.json`
- Read: `D:/A学业/赛事/小挑/揭榜挂帅/code design/code design v3/tiaozhanbei_data_2026-07-29/`

**Step 1: Run the Atlas importer in read-only mode**

Run:

```powershell
Set-Location backend/competition/backend-handoff-20260720
& 'D:/miniforge3/envs/fastapi/python.exe' -m APP.backend.scripts.import_knowledge_atlas_assets \
  --package-root 'D:/A学业/赛事/小挑/揭榜挂帅/code design/code design v3/tiaozhanbei_data_2026-07-29' \
  --data-root 'D:/A学业/赛事/小挑/揭榜挂帅/code design/code design v3/tiaozhanbei_data_2026-07-29/知识库管理组件/data/backend_delivery' \
  --video-root 'D:/A学业/赛事/小挑/揭榜挂帅/code design/code design v3/tiaozhanbei_data_2026-07-29/bilibili_video_page/runtime' \
  --verify-only
```

Expected: `ok: true`, four verified components, and zero copied files.

### Task 2: Configure external asset references and MySQL persistence

**Files:**

- Modify: `backend/competition_app/.env.local`

**Step 1: Preserve all existing secrets and update only deployment settings**

Set the MySQL connection to the `competition_app` account, point the handoff root to the supplied package, point both vector roots to `indexes`, and use a writable project-local knowledge runtime directory.

**Step 2: Inspect effective settings without printing secrets**

Run a Python settings summary that reports booleans and resolved paths only.

Expected: MySQL persistence enabled, handoff enabled, and all supplied asset paths exist.

### Task 3: Initialize schemas and mount the compatibility application

**Files:**

- Read/Write: MySQL `competition_app`
- Read/Write: MySQL `competition_frontend`

**Step 1: Initialize main schema**

Run from `backend/`:

```powershell
& 'D:/miniforge3/envs/fastapi/python.exe' -m competition_app.cli.app init-db
```

Expected: `database initialized`.

**Step 2: Start application once**

Run the FastAPI service on port 7860 to initialize the compatibility schema.

Expected: startup completes and the mounted handoff reports MySQL as its database backend.

### Task 4: Verify deployed behavior

**Files:**

- Read: `http://127.0.0.1:7860/health`
- Read: `http://127.0.0.1:7860/openapi.json`

**Step 1: Confirm service and database tables**

Check health, OpenAPI route presence, and that both MySQL schemas contain tables.

**Step 2: Confirm no source asset was copied**

Compare the supplied package's modification time and file count before and after startup.

Expected: service healthy, both schemas populated, and no asset duplication into the repository.

### Task 5: Import formal data only when the endpoint contract requires it

**Files:**

- Read: `backend/competition/backend-handoff-20260720/APP/backend/scripts/import_formal_learning_content.py`
- Read: `backend/competition/backend-handoff-20260720/APP/backend/scripts/import_formal_question_bank.py`

**Step 1: Inspect API behavior after the package is mounted**

Do not import the supplied legacy SQLite database. Determine whether formal question/knowledge records are read directly from the mounted package or require the supported import scripts.

Expected: any import is source-specific, idempotent, and applied only after a verified endpoint need.
