# Frontend Productization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the formal React frontend into a continuous learning workspace with a six-agent user-facing execution path, reliable scrolling, polished chat and fully adapted training modules.

**Architecture:** Keep backend contracts and page-intent routing authoritative. Add a pure presentation adapter between runtime events and UI components, then let focused React components render that display model. Fix authentication and scrolling at their owning boundaries instead of adding page-specific workarounds.

**Tech Stack:** React 19, Tailwind CSS 4, Zustand 5, Vitest, Testing Library, FastAPI, pytest, Playwright MCP.

## Global Constraints

- Do not modify `/demo/` or its static data-flow testing files.
- Do not run Live pytest from WSL; Live validation is performed from the started frontend Execute flow.
- Use `/home/wangjl/miniconda3/envs/torch/bin/python` for Python validation.
- Do not use Git, create commits or create worktrees.
- Keep agent output natural-language first; expose system IDs and tool payloads only inside technical details.
- The six user-facing agents are task planning, memory management, learning diagnosis, knowledge management, expert and audit referee.
- Auxiliary services must be nested under an owning agent or shown as a deterministic system action, never as another agent.

---

### Task 1: Restore the unauthenticated React entry point

**Files:**
- Modify: `backend/competition_app/api/app.py`
- Modify: `backend/competition_app/tests/api/test_auth.py`

**Interfaces:**
- Consumes: `create_app(container, auth_required=True)` and configured `frontend_dist_root`.
- Produces: public GET access for built static assets while all business APIs remain protected.

- [ ] **Step 1: Add a failing authentication-boundary test**

Create a temporary frontend dist containing `index.html`, `assets/app.js`, `assets/app.css` and `favicon.ico`. Assert that anonymous GET requests return `200` for the entry document and those static files, while `/api/v1/review-cards` remains `401`.

- [ ] **Step 2: Verify the regression test fails**

Run:

```bash
PYTHONPATH=/mnt/d/code/AI/deeplearning/tiaozhanbei/backend /home/wangjl/miniconda3/envs/torch/bin/python -m pytest competition_app/tests/api/test_auth.py -q
```

Expected: asset requests fail with `401` before the fix.

- [ ] **Step 3: Make only deployable frontend assets public**

Define a path predicate in `create_app` that permits `/assets/*`, `/design-images/*`, `/assistant-character/*` and `/favicon.ico`. Do not permit `/api`, mounted business routes or arbitrary unknown paths.

- [ ] **Step 4: Re-run authentication tests**

Run the command from Step 2. Expected: all tests pass and API isolation assertions remain green.

---

### Task 2: Introduce the six-agent presentation model

**Files:**
- Create: `frontend/llm/src/agentPresentationModel.js`
- Create: `frontend/llm/src/agentPresentationModel.test.js`
- Modify: `frontend/llm/src/workflowChatClient.js`
- Modify: `frontend/llm/src/workflowChatClient.test.jsx`
- Modify: `frontend/llm/src/stores/useLangGraphStore.js`
- Modify: `frontend/llm/src/assistantDockModel.test.jsx`

**Interfaces:**
- Consumes: authoritative SSE events containing `event`, `agent`, `step_id`, `status`, `message`, `query` and timestamps.
- Produces: `buildAgentPresentation(nodes)` returning six stable role records with `key`, `label`, `description`, `status`, `summary`, `details`, `tools`, `startedAt` and `endedAt`.

- [ ] **Step 1: Write failing mapping tests**

Test exact ownership rules:

```js
expect(resolveAgentRole('planner_agent')).toBe('planner');
expect(resolveAgentRole('memory_agent')).toBe('memory');
expect(resolveAgentRole('diagnosis_agent')).toBe('diagnosis');
expect(resolveAgentRole('knowledge_base_agent')).toBe('knowledge');
expect(resolveAgentRole('paper_blueprint_agent')).toBe('expert');
expect(resolveAgentRole('learning_plan_service')).toBe('diagnosis');
expect(resolveAgentRole('review_scheduler')).toBe('system');
expect(resolveAgentRole('audit_agent')).toBe('audit');
```

Also assert six stable seats, inactive text `本次无需参与`, Chinese status labels, duplicate-log removal and omission of empty tool calls from the user summary.

- [ ] **Step 2: Verify the presentation tests fail**

Run:

```bash
npm run test:unit -- src/agentPresentationModel.test.js src/workflowChatClient.test.jsx
```

Expected: missing module or unmet six-role assertions.

- [ ] **Step 3: Preserve agent identity in trace events**

Change `runtimeEventToTrace` so step events carry the authoritative `agent`, `stepId` and service status in addition to the existing event type. Keep run/interruption/failure events backward compatible.

- [ ] **Step 4: Build the pure role adapter**

Export `AGENT_ROLES`, `resolveAgentRole`, `agentStatusLabel`, `sanitizeAgentLog` and `buildAgentPresentation`. Collapse `default_route_resolver` and `learning_plan_service` into diagnosis, paper blueprint/assembly and knowledge explanation into expert, and keep review scheduling as a system detail.

- [ ] **Step 5: Update the Zustand reducer without inventing generic nodes**

