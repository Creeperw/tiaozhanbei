# Learning Workshop Hover Cards and Compact Height Implementation Plan

> **For Codex:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Make the desktop stage preview reliably usable on hover, keep the complete-route action visible, and let the learning workshop end naturally after yesterday's feedback.

**Architecture:** Keep `LearningStageSwitcher` inside the path header and preserve the existing mobile bottom sheet. Stabilize desktop hover transitions in the component, move clipping responsibility from the path card to the path canvas, and override the fullscreen workshop sizing only for the path-first workshop layout.

**Tech Stack:** React 19, Vitest, Testing Library, CSS, Vite, Chrome responsive inspection

---

### Task 1: Add hover-stability regression coverage

**Files:**

- Modify: `frontend/llm/src/components/learning-stage/LearningStageSwitcher.test.jsx`
- Modify: `frontend/llm/src/components/learning-stage/LearningStageSwitcher.jsx`

**Step 1: Write the failing test**

Add a desktop `matchMedia` case that:

```jsx
fireEvent.mouseEnter(trigger.closest('.learning-stage-switcher'));
expect(screen.getByRole('dialog', { name: '学习阶段选择' })).toBeInTheDocument();

fireEvent.mouseLeave(trigger.closest('.learning-stage-switcher'));
fireEvent.mouseEnter(screen.getByRole('dialog', { name: '学习阶段选择' }));
vi.advanceTimersByTime(160);

expect(screen.getByRole('button', { name: '查看完整进阶路线' })).toBeInTheDocument();
```

Also click the full-route button and assert:

```jsx
expect(onNavigate).toHaveBeenCalledWith({
  page: 'practice',
  params: { view: 'stages' },
});
```

**Step 2: Run test to verify it fails**

Run:

```bash
npm run test:unit -- src/components/learning-stage/LearningStageSwitcher.test.jsx
```

Expected: FAIL because desktop `onMouseLeave` currently closes immediately.

**Step 3: Implement minimal hover grace**

- Store a close timer in a ref.
- Cancel the timer on root or dialog re-entry.
- Schedule close about 120 ms after pointer leave.
- Clear the timer on unmount.
- Do not alter the mobile modal, focus trap, Escape handling, or click behavior.

**Step 4: Run test to verify it passes**

Run the same test command.

Expected: all `LearningStageSwitcher` tests PASS.

**Step 5: Commit**

```bash
git add frontend/llm/src/components/learning-stage/LearningStageSwitcher.jsx frontend/llm/src/components/learning-stage/LearningStageSwitcher.test.jsx
git commit -m "fix: stabilize learning stage hover preview"
```

### Task 2: Expose the complete-route action without leaking the path canvas

**Files:**

- Modify: `frontend/llm/src/index.css`

**Step 1: Record the visual failure**

At desktop width, open the stage preview and confirm:

- `.learning-stage-switcher__dialog` extends below `.dashboard-daily__path`;
- `.learning-stage-switcher__full-route` is outside the visible clipped area.

Save the before-state dimensions in the task notes.

**Step 2: Apply the clipping-boundary fix**

Add scoped workshop rules:

```css
.dashboard-daily--training-workshop .dashboard-daily__path {
  overflow: visible;
}

.dashboard-daily--training-workshop .dashboard-daily__path-stage {
  overflow: hidden;
  border-radius: 0 0 24px 24px;
}

.learning-stage-switcher__dialog {
  top: calc(100% + 8px);
  max-height: min(520px, calc(100dvh - 96px));
  overflow-y: auto;
}

.learning-stage-switcher__full-route {
  position: sticky;
  bottom: 0;
}
```

Keep the mobile fixed bottom-sheet rules more specific and unchanged.

**Step 3: Verify in a real browser**

Check desktop widths 1180 px and 1440 px:

- full-route action is visible and clickable;
- the preview overlays rather than pushes the path;
- path nodes and edges remain clipped to the rounded canvas;
- no horizontal page overflow appears.

**Step 4: Commit**

```bash
git add frontend/llm/src/index.css
git commit -m "fix: keep stage preview actions visible"
```

### Task 3: Let the desktop workshop end after its content

**Files:**

- Modify: `frontend/llm/src/index.css`

**Step 1: Record the current sizing**

At desktop width, capture the computed heights for:

- `.dashboard-daily--training-workshop`;
- `.dashboard-daily__workspace`;
- `.dashboard-daily__learning-column`;
- `.dashboard-daily__feedback`.

Confirm the workshop inherits the fullscreen `100dvh` minimum.

**Step 2: Add a scoped natural-height override**

Use a selector specific to the path-first training workshop:

```css
.dashboard-daily--workspace-only.dashboard-daily--training-workshop[data-layout="fullscreen"] {
  height: auto;
  min-height: 0;
  flex: 0 0 auto;
  padding-bottom: 12px;
}

.dashboard-daily--training-workshop .dashboard-daily__workspace[data-right-column="stable"] {
  height: auto;
  align-items: stretch;
}
```

Do not change the shared fullscreen rules used by other workspaces. Preserve the mobile natural-scroll override.

**Step 3: Verify in a real browser**

At 1440 px, 1024 px, 740 px, and 390 px:

- desktop workshop content ends after feedback with normal spacing;
- there is no extra scrollable blank region;
- right work rail matches the content row height;
- tablet remains a one-row grid;
- mobile remains a stacked page with a bottom sheet.

**Step 4: Commit**

```bash
git add frontend/llm/src/index.css
git commit -m "fix: use natural learning workshop height"
```

### Task 4: Regression verification

**Files:**

- Test: `frontend/llm/src/components/learning-stage/LearningStageSwitcher.test.jsx`
- Test: `frontend/llm/src/components/DashboardPage.test.jsx`
- Test: `frontend/llm/src/components/dashboard/DashboardDailyWorkspace.test.jsx`

**Step 1: Run related tests**

```bash
npm run test:unit -- src/App.test.jsx src/components/DashboardPage.test.jsx src/components/dashboard/DashboardDailyWorkspace.test.jsx src/components/learning-stage/LearningStageSwitcher.test.jsx src/components/learning-stage/LearningStageLanding.test.jsx src/components/learning-stage/StagePageTransition.test.jsx src/components/learning-tree/LearningPathOverview.test.jsx src/components/CompactAssistant.test.jsx
```

Expected: all related tests PASS.

**Step 2: Run static checks**

```bash
npx eslint src/App.jsx src/components/DashboardPage.jsx src/components/dashboard/DashboardDailyWorkspace.jsx src/components/learning-stage/LearningStageSwitcher.jsx src/components/learning-stage/LearningStageSwitcher.test.jsx
npm run build
git diff --check
```

Expected: ESLint and build PASS; `git diff --check` has no whitespace errors.

**Step 3: Final browser acceptance**

Confirm:

- hover preview opens without flicker;
- all six brief stage cards and full-route CTA are visible;
- CTA enters the existing full stage page;
- desktop empty scroll area is gone;
- mobile modal and keyboard focus loop still work.

**Step 4: Commit**

```bash
git add frontend/llm/src frontend/llm/src/index.css
git commit -m "test: verify learning workshop stage preview"
```
