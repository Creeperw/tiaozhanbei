# Training Workshop Navigation Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Move the training workshop and its AI patient and mistake-variation flows into distinct sidebar entries while removing retired training UI.

**Architecture:** Keep the existing `PracticePage` and training panels, selecting their mode through page intent parameters. Extend the app shell with three page keys and remove the workshop's module picker, knowledge-card path, source selector, and header summary.

**Tech Stack:** React 19, Vite, Vitest, Testing Library.

---

### Task 1: Define the intended navigation and training surfaces

**Files:**

- Modify: `frontend/llm/src/appShell.test.js`
- Modify: `frontend/llm/src/components/PracticePage.test.jsx`

**Step 1: Write failing tests**

Assert that the sidebar exposes `训练工坊`、`AI 病患模拟`、`错题变式` as distinct entries, and that the workshop no longer exposes its retired header, knowledge-card module, or question-source controls.

**Step 2: Run tests to verify they fail**

Run: `npm run test:unit -- src/appShell.test.js src/components/PracticePage.test.jsx`

Expected: FAIL because the existing UI still contains the old navigation and controls.

### Task 2: Route the three sidebar entries into focused training views

**Files:**

- Modify: `frontend/llm/src/appShell.js`
- Modify: `frontend/llm/src/components/AppShell.jsx`
- Modify: `frontend/llm/src/App.jsx`

**Step 1: Implement minimal navigation**

Add dedicated page keys with matching icons and map them to `PracticePage` with `question_training`, `ai_patient_simulation`, or `mistake_variation` task types.

**Step 2: Run focused tests**

Run: `npm run test:unit -- src/appShell.test.js src/components/AppShell.test.jsx`

Expected: PASS.

### Task 3: Remove retired training-workshop UI

**Files:**

- Modify: `frontend/llm/src/components/PracticePage.jsx`
- Modify: `frontend/llm/src/components/QuestionTrainingPanel.jsx`

**Step 1: Implement minimal focused rendering**

Remove the workshop header, internal module navigator, knowledge-card branch, and question-source selector. Keep the formal question-bank scope as the fixed default and render AI patient and mistake variation directly from their sidebar entries.

**Step 2: Run tests and build**

Run: `npm run test:unit -- src/appShell.test.js src/components/AppShell.test.jsx src/components/PracticePage.test.jsx`

Run: `npm run build`

Expected: all tests and the production build pass.
