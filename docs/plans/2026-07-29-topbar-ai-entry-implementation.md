# Topbar AI Assistant Entry Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 在不改变导航位置和点击逻辑的前提下，让桌面顶部的“AI 智能助教”成为清晰、克制的核心入口。

**Architecture:** 为现有按钮增加一个语义明确的修饰类，并在既有桌面顶部栏媒体规则中覆盖视觉令牌。保持共享基础按钮样式与移动断点规则不变。

**Tech Stack:** React、CSS、Vitest、Testing Library、Vite。

---

### Task 1: 用失败测试定义突出入口标记

**Files:**
- Modify: `frontend/llm/src/components/AppShell.test.jsx`
- Modify: `frontend/llm/src/components/AppShell.jsx`

1. 在 `AppShell.test.jsx` 新增断言，要求名称为“AI 智能助教”的桌面按钮带有 `app-shell__assistant-entry--featured` 类。
2. 运行 `npm --prefix frontend/llm run test:unit -- --run src/components/AppShell.test.jsx`，确认因类缺失而失败。
3. 仅为现有按钮补上该修饰类，重跑同一测试确认通过。

### Task 2: 在桌面样式中实现克制强调

**Files:**
- Modify: `frontend/llm/src/index.css`

1. 在 `.app-shell__assistant-entry` 既有桌面规则附近添加 `--featured` 样式：翠绿渐变、白色文本和图标、轻描边及轻柔阴影。
2. 添加对应 hover 与 focus-visible 状态，保持无障碍焦点可见且不使用持续动画。
3. 不改动移动断点中隐藏文字、紧凑宽度的既有规则。

### Task 3: 回归与成品验证

**Files:**
- Verify: `frontend/llm/src/components/AppShell.test.jsx`
- Verify: `frontend/llm/dist/`

1. 运行 AppShell 单测和现有重点前端单测集。
2. 运行 `npm --prefix frontend/llm run build`。
3. 在 `http://127.0.0.1:7860` 的桌面尺寸检查按钮外观与访客点击登录行为。
