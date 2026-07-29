# Platform Home and Top Navigation Implementation Plan

> **For Codex:** REQUIRED SUB-SKILL: Use `executing-plans` to implement this plan task-by-task.

**Goal:** Build the new video-led platform home, restore the former certificate-driven home as an explicit learning-path page, and replace the authenticated global sidebar with accessible top navigation.

**Architecture:** Keep `dashboard` as the authenticated default and render the new `HomePage`. Add an explicit `learning-path` route that restores the page represented by the second reference image from Git history `e8c645c`. Define navigation and dropdown intents in `appShell.js`, render them through `AppShell`, and share one persisted qualification selector between the platform home and learning-path page.

**Tech Stack:** React 18, Vite, Vitest, Testing Library, Lucide React, CSS, existing authenticated API helpers.

**Design reference:** `docs/plans/2026-07-27-platform-home-top-navigation-design.md`

**Relevant skills:** `@test-driven-development`, `@ui-ux-pro-max`, `@requesting-code-review`

---

### Task 1: Add the explicit learning-path route and restore the former page

**Files:**

- Create: `frontend/llm/src/components/LearningPathPage.jsx`
- Create: `frontend/llm/src/components/LearningPathPage.test.jsx`
- Create: `frontend/llm/src/components/LearningPathPage.css`
- Modify: `frontend/llm/src/App.jsx:202-275`
- Modify: `frontend/llm/src/App.test.jsx:99-190`
- Modify: `frontend/llm/src/appShell.js:1-59`
- Modify: `frontend/llm/src/appShell.test.js:1-165`

**Step 1: Write the failing route-model test**

Add assertions that the authenticated default remains `dashboard`, `learning-path` is an allowed page, and the primary labels use the confirmed hierarchy:

```js
test('exposes platform home and learning path as separate top-level pages', () => {
  const shell = getAppShellConfig({
    currentUser: { role: 'user' },
    currentPage: 'learning-path',
  });

  expect(shell.defaultPage).toBe('dashboard');
  expect(shell.currentPage).toBe('learning-path');
  expect(shell.primaryNav.map(({ key, label }) => [key, label])).toEqual([
    ['dashboard', '平台首页'],
    ['learning-path', '学习路径'],
    ['practice', '学习工坊'],
    ['training-workshop', '训练工坊'],
    ['personalization', '个性数据'],
  ]);
});
```

Keep `assistant`, `knowledge`, and `settings` in an internal allowed-page set even though they are no longer primary entries.

**Step 2: Run the focused model test and verify failure**

Run:

```powershell
cd frontend/llm
npm run test:unit -- src/appShell.test.js
```

Expected: FAIL because `learning-path` is currently normalized back to `dashboard`.

**Step 3: Write the failing App integration test**

Mock `LearningPathPage` and verify the new page intent renders it:

```jsx
vi.mock('./components/LearningPathPage', () => ({
  default: () => <div>Learning path page</div>,
}));

it('keeps platform home and the certificate learning path as separate destinations', async () => {
  render(<App />);
  await userEvent.click(await screen.findByRole('link', { name: '学习路径' }));
  expect(await screen.findByText('Learning path page')).toBeInTheDocument();
});
```

**Step 4: Run the App test and verify failure**

Run:

```powershell
npm run test:unit -- src/App.test.jsx
```

Expected: FAIL because the route and component do not exist.

**Step 5: Restore the former page as a new component**

Use the historical source only as a reference:

```powershell
git show e8c645c:frontend/llm/src/components/HomePage.jsx
git show e8c645c:frontend/llm/src/components/HomePage.test.jsx
git show e8c645c:frontend/llm/src/index.css
```

Do not cherry-pick `e8c645c`; it changes unrelated backend and frontend files. Move the learning-path-related component logic into `LearningPathPage.jsx`, including:

- `HomeLearningRoute`
- `ReviewTaskRail`
- current/review task mapping
- planned path loading and stage drill-down
- learning target display

