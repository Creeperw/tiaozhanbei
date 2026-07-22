# Learning Workshop Stage Landing Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Replace the learning workshop's reused learning-path landing with a backend-ready, adaptive 4–6 stage Figma entry, then open the existing path view in classic-route mode by default through an OriginKit-inspired flip transition.

**Architecture:** Keep the existing `practice` page intent and split it into `stages`, `path`, and `workspace` views. Add a data-driven responsive stage component, keep the flip overlay at `App` level so it survives the route midpoint, keep `DashboardPage` as the path experience, and preserve `PracticePage` for deep-linked training modules.

**Tech Stack:** React 19, JavaScript, CSS, Framer Motion, Vitest, Testing Library, existing page-intent navigation, existing Tailwind utility classes.

**Approved revision (supersedes fixed-six code samples below):**

- `LearningStageLanding` accepts a stage array and renders the actual 4–6 results. `getStageLayout(count)` computes equal-width columns, responsive gaps, and normalized staircase heights for 4, 5, and 6 stages.
- Keep the approved six-stage content only as the empty/backend-pending fallback; do not force a backend result into six slots.
- Adapt OriginKit Magnetic Carousel's pointer-distance falloff to the stage buttons without adopting its image-strip presentation.
- Add `StagePageTransition` above the routed page in `App`. Adapt OriginKit Flip Gallery's perspective, two faces, hidden backface, and 180-degree final flip. Change the route at half duration and remove the overlay only after completion.
- Add `framer-motion` (authorized by the user) and update both `package.json` and `package-lock.json`.
- Verify keyboard activation, touch behavior, transition double-click protection, and `prefers-reduced-motion` fallback in addition to the tests below.
- Add a content-density contract: every card exposes its normalized progress, cards in the first half use a compact one-line resource treatment, later cards use full resource tags, and the redundant action label is removed from layout flow.
- Raise the responsive base height while reducing the total height range so all core tasks fit and 4–6 cards retain equal visual steps. Verify in the browser that every card has `scrollHeight <= clientHeight` and that resource blocks do not overlap task blocks.
- Copy the six supplied PNG line drawings into `frontend/llm/public/learning-stage/`, add optional illustration metadata to the stage model, and render decorative `<img alt="" aria-hidden="true">` watermarks below the text layer. Use low-opacity multiply blending because the supplied files contain baked checkerboard backgrounds; give the duplicate classics/mastery artwork different object positions.
- Replace the rainbow palette with the approved exact stage colors `#3F8F68`, `#347D70`, `#33777B`, `#3B6876`, `#3B586A`, and `#293D4C`. Keep those values as each stage's primary color and pair them with same-hue darker gradient endpoints for small-text contrast; reuse the palette in markers, legend, cards, and the flip overlay.

---

### Task 1: Lock the three-view routing contract

**Files:**

- Modify: `frontend/llm/src/App.test.jsx`
- Modify: `frontend/llm/src/App.jsx:1-170`

**Step 1: Write the failing routing tests**

Mock the new stage component in `App.test.jsx`:

```jsx
vi.mock('./components/learning-stage/LearningStageLanding', () => ({
  default: ({ onNavigate }) => (
    <button
      type="button"
      onClick={() => onNavigate({
        page: 'practice',
        params: { view: 'path', pathMode: 'classic', stageId: 'foundation', stageIndex: 0 },
      })}
    >
      Stage landing
    </button>
  ),
}));
```

Update the navigation test so that base `practice` renders `Stage landing`, clicking it renders the mocked `DashboardPage` with `data-view="path"`, and `view=workspace` still renders `PracticePage`.

**Step 2: Run the routing test to verify it fails**

Run:

```powershell
Set-Location frontend/llm
npm run test:unit -- src/App.test.jsx
```

Expected: FAIL because `LearningStageLanding` is not imported and base `practice` still renders `DashboardPage`.

**Step 3: Implement the minimal route split**

In `App.jsx`, import `LearningStageLanding` and replace the `practice` branch with the three-view decision:

```jsx
case 'practice':
  if (pageIntent.params.view === 'workspace') {
    return <PracticePage navigationContext={pageIntent.params} onBackHome={() => navigateToPage('dashboard')} />;
  }
  if (pageIntent.params.view === 'path') {
    return (
      <DashboardPage
        currentUser={currentUser}
        navigationContext={pageIntent.params}
        onNavigate={navigateToPage}
        onKnowledgeContextChange={setKnowledgeNavigationContext}
      />
    );
  }
  return <LearningStageLanding onNavigate={navigateToPage} />;
```

