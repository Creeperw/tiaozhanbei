# Knowledge Atlas UI Refinement Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use test-driven-development to implement this plan task-by-task.

**Goal:** Remove redundant entry actions, compact the assistant header, and refine the Knowledge Atlas default view, node styling, and clustering behavior.

**Architecture:** Keep the existing React and Canvas architecture. Change only component defaults, Tailwind/CSS sizing, Canvas drawing primitives, and semantic coordinate generation; preserve navigation and API behavior.

**Tech Stack:** React 19, Tailwind CSS utilities, HTML Canvas 2D, Vitest, Testing Library, Vite.

---

### Task 1: Remove redundant home and assistant actions

**Files:**

- Modify: `frontend/llm/src/components/HomePage.jsx`
- Modify: `frontend/llm/src/components/HomePage.test.jsx`
- Modify: `frontend/llm/src/components/ChatInterface.jsx`
- Modify: `frontend/llm/src/index.css`

1. Update the home test to assert “上传资料”和“开始学习” are absent while normal portal actions remain available.
2. Run `npm run test:unit -- src/components/HomePage.test.jsx` and verify the new assertion fails.
3. Remove both Hero buttons and their unused icon imports.
4. Remove only the assistant “新对话” button, retain the sidebar expand control, and reduce the sticky header to `h-11` with compact padding.
5. Re-run the focused tests and verify they pass.

### Task 2: Make sequence the Knowledge Atlas default

**Files:**

- Modify: `frontend/llm/src/components/knowledge-atlas/KnowledgeAtlas.jsx`
- Modify: `frontend/llm/src/components/knowledge-atlas/KnowledgeAtlas.test.jsx`

1. Change the existing initial-view test to expect the sequence button pressed and the sequence panel visible.
2. Run the focused Knowledge Atlas test and verify it fails.
3. Initialize `arrangement` and `arrangementRef` to `sequence` and keep manual sphere/cluster switching intact.
4. Re-run the focused test and verify it passes.

### Task 3: Refine nodes and stabilize semantic clustering

**Files:**

- Modify: `frontend/llm/src/components/knowledge-atlas/useKnowledgeAtlasCanvas.js`
- Modify: `frontend/llm/src/components/knowledge-atlas/knowledgeAtlasModel.js`
- Modify: `frontend/llm/src/components/knowledge-atlas/knowledgeAtlasModel.test.jsx`
- Modify: `frontend/llm/src/components/knowledge-atlas/KnowledgeAtlas.test.jsx`

1. Add tests that semantic coordinates stay centered within bounded vertical space and that level-three resource kinds retain a filled base.
2. Run the model and component tests and verify the assertions fail.
3. Reduce projected node radii; draw a filled base for every resource kind and apply resource-specific rings/markers on top.
4. Remove label background rectangles, reduce label font size, and add subtle text shadow.
5. Generate semantic cluster centers on a balanced front-facing grid/ring with bounded local offsets; reduce cluster zoom preset.
6. Run the focused tests and verify they pass.

### Task 4: Visual density and regression verification

**Files:**

- Modify: `frontend/llm/src/components/knowledge-atlas/knowledgeAtlas.css`

1. Tighten only the Knowledge Atlas density affected by the smaller node presentation if needed; retain responsive and focus styles.
2. Run `npm run test:unit -- src/components/HomePage.test.jsx src/components/knowledge-atlas/KnowledgeAtlas.test.jsx src/components/knowledge-atlas/knowledgeAtlasModel.test.jsx`.
3. Run `npm run build`.
4. Review `git diff` and confirm no unrelated user changes were overwritten.
