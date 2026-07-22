# Assistant Sidebar and Composer Refinement Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use test-driven-development to implement this plan task-by-task.

**Goal:** Make the assistant history rail slimmer and reduce the composer to one clear visual container.

**Architecture:** Preserve all React behavior and only adjust existing Tailwind utilities plus the assistant-specific CSS rule. Add focused component assertions for the visual contract.

**Tech Stack:** React 19, Tailwind CSS utilities, Vitest, Testing Library, Vite.

---

### Task 1: Lock the compact sidebar contract

**Files:**

- Modify: `frontend/llm/src/components/ChatInterface.test.jsx`
- Modify: `frontend/llm/src/components/ChatInterface.jsx`

1. Add assertions that the open session rail uses `w-[244px]` and its “新对话” button uses `h-9`.
2. Run `npm run test:unit -- src/components/ChatInterface.test.jsx` and verify the assertions fail against the current 280px rail and padded button.
3. Apply the minimal width and button utility changes while preserving session actions.
4. Re-run the focused test and verify it passes.

### Task 2: Remove redundant composer frames

**Files:**

- Modify: `frontend/llm/src/components/ChatInterface.test.jsx`
- Modify: `frontend/llm/src/components/ChatInterface.jsx`
- Modify: `frontend/llm/src/index.css`

1. Add assertions that the textarea explicitly suppresses its focus-visible outline and the composer owns no top border.
2. Run the focused test and verify the assertions fail.
3. Remove `.assistant-composer`'s top border and add `focus-visible:outline-none` to the textarea; keep focus feedback on the outer `focus-within` container.
4. Re-run the focused test and verify it passes.

### Task 3: Regression verification

1. Run scoped ESLint for `ChatInterface.jsx` and `ChatInterface.test.jsx`.
2. Run `npm run test:unit -- src/components/ChatInterface.test.jsx`.
3. Run `npm run build`.
4. Review the scoped diff and confirm unrelated worktree changes remain untouched.
