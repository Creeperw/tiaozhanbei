# Home Learning Path Navigation Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 完成首页首次考试类别选择、学习路径双层切换与阶段定位、当前考试类别提示，以及头像快捷入口的来源返回。

**Architecture:** 在 `App` 导航边界增加学习目标门控与当前页面意图传递；复用现有资格考试、经典路径、个性化路径和调研 API。学习路径组件分别管理路线来源与呈现方式，头像快捷入口通过 `returnTo` 恢复原页面。

**Tech Stack:** React 18、Vite、Vitest、Testing Library、Lucide React、CSS。

---

### Task 1: 首页首次考试类别门控

**Files:**

- Modify: `frontend/llm/src/App.jsx`
- Modify: `frontend/llm/src/components/HomePage.jsx`
- Create: `frontend/llm/src/components/QualificationTargetDialog.jsx`
- Test: `frontend/llm/src/App.test.jsx`
- Test: `frontend/llm/src/components/HomePage.test.jsx`
- Test: `frontend/llm/src/components/QualificationTargetDialog.test.jsx`

**Step 1: Write the failing tests**

- 断言首页不再渲染“多智能体助教”按钮。
- 断言 `target: null` 时点击“开始学习”打开选择框且未导航。
- 断言已有目标时直接进入学习路径。
- 断言选择并保存后关闭弹窗并进入学习路径；保存失败时显示错误。

**Step 2: Run tests to verify RED**

Run:

```powershell
npm --prefix frontend/llm test -- --run src/components/HomePage.test.jsx src/App.test.jsx src/components/QualificationTargetDialog.test.jsx
```

Expected: FAIL，因为按钮仍存在且门控弹窗尚未实现。

**Step 3: Write minimal implementation**

- 删除 `HomePage` 的次按钮。
- 新增无浏览器缓存的 `QualificationTargetDialog`，读取目录并调用 `saveLearningTarget`。
- 在 `App.navigateToPage` 处理首页发出的 `learning-path` 意图：学习目标为空时暂存意图并打开弹窗；已有目标时直接导航。

**Step 4: Run tests to verify GREEN**

重复 Step 2 命令，Expected: PASS。

### Task 2: 学习路径路线来源和阶段定位

**Files:**

- Modify: `frontend/llm/src/components/QualificationRoutePage.jsx`
- Modify: `frontend/llm/src/components/learning-stage/LearningStageLanding.jsx`
- Modify: `frontend/llm/src/index.css`
- Test: `frontend/llm/src/components/QualificationRoutePage.test.jsx`
- Test: `frontend/llm/src/components/learning-stage/LearningStageLanding.test.jsx`

**Step 1: Write the failing tests**

- 断言默认激活“经典路径”和“学习阶段”。
- 断言按钮顺序为“学习阶段”在“学习路径”左侧。
- 断言切换“个性化路径”会读取计划路径。
- 断言点击第一阶段会切换到路径视图并展示该阶段教材节点。

**Step 2: Run tests to verify RED**

```powershell
npm --prefix frontend/llm test -- --run src/components/QualificationRoutePage.test.jsx src/components/learning-stage/LearningStageLanding.test.jsx
```

Expected: FAIL，因为当前默认视图、切换层级和阶段选择行为不符合设计。

**Step 3: Write minimal implementation**

- 将路线来源 `routeMode` 与呈现方式 `routeView` 分离。
- 经典与个性化路线分别按需加载。
- 默认 `routeMode=classic`、`routeView=cards`。
- 抽取阶段打开逻辑，让阶段卡选择复用 `openNode` 的子节点加载流程。
- 增加两组视觉层级明确的分段按钮和响应式样式。

**Step 4: Run tests to verify GREEN**

重复 Step 2 命令，Expected: PASS。

### Task 3: 学情调研入口

**Files:**

- Modify: `frontend/llm/src/components/QualificationRoutePage.jsx`
- Modify: `frontend/llm/src/components/OnboardingSurveyPanel.jsx`
- Modify: `frontend/llm/src/index.css`
- Test: `frontend/llm/src/components/QualificationRoutePage.test.jsx`
- Test: `frontend/llm/src/components/OnboardingSurveyPanel.test.jsx`

