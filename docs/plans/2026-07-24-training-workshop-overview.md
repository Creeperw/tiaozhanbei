# Training Workshop Overview Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Provide a visual training-workshop overview that routes learners into the existing training workflows without changing their behavior.

**Architecture:** Add an explicit `overview` versus `workspace` state to `PracticePage`, derived from `navigationContext`. Render the overview as a dedicated presentational component with callbacks, while the existing task and result panels remain the workspace implementation. Keep navigation local to the page intent so the established application shell and deep links continue to work.

**Tech Stack:** React 19, Vite, Tailwind utility classes, Lucide React, Vitest, Testing Library.

---

### Task 1: Define overview navigation behavior

**Files:**

- Modify: `frontend/llm/src/components/PracticePage.test.jsx`

**Step 1: Write failing tests**

Assert that a default `PracticePage` renders the workshop overview and that selecting a training card opens its corresponding existing panel. Assert that the workspace exposes a return action that restores the overview.

**Step 2: Run the focused test**

Run: `npm run test:unit -- src/components/PracticePage.test.jsx`

Expected: FAIL because `PracticePage` currently renders the task tabs immediately.

### Task 2: Implement the overview and local view transition

**Files:**

- Modify: `frontend/llm/src/components/PracticePage.jsx`

**Step 1: Add overview data and rendering**

Define the training and utility card metadata in the page module. Use Lucide icons and semantic buttons for the interactive entries. Preserve inactive utility cards as unavailable rather than attaching placeholder navigation.

**Step 2: Add an explicit workspace view**

Initialize the page in overview mode unless an external navigation intent includes `taskType` or `view=workspace`. Selecting a training card sets its task type and enters workspace mode; the workspace return button resets local state to the overview.

**Step 3: Run focused tests**

Run: `npm run test:unit -- src/components/PracticePage.test.jsx`

Expected: PASS.

### Task 3: Refine the responsive visual system

**Files:**

- Modify: `frontend/llm/src/index.css`

**Step 1: Add scoped overview styles**

Implement the header banner, illustration, responsive main-and-aside layout, stable card dimensions, summary strip, focus state, hover transition, and reduced-motion fallback under `practice-overview` selectors.

**Step 2: Verify production build**

Run: `npm run build`

Expected: PASS.

### Task 4: Perform regression and visual checks

**Files:**

- Verify: `frontend/llm/src/components/PracticePage.test.jsx`
- Verify: `frontend/llm/src/index.css`

**Step 1: Run focused regression suite**

Run: `npm run test:unit -- src/components/PracticePage.test.jsx src/appShell.test.js src/components/AppShell.test.jsx`

Expected: PASS.

**Step 2: Run static checks**

Run: `npm run lint && npm run build`

Expected: PASS.

**Step 3: Visual acceptance**

Start the frontend dev server, capture the overview at desktop and mobile sizes with Playwright, and verify that cards, banner copy, sidebar, and task/result workspace have no overlap or horizontal scroll.
