# 针灸空间针体 PNG 参考材质 Implementation Plan

> **For Codex:** REQUIRED SUB-SKILL: Use `executing-plans` to implement this plan task-by-task.

**Goal:** 在不改变 GLB 空间针体、皮肤法线对齐或进针角度的前提下，使针的材质外观匹配用户提供的 PNG：暖铜色针柄与银色针身。

**Architecture:** 继续使用 `realisticNeedle.js` 的 GLB 模板克隆和本地 Y 轴对齐逻辑。为针身与握柄定义两组共享的 `MeshPhysicalMaterial`，并在加载 GLB 模板时依据节点名称替换其网格材质；备用网格采用同一材质分配。

**Tech Stack:** React、Three.js、Vitest、ESLint、Vite。

---

### Task 1: 锁定 PNG 参考材质分配

**Files:**

- Modify: `frontend/llm/src/components/acupuncture/AcupunctureModelCanvas.test.jsx`
- Modify: `frontend/llm/src/components/acupuncture/realisticNeedle.js`

**Step 1: Write the failing test**

为 `createRealisticNeedle` 新增测试，断言备用 3D 针的 `needle-shaft` 采用银色材质，`needle-handle` 采用暖铜材质，且两者不共享同一材质对象。

**Step 2: Run test to verify it fails**

Run: `npm --prefix frontend/llm test -- AcupunctureModelCanvas.test.jsx`

Expected: FAIL，因为当前针身和针柄共用银色金属材质。

**Step 3: Write minimal implementation**

在 `realisticNeedle.js` 定义银色针身和暖铜针柄的共享 `MeshPhysicalMaterial`。备用网格按部件分配材质；缓存 GLB 模板时按 `needle-handle`、`needle-grip-ring` 和 `needle-loop` 节点替换为暖铜材质，其余针身、针尖为银色材质。

**Step 4: Run test to verify it passes**

Run: `npm --prefix frontend/llm test -- AcupunctureModelCanvas.test.jsx`

Expected: PASS。

### Task 2: 构建与静态资源回归

**Files:**

- Verify: `frontend/llm/public/acupuncture/needles/needle-direct.png`
- Verify: `frontend/llm/public/acupuncture/needles/needle-oblique.png`
- Verify: `frontend/llm/public/acupuncture-models/realistic-acupuncture-needle.glb`

**Step 1: Lint and build**

Run: `npm --prefix frontend/llm run lint` and `npm --prefix frontend/llm run build`.

Expected: 通过；仅保留项目既有的非阻塞构建提示。

**Step 2: Verify tracked assets and commit**

确认两张最终 PNG 和 GLB 均由 Git 追踪，并只提交本次针灸实现文件。