Rename page-level classes from `home-portal__...` to `learning-path-page__...` to prevent collisions with the new platform home. Copy only the selectors used by `LearningPathPage` into `LearningPathPage.css`.

**Step 6: Wire the page into App**

Add:

```jsx
case 'learning-path':
  return (
    <LearningPathPage
      currentUser={currentUser}
      onNavigate={navigateToPage}
    />
  );
```

Do not change the existing `practice` and `training-workshop` page behavior.

**Step 7: Run focused tests**

Run:

```powershell
npm run test:unit -- src/appShell.test.js src/App.test.jsx src/components/LearningPathPage.test.jsx
```

Expected: PASS.

**Step 8: Commit**

```powershell
git add -- frontend/llm/src/appShell.js frontend/llm/src/appShell.test.js frontend/llm/src/App.jsx frontend/llm/src/App.test.jsx frontend/llm/src/components/LearningPathPage.jsx frontend/llm/src/components/LearningPathPage.test.jsx frontend/llm/src/components/LearningPathPage.css
git commit -m "feat: restore learning path as a dedicated page"
```

---

### Task 2: Extract the shared persisted qualification selector

**Files:**

- Create: `frontend/llm/src/components/LearningTargetSelector.jsx`
- Create: `frontend/llm/src/components/LearningTargetSelector.test.jsx`
- Modify: `frontend/llm/src/components/LearningPathPage.jsx`

**Step 1: Write failing selector tests**

Cover load, save, disabled state, success status, and failure rollback:

```jsx
it('persists a selected qualification and stays on the current page', async () => {
  const onSaved = vi.fn();
  render(<LearningTargetSelector onSaved={onSaved} />);

  const select = await screen.findByRole('combobox', { name: '学习目标' });
  await userEvent.selectOptions(select, 'target-b');

  expect(saveLearningTarget).toHaveBeenCalledWith('track-b');
  expect(await screen.findByRole('status')).toHaveTextContent('学习目标已更新');
  expect(onSaved).toHaveBeenCalledWith(expect.objectContaining({
    exam_track_id: 'track-b',
  }));
});

it('rolls back the visible selection when persistence fails', async () => {
  saveLearningTarget.mockRejectedValueOnce(new Error('保存失败'));
  render(<LearningTargetSelector />);

  const select = await screen.findByRole('combobox', { name: '学习目标' });
  await userEvent.selectOptions(select, 'target-b');

  expect(await screen.findByRole('alert')).toHaveTextContent('保存失败');
  expect(select).toHaveValue('target-a');
});
```

**Step 2: Verify the tests fail**

Run:

```powershell
npm run test:unit -- src/components/LearningTargetSelector.test.jsx
```

Expected: FAIL because the component does not exist.

**Step 3: Implement the minimal shared component**

Use:

- `GET ${MAIN_API_BASE}/qualification-targets`
- `loadLearningTarget()`
- `saveLearningTarget(examTrackId)`

State contract:

```js
{
  options: [],
  selectedId: '',
  loading: true,
  saving: false,
  error: '',
  message: '',
}
```

On change:

1. Store the previous ID.
2. Optimistically show the new ID.
3. Disable the select while saving.
4. Await `saveLearningTarget`.
5. Show `学习目标已更新` and call `onSaved`.
6. On error, restore the previous ID and show a local alert.

The component must not navigate.

**Step 4: Replace the restored page’s inline selector**

Render:

```jsx
<LearningTargetSelector className="learning-path-page__target-select" />
```

Keep the page’s countdown/display data separate from persistence if it still needs the selected exam label.

**Step 5: Run focused tests**

Run:

```powershell
npm run test:unit -- src/components/LearningTargetSelector.test.jsx src/components/LearningPathPage.test.jsx
```

Expected: PASS.

**Step 6: Commit**

