# 2026-08-03 多智能体协作「展示升级 + 功能融合」设计方案

## 一、背景与目标

时珍智训（挑战杯/云之脑比赛）的核心差异化卖点是**六智能体协作**，
但当前产品形态中它更像是后台技术细节，而不是用户可感知、可参与、可受益的能力：

- 首页有演示动画、能力卡有介绍、回复上方有小按钮、侧栏有 AgentTimeline——
  但用户始终是**旁观者**：协作在后台发生，用户只能事后看到结果。
- 后端其实已经铺好了大量"人机协作"管道（中断追问、人工复核、记忆冲突、检查点恢复），
  但前端没有把它们变成真正的交互功能。

因此本次设计分两层，递进实施：

1. **展示升级**（L1–L4）：让协作过程"看得见、看得懂、看得有记忆点"。
2. **功能融合**（A–D）：让用户"参与决策、指挥智能体、把产物转成行动"。

设计原则：

- **自然语言优先**：面向用户的文案一律用自然语言表达协作意图，不堆技术术语。
- **复用现有管道**：优先接通后端已存在的 `interrupt / resume / waiting_human_review / plan-reviews` 能力，
  不做破坏性后端改动。
- **可追溯即可信**：每个结论都能展开到"哪个智能体、用了哪些证据、审核怎么判"。
- **闭环**：协作产物 → 用户行动 → 新行为数据 → 下一轮诊断。

---

## 二、现状盘点与差距

### 2.1 现有 4 处展示

| 位置 | 形态 | 局限 |
|---|---|---|
| 首页演示动画（`HomePage.jsx` 平台首页视觉区） | 六角色图标流转 | 演示性质，不可交互，不产生数据 |
| 能力卡「多智能体协同」（`platformCapabilities.js`） | 静态介绍 + 六角色 feature 列表 | 只讲"有什么"，不讲"怎么协作" |
| 回复上方协作回执小按钮（`ChatInterface.jsx`） | 徽标 + 展开 AgentTimeline | 藏在侧栏，偏技术调试视角 |
| AgentTimeline 侧栏（`AgentTimeline.jsx` / `agentPresentationModel.js`） | 六角色线性列表 + 技术详情 | 线性任务列表看不出协作流转 |

### 2.2 已具备的技术底座（前端）

- `agentPresentationModel.js`：`AGENT_ROLES` 六角色（planner / memory / diagnosis / knowledge / expert / audit），
  各含 `key / label / description`；`buildAgentPresentation()` 已把运行时节点按角色分组归一，
  产出 `status / statusLabel / summary / tools / modelCalls / startedAt / endedAt`。
- `workflowChatClient.js`：`runtimeEventToTrace()` 已把后端 LangGraph 事件流归一为
  `planning_start / planning_done / context_* / execution_* / feedback_* / human_review_waiting / workflow_done` 等轨迹事件。
- `pageIntent.js`：`WORKSHOP_DESTINATIONS` 已打通
  `workshop.paper / workshop.knowledge_card / workshop.knowledge_video / workshop.question_training / workshop.topic_training` 等产物跳转。

### 2.3 已具备的技术底座（后端）

| 能力 | 位置 | 现状 |
|---|---|---|
| 澄清追问（Planner/诊断 `clarification_questions`） | `agents/planner.py`、`agents/diagnosis.py`、`application/workflow_presentation.py` | 中断返回 `interrupt{reason, questions}`，前端只显示"生成已中断"文本 |
| 人工复核 `waiting_human_review` | `agents/audit.py`、`application/personalized_review_card.py` | 前端只显示"等待人工复核"，无操作入口 |
| 检查点恢复 `resume(thread_id, answer)` | `api/app.py` `/api/v1/review-cards/runs/{thread_id}/resume/stream` | 前端仅用于"重发消息"续跑 |
| 计划审核 accept/reject | `api/app.py` `/api/v1/plan-reviews/{review_id}/decision` | 前端对话流未接入 |
| 记忆冲突治理 `memory_conflict` | `agents/memory.py`、`contracts/memory.py`、前端 `ProfileConflictList` | 只存在于设置页，未进入对话流 |
| 回复反馈 like/dislike | `ChatInterface.jsx` → `/api/v1/feedback` | 仅针对整条回复，无法针对单个智能体环节 |

---

## 三、展示升级设计（L1–L4）

### L1 常驻协作带（CollaborationStrip）

