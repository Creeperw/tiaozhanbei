# Home Capability Innovation Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 用四张首页能力卡片清晰呈现多智能体可信协同、安全学习路径、专项训练和学生参与的人机协同闭环。

**Architecture:** 复用 `platformCapabilities.js` 作为首页和能力详情页共用的数据源。只更新四项能力记录及其卡片点击测试，不改变首页布局组件或既有详情页渲染机制。

**Tech Stack:** React、Lucide、Vitest、Testing Library、Vite。

---

### Task 1: 用失败测试定义四张卡片的最终名称与路由

**Files:**
- Modify: `frontend/llm/src/components/HomePage.test.jsx`
- Modify: `frontend/llm/src/components/CapabilityDetailPage.test.jsx`

1. 将能力卡片参数化测试改为“多智能体协同、个性化学习路径、专项训练、人机协同”。
2. 断言第四张卡片使用新的 `human-collaboration` 能力键。
3. 运行两份测试，确认因旧标题和旧能力键而失败。

### Task 2: 以共用能力数据更新文案与详情

**Files:**
- Modify: `frontend/llm/src/platformCapabilities.js`

1. 更新前三张卡片的 `eyebrow`、`description` 与 `shortDescription` 为已确认文案。
2. 将旧的资料溯源记录替换为 `human-collaboration`，保留其索引位置、卡片图标和通往能力详情页的通用交互。
3. 为第四项提供三条简洁能力特征，使通用能力详情页仍可正常展示。
4. 重跑两份测试确认通过。

### Task 3: 构建和页面回归

**Files:**
- Verify: `frontend/llm/src/components/HomePage.jsx`
- Verify: `frontend/llm/dist/`

1. 运行首页与能力详情页测试。
2. 运行 `npm --prefix frontend/llm run build`。
3. 在 `http://127.0.0.1:7860` 确认四张卡片标题、描述及第四张卡片详情页均正确显示。
