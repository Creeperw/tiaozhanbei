<!-- markdownlint-disable MD032 -->

# Competition App Personalized Review Card Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a modular Python application that runs a personalized TCM review-card request through six agents, deterministic review scheduling, audit-gated publication, MySQL persistence, and sanitized JSON snapshot export.

**Architecture:** A modular monolith exposes one application use case to CLI and FastAPI. A constrained planner creates a validated DAG, an asynchronous orchestrator invokes registered agents and tools, agents emit typed artifacts and writeback intents, and services persist approved state to MySQL while exporting a complete trace snapshot.

**Tech Stack:** Python 3.11+, Pydantic 2, httpx, FastAPI, Uvicorn, Typer, SQLAlchemy 2 with PyMySQL, pytest, pytest-asyncio.

## Global Constraints

- Use the existing Conda `torch` environment; do not create a virtual environment.
- Chat base URL is `https://llm-1nvjq1o5rj1bf5yi.cn-beijing.maas.aliyuncs.com/compatible-mode/v1` and model is `qwen-plus`.
- Embedding base URL is `https://api.siliconflow.cn/v1` and model is `Qwen/Qwen3-Embedding-4B`.
- Secrets are read only from `DASHSCOPE_API_KEY`, `SILICONFLOW_API_KEY`, and `MYSQL_PASSWORD`.
- MySQL defaults are host `localhost`, port `3306`, user `root`, and database `competition_app`.
- Tests use deterministic Stub clients and may not call external model APIs by default.
- Agent output is a proposal; only services may persist formal business state.
- Conversation summaries are runtime artifacts and may not automatically become long-term memories.

---

### Task 1: Package Configuration and Core Contracts

**Files:**
- Create: `competition_app/__init__.py`
- Create: `competition_app/config.py`
- Create: `competition_app/contracts/__init__.py`
- Create: `competition_app/contracts/base.py`
- Create: `competition_app/contracts/execution.py`
- Create: `competition_app/contracts/memory.py`
- Create: `competition_app/contracts/knowledge.py`
- Create: `competition_app/contracts/review.py`
- Create: `competition_app/contracts/resource.py`
- Create: `competition_app/tests/test_config.py`
- Create: `competition_app/tests/contracts/test_execution.py`

**Interfaces:**
- Produces: `Settings.from_env()`, `ExecutionPlan.validate_dag()`, typed artifact contracts, `AgentEnvelope[T]`, and `WritebackIntent`.

- [ ] Write failing tests proving Stub mode needs no API keys, Live mode reports missing variable names without values, secrets are absent from `repr`, valid DAGs pass, and unknown/cyclic dependencies fail.
- [ ] Run `conda run -n torch pytest competition_app/tests/test_config.py competition_app/tests/contracts/test_execution.py -q` and confirm failure due to missing modules.
- [ ] Implement the minimum Pydantic contracts and settings loader.
- [ ] Re-run the focused tests and confirm they pass.
- [ ] Run `conda run -n torch python -m compileall -q competition_app`.

### Task 2: Deterministic Review Domain

**Files:**
- Create: `competition_app/review/__init__.py`
- Create: `competition_app/review/math.py`
- Create: `competition_app/review/scheduler.py`
- Create: `competition_app/tests/review/test_math.py`
- Create: `competition_app/tests/review/test_scheduler.py`

**Interfaces:**
- Produces: `retention_estimate(now, last_review_at, stability_seconds)`, `is_due(now, next_review_at)`, and `ReviewScheduler.schedule(...) -> ReviewTask`.

- [ ] Write failing tests for retention using `last_review_at`, initial recall state, due system task, not-due user-requested task, and invalid timestamps.
- [ ] Run the focused tests and confirm expected failures.
- [ ] Implement the eight review stages and scheduler without LLM decisions.
- [ ] Re-run tests and confirm all pass.

### Task 3: Model Ports, Live Clients, and Deterministic Stubs

