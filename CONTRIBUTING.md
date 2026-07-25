# 协作与贡献说明

感谢所有参与时珍智训前端、后端、知识资源和部署工作的贡献者。本仓库以 `main` 作为可部署基线，功能开发通过独立分支完成，并保留原作者提交历史后合并。

## 当前协作贡献

| 贡献者 | 已合并工作 |
|---|---|
| Creeperw | 多智能体后端、前后端整合、章节层级、数据持久化、接口与部署文档 |
| sunjingyan（GitHub：[@Monologue-8106](https://github.com/Monologue-8106)） | 登录体验、顺序学习路径、知识空间和学习工坊前端交互 |
| 11075 | 团队交接与 PowerShell 启动端口校准 |
| noalternative9 | 模拟病患组件后端接入、SimulatedPatientChat 前端、训练工坊 UI 优化、收藏夹/笔记本/错题库功能、资格试卷数据导入、本地部署配置与调试 |
| fxz729 | 时珍智训训练工坊主线维护与功能整合：平台导航和训练工作台、模拟病患与资格试卷流程、收藏/笔记/错题等学习闭环、智能组卷正式题库与个性化链路、前后端体验优化、Live 部署和接口文档 |

`fxz729` 当前协作分支为 `fxz729/训练工坊`，也是本轮训练工坊相关功能的主要维护与整合账号。除本次智能组卷外，fxz729 还负责或参与了训练工坊页面重构、模拟病患入口与交互、资格试卷工作流和答题体验、收藏夹/笔记本/错题闭环、前后端接口适配、部署调试及相关文档维护。智能组卷部分包括：正式题库流式检索、个性化蓝图、正式题优先组装、候选题冷却去重、练习/测试模式补题边界、题量解析修复、前端题源展示以及 Live 环境启动校验。大体积题库、向量索引、运行时数据库和密钥不属于贡献内容，不应随分支提交。

GitHub Contributors 页面依据 `main` 可达提交的作者邮箱统计。提交者应使用已绑定到个人 GitHub 账号的邮箱；修改历史提交作者会破坏审计链路，不应为了统计而重写已经共享的提交。

## 已保留的协作分支历史

- `feature/light-login-page`
- `feat/sequential-learning-path`
- `fxz/merge-sequential-learning-path-20260722`
- `feat/chapter-hierarchy-api-settings-20260722`
- `codex/team-handoff-2026-07-20`
- `fxz729/训练工坊`

---

## 本次修改涉及的后端接口

### 1. 模拟病患 API — `POST /api/v1/simulated-patient`

已有统一入口，本次新增响应字段：

| 修改 | 字段 | 说明 |
|------|------|------|
| `engine.py:_handle_start` | `data.case_id` | 新增，返回案例 ID，供前端匹配收藏/误诊记录 |
| `engine.py:_handle_start` | `data.case_name` | 新增，返回案例疾病名称，供历史记录显示 |
| `session_manager.py` | 文件持久化 | `SessionManager` 新增 `data_dir` 参数，session 数据写入 `{data_dir}/sessions/{id}.json`，服务重启后不丢失 |

**Session 文件持久化**：`SessionManager.get()` 先查内存再查文件；`set()` 同时写入内存和文件。解决了原纯内存存储导致服务重启后 `dialogue` 返回 `Session not initialized` 的问题。

### 2. 资格试卷 API

本次无后端修改。调用方式见 `QualificationPaperPanel.jsx` 中的 `request()` 封装：

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/v1/qualification-papers/catalog` | 获取考试类别 + 套题目录 |
| POST | `/api/v1/qualification-papers/{template_id}/attempts` | 创建作答记录 |
| GET | `/api/v1/qualification-paper-attempts/{attempt_id}` | 获取作答详情（含保存的答案和进度） |
| PUT | `/api/v1/qualification-paper-attempts/{attempt_id}/progress` | 保存作答进度（含 `paused` 标志） |
| POST | `/api/v1/qualification-paper-attempts/{attempt_id}/submit` | 提交试卷 |
| GET | `/api/v1/qualification-paper-attempts/{attempt_id}/items/{question_id}/explanation` | 获取单题解析 |

**断点续答**：sessionStorage 改为按 `qp-attempt-{template_id}` 分试卷存储，支持多试卷独立保存/恢复。测试模式恢复时 `remaining_seconds` 由后端实时计算（`started_at + duration - now`）。

### 3. 用户认证 API — `GET /api/v1/auth/me`

用于 SimulatedPatientChat 欢迎页获取当前登录用户名，显示 "欢迎XX医生"。

### 4. 智能组卷与 Live 正式题库

本次 `fxz729/训练工坊` 分支新增/调整的智能组卷链路如下：

```text
POST /api/v1/review-cards
  → PlannerAgent
  → PaperBlueprintAgent
  → KnowledgeBaseAgent
  → PaperAssemblyAgent
  → AuditAgent
  → /api/v1/workshop/papers
```

关键接口：

| 方法 | 路径 | 用途 |
|---|---|---|
| `GET` | `/health` | 确认服务为 `mode=live`、`knowledge_source=formal` |
| `POST` | `/api/v1/review-cards` | 普通组卷请求 |
| `POST` | `/api/v1/review-cards/stream` | 流式组卷与阶段事件 |
| `GET` | `/api/v1/review-cards/runs/{thread_id}` | 查询运行状态 |
| `GET` | `/api/v1/workshop/papers` | 当前用户试卷列表 |
| `GET` | `/api/v1/workshop/papers/{paper_id}` | 获取试卷、题源标签和答题内容 |
| `PUT` | `/api/v1/workshop/papers/{paper_id}/answers` | 保存答案 |
| `POST` | `/api/v1/workshop/papers/{paper_id}/submit` | 交卷并评分 |

正式题库候选优先于模型生成题；练习模式在正式候选不足时允许审核生成补题，测试模式不会使用生成题。近期已发布题按题目 ID 和规范化题干进入冷却，回填路径同样遵守冷却规则。题目视图会显示“正式题库”“个人已确认题”或“审核生成补题”等来源标签。

Live部署、模型密钥、正式知识资产、SQLite/MySQL持久化、`ijson`/`requests`依赖和队友拉取后的启动步骤见 [`backend/competition_app/docs/smart-paper-live-deployment.md`](backend/competition_app/docs/smart-paper-live-deployment.md)。

---

## 前端接口调用清单

### 模拟病患对话 (`SimulatedPatientChat.jsx`)

| 功能 | action | 额外参数 | 响应字段 |
|------|--------|---------|---------|
| 开始问诊 | `start` | — | `patient_reply`, `patient_info`, `case_name`, `case_id`, `session_id` |
| 对话 | `dialogue` | `user_input` | `patient_reply`, `turn_count`, `help_available` |
| 援助 | `help` | `help_type` | `question` / `interpretation` |
| 提交诊断 | `submit` | `diagnosis` | `grading_report`, `history_id` |
| 清空对话 | `reset` | — | `patient_reply` |
| 统计 | `stats` | — | `total`, `rate` |
| 错题列表 | `mistakes` | — | `list[{mistake_id, case_name, score}]` |
| 收藏列表 | `collections` | — | `list[{case_id, case_name}]` |
| 对话历史 | `dialog_history` | `limit` | `list[{session_id, role, content}]` |
| 历史详情 | `history_detail` | `history_id` | `full_dialogue`, `grading_report` |

### 前端 localStorage 数据结构

| Key | 格式 | 说明 |
|-----|------|------|
| `sp-session-id` | `string` | 当前 SP 会话 ID（sessionStorage） |
| `sp-completed-sessions` | `[{session_id, case_name, case_id, gender, age_range, body_type, score, diagnosis_correct, status, messages, grading_report, ...}]` | 完整接诊记录 |
| `sp-collections` | `{bookName: [{session_id, case_name, score, ...}]}` | 收藏簿（按簿分组） |
| `qp-favorite-questions` | `[{question_id, question_content, options, my_answer, standard_answer, explanation, book, source, saved_at}]` | 收藏的题目 |
| `qp-collection-books` | `["簿名1", "簿名2"]` | 收藏簿列表 |
| `study-notes` | `[{id, title, content, type, source, question_content, options, standard_answer, explanation, created_at}]` | 学习笔记 |
| `qp-notes-{attemptId}` | `{posKey: {title, content, type, source}}` | 按试卷 ID 存储的做题笔记 |
| `qp-catalog-cache` | `{exams, papers}` | 资格试卷目录缓存 |
| `qp-attempt-{template_id}` | `string` | 按试卷模板存储的 attempt ID（sessionStorage） |
| `qp-completed-papers` | `{examId: count}` | 各考试类别已完成套题数 |

---

## 修改文件清单

### 后端

| 文件 | 改动 |
|------|------|
| `backend/competition_app/simulated_patient/session_manager.py` | **重写** — 新增文件持久化（内存 + JSON 文件双写），`get()` 先查内存再查文件 |
| `backend/competition_app/simulated_patient/engine.py` | `_handle_start` 响应新增 `case_name`、`case_id` 字段；`SessionManager` 传入 `data_dir` |
| `backend/competition_app/simulated_patient/case_adapter.py` | 正则放宽：`主诉[：:\s]+` 支持空格分隔；新增病史摘要/现病史备选提取；同步修复体格检查/辅助检查正则 |
| `backend/competition_app/simulated_patient/agent_clients.py` | 新增 `ProductionMemoryAgent` / `ProductionDiagnosisAgent` 生产环境桩 |
| `backend/competition_app/simulated_patient/llm_adapter.py` | **新增** — 将项目 DashScope 模型适配为组件 LLMProvider |
| `backend/competition_app/api/simulated_patient_routes.py` | **新增** — FastAPI 路由 |
| `backend/competition_app/api/app.py` | 注册模拟病患路由（+6 行） |
| `backend/competition_app/data/clinical_cases.json` | **新增** — 20 个 CMB 临床案例 |

### 前端

| 文件 | 改动 |
|------|------|
| `frontend/llm/src/components/SimulatedPatientChat.jsx` | **新增** — ~900 行，含侧栏/欢迎页/问诊对话/评分报告/历史记录 |
| `frontend/llm/src/components/SimulatedPatientChat.test.jsx` | **新增** — 6 个 Vitest 测试 |
| `frontend/llm/src/components/PracticePage.jsx` | SP 全屏渲染、`isFullPanel` 布局、`QuestionFavoritesPanel`（收藏簿）、`StudyNotesPanel`（笔记本+筛选+编辑）、`TrainingBannerIllustration`（李时珍 QQ 人+气泡） |
| `frontend/llm/src/components/QuestionTrainingPanel.jsx` | 新增 header + 返回按钮 |
| `frontend/llm/src/components/SmartPaperPanel.jsx` | 新增 header + 返回按钮；折叠面板渐变+图标；试卷卡片点击打开 |
| `frontend/llm/src/components/MistakeVariationPanel.jsx` | 新增 header + 返回按钮 |
| `frontend/llm/src/components/QualificationAttemptWorkspace.jsx` | 笔记本面板（75/25 分栏）；「加入收藏」按钮（选择/新建收藏簿）；保存按钮 |
| `frontend/llm/src/components/QualificationPaperPanel.jsx` | 考试类别卡片渐变+图标+统计；年份/类型筛选水平布局；`catalog` 缓存；per-paper sessionStorage |
| `frontend/llm/src/components/PaperGenerationPanel.jsx` | 评分解析卡下方「加入收藏」「记笔记」按钮 + 记笔记弹窗；返回试卷列表回调；`onBack` prop |
| `frontend/llm/src/index.css` | ~600 行 `sp-*` 样式；训练工坊首页渐变卡片+QQ 人；手绘草药 SVG 背景；侧栏渐变按钮 |

---

## 依赖修复

| 问题 | 修复 |
|------|------|
| `fastapi-mail==1.5.2` 缺少 `SecretStr` 导入 | 升级到 `1.6.5` |
| `exa_py` 未安装 | `pip install exa_py` |

---

## 验收命令

```bash
cd backend
python -m pytest -q competition_app/tests --ignore=competition_app/tests/integration/test_learning_plan_live_flow.py

cd ../frontend/llm
npm run test:unit
npm run lint
npm run build
```
