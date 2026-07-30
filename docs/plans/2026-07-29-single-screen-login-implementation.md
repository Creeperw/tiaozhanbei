# Single-Screen Login Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 将访客登录覆盖层收敛为无纵向滚动的单屏登录页，同时保持现有认证交互。

**Architecture:** 删除 `AuthPage` 内首屏之外的静态介绍 DOM；用一个显式修饰类和覆盖层 CSS 锁定视口。认证状态、表单状态和 API 调用不变。

**Tech Stack:** React、CSS、Vitest、Testing Library、Vite。

---

### Task 1: 用失败测试定义单屏内容边界

**Files:**
- Modify: `frontend/llm/src/components/AuthPage.test.jsx`

1. 新增测试，断言登录页带有 `auth-page--single-screen`，保留账号表单，且不再渲染“以智能重塑本草学习”、能力卡或页脚。
2. 运行 AuthPage 测试，确认因旧介绍内容和缺失修饰类而失败。

### Task 2: 移除介绍区并锁定视口

**Files:**
- Modify: `frontend/llm/src/components/AuthPage.jsx`
- Modify: `frontend/llm/src/components/AuthPage.css`
- Modify: `frontend/llm/src/index.css`

1. 移除仅被介绍区使用的图标和能力卡数据。
2. 删除介绍区与页脚，为根节点添加单屏修饰类。
3. 让认证覆盖层、对话容器和登录页使用 `100dvh` 并隐藏纵向溢出；保留小屏表单的可用空间。
4. 重跑 AuthPage 测试确认通过。

### Task 3: 视觉回归

**Files:**
- Verify: `frontend/llm/dist/`

1. 运行登录相关和首页重点测试。
2. 构建生产前端。
3. 在 7860 桌面尺寸打开登录弹窗，确认背景铺满、右侧无滚动轨道、首屏无长介绍。