**Step 1: Write the failing tests**

- 断言学习路径标题旁存在“学情调研”。
- 断言点击后打开调研弹窗并可关闭。
- 断言保存成功后关闭弹窗并重新读取路径。
- 断言站内调研显示“退出调研”而不是“退出注册”。

**Step 2: Run tests to verify RED**

```powershell
npm --prefix frontend/llm test -- --run src/components/QualificationRoutePage.test.jsx src/components/OnboardingSurveyPanel.test.jsx
```

Expected: FAIL，因为学习路径尚无调研入口。

**Step 3: Write minimal implementation**

- 为 `OnboardingSurveyPanel` 增加可配置退出标签。
- 在 `QualificationRoutePage` 使用可访问对话框承载调研面板。
- 保存后递增路线修订号并关闭弹窗。

**Step 4: Run tests to verify GREEN**

重复 Step 2 命令，Expected: PASS。

### Task 4: 当前考试类别提示与来源返回

**Files:**

- Modify: `frontend/llm/src/App.jsx`
- Modify: `frontend/llm/src/components/AppShell.jsx`
- Modify: `frontend/llm/src/components/LearningTargetSelector.jsx`
- Modify: `frontend/llm/src/components/PracticePage.jsx`
- Modify: `frontend/llm/src/index.css`
- Test: `frontend/llm/src/App.test.jsx`
- Test: `frontend/llm/src/components/AppShell.test.jsx`
- Test: `frontend/llm/src/components/LearningTargetSelector.test.jsx`
- Test: `frontend/llm/src/components/PracticePage.test.jsx`

**Step 1: Write the failing tests**

- 断言顶部考试类别触发器显示当前选中名称。
- 断言头像收藏夹、笔记本意图包含完整 `returnTo`。
- 断言收藏夹、笔记本左上角返回调用真实来源意图并显示对应文案。

**Step 2: Run tests to verify RED**

```powershell
npm --prefix frontend/llm test -- --run src/App.test.jsx src/components/AppShell.test.jsx src/components/LearningTargetSelector.test.jsx src/components/PracticePage.test.jsx
```

Expected: FAIL，因为顶部未显示名称且快捷入口未携带来源。

**Step 3: Write minimal implementation**

- `LearningTargetSelector` 将当前选项回传给菜单触发器。
- 顶部触发器增加截断的“当前 · …”辅助文字。
- `App` 向 `AppShell` 传入未经壳层归一化的 `pageIntent`。
- 头像快捷入口把该意图作为 `returnTo` 传给收藏夹和笔记本。
- `PracticePage` 根据来源生成返回标签并恢复来源。

**Step 4: Run tests to verify GREEN**

重复 Step 2 命令，Expected: PASS。

### Task 5: 综合验证

**Files:**

- Verify all files changed by Tasks 1–4.

**Step 1: Run targeted tests**

```powershell
npm --prefix frontend/llm test -- --run src/App.test.jsx src/components/HomePage.test.jsx src/components/QualificationTargetDialog.test.jsx src/components/QualificationRoutePage.test.jsx src/components/OnboardingSurveyPanel.test.jsx src/components/AppShell.test.jsx src/components/LearningTargetSelector.test.jsx src/components/PracticePage.test.jsx
```

Expected: PASS。

**Step 2: Run production build**

```powershell
npm --prefix frontend/llm run build
```

Expected: PASS，并更新生产资源。

**Step 3: Run lint and whitespace checks**

```powershell
npm --prefix frontend/llm run lint -- --no-warn-ignored src/App.jsx src/components/HomePage.jsx src/components/QualificationTargetDialog.jsx src/components/QualificationRoutePage.jsx src/components/OnboardingSurveyPanel.jsx src/components/AppShell.jsx src/components/LearningTargetSelector.jsx src/components/PracticePage.jsx
git diff --check
```

Expected: PASS，无新增 lint 或空白错误。