Use `event.agent` to identify nodes. Keep persisted historical events readable, but remove creation of repeated blank `web_search` tool items and repeated identical logs.

- [ ] **Step 6: Run adapter/store tests**

Run the command from Step 2 plus `src/assistantDockModel.test.jsx`. Expected: all selected tests pass.

---

### Task 3: Replace observability demo UI with the six-agent task desk

**Files:**
- Modify: `frontend/llm/src/components/AgentTimeline.jsx`
- Create: `frontend/llm/src/components/AgentTimeline.test.jsx`
- Modify: `frontend/llm/src/index.css`

**Interfaces:**
- Consumes: `buildAgentPresentation(nodes)` and optional references.
- Produces: an accessible `执行进度` complementary region with six agent seats and expandable technical details.

- [ ] **Step 1: Write failing component tests**

Render planning, knowledge and audit events. Assert all six Chinese role names exist; involved roles show `执行中` or `已完成`; uninvolved roles show `本次无需参与`; `Planner`, `Executor`, `Tool Calls` and `running` are absent from default text; technical details reveal internal names only after clicking.

- [ ] **Step 2: Verify the timeline tests fail**

Run:

```bash
npm run test:unit -- src/components/AgentTimeline.test.jsx
```

- [ ] **Step 3: Implement the compact six-seat task desk**

Render a concise progress header, six stable role rows, status icon plus text, a one-line current summary and a per-role details disclosure. Keep source inspection as a separate action below the roles.

- [ ] **Step 4: Add responsive and reduced-motion styles**

Desktop uses a compact right rail; medium screens use a drawer; mobile uses a bottom sheet. Add `prefers-reduced-motion` overrides and ensure each scrollable detail region has a visible focus state.

- [ ] **Step 5: Run component tests**

Run the command from Step 2. Expected: all assertions pass.

---

### Task 4: Productize the formal conversation workspace

**Files:**
- Modify: `frontend/llm/src/components/ChatInterface.jsx`
- Modify: `frontend/llm/src/components/ChatInterface.test.jsx`
- Modify: `frontend/llm/src/index.css`

**Interfaces:**
- Consumes: existing conversation endpoints, stream client, message actions, `AgentTimeline` and `ui_actions`.
- Produces: a responsive conversation workspace with an independently scrolling message region, sticky composer and discoverable execution progress.

- [ ] **Step 1: Add failing chat UX tests**

Assert the empty conversation presents useful starter prompts, the composer has a visible label, assistant output is an article, interrupted runs present the follow-up prompt, failed runs retain retry, and business actions retain destination params.

- [ ] **Step 2: Verify the chat tests fail**

Run:

```bash
npm run test:unit -- src/components/ChatInterface.test.jsx
```

- [ ] **Step 3: Separate chat rendering responsibilities**

Extract small local components for empty welcome, message actions, execution trigger and composer status. Keep network/session behavior unchanged. Replace English speaker/status labels with consistent Chinese user language.

- [ ] **Step 4: Fix message and composer layout**

Make the message list the only flexible vertical scroller. Keep the composer sticky inside the conversation column, constrain prose width, provide explicit upload/stop/send labels and prevent the right execution rail from shrinking message content below a usable width.

- [ ] **Step 5: Run chat and protocol tests**

Run:

```bash
npm run test:unit -- src/components/ChatInterface.test.jsx src/chatProtocol.test.js src/workflowChatClient.test.jsx
```

Expected: all selected tests pass.

---

### Task 5: Fix application-shell and workshop scrolling

**Files:**
- Modify: `frontend/llm/src/components/AppShell.jsx`
- Modify: `frontend/llm/src/components/AppShell.test.jsx`
- Modify: `frontend/llm/src/components/DashboardPage.jsx`
- Modify: `frontend/llm/src/components/DashboardPage.test.jsx`
- Modify: `frontend/llm/src/index.css`

**Interfaces:**
- Consumes: `data-page`, `data-mode` and existing responsive breakpoints.
- Produces: explicit scroll ownership via `data-scroll-region`, with complete vertical access on desktop and natural document flow on mobile.

- [ ] **Step 1: Add failing scroll-ownership tests**

Assert the main element exposes `data-scroll-region="page"` for dashboard/practice and `data-scroll-region="contained"` for assistant/knowledge. Add a CSS contract test that the dashboard desktop selector resolves to `overflow-y: auto` rather than `hidden`.

- [ ] **Step 2: Verify scroll tests fail**

Run:

```bash
npm run test:unit -- src/components/AppShell.test.jsx src/components/DashboardPage.test.jsx
```

- [ ] **Step 3: Assign scroll ownership in AppShell**

Derive the scroll-region attribute from shell mode. Use `min-height: 0` on nested grid/flex containers and `overflow-y: auto` on page regions. Preserve contained scrolling for full-screen chat and knowledge canvases.

- [ ] **Step 4: Remove conflicting late CSS overrides**

Consolidate duplicate dashboard selectors so desktop, tablet and mobile each have one intentional rule. Ensure training module bodies, knowledge-card detail and paper workspace can grow beyond the viewport.

