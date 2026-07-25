# 协作与贡献说明

感谢所有参与时珍智训前端、后端、知识资源和部署工作的贡献者。本仓库以 `main` 作为可部署基线，功能开发通过独立分支完成，并保留原作者提交历史后合并。

## 当前协作贡献

| 贡献者 | 已合并工作 |
|---|---|
| Creeperw | 多智能体后端、前后端整合、章节层级、数据持久化、接口与部署文档 |
| sunjingyan（GitHub：[@Monologue-8106](https://github.com/Monologue-8106)） | 登录体验、顺序学习路径、知识空间和学习工坊前端交互 |
| 11075 | 团队交接与 PowerShell 启动端口校准 |
| noalternative9 | 模拟病患组件后端接入、SimulatedPatientChat 前端组件、训练工坊 UI 优化、资格试卷数据导入、本地部署配置与调试 |

GitHub Contributors 页面依据 `main` 可达提交的作者邮箱统计。提交者应使用已绑定到个人 GitHub 账号的邮箱；修改历史提交作者会破坏审计链路，不应为了统计而重写已经共享的提交。

当前 GitHub 已将 `sunjingyan <2136945143@qq.com>` 正确映射为 `@Monologue-8106`。`11075 <11075@local>` 不是 GitHub 可识别邮箱，因此提交历史已经保留，但在该邮箱绑定到账号前不会出现在 GitHub Contributors 页面。

## 已保留的协作分支历史

2026-07-23 的整合基线已将下列历史作为合并父提交接入 `main`：

- `feature/light-login-page`
- `feat/sequential-learning-path`
- `fxz/merge-sequential-learning-path-20260722`
- `feat/chapter-hierarchy-api-settings-20260722`
- `codex/team-handoff-2026-07-20`

这些功能在合并前已经进入当前工作区并完成在线验收，因此合并提交以当前可运行文件树为准，同时保留原分支提交和作者信息。后续不要再次复制或压缩这些历史。

## 模拟病患组件集成（2026-07-24 ~ 2026-07-25）

基于 `模拟病患5/` 交付包，完成以下后端接入与前端改造，工作在 `fxz729/训练工坊` 分支。

### 后端修改

| 文件 | 改动 |
|------|------|
| `backend/competition_app/simulated_patient/` | **新增** — 组件核心模块，从交付包复制 |
| `backend/competition_app/simulated_patient/llm_adapter.py` | **新增** — 将项目 DashScope `qwen3.7-max-2026-05-20` 适配为组件 LLMProvider 接口 |
| `backend/competition_app/simulated_patient/agent_clients.py` | **修改** — 新增 `ProductionMemoryAgent` / `ProductionDiagnosisAgent` 生产环境桩，解决原组件在生产环境抛出 `NotImplementedError` 的问题 |
| `backend/competition_app/simulated_patient/case_adapter.py` | **修改** — 放宽正则匹配支持 `主诉 `（空格分隔），新增现病史/病史摘要备选提取，修复 20 个案例症状全部提取为空的 bug |
| `backend/competition_app/simulated_patient/engine.py` | **修改** — `_handle_start` 响应新增 `case_name` 字段，供前端历史记录使用 |
| `backend/competition_app/api/simulated_patient_routes.py` | **新增** — FastAPI 路由：`POST /api/v1/simulated-patient` 统一入口 + 2 个 GET 快捷接口 |
| `backend/competition_app/api/app.py` | **修改** — 注册模拟病患路由，初始化引擎（6 行新增） |
| `backend/competition_app/data/clinical_cases.json` | **新增** — 20 个 CMB 临床案例数据 |

### 前端修改

| 文件 | 改动 |
|------|------|
| `frontend/llm/src/components/SimulatedPatientChat.jsx` | **新增** — 模拟病患对话主组件（~900 行），含侧栏、欢迎页、问诊对话、评分报告、历史记录完整功能 |
| `frontend/llm/src/components/SimulatedPatientChat.test.jsx` | **新增** — 6 个 Vitest 单元测试 |
| `frontend/llm/src/components/PracticePage.jsx` | **修改** — `CaseTrainingPanel` 替换为 `SimulatedPatientChat`，SP 模式下隐藏 toolbar/header/result-panel/mobile-tabs，改为全屏渲染 |
| `frontend/llm/src/components/QuestionTrainingPanel.jsx` | **修改** — 同上前端组件替换（2 行） |
| `frontend/llm/src/components/PracticePage.test.jsx` | **修改** — Mock 名称从 `CaseTrainingPanel` 更新为 `SimulatedPatientChat` |
| `frontend/llm/src/index.css` | **修改** — 新增 ~600 行 `sp-*` CSS 命名空间样式（侧栏、欢迎页、问诊对话、评分报告、手绘草药背景等） |
| `frontend/llm/API_CHANGES.md` | **新增** — API 调用清单与客户端 workaround 说明 |

### 前端功能概览

- **侧栏**（320px，可伸缩，默认展开）：返回训练工坊、开始诊断、我的收藏、我的误诊、历史记录按钮；下方展示接诊记录列表（待作答优先，已作答按时间倒序）
- **欢迎页**：听诊器图标 + "欢迎XX医生" + 今日统计卡片 + "开始今天的问诊吧"CTA
- **模式选择**：随心练 / 题型专练（带科室输入框），绿色渐变边框 + 阴影
- **问诊对话**：患者信息卡 + 对话气泡（患者红底/医生绿底 + emoji头像）+ 操作按钮（清空对话/申请援助/提交诊断）
- **申请援助**：10 轮后解锁，弹出选择卡片（关键问题/症状解读）
- **评分报告**：六维得分进度条 + 正确答案 + 知识点 + 疾病辨析 + 综合建议 + 加入收藏/休息/下一位操作
- **历史查看**：点击任何侧栏案例跳转对应对话，底部绿色评分报告折叠按钮，已提交案例隐藏输入框
- **存储机制**：全部对话实时保存到 `localStorage`，未提交退出后侧栏显示「待作答」标签，点击恢复全部对话
- **视觉设计**：绿色系（#059669），手绘草药 SVG 暗纹背景，输入框复用智能助教 `compact-assistant__composer` 样式

### 依赖修复

- `fastapi-mail` 从 1.5.2 升级到 1.6.5，修复 `SecretStr` 未导入导致的 `BACKEND_HANDOFF_ENABLED=true` 启动崩溃
- `exa_py` 补充安装（原 `requirements.txt` 遗漏）

## 资格试卷数据导入（2026-07-25 Pull）

从 `fxz729/训练工坊` 远程拉取的更新：

- `backend/competition_app/data/qualification_papers/` — 80+ 套资格试卷 JSON + 目录 + 审计报告
- `backend/competition_app/services/qualification_papers.py` — 试卷查询服务
- 前端 `QualificationPaperPanel` / `QualificationAttemptWorkspace` / `SmartPaperPanel` 组件

## 开发流程

1. 从最新 `main` 创建单一目标的功能分支。
2. 后端接口变化同步修改 OpenAPI/Pydantic 契约和 `docs/frontend-api-reference.md`。
3. 数据库变化新增编号迁移，不修改已经执行的迁移文件。
4. 不提交 `.env`、密钥、数据库、向量索引、缓存、用户数据或运行快照。
5. 合并前完成与改动对应的单元测试、前端构建和在线流程验收。
6. 使用普通 merge 或 Pull Request 保留作者历史，避免 squash 掉需要计入贡献列表的多人提交。

## 验收命令

```bash
cd backend
conda run -n torch python -m pytest -q competition_app/tests \
  --ignore=competition_app/tests/integration/test_learning_plan_live_flow.py

cd ../frontend/llm
npm run test:unit
npm run lint
npm run build
```

不要从 WSL 命令行运行 Live pytest。Live 流程应在已经启动的前端运行面板点击 Execute，并以浏览器真实接口结果为准。

部署、数据库和接口细节分别见：

- [部署与升级指南](docs/deployment.md)
- [数据库运维指南](docs/database-operations.md)
- [前端接口参考](docs/frontend-api-reference.md)
- [学情监测与资源匹配口径](docs/learning-monitoring-methodology.md)