Create a temporary minimal export at `frontend/llm/src/components/learning-stage/LearningStageLanding.jsx` so the route can compile:

```jsx
import React from 'react';

export default function LearningStageLanding() {
  return <main aria-label="学习阶段">学习阶段</main>;
}
```

**Step 4: Run the routing test to verify it passes**

Run: `npm run test:unit -- src/App.test.jsx`

Expected: PASS.

**Step 5: Commit**

```powershell
git add frontend/llm/src/App.jsx frontend/llm/src/App.test.jsx frontend/llm/src/components/learning-stage/LearningStageLanding.jsx
git commit -m "feat: split learning workshop views"
```

### Task 2: Build the Figma stage landing with TDD

**Files:**

- Create: `frontend/llm/src/components/learning-stage/LearningStageLanding.test.jsx`
- Modify: `frontend/llm/src/components/learning-stage/LearningStageLanding.jsx`
- Create: `frontend/llm/src/components/learning-stage/LearningStageLanding.css`

**Step 1: Write the failing component test**

Cover the six stage names, keyboard-accessible buttons, and emitted path intent:

```jsx
import React from 'react';
import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import LearningStageLanding from './LearningStageLanding';

describe('LearningStageLanding', () => {
  it('renders six learning stages and opens the classic path view', () => {
    const onNavigate = vi.fn();
    render(<LearningStageLanding onNavigate={onNavigate} />);

    expect(screen.getAllByRole('button', { name: /\u8fdb入.+阶段/ })).toHaveLength(6);
    fireEvent.click(screen.getByRole('button', { name: '进入基础筑基阶段' }));

    expect(onNavigate).toHaveBeenCalledWith({
      page: 'practice',
      params: { view: 'path', pathMode: 'classic', stageId: 'foundation', stageIndex: 0 },
    });
  });
});
```

**Step 2: Run the component test to verify it fails**

Run: `npm run test:unit -- src/components/learning-stage/LearningStageLanding.test.jsx`

Expected: FAIL because the temporary component has no stage buttons.

**Step 3: Implement the six-stage model and semantic markup**

Define a local immutable `STAGES` array containing the approved titles, duration, checklist, resources, color pair, and stable ID:

```jsx
const STAGES = [
  { id: 'foundation', label: '基础筑基', level: '入门', duration: '1-2个月', tasks: ['阴阳五行藏象学说', '望闻问切四诊基础'], resources: [] },
  { id: 'classics', label: '经典研读', level: '基础', duration: '2-3个月', tasks: ['《伤寒论》六经辨证', '《金匮要略》杂病论治', '《温病条辨》温病学', '背诵常用方剂歌诀'], resources: ['经典原文'] },
  { id: 'formulas', label: '中药方剂', level: '提高', duration: '2-3个月', tasks: ['300+常用中药药性', '中药炮制方法功效', '100+经典方剂组成', '君臣佐使配伍原则'], resources: ['药性歌诀', '方剂手册'] },
  { id: 'clinical', label: '临床实践', level: '进阶', duration: '3-6个月', tasks: ['跟师门诊观察学习', '针灸推拿特色疗法', '参与病例讨论分析', '练习脉诊舌诊技能'], resources: ['名医跟诊', '针灸培训'] },
  { id: 'specialty', label: '专科深化', level: '专精', duration: '3-6个月', tasks: ['选择内科/妇科/儿科', '学习专科经典著作', '研究专科名医经验', '收集分析专科医案'], resources: ['专科专著', '经验集'] },
  { id: 'mastery', label: '融会贯通', level: '精通', duration: '持续精进', tasks: ['独立接诊积累医案', '参加学术交流研讨', '研究现代中医成果', '总结个人诊疗心得'], resources: ['学术会议', '科研论文'] },
];
```

Render semantic headings, a top stage marker list, six `<button>` cards, checklists, resource tags, and the bottom legend. Use CSS custom properties for each card's two gradient colors rather than image assets for simple Figma circles and dividers.

**Step 4: Implement responsive styling**

In `LearningStageLanding.css`:

- Use an aspect-aware desktop container and a six-column grid.
- Align card bottoms and vary card minimum heights to preserve the staircase.
- Preserve the Figma palette, borders, shadows, radii, and typography intent.
- At `max-width: 900px`, hide the redundant top marker row and render cards as a single vertical list with equal minimum height.
- Add `:focus-visible`, `:hover`, and `prefers-reduced-motion` behavior.
- Avoid horizontal page overflow at 320px width.