```powershell
git add -- frontend/llm/src/components/LearningTargetSelector.jsx frontend/llm/src/components/LearningTargetSelector.test.jsx frontend/llm/src/components/LearningPathPage.jsx
git commit -m "feat: share persisted qualification selector"
```

---

### Task 3: Rebuild the platform home around the supplied video

**Files:**

- Modify: `frontend/llm/src/components/HomePage.jsx`
- Modify: `frontend/llm/src/components/HomePage.test.jsx`
- Create: `frontend/llm/src/components/PlatformHome.css`
- Create: `frontend/llm/public/design-images/home/platform-agents.mp4`

**Step 1: Replace the HomePage tests with the confirmed contract**

Test:

- heading text
- shared qualification selector
- primary and secondary CTA intents
- four capability cards
- video attributes
- graceful video error fallback

Example:

```jsx
it('renders the video-led platform home and routes its primary actions', async () => {
  const onNavigate = vi.fn();
  const { container } = render(
    <HomePage currentUser={{ username: 'hey' }} onNavigate={onNavigate} />,
  );

  expect(screen.getByRole('heading', {
    name: '多智能体协同，让中医药学习更高效',
  })).toBeInTheDocument();

  const video = container.querySelector('video');
  expect(video).toHaveAttribute('autoplay');
  expect(video).toHaveAttribute('muted');
  expect(video).toHaveAttribute('loop');
  expect(video).toHaveAttribute('playsinline');
  expect(video).toHaveAttribute('src', '/design-images/home/platform-agents.mp4');

  await userEvent.click(screen.getByRole('button', { name: '开始学习路径' }));
  expect(onNavigate).toHaveBeenCalledWith({ page: 'learning-path', params: {} });
});
```

**Step 2: Verify the HomePage test fails**

Run:

```powershell
npm run test:unit -- src/components/HomePage.test.jsx
```

Expected: FAIL against the current ribbon/card portal.

**Step 3: Copy the approved video asset**

Use an explicit source and destination:

```powershell
Copy-Item -LiteralPath '..\..\other\test1.mp4' -Destination '.\public\design-images\home\platform-agents.mp4'
```

Verify:

```powershell
Get-Item -LiteralPath '.\public\design-images\home\platform-agents.mp4' | Select-Object Length
```

Expected size: `4058367` bytes.

**Step 4: Implement the new HomePage**

Use semantic structure:

```jsx
<div className="platform-home">
  <section className="platform-home__hero">
    <div className="platform-home__copy">
      <LearningTargetSelector />
      <h1>多智能体协同，让中医药学习更高效</h1>
      <p>融合多智能体协同与中医知识图谱，个性化规划学习路径，实时伴学答疑，精准提升学习效果。</p>
      <div className="platform-home__actions">
        <button onClick={() => onNavigate?.({ page: 'learning-path', params: {} })}>开始学习路径</button>
        <button onClick={() => onNavigate?.({ page: 'assistant', params: { newConversation: true } })}>了解多智能体如何协同</button>
      </div>
    </div>
    <div className="platform-home__visual">
      <video
        src="/design-images/home/platform-agents.mp4"
        autoPlay
        muted
        loop
        playsInline
        preload="metadata"
        aria-label="多智能体协同学习演示"
      />
    </div>
  </section>
</div>
```

Add four capability cards with these destinations:

- 多智能体协同 → `assistant`
- 个性化学习路径 → `learning-path`
- 知识图谱驱动 → `knowledge` with `{ view: 'atlas' }`
- 数据驱动成长 → `personalization`

For `prefers-reduced-motion`, pause after the first rendered frame and keep the visual container visible. On `error`, add an `is-fallback` state without rendering broken-media copy.

**Step 5: Add isolated responsive styles**

In `PlatformHome.css`:

- desktop hero columns `minmax(0, 0.85fr) minmax(520px, 1.15fr)`
- consistent `max-width`
- four-card grid
- video `object-fit: contain`
- no layout-shifting hover transform
- breakpoints at 1024 px, 768 px, and 480 px
- visible focus styles
- `@media (prefers-reduced-motion: reduce)`