- **位置**：对话输入框上方常驻一条"协作状态带"，替代/升级现有回复上方小按钮。
- **内容**：实时显示"本次协作中：任务规划 ✓ → 学情诊断 → 知识库检索中 → 审核裁判"，
  每步带角色色点与图标；完成后收缩为一条"协作凭证"摘要。
- **数据**：直接消费 `workflowChatClient` 已归一化的轨迹事件，无需后端改动。

### L2 协作泳道图（CollaborationFlow）

- **位置**：AgentTimeline 侧栏内，从"线性任务列表"升级为**时间轴 + 角色泳道**。
- **内容**：横轴为时间，纵轴为六角色泳道；节点间用连线表达依赖与**审核返修回环**
  （audit → expert 的 revise 箭头用不同颜色/虚线）。
- **数据**：`agentPresentationModel.js` 已含节点时序与 agent 归属；正式 React 助教中的执行轨迹
  已保留返修边（revision）语义，可直接作为泳道连线的数据来源。

### L3 协作凭证卡（CollaborationReceiptCard）

- **位置**：每条 assistant 回复底部。
- **内容**：本次协作的"收据"——参与角色徽标、各角色一句话总结、耗时、审核结论
  （通过/返修 N 次/转人工），点击展开完整泳道。
- **文案原则**：全部用自然语言（如"任务规划帮你拆分了 3 个步骤，审核裁判检查了 8 条依据后通过"），
  不出现 `planner_agent` 等运行名（`sanitizeAgentLog()` 已做替换映射）。

### L4 首页演示升级（AgentOrbitDemo）

- **位置**：`HomePage.jsx` 平台首页视觉区。
- **内容**：把静态流转动画升级为**可点击体验**——点任一角色弹出该角色"最近一次真实协作"卡片
  （来自真实会话数据，而非写死的演示文案），并给出"去对话中点名这个智能体"入口（衔接功能融合 B 模块）。

### 六角色视觉体系

| 角色 | 专属色 | 图标 | 职责口号 |
|---|---|---|---|
| planner 任务规划 | 靛蓝 `#4f6ef7` | ClipboardList | 理解需求，安排执行路径 |
| memory 记忆管理 | 琥珀 `#e8a13c` | Archive | 读取会话与偏好，保持连续上下文 |
| diagnosis 学情诊断 | 青绿 `#2bb3a3` | Activity | 判断掌握状态与学习节奏 |
| knowledge 知识库管理 | 天蓝 `#3d9bf0` | LibraryBig | 检索教材、题目、视频与证据 |
| expert 专家 | 紫 `#8b6cf0` | Sparkles | 生成计划、讲解、题目或试卷 |
| audit 审核裁判 | 玫瑰 `#e05a6f` | ShieldCheck | 检查事实、质量与发布条件 |

---

## 四、功能融合设计（A–D）

### A. 决策点参与（Human-in-the-loop 交互卡）

目标：智能体需要信息或拿不准时，不是沉默中断，而是弹出**可交互卡片**等用户作答，答完自动 resume。

**A1 · 澄清追问卡**（Planner/诊断 `clarification_questions`）
- 后端中断返回 `{status:'interrupted', interrupt:{reason, questions, fields}}`。
- 前端渲染卡片：原因 + 问题列表 + 快捷选项（按 `fields` 给出推荐选项）+ 自由输入。
- 提交 → 调 `/resume/stream` 带 answer → 协作继续。
- 工作量：前端新增"协作交互卡"组件 + 状态机；后端管道已通，无需改动。

**A2 · 人工复核卡**（audit `needs_human_review`）
- 展示"待复核内容 + 审核结论 + 证据清单"。
- 三个动作：**批准发布** / **驳回并要求修改**（带原因，回灌 expert 返修）/ **补充资料后重试**。
- 接入 `plan-reviews/{id}/decision`；后端管道已通。

**A3 · 记忆冲突裁决卡**（`memory_conflict`）
- 并排展示冲突双方（旧记忆 vs 新信息 + 各自证据）。
- 用户选"保留旧 / 采用新 / 合并"，写入记忆治理结果（复用 `ProfileConflictList` 数据模型）。

### B. 智能体定向呼叫（Agent Dispatch）

目标：用户不仅能发起整条流水线，还能**点名某个智能体干活**。

| 输入方式 | 示例 | 效果 |
|---|---|---|
| 对话前缀 `@专家` | `@专家 讲一下痰湿证的辨证要点` | 定向走 expert 链路 |
| 输入框角色选择器点选 | 选中"审核裁判" | 提示"让审核裁判检查什么？" |
| 快捷指令 `/诊断` `/记忆` `/检索` | `/检索 心脾两虚 教材出处` | 单智能体任务卡 |

