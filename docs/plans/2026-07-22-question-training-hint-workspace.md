# Question Training Hint Workspace Implementation Plan

> **For Codex:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 将训练工坊的题目训练模块改造成 Figma 风格的双栏作答与按需提示工作台，并移除证据检查器。

**Architecture:** 保留 `PracticePage` 的模块导航、训练产物和现有接口；在 `AtlasPracticePanel` 内组织题目、答案和提示状态。使用项目现有 React、Tailwind 工具类、CSS 变量、UI Button 与 lucide-react 图标，不新增依赖。

**Tech Stack:** React 19、Vite、Tailwind CSS 4、lucide-react、Vitest、Testing Library、Playwright

---

### Task 1: 锁定行为测试

**Files:**

- Modify: `frontend/llm/src/components/PracticePage.test.jsx`
- Modify: `frontend/llm/src/components/exam-atlas/AtlasPracticePanel.test.jsx`

**Step 1:** 新增提示默认锁定、点击展开和知识点动态内容测试。

**Step 2:** 更新移动端页面测试，断言仅存在“任务 / 结果”且证据检查器被移除。

**Step 3:** 运行目标测试并确认在实现前失败。

### Task 2: 移除证据检查器

**Files:**

- Modify: `frontend/llm/src/components/PracticePage.jsx`
- Modify: `frontend/llm/src/index.css`

**Step 1:** 删除证据页签状态、抽屉开关、遮罩、检查器渲染和相关辅助函数。

**Step 2:** 将外层桌面网格改为模块导航 + 内容两列，移动端页签改为任务 + 结果。

**Step 3:** 运行 `PracticePage` 测试。

### Task 3: 构建按需提示工作台

**Files:**

- Modify: `frontend/llm/src/components/QuestionTrainingPanel.jsx`
- Modify: `frontend/llm/src/components/exam-atlas/AtlasPracticePanel.jsx`
- Modify: `frontend/llm/src/index.css`

**Step 1:** 使用语义化容器重排题目、知识点、答案和提交操作。

**Step 2:** 添加提示触发器和右侧提示面板；新题或上下文变化时重置提示。

**Step 3:** 使用题型、难度、题源和知识点生成非答案性质的动态作答建议。

**Step 4:** 用项目 CSS 变量和匹配的 lucide 图标复现参考视觉。

### Task 4: 响应式与交付验证

**Files:**

- Modify: `frontend/llm/src/index.css`

**Step 1:** 桌面采用题目主栏 + 提示侧栏，窄屏改为单栏。

**Step 2:** 运行目标组件测试、前端构建和 lint。

**Step 3:** 在桌面和移动视口检查布局、焦点状态和提示展开行为。
