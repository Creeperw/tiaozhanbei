# Learning Workshop Primary Path Figma Implementation Plan

> **For Codex:** REQUIRED SUB-SKILL: Use `executing-plans` to implement this plan task-by-task.

**Goal:** Add an editable, review-ready Figma prototype that makes the learning path the learning workshop's primary page, restores the last learning position, exposes stages through a compact card stack, and collapses the AI assistant without leaving unused space.

**Architecture:** Keep the existing stage overview frame `63:219` unchanged and add new comparison frames on `Page 1` in Figma file `7jGPdAf1ERZd65KPms8GrD`. Build the prototype incrementally with reusable local components for the stage switcher and right work rail, then compose desktop default, stage-expanded, assistant-expanded, and mobile states. Reuse the file's existing visual language and components where available; create only the missing workshop-specific components.

**Tech Stack:** Figma Design, Figma Plugin API through `use_figma`, Auto Layout, local component variants, existing TW design-system instances, screenshots and metadata validation.

---

### Task 1: Audit the current Figma and product UI

**Files and targets:**

- Inspect: Figma file `7jGPdAf1ERZd65KPms8GrD`, page `0:1`
- Inspect: Figma stage overview frame `63:219`
- Inspect: `frontend/llm/src/components/DashboardPage.jsx`
- Inspect: `frontend/llm/src/components/dashboard/DashboardDailyWorkspace.jsx`
- Inspect: `frontend/llm/src/components/learning-stage/LearningStageLanding.jsx`
- Inspect: `frontend/llm/src/components/learning-tree/LearningPathTrainingModules.jsx`
- Inspect: `frontend/llm/src/index.css`

**Step 1: Load the required Figma skills**

Read `@figma-use` and `@figma-generate-design` completely. Before creating local component variants, also read `@figma-generate-library`.

**Step 2: Check Code Connect before library search**

Run:

```powershell
git ls-files "frontend/llm/**/*.figma.ts" "frontend/llm/**/*.figma.tsx" "frontend/llm/**/*.figma.js"
```

Expected: either a list of relevant TW component mappings or an empty result recorded as Code Connect unavailable.

**Step 3: Enumerate Figma pages and top-level frames**

Use `get_metadata` without a node ID, then inspect page `0:1`. Record:

- existing desktop frame size conventions;
- component and layer naming conventions;
- the rightmost occupied canvas boundary;
- the IDs of any existing learning-path or workshop workbench frames.

**Step 4: Inspect the most relevant existing screens**

Use `get_design_context` or targeted metadata and screenshots for:

- stage overview `63:219`;
- the closest existing TW workbench screen;
- existing sidebar, button, tag, header, and tab instances.

Expected: a concise map of reusable components, product font, colors, radii, shadows, and spacing.

**Step 5: Inspect local design-system dependencies**

Use a read-only `use_figma` call on `Page 1` to inspect instances, bound variables, text styles, and effect styles inside the closest workbench frame. Include:

```js
figma.skipInvisibleInstanceChildren = true;
const page = await figma.getNodeByIdAsync("0:1");
await figma.setCurrentPageAsync(page);
const frame = await figma.getNodeByIdAsync("CLOSEST_WORKBENCH_ID");
const instances = frame.findAllWithCriteria({ types: ["INSTANCE"] }).map((node) => ({
  id: node.id,
  name: node.name,
  component: node.mainComponent?.name || "",
  key: node.mainComponent?.parent?.type === "COMPONENT_SET"
    ? node.mainComponent.parent.key
    : node.mainComponent?.key || "",
}));
return { instances };
```

Expected: an authoritative reusable-component map before any `search_design_system` fallback.

**Step 6: Validate the audit**

No Figma mutations should exist yet. Save the current stage screenshot as the visual baseline and record the discovered font family explicitly.

---

### Task 2: Create the prototype section and reusable foundations

**Figma targets:**

- Create section: `08 · 学习工坊 · 学习路径一级页 v2`
- Create component set: `LW/StageSwitcher`
- Create component set: `LW/WorkRail`

