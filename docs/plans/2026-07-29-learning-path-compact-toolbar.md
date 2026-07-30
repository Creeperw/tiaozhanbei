# Learning Path Compact Toolbar Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 将学习路径头部改成单行紧凑工具栏，把学情调研移到签到下方，并将规划详情改成无横向滚动的单列 Markdown 文档。

**Architecture:** 仅调整 `QualificationRoutePage` 的现有 JSX 分组与局部 CSS，不改变学习路径、调研或规划数据流。工具栏使用固定顺序的三个区域并统一按钮尺寸；详情区复用现有 Markdown 渲染器，改为单列章节流并让表格在容器内换行。

**Tech Stack:** React 19、Vite、Vitest、Testing Library、React Markdown、Remark GFM、CSS

---

### Task 1: 固化工具栏顺序与调研入口位置

**Files:**

- Modify: `frontend/llm/src/components/QualificationRoutePage.test.jsx`
- Modify: `frontend/llm/src/components/QualificationRoutePage.jsx`

**Step 1: Write the failing test**

在 `QualificationRoutePage.test.jsx` 中断言：

```jsx
const header = screen.getByRole('heading', {
  name: '中医类别执业医师资格考试',
}).closest('.home-portal__route-header');

expect([...header.children].map((node) => node.className)).toEqual([
  'home-portal__route-title-block',
  'home-portal__route-switch',
  'home-portal__route-mode',
  'home-portal__route-detail-toggle',
]);
expect(within(header).getByRole('button', { name: '规划详情' })).toBeInTheDocument();
expect(within(header).queryByRole('button', { name: '学情调研' })).not.toBeInTheDocument();
expect(within(screen.getByLabelText('学习首页操作')).getByRole('button', {
  name: '学情调研',
})).toBeInTheDocument();
```

**Step 2: Run test to verify it fails**

```powershell
npm --prefix frontend/llm run test:unit -- --run src/components/QualificationRoutePage.test.jsx
```

Expected: FAIL，因为当前工具栏仍有 `route-controls` 包装，详情按钮仍叫“了解详情”，调研按钮仍在工具栏。

**Step 3: Write minimal implementation**

- 将 `home-portal__route-mode` 提升为头部直接子元素，并放在视图切换右侧。
- 将详情按钮提升为头部最后一个直接子元素，文案改为“规划详情”，详情状态改为“返回路径”。
- 将调研按钮移动到 `home-portal__hero-actions` 内、签到按钮之后。
- 为英雄区操作组添加 `aria-label="学习首页操作"`。

**Step 4: Run tests to verify GREEN**

重复 Step 2，Expected: PASS。

**Step 5: Commit**

```powershell
git add -- frontend/llm/src/components/QualificationRoutePage.jsx frontend/llm/src/components/QualificationRoutePage.test.jsx
git commit -m "fix: reorder learning path controls"
```

### Task 2: 将规划详情改成单列 Markdown 文档

**Files:**

- Modify: `frontend/llm/src/components/QualificationRoutePage.test.jsx`
- Modify: `frontend/llm/src/components/QualificationRoutePage.jsx`

**Step 1: Write the failing test**

断言详情容器中只有一个文档节点，并验证章节顺序：

```jsx
const details = await screen.findByRole('region', {
  name: '长期规划和短期规划说明',
});
const document = details.querySelector('.home-portal__planning-document');
expect(document).toBeInTheDocument();
expect(document.querySelectorAll(':scope > section')).toHaveLength(2);
expect([...document.querySelectorAll(':scope > section > h3')].map(
  (heading) => heading.textContent,
)).toEqual(['长期规划', '短期规划']);
```

同时保留对 Markdown `h2`、`table`、`th` 和 `li` 的语义断言。

**Step 2: Run test to verify it fails**

运行 Task 1 的测试命令。

Expected: FAIL，因为当前详情区仍是双列 `article` 网格。

**Step 3: Write minimal implementation**