**Step 6: Run tests and build**

Run:

```powershell
npm run test:unit -- src/components/HomePage.test.jsx src/components/LearningTargetSelector.test.jsx
npm run build
```

Expected: PASS and Vite build succeeds.

**Step 7: Commit**

```powershell
git add -- frontend/llm/src/components/HomePage.jsx frontend/llm/src/components/HomePage.test.jsx frontend/llm/src/components/PlatformHome.css frontend/llm/public/design-images/home/platform-agents.mp4
git commit -m "feat: build video-led platform home"
```

---

### Task 4: Replace the global sidebar with accessible top navigation

**Files:**

- Modify: `frontend/llm/src/appShell.js`
- Modify: `frontend/llm/src/appShell.test.js`
- Modify: `frontend/llm/src/components/AppShell.jsx`
- Modify: `frontend/llm/src/components/AppShell.test.jsx`
- Modify: `frontend/llm/src/index.css`

**Step 1: Write failing navigation-configuration tests**

Define dropdown items as navigation intents, not strings:

```js
expect(shell.primaryNav.find((item) => item.key === 'training-workshop').children)
  .toEqual([
    { label: '题目训练', intent: { page: 'training-workshop', params: { taskType: 'question_training' } } },
    { label: 'AI 病患模拟', intent: { page: 'training-workshop', params: { taskType: 'simulated_patient' } } },
    { label: '错题变式', intent: { page: 'training-workshop', params: { taskType: 'mistake_variation' } } },
    { label: '试卷生成', intent: { page: 'training-workshop', params: { taskType: 'paper_generation' } } },
  ]);
```

Also assert:

- assistant and knowledge live under 学习工坊
- settings is absent from primary navigation
- admin entry is only returned for `role: 'admin'`

**Step 2: Write failing AppShell interaction tests**

Cover:

```jsx
it('opens a desktop module menu by hover and keyboard focus', async () => {
  renderShell();
  const workshop = screen.getByRole('link', { name: '学习工坊' });

  await userEvent.hover(workshop);
  expect(screen.getByRole('menuitem', { name: '智能助教' })).toBeVisible();

  workshop.focus();
  await userEvent.keyboard('{ArrowDown}');
  expect(screen.getByRole('menuitem', { name: '智能助教' })).toHaveFocus();
});

it('shows settings, notifications, and logout in the avatar menu', async () => {
  renderShell();
  await userEvent.click(screen.getByRole('button', { name: '打开用户菜单' }));

  expect(screen.getByRole('menuitem', { name: '用户设置' })).toBeVisible();
  expect(screen.getByRole('menuitem', { name: /系统通知/ })).toBeVisible();
  expect(screen.getByRole('menuitem', { name: '退出登录' })).toBeVisible();
});
```

Retain the notification shortcut test and unread count.

**Step 3: Verify the tests fail**

Run:

```powershell
npm run test:unit -- src/appShell.test.js src/components/AppShell.test.jsx
```

Expected: FAIL because the current shell renders a fixed sidebar and modal profile action.

**Step 4: Implement the desktop header**

Refactor helper components inside `AppShell.jsx`:

- `DesktopNavigation`
- `NavigationMenu`
- `AccountMenu`
- `MobileDrawer`

Desktop behavior:

- fixed-height header with `ShellIdentity`
- primary navigation in the middle
- notification and avatar controls on the right
- menu opens on pointer enter or keyboard focus
- menu closes on Escape, outside click, and focus leaving the menu group
- `ArrowDown`, `ArrowUp`, `Home`, and `End` move between menu items
- clicking a child passes the stored intent to `onNavigate`

Do not open `UserProfileModal` from the avatar itself. “用户设置” navigates to:

```js
{ page: 'settings', params: { view: 'account' } }
```

“系统通知” navigates to:

```js
{ page: 'settings', params: { view: 'governance' } }
```