**Step 1: Create a new section in clear canvas space**

Use `use_figma` to find the rightmost top-level bound and create a section at least 200px to its right. Do not move or overwrite existing frames.

Required return:

```js
return {
  createdNodeIds: [section.id],
  sectionId: section.id,
  position: { x: section.x, y: section.y },
};
```

Expected: the section is visible beside existing work and contains no page frame yet.

**Step 2: Create the closed stage switcher component**

Build a 220 × 42px Auto Layout component containing:

- stage status icon;
- `阶段 3 · 中药方剂`;
- `42%`;
- chevron icon imported from SVG or an existing icon component.

Expose stage label, progress, and state as component properties where practical. Use the discovered product font and design tokens.

**Step 3: Create the expanded stage switcher variant**

Create an `Open` variant containing:

- six compact stage cards;
- current, completed, next, and locked visual states;
- `查看完整进阶路线` footer action.

Use a card component once and create instances for the six stages. Do not duplicate six manually constructed card trees.

**Step 4: Combine the stage switcher variants**

Combine as:

```text
LW/StageSwitcher
State=Closed
State=Open
```

Expected: both variants preserve the same anchor point and can be swapped without shifting the page title.

**Step 5: Create the right work rail variants**

Build a 300px-wide component set:

```text
LW/WorkRail
View=Today
View=Assistant
```

`View=Today` contains current task, knowledge points, review schedule, next-step suggestion, and an AI entry. `View=Assistant` contains the assistant header, return-to-task action, message history, composer, and retained conversation state indicator.

**Step 6: Validate reusable components**

Take separate screenshots of `LW/StageSwitcher` and `LW/WorkRail`. Verify:

- no clipped Chinese text;
- correct product font;
- component variants and instance properties are exposed;
- all created or mutated node IDs were returned;
- no hardcoded duplicate icons built from rotated primitives.

---

### Task 3: Build the desktop default state

**Figma target:**

- Create frame: `08A · 学习路径一级页 · 默认`
- Size: match the closest existing TW desktop workbench, expected `1672 × 941`

**Step 1: Create the outer frame first**

Create the page frame directly inside the new section with Auto Layout or a structured grid. Position it without overlapping other content and return its ID.

**Step 2: Reuse the existing application shell**

Reuse the closest available sidebar and shell/header instances. Highlight `学习工坊` in the navigation. Do not rebuild a manual sidebar if a TW instance exists.

**Step 3: Build the learning-path control header**

Add:

- `Learning path / 学习路径`;
- `我的学习路径 / 经典路线` segmented control;
- classic route selector;
- closed `LW/StageSwitcher` instance;
- second-row breadcrumb;
- subtle `已恢复上次学习位置` status.

Keep all route controls in one title region and remove the standalone “返回学习阶段” row.

**Step 4: Build the main path canvas**

Create the dominant left canvas at approximately 72%–75% of the main workspace width and 480–520px height. Reuse or adapt the existing path visualization style, showing:

- a clear current/restored node;
- a low-saturation stage color band;
- readable nodes and connectors;
- no artificial dense data beyond what is needed to communicate hierarchy.

**Step 5: Place the default right work rail**

Instance `LW/WorkRail, View=Today` at approximately 25%–28% width and the same height as the path canvas. It must fill the entire right column; no assistant placeholder or empty lower cell remains.

**Step 6: Add the training-workshop quick row**

Place an 80–92px row below the main workspace with:

- `题目训练`;
- `知识卡片`;
- `试卷生成`.

Reuse button or card instances when available. Keep descriptions concise enough to remain single- or two-line.

**Step 7: Add the feedback summary**

Place the 56–68px `昨日学习反馈` summary directly below the training row. Include a representative populated state rather than only an empty state.

**Step 8: Validate the default desktop frame**

Take screenshots of:

- the full frame;
- the path control header;
- the main workspace;
- the training and feedback rows.

Expected:

- no right-bottom blank area;
- no title or selector wrapping at desktop width;
- path remains the primary visual focus;
- the frame fits its intended viewport without page-level horizontal scrolling.

---

### Task 4: Build the stage-expanded and assistant-expanded desktop states

**Figma targets:**

- Create frame: `08B · 学习路径一级页 · 阶段展开`
- Create frame: `08C · 学习路径一级页 · 助教展开`

**Step 1: Duplicate the validated default state**

Duplicate `08A` twice inside the same section. Preserve component instances and bindings; do not detach reusable components.

**Step 2: Show the stage-expanded state**

In `08B`, swap the stage switcher instance to `State=Open`. Ensure:

- the expanded panel is anchored under the closed control;
- the current stage is visually frontmost;
- six stages remain readable;
- the panel does not cover the route selector or essential current node;
- `查看完整进阶路线` is visible.

**Step 3: Show the assistant-expanded state**

In `08C`, swap the work rail to `View=Assistant`. Keep the learning-path canvas width and node positions unchanged.

**Step 4: Add prototype reactions**

Where supported, wire:

- closed stage switcher → open state;
- `Esc` behavior through component semantics or annotation;
- AI entry → assistant state;
- return-to-task → default state;
- full-route action → existing stage frame `63:219`.

Do not delete or modify `63:219`.

**Step 5: Validate both states**

Compare screenshots of `08A`, `08B`, and `08C`. Expected:

- no layout shift between default and assistant states;
- stage overlay is visually layered but not modal;
- route controls and breadcrumb remain stable;
- component swaps do not detach or flatten instances.

---

### Task 5: Build the mobile behavior reference

**Figma targets:**

- Create frame: `08D · 学习路径一级页 · Mobile`
- Suggested size: `390 × 844`
- Create overlay frame or variant: `08E · 阶段选择 · Bottom Sheet`

**Step 1: Compose the mobile primary page**

Stack:

- compact learning-path header;
- route controls;
- path canvas with a usable minimum height;
- `今日任务 / AI 助教` tabbed work rail;
- horizontally scrollable training actions;
- horizontally scrollable feedback metrics.

**Step 2: Create the mobile stage bottom sheet**

Replace the hover card fan with a bottom sheet containing all stages as compact list cards. Include drag handle, close action, current-stage state, locked-stage explanation, and full-route action.

**Step 3: Validate touch-first behavior**

Expected:

- no hover-dependent instruction;
- touch targets are at least 44px;
- no viewport-level horizontal overflow;
- route and stage labels remain readable;
- path canvas is not compressed below an operable height.

---

### Task 6: Final Figma review and handoff

**Figma targets:**

- Review section `08 · 学习工坊 · 学习路径一级页 v2`
- Review frames `08A` through `08E`
- Review component sets `LW/StageSwitcher` and `LW/WorkRail`

**Step 1: Run a structural audit**

Use metadata or a read-only `use_figma` script to confirm:

- all major containers use Auto Layout where children have structural relationships;
- repeated stage cards are instances;
- frame and component names follow the planned convention;
- no placeholder shimmer remains;
- product font is used on all free-standing text.

**Step 2: Run a visual audit**

Capture each full frame and individual high-risk sections. Check:

- clipped or overflowing Chinese text;
- wrong font family or style;
- overlapping stage cards;
- inconsistent spacing or radii;
- placeholder copy;
- missing component instances;
- empty lower-right area;
- path reflow between Today and Assistant states.

**Step 3: Apply only targeted corrections**

Fix individual nodes or variants in small `use_figma` calls. Do not rebuild validated frames.

**Step 4: Save a review checkpoint**

Create a named Figma version or clearly identify the final prototype section. Keep the original stage frame unchanged.

**Step 5: Report the handoff**

Provide:

- the Figma file link;
- created frame and section node IDs;
- screenshots of default, stage-expanded, assistant-expanded, and mobile states;
- a short list of any deliberate deviations from the approved design;
- confirmation that no existing screen was overwritten.