- [ ] **Step 5: Run shell/workshop tests**

Run the command from Step 2 plus `src/components/PracticePage.test.jsx`. Expected: all selected tests pass.

---

### Task 6: Finish training-module presentation and states

**Files:**
- Modify: `frontend/llm/src/components/PracticePage.jsx`
- Modify: `frontend/llm/src/components/PracticePage.test.jsx`
- Modify: `frontend/llm/src/components/QuestionTrainingPanel.jsx`
- Modify: `frontend/llm/src/components/KnowledgeCardLibrary.jsx`
- Modify: `frontend/llm/src/components/PaperGenerationPanel.jsx`
- Modify: `frontend/llm/src/index.css`

**Interfaces:**
- Consumes: existing workshop overview, card, paper, save and submit contracts.
- Produces: task-oriented module navigation, resource-reader knowledge cards and exam-style paper workspace with complete loading/empty/error states.

- [ ] **Step 1: Add failing module-state tests**

Assert module navigation uses tabs with Chinese names; an empty knowledge-card library offers a direct learning action; resource coverage labels distinguish repository and web supplementation; paper cards expose score/question/time metadata; expired papers disable editing but retain submission behavior.

- [ ] **Step 2: Verify selected tests fail**

Run:

```bash
npm run test:unit -- src/components/PracticePage.test.jsx src/components/KnowledgeCardLibrary.test.jsx src/components/PaperGenerationPanel.test.jsx
```

If the two panel test files do not exist, create them before running this command.

- [ ] **Step 3: Reshape the workshop shell**

Use a compact module switcher and one primary content region. Hide the generic training-output card until a module produces an artifact. Keep evidence inspection secondary.

- [ ] **Step 4: Reshape knowledge cards as a reader**

Use a searchable/list navigation column and semantic article detail. Group explanation, textbook chunks, videos and questions with counts, provenance and fallback status. Provide meaningful loading, empty, incomplete and error messages.

- [ ] **Step 5: Reshape papers as an exam workspace**

Use a paper library, exam header, sticky timer/save state, question navigator and readable question canvas. Preserve autosave, explicit submit and expiry handling.

- [ ] **Step 6: Run all module tests**

Run the command from Step 2 and `src/components/QuestionWorkspacePage.test.jsx`. Expected: all selected tests pass.

---

### Task 7: Complete cross-page, accessibility and visual consistency checks

**Files:**
- Modify: `frontend/llm/src/App.test.jsx`
- Modify: `frontend/llm/src/App.css`
- Modify: `frontend/llm/src/index.css`
- Modify: `frontend/llm/index.html`

**Interfaces:**
- Consumes: `competition.pending-navigation`, `pageIntent`, current design tokens and semantic components.
- Produces: stable deep links, consistent focus/disabled/error states, reduced motion and complete document metadata.

- [ ] **Step 1: Add failing navigation and accessibility assertions**

Assert agent actions open the intended knowledge card or paper ID once, focus-visible states exist for icon buttons, the page has a skip link and document metadata includes title, description and theme color.

- [ ] **Step 2: Verify targeted tests fail**

Run:

```bash
npm run test:unit -- src/App.test.jsx src/components/ui/ui.test.jsx
```

- [ ] **Step 3: Implement the minimum consistency fixes**

Add the skip link, normalize icon-button accessible labels, retain one-time deep-link consumption, add reduced-motion rules and remove remaining visible English runtime labels from formal product pages.

- [ ] **Step 4: Run the complete frontend suite**

Run:

```bash
npm run test:unit
npm run build
npm run lint
```

Expected: unit tests and build pass; lint reports zero errors.

---

### Task 8: Full backend and Live acceptance

**Files:**
- Test only; no production edit unless a failing acceptance case identifies a root cause.

**Interfaces:**
- Consumes: built frontend, running FastAPI service and existing frontend Execute panel.
- Produces: verified release candidate.

- [ ] **Step 1: Run the formal offline backend suite**

```bash
PYTHONPATH="/mnt/d/code/AI/deeplearning/tiaozhanbei/backend:/mnt/d/code/AI/deeplearning/competition/知识星球视频知识库_前端交接包_2026-07-18" DATABASE_URL=sqlite:////tmp/tiaozhanbei_frontend_productization.sqlite3 OFFICIAL_EXAM_DATA_DIR="/mnt/d/code/AI/deeplearning/competition/08_exam_learning_path_2025" /home/wangjl/miniconda3/envs/torch/bin/python -m pytest -q competition_app/tests --ignore=competition_app/tests/integration/test_learning_plan_live_flow.py
```

Expected: all formal offline backend tests pass.

- [ ] **Step 2: Validate five Live user flows with Playwright MCP**

Validate anonymous entry/login, long chat scrolling, six-agent progress with a knowledge explanation request, training-workshop vertical scrolling, and an agent action opening a knowledge card or timed paper. Record locators and outcomes, then close the browser context.

- [ ] **Step 3: Validate Live execution from the existing frontend panel**

Click Execute in the already-started frontend runtime panel. Do not invoke Live pytest from WSL. Confirm the final answer, six-agent statuses and business navigation action complete without a console error.