**Files:**
- Create: `competition_app/llm/__init__.py`
- Create: `competition_app/llm/base.py`
- Create: `competition_app/llm/openai_compatible.py`
- Create: `competition_app/llm/stub.py`
- Create: `competition_app/embeddings/__init__.py`
- Create: `competition_app/embeddings/base.py`
- Create: `competition_app/embeddings/siliconflow.py`
- Create: `competition_app/embeddings/stub.py`
- Create: `competition_app/tests/llm/test_stub.py`
- Create: `competition_app/tests/llm/test_live_client.py`

**Interfaces:**
- Produces: `ChatModel.complete_json(...)`, `EmbeddingModel.embed(...)`, `OpenAICompatibleChatModel`, `SiliconFlowEmbeddingModel`, and deterministic Stub implementations.

- [ ] Write failing tests for role-specific Stub output, OpenAI-compatible request shape, timeout mapping, one JSON-repair attempt, and secret-free exceptions.
- [ ] Implement transport-injectable `httpx` clients and deterministic Stubs.
- [ ] Run focused tests; default tests must not access the network.

### Task 4: Agent and Tool Registries

**Files:**
- Create: `competition_app/runtime/__init__.py`
- Create: `competition_app/runtime/agent_registry.py`
- Create: `competition_app/runtime/tool_registry.py`
- Create: `competition_app/runtime/permissions.py`
- Create: `competition_app/tests/runtime/test_registries.py`

**Interfaces:**
- Produces: `AgentRegistry.register/get`, `ToolRegistry.register/invoke`, and explicit role-to-tool authorization.

- [ ] Write failing tests for duplicate registration, unknown names, denied tools, allowed invocations, and parameter validation.
- [ ] Implement registries and permission checks.
- [ ] Run focused tests and confirm pass.

### Task 5: Six Agent Adapters and Context Compression

**Files:**
- Create: `competition_app/agents/__init__.py`
- Create: `competition_app/agents/base.py`
- Create: `competition_app/agents/planner.py`
- Create: `competition_app/agents/memory.py`
- Create: `competition_app/agents/knowledge_base.py`
- Create: `competition_app/agents/diagnosis.py`
- Create: `competition_app/agents/expert.py`
- Create: `competition_app/agents/audit.py`
- Create: `competition_app/prompts/*.txt`
- Create: `competition_app/tests/agents/test_memory.py`
- Create: `competition_app/tests/agents/test_agents.py`

**Interfaces:**
- Produces: six `AgentAdapter.run(context) -> AgentEnvelope` implementations. Memory produces `ConversationContextSummary`, `LearnerContextBrief`, and optional `LongTermMemoryCandidate` without persistence.

- [ ] Write failing tests that every agent produces a validated Envelope and correct producer, memory summary preserves source references, compression triggers deterministically, and summary candidates are not formal memories.
- [ ] Implement constrained planner templates and agent adapters over `ChatModel`.
- [ ] Run focused tests and confirm pass.

### Task 6: Orchestrator, Trace, Audit Branching, and Snapshot

**Files:**
- Create: `competition_app/runtime/orchestrator.py`
- Create: `competition_app/runtime/trace.py`
- Create: `competition_app/runtime/snapshot.py`
- Create: `competition_app/tests/runtime/test_orchestrator.py`
- Create: `competition_app/tests/runtime/test_snapshot.py`

**Interfaces:**
- Produces: `Orchestrator.execute(plan, context) -> ExecutionResult`, `TraceRecorder`, and `SnapshotExporter.export(...)`.

- [ ] Write failing tests for parallel independent steps, dependency ordering, one retry, one revise loop, human-review pause, reject behavior, and sanitized snapshots.
- [ ] Implement an asyncio DAG executor with trace recording and audit gates.
- [ ] Run focused tests and confirm pass.

### Task 7: MySQL Bootstrap, Migrations, and Repositories

**Files:**
- Create: `competition_app/db/__init__.py`
- Create: `competition_app/db/bootstrap.py`
- Create: `competition_app/db/migrations.py`
- Create: `competition_app/migrations/001_initial.sql`
- Create: `competition_app/repositories/__init__.py`
- Create: `competition_app/repositories/execution.py`
- Create: `competition_app/repositories/learner.py`
- Create: `competition_app/repositories/review.py`
- Create: `competition_app/repositories/resource.py`
- Create: `competition_app/services/writeback.py`
- Create: `competition_app/tests/db/test_migrations.py`
- Create: `competition_app/tests/services/test_writeback.py`