- 后端小增强：支持 `target_agent` 参数做定向路由（现有 `task_type`
  （`learner_data_query` / `learning_plan` 等）体系上扩展一个"点名模式"）。
- 价值：把六智能体从后台概念变成用户可感知、可指挥的能力清单。

### C. 协作产物「一键转行动」

目标：协作结论不是终点，而是**可落地的功能入口**。在协作回执/回复底部新增**产物操作条**：

| 协作产物 | 操作按钮 | 落地目标 |
|---|---|---|
| 学习计划 | `采纳到今日任务` / `调整后采纳` | 今日任务面板（`QualificationRoutePage`） |
| 薄弱点诊断 | `去练习` / `加入复习队列` | 专题训练（已打通 `kpId` 跳转） |
| 知识讲解/证据包 | `存入知识卡片` / `开始精读` | 知识卡片 / 教材精读 |
| 试卷 | `开始作答` / `保存草稿` | 试卷工作坊（已打通 `workshop.paper`） |
| 审核结论 | `查看依据`（证据溯源） | 证据列表弹层 |

### D. 协作透明度与反馈闭环

**D1 · 结论溯源展开**
- 回复中关键结论旁加"依据"角标，点击展开：哪个智能体产出、用了哪些证据、审核怎么判。
- 复用 AgentTimeline 已有的 `tools / modelCalls` 数据，换"面向结果"的呈现。

**D2 · 环节级反馈**
- 现有点赞/点踩扩展为可针对单个智能体环节（诊断不准 / 讲解太浅 / 审核太严）。
- 写入 `/feedback`（扩展 `subject` 字段），进入记忆智能体的画像更新与诊断智能体的策略调整。

---

## 五、实施计划

| 优先级 | 模块 | 工作量 | 后端改动 |
|---|---|---|---|
| P0 | A1 澄清追问卡 | 0.5 天 | 无 |
| P0 | C 产物操作条 | 0.5 天 | 无 |
| P0 | D1 结论溯源展开 | 0.5 天 | 无 |
| P0 | L3 协作凭证卡 | 0.5 天 | 无 |
| P1 | A2 人工复核卡 | 0.5 天 | 无（接 `plan-reviews` API） |
| P1 | B 智能体定向呼叫（`@专家` + 选择器） | 1.5 天 | 小增强：`target_agent` 定向路由 |
| P1 | L1 常驻协作带 | 0.5 天 | 无 |
| P2 | L2 协作泳道图 | 1 天 | 无 |
| P2 | A3 记忆冲突裁决卡 | 0.5 天 | 无 |
| P2 | D2 环节级反馈 | 1 天 | 小增强：`/feedback` 加 `subject` |
| P3 | L4 首页演示升级 | 0.5 天 | 无 |

合计约 **7.5 人日**；其中 **P0（2 天）** 即可让用户在对话中第一次真正与六智能体交互。

建议落地顺序：**先 P0 功能融合**（追问卡 + 产物操作条，改动小、价值立现）→
再上 L1/L3 作为"看得见"的外层 → P1 定向呼叫 → P2 泳道图与环节反馈 → P3 首页统一收口。

---

## 六、验收标准

- **P0 后**：对话中触发澄清时，用户能在卡片上作答并续跑，无需重发消息；
  协作回复底部出现产物操作条，点击可跳到对应功能页。
- **P1 后**：用户可用 `@专家` / 选择器点名智能体；人工复核场景可批准/驳回；
  对话输入框上方出现实时协作带。
- **P2 后**：侧栏泳道图能展示角色流转与审核返修回环；单个智能体环节可反馈；
  记忆冲突可在对话流中裁决。
- **P3 后**：首页演示用真实会话数据，点角色可看最近一次真实协作并跳转对话。

## 七、风险与边界

- **不改后端核心**：A/B/C/D 全部基于现有 `interrupt / resume / plan-reviews / feedback` 管道；
  唯一两处小增强（`target_agent` 定向路由、`/feedback` 的 `subject` 字段）为可选，不影响现有测试。
- **既有测试保护**：前端 491 个测试、后端 1058 个测试为回归基线；新增组件均带独立测试。
- **文案约束**：所有用户可见文案用自然语言，运行名/技术详情只出现在"展开技术细节"层级。
- **演示数据真实化**：首页演示不得写死演示文案，一律取真实会话产物，避免"看起来很美、实际做不到"。