将详情结构改为：

```jsx
<article className="home-portal__planning-document">
  <section>
    <h3>长期规划</h3>
    <PlanningMarkdown ... />
  </section>
  <section>
    <h3>短期规划</h3>
    <PlanningMarkdown ... />
  </section>
</article>
```

不改变规划数据加载、失败提示和返回路径行为。

**Step 4: Run tests to verify GREEN**

重复 Task 1 的测试命令，Expected: PASS。

**Step 5: Commit**

```powershell
git add -- frontend/llm/src/components/QualificationRoutePage.jsx frontend/llm/src/components/QualificationRoutePage.test.jsx
git commit -m "fix: present planning details as one document"
```

### Task 3: 实现紧凑单行样式与无横向滚动的规划表格

**Files:**

- Modify: `frontend/llm/src/components/QualificationRoutePage.test.jsx`
- Modify: `frontend/llm/src/index.css`

**Step 1: Write the failing CSS regression test**

读取 `src/index.css` 并断言：

```jsx
expect(css).toMatch(/home-portal__route-header[\s\S]*flex-wrap:\s*nowrap/);
expect(css).toMatch(/home-portal__route-header[\s\S]*overflow-x:\s*auto/);
expect(css).toMatch(/home-portal__route-header[\s\S]*min-height:\s*34px/);
expect(css).toMatch(/home-portal__planning-markdown table[\s\S]*table-layout:\s*fixed/);
expect(css).not.toMatch(/home-portal__planning-markdown table[^}]*min-width:\s*520px/);
expect(css).toMatch(/home-portal__planning-markdown td[\s\S]*overflow-wrap:\s*anywhere/);
```

**Step 2: Run test to verify it fails**

运行 Task 1 的测试命令。

Expected: FAIL，因为当前工具栏允许换行，按钮高度为 38px，表格有 520px 最小宽度。

**Step 3: Write minimal implementation**

- 头部使用 `flex-wrap: nowrap; overflow-x: auto;`，所有直接子项 `flex: 0 0 auto`。
- 考试名称使用较小 `clamp()` 字号，保留 `white-space: nowrap`。
- 两组切换和详情按钮统一为 34px 高、较小字号和横向内边距。
- 英雄区操作按钮改为竖向排列，调研按钮位于签到下方。
- 规划详情使用单列容器；表格设置 `table-layout: fixed; min-width: 0;`。
- `th`、`td` 使用 `white-space: normal; overflow-wrap: anywhere; word-break: break-word;`。

**Step 4: Run tests to verify GREEN**

重复 Task 1 的测试命令，Expected: PASS。

**Step 5: Commit**

```powershell
git add -- frontend/llm/src/index.css frontend/llm/src/components/QualificationRoutePage.test.jsx
git commit -m "style: compact learning path toolbar"
```

### Task 4: 综合验证与远端发布

**Files:**

- Verify: `frontend/llm/src/components/QualificationRoutePage.jsx`
- Verify: `frontend/llm/src/components/QualificationRoutePage.test.jsx`
- Verify: `frontend/llm/src/index.css`

**Step 1: Run focused tests**

```powershell
npm --prefix frontend/llm run test:unit -- --run src/components/QualificationRoutePage.test.jsx src/components/AppShell.test.jsx src/components/HomePage.test.jsx
```

Expected: PASS。

**Step 2: Run production build**

```powershell
npm --prefix frontend/llm run build
```

Expected: PASS；仅允许已有的 Browserslist 和 chunk-size 提示。

**Step 3: Run whitespace and scope checks**

```powershell
git diff --check
git status --short
```

Expected: 无空白错误；`.codegraph/` 仍保持未跟踪且未暂存。

**Step 4: Push and verify remote SHA**

```powershell
git push
git rev-parse HEAD
git ls-remote --heads origin fxz/2026-07-29-home-login-navigation
```

Expected: 本地 HEAD 与远端分支 SHA 一致。