“退出登录” invokes `onLogout`.

**Step 5: Adapt the mobile drawer**

Use buttons with `aria-expanded` for primary modules with children. Render children only for the expanded module. Keep direct navigation available on the module label.

Do not depend on hover on mobile.

**Step 6: Add top-shell CSS overrides**

Append one consolidated section to `index.css`:

- `.app-shell` becomes a vertical page frame
- `.app-shell__topbar` replaces `.app-shell__sidebar`
- `.app-shell__workspace` occupies remaining height
- `.app-shell__main` keeps existing `page` versus `contained` scrolling
- dropdown z-index uses an explicit scale
- desktop header collapses to mobile header below 900 px
- old sidebar selectors remain untouched unless they are directly unused by the new markup

**Step 7: Run focused navigation tests**

Run:

```powershell
npm run test:unit -- src/appShell.test.js src/components/AppShell.test.jsx src/App.test.jsx
```

Expected: PASS.

**Step 8: Commit**

```powershell
git add -- frontend/llm/src/appShell.js frontend/llm/src/appShell.test.js frontend/llm/src/components/AppShell.jsx frontend/llm/src/components/AppShell.test.jsx frontend/llm/src/index.css
git commit -m "feat: replace global sidebar with top navigation"
```

---

### Task 5: Complete regression, accessibility, and visual verification

**Files:**

- Modify only if verification reveals a scoped defect:
  - `frontend/llm/src/components/PlatformHome.css`
  - `frontend/llm/src/components/LearningPathPage.css`
  - `frontend/llm/src/index.css`
  - relevant focused tests

**Step 1: Run the full focused regression suite**

Run:

```powershell
cd frontend/llm
npm run test:unit -- src/App.test.jsx src/appShell.test.js src/components/AppShell.test.jsx src/components/HomePage.test.jsx src/components/LearningPathPage.test.jsx src/components/LearningTargetSelector.test.jsx src/components/PracticePage.test.jsx src/components/exam-atlas/ExamAtlas.test.jsx
```

Expected: all tests PASS.

**Step 2: Run lint for touched source files**

Run:

```powershell
npx eslint src/App.jsx src/appShell.js src/components/AppShell.jsx src/components/HomePage.jsx src/components/LearningPathPage.jsx src/components/LearningTargetSelector.jsx
```

Expected: zero errors.

**Step 3: Run the production build**

Run:

```powershell
npm run build
```

Expected: Vite build succeeds and `dist/design-images/home/platform-agents.mp4` exists.

**Step 4: Perform browser verification**

Verify at:

- 1440 × 900
- 1024 × 768
- 768 × 1024
- 375 × 812

Check:

- no horizontal overflow
- top navigation does not wrap over account controls
- dropdowns remain inside viewport
- mobile accordion is fully keyboard/touch operable
- video does not crop its core content
- new home selector saves without navigating
- “开始学习路径” opens the restored page
- ordinary users do not see 管理入口
- administrators do see 管理入口

Capture a desktop screenshot for comparison with the third reference image.

**Step 5: Verify the FastAPI-served bundle when port 7860 is used**

After copying the built frontend into the configured distribution location, fetch `/`, extract the hashed JavaScript asset, and verify it contains:

- `多智能体协同，让中医药学习更高效`
- `学习路径`
- `试卷生成`
- `系统通知`

Do not diagnose browser cache until root HTML and the served hashed bundle have been checked.

**Step 6: Request code review**

Use `@requesting-code-review` with focus on:

- lost navigation paths
- dropdown keyboard/focus behavior
- target-save race conditions
- role-gated admin rendering
- responsive overflow
- unintended inclusion of unrelated user files

**Step 7: Commit any verification-only fixes**

Stage only explicit touched paths:

```powershell
git add -- <explicit-paths>
git commit -m "fix: finalize platform home navigation"
```

Do not stage `.claude/`, `artifacts/`, `node_modules/`, `other/`, or unrelated plan files.