**Interfaces:**
- Produces: `DatabaseBootstrap.ensure_database()`, checksum-verified migrations, repository ports, and idempotent `WritebackExecutor.execute(intent)`.

- [ ] Write failing tests using an isolated test database for automatic creation, migration idempotency, checksum mismatch, transaction rollback, and duplicate idempotency keys.
- [ ] Implement SQLAlchemy/PyMySQL bootstrap, migration runner, repositories, and writeback service.
- [ ] Run DB tests only when `MYSQL_PASSWORD` is available; otherwise mark integration tests skipped with a clear reason.

### Task 8: Existing Knowledge Asset Adapter

**Files:**
- Create: `competition_app/tools/knowledge_assets.py`
- Create: `competition_app/tools/knowledge_retrieval.py`
- Create: `competition_app/tests/tools/test_knowledge_assets.py`
- Create: `competition_app/tests/fixtures/knowledge_delivery/`

**Interfaces:**
- Produces: `KnowledgeAssetRepository.resolve_topic(...)`, `get_chunk_evidence(...)`, and `KnowledgeRetrievalTool.build_evidence_pack(...)`.

- [ ] Write failing fixture tests for topic-to-KP alignment, strict Bridge priority, chunk provenance, weak similarity labeling, and vector fallback.
- [ ] Implement streaming/lazy adapters over the existing delivery format; do not copy the full production datasets into tests or MySQL.
- [ ] Run focused tests and a read-only smoke query against the real delivery directory.

### Task 9: Application Use Case and Stub End-to-End Flow

**Files:**
- Create: `competition_app/application/__init__.py`
- Create: `competition_app/application/container.py`
- Create: `competition_app/application/personalized_review_card.py`
- Create: `competition_app/tests/integration/test_review_card_stub.py`

**Interfaces:**
- Produces: `ApplicationContainer.build(settings)` and `PersonalizedReviewCardUseCase.execute(request)`.

- [ ] Write a failing integration test that asserts all six Agent Envelopes, context summary isolation, evidence references, review task identity, audit-pass publication, writeback intents, and snapshot consistency.
- [ ] Wire the registries, agents, scheduler, repositories, orchestrator, and snapshot exporter.
- [ ] Run the complete Stub integration test and confirm pass.

### Task 10: CLI and FastAPI

**Files:**
- Create: `competition_app/cli/__init__.py`
- Create: `competition_app/cli/app.py`
- Create: `competition_app/api/__init__.py`
- Create: `competition_app/api/app.py`
- Create: `competition_app/api/routes.py`
- Create: `competition_app/main.py`
- Create: `competition_app/tests/api/test_api.py`
- Create: `competition_app/tests/cli/test_cli.py`

**Interfaces:**
- Produces CLI commands `init-db`, `seed-demo`, `run-review-card`, `show-run`, `export-snapshot`, `serve`; and the specified `/api/v1` routes.

- [ ] Write failing CLI and API tests that use the same injected use case.
- [ ] Implement protocol-only adapters with no duplicated orchestration logic.
- [ ] Run focused tests and FastAPI schema generation.

### Task 11: Documentation and Full Verification

**Files:**
- Create: `competition_app/README.md`
- Create: `competition_app/.env.example`
- Create: `competition_app/requirements.txt`
- Create: `competition_app/tests/live/test_live_smoke.py`

**Interfaces:**
- Produces setup documentation, environment variable inventory without secret values, and opt-in Live smoke tests.

- [ ] Document architecture, commands, MySQL setup, Stub/Live modes, snapshot schema, security boundaries, and extension points.
- [ ] Run `conda run -n torch pytest competition_app/tests -q` and record pass/skip counts.
- [ ] Run `conda run -n torch python -m compileall -q competition_app`.
- [ ] Run `git diff --check -- competition_app`.
- [ ] When API keys are available, run opt-in chat, embedding, and full review-card Live smoke tests; otherwise report them as not run.