**Step 5: Run the component and route tests**

Run:

```powershell
npm run test:unit -- src/components/learning-stage/LearningStageLanding.test.jsx src/App.test.jsx
```

Expected: PASS.

**Step 6: Commit**

```powershell
git add frontend/llm/src/components/learning-stage
git commit -m "feat: add learning stage landing"
```

### Task 3: Default the path page to classic routes and add a return action

**Files:**

- Modify: `frontend/llm/src/components/DashboardPage.test.jsx`
- Modify: `frontend/llm/src/components/DashboardPage.jsx:97-470`

**Step 1: Write the failing path-mode tests**

Add tests that render `DashboardPage` with `navigationContext={{ view: 'path', pathMode: 'classic' }}` and assert:

```jsx
expect(await screen.findByRole('tab', { name: '经典路线' })).toHaveAttribute('aria-selected', 'true');
expect(screen.getByRole('button', { name: '返回学习阶段' })).toBeInTheDocument();
```

Click the return button and assert:

```jsx
expect(onNavigate).toHaveBeenCalledWith({ page: 'practice', params: {} });
```

Then click `我的学习路径` and assert that its tab becomes selected.

**Step 2: Run the path test to verify it fails**

Run: `npm run test:unit -- src/components/DashboardPage.test.jsx`

Expected: FAIL because the page currently initializes `pathMode` as `personalized` and has no stage-return action.

**Step 3: Implement the classic default**

Initialize the state from navigation context while keeping classic as the fallback:

```jsx
const [pathMode, setPathMode] = useState(
  () => (navigationContext.pathMode === 'personalized' ? 'personalized' : 'classic'),
);
```

Add a compact return button at the start of `pathTopContent`:

```jsx
<button
  type="button"
  className="learning-path-content__back"
  onClick={() => onNavigate?.({ page: 'practice', params: {} })}
>
  返回学习阶段
</button>
```

Keep the existing classic route selector and personalized planning empty state unchanged.

**Step 4: Add best-effort stage context**

Preserve `navigationContext.stageId` and `stageIndex` in the path page. When classic route data exposes a stable matching stage, initialize the matching stage as the highlighted context; otherwise leave the root graph unchanged. Do not infer progress or block rendering when there is no match.

**Step 5: Run the path test to verify it passes**

Run: `npm run test:unit -- src/components/DashboardPage.test.jsx`

Expected: PASS.

**Step 6: Commit**

```powershell
git add frontend/llm/src/components/DashboardPage.jsx frontend/llm/src/components/DashboardPage.test.jsx
git commit -m "feat: default learning path to classic routes"
```

### Task 4: Verify the complete workshop flow and visual behavior

**Files:**

- Verify: `frontend/llm/src/components/learning-stage/LearningStageLanding.jsx`
- Verify: `frontend/llm/src/components/learning-stage/LearningStageLanding.css`
- Verify: `frontend/llm/src/App.jsx`
- Verify: `frontend/llm/src/components/DashboardPage.jsx`

**Step 1: Run the focused test suite**

Run:

```powershell
Set-Location frontend/llm
npm run test:unit -- src/App.test.jsx src/components/learning-stage/LearningStageLanding.test.jsx src/components/DashboardPage.test.jsx src/components/PracticePage.test.jsx src/components/AppShell.test.jsx
```

Expected: PASS with no test failures.

**Step 2: Run lint and production build**

Run:

```powershell
npm run lint
npm run build
```

Expected: both commands exit 0.

**Step 3: Perform visual checks**

Start the existing frontend development server and verify:

- Desktop stage landing matches the Figma hierarchy, palette, staircase, and labels.
- 1024px layout remains readable.
- 768px and 320px layouts become vertical without horizontal scrolling.
- Keyboard Tab visits all six cards in order and Enter opens the path page.
- The path page starts on classic routes, can switch to the personal route, and can return to stages.
- Stage artwork remains visible before selection and is carried into both sides of the flip; transition copy wraps inside a bounded translucent panel without overflow.
- A direct `view=workspace` intent still opens the requested training module.

**Step 4: Inspect the final diff**

Run:

```powershell
git diff --check
git status --short
```

Expected: no whitespace errors and only the planned frontend files are modified.

**Step 5: Commit any final targeted polish**

Only if visual verification required a small scoped correction:

```powershell
git add frontend/llm/src/components/learning-stage frontend/llm/src/App.jsx frontend/llm/src/components/DashboardPage.jsx
git commit -m "fix: polish workshop stage navigation"
```
