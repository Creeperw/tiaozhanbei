# 时珍智训（挑战杯项目）

本仓库用于前后端协作与统一部署。当前提交包含完整后端与可直接体验的 React 前端。

## 系统能力

- 长期规划、短期规划和当日任务按“画像就绪 → 长期 → 短期 → 当日”分层生成，并提供统一前置状态接口；规划投影为“阶段 → 教材 → 知识点”学习路径，同时提供可独立浏览的非个性化经典路线；
- 正式对话界面支持 LangGraph 流式执行、中断恢复、引用展示和六智能体执行轨迹；
- 学习工坊包含客观题、案例简答、AI 病患模拟、全量错题与变式训练、知识卡片和计时试卷；知识卡可聚合讲解、教材切片、视频与题目；
- 智能助教按会话恢复连续问答，超过上下文阈值后由 Memory Agent 压缩；客观错题先补充作答情境再开放变式，主观题由 Expert Agent 批改；
- 学习行为、掌握度、复习队列和资源推送按登录用户隔离并持久化；学情诊断依赖带样本数与新鲜度的监控快照，复习队列只接纳已完成且批改通过的知识点题目作答；
- 学情报告提供能力结构、行为趋势、薄弱知识点、错因分布和资源匹配依据；主动干预、站内通知及规划自动复盘形成可确认、可追踪的闭环；
- 数据库模式下 LangGraph 中断检查点和恢复上下文均持久化，页面刷新、断线或服务重启后可从原节点继续；
- 知识库优先使用本地可信资料，资源不足时保留网络检索与专家补题能力；
- Cookie 会话认证、注册登录、管理员权限和前后端同源部署已经接入；未登录用户先进入公开展示页，再通过登录或注册弹层进入学习工作台。

系统对外统一呈现六个智能体角色：任务规划、记忆管理、学情诊断、知识库管理、专家、审核裁判。复习调度、计划持久化等确定性能力作为后端服务运行，不额外伪装成智能体。

## 仓库结构

```text
tiaozhanbei/
├── backend/
│   ├── competition_app/                 # FastAPI + LangGraph 主后端
│   └── competition/
│       └── backend-handoff-20260720/     # 已装配到主进程的业务接口包
├── frontend/llm/                         # React + Vite 正式前端
├── .gitignore
└── README.md
```

后端完整环境、数据库、接口、SSE、中断恢复和测试说明见
[backend/competition_app/README.md](backend/competition_app/README.md)。

团队部署与运维请阅读 [部署与升级指南](docs/deployment.md) 和
[数据库运维指南](docs/database-operations.md)。两份文档覆盖无 Docker 安装、双库初始化、迁移、备份恢复、生产启动、升级回滚和常见故障。

前端开发和联调请优先阅读 [前端接口参考](docs/frontend-api-reference.md)。文档明确区分正式 `/api/v1` 与迁移期 `/api` 接口，并包含认证、SSE、中断恢复、学习路径、学习行为、正式题库取题与批改、全量错题、案例训练、知识库顺序节点、复习队列和错误处理契约；关键接口均给出请求、成功响应、空状态、幂等/一次性凭证与用户隔离说明。

学情指标的数据表、采集动作、时间窗口、公式、推荐权重、版本和研究依据见 [学情监测与资源匹配口径](docs/learning-monitoring-methodology.md)。

参与开发前请阅读 [协作与贡献说明](CONTRIBUTING.md)。协作分支以保留原作者提交历史的方式进入 `main`，确保 GitHub Contributors 能正确识别团队成员贡献。

## 快速启动

项目不要求 Docker，统一使用 Python 3.10 与已有 Conda `torch` 环境：

```bash
git clone https://github.com/Creeperw/tiaozhanbei.git
cd tiaozhanbei/backend
conda activate torch
python -m pip install -r competition_app/requirements.txt
cp competition_app/.env.example competition_app/.env.local
```

构建正式前端，随后由 FastAPI 同源托管：

```bash
cd ../frontend/llm
npm install
npm run build
cd ../../backend
COMPETITION_APP_MODE=stub python -m competition_app.cli.app serve
```

打开：

- 正式应用：`http://127.0.0.1:7860/`
- 健康检查：`http://127.0.0.1:7860/health`
- OpenAPI：`http://127.0.0.1:7860/docs`

`/chat/` 与 `/demo/` 只作为迁移期回归入口保留，不再是产品入口。

Stub 模式不需要外部模型、向量库或数据库，适合前端先完成接口联调。
需联调前端交接业务域时，再设置 `BACKEND_HANDOFF_ENABLED=true`；其余时间保持
默认的 `false`。

完整本地模式示例：

```bash
cd backend
export COMPETITION_APP_MODE=live
export BACKEND_HANDOFF_ENABLED=true
python -m competition_app.cli.app serve
```

模型密钥、MySQL 密码和本地知识资产路径只写入 `competition_app/.env.local` 或操作系统环境变量，不提交到 Git。

## 前端接入要点

1. 开发服务器将 `/api` 和登录页健康检查 `/health` 代理到 `http://127.0.0.1:7860`。
2. Cookie 会话请求设置 `credentials: 'include'`。
3. 以 `/api/v1/platform/openapi.json` 与 `/docs` 为接口真源。
4. 对话执行使用 `POST /api/v1/review-cards/stream`，按 SSE `event` 字段消费。
5. 收到 `run_interrupted` 后保存 `thread_id`，调用
   `POST /api/v1/review-cards/runs/{thread_id}/resume/stream` 恢复。
6. 普通自然语言输入只可提交 `plan_scope_hint`；只有用户明确选择长期、短期或当日层级时
   才提交强约束 `plan_scope`。
7. 用户和学习数据以服务端登录身份隔离，前端不得用请求体中的 `learner_id` 切换用户。

正式前端已使用主后端 HttpOnly Cookie，不在 localStorage 保存认证令牌。体验完整业务域时
设置 `BACKEND_HANDOFF_ENABLED=true`。Vite 保留两类代理：`/api/v1/*` 原样转发给主 API，
迁移期 `/api/*` 去掉 `/api` 前缀后交给兼容业务路由；`/health` 原样转发给主后端，供登录页
判断认证服务是否可用：

```powershell
$env:BACKEND_HANDOFF_ENABLED = "true"
cd backend
python -m competition_app.cli.app serve

# 另开一个 PowerShell
cd frontend/llm
npm install
npm run dev
```

打开 `http://127.0.0.1:5173`。登录、会话、首页和 LangGraph 对话已经使用主 `/api/v1`；
只验证这些主功能时可保持 `BACKEND_HANDOFF_ENABLED=false`。

未登录时前端默认展示“承时珍医脉，启智慧学习”公开页，页面本身不直接暴露账号输入框。
点击“登录”或“登录已有账号”打开登录弹层，点击“开始学习”或“开启智训之旅”打开注册弹层；
弹层可返回展示页。登录和注册仍分别调用 `/api/v1/auth/login`、`/api/v1/auth/register`，
不会在浏览器本地保存令牌。

## Live 环境与大体积数据

Live 模式使用 `qwen3.7-max-2026-05-20`、`Qwen/Qwen3-Embedding-4B` 和正式知识库。下列内容
不进入 Git，由项目共享盘提供并通过环境变量指向绝对路径：

- 题库与知识点原始交付包；
- FAISS `vdb_store`；
- 视频知识库与知识库管理组件 runtime；
- MySQL 数据、本地 SQLite、用户上传文件和运行快照。

需要的环境变量名称已列在 `backend/competition_app/.env.example`，其中没有有效密钥。

本机联调可以直接把路径指向已有资产，不需要复制数 GB 的索引。例如在
`backend/competition_app/.env.local` 中设置绝对路径：

```bash
QUESTION_VECTOR_STORE_ROOT=/absolute/path/to/competition/vdb_store
KNOWLEDGE_VECTOR_STORE_ROOT=/absolute/path/to/competition/vdb_store
KNOWLEDGE_HANDOFF_ROOT=/absolute/path/to/知识星球视频知识库_前端交接包_2026-07-18
# 可选；留空时使用仓库内置的 2026-07-22 章节映射
KNOWLEDGE_ATLAS_CHAPTER_ROOT=/absolute/path/to/chapter-mapping
```

仓库内 `backend/competition/knowledge_atlas_chapters/2026-07-22` 保存章节顺序映射，
不复制原始教材和切片正文。知识星球据此按“教材 → 章节 → 小节 → 知识点”四级展示；
同章同名的重复切片区间会合并为一个小节入口，底层 `chunk_uid` 关联保持不变。

也可以把这两个资产软链接到 `backend/competition/` 下的同名目录；这些入口已被
`.gitignore` 排除。启动时可通过 `/health` 和 `/api/v1/platform/status` 检查主框架与交接
后端，通过知识库状态接口检查索引是否可读。

## 数据库

正式联调建议使用同一 MySQL 实例中的两个数据库：

- `competition_app`：认证、主规划、复习队列和 LangGraph 运行状态；
- `competition_frontend`：前端交接业务域。

```bash
cd backend
export MYSQL_PASSWORD='本机密码'
python -m competition_app.cli.app init-db
```

首次启动后通过正式应用注册普通用户。管理员账号只有在设置 `ADMIN_DEFAULT_PASSWORD` 时才应启用；生产环境同时设置随机 `SECRET_KEY` 并在 HTTPS 部署中启用安全 Cookie。

主库使用带校验和的编号迁移，已执行的迁移文件不得修改；兼容业务库在模块装载时执行结构初始化和增量修复。完整授权、状态检查、备份恢复与升级顺序见
[数据库运维指南](docs/database-operations.md)。

## 测试

```bash
cd backend
conda run -n torch python -m pytest -q competition_app/tests \
  --ignore=competition_app/tests/integration/test_learning_plan_live_flow.py

cd ../frontend/llm
npm run test:unit
npm run lint
npm run build
```

不要从 WSL 命令行运行 Live pytest；Live 验收在已启动前端运行面板点击 Execute。

当前基线：主后端非 Live 测试 620 项通过、3 项按环境跳过；前端完整测试基线随功能增加，
以 CI/本地最新输出为准。登录页定向测试、lint 与生产构建必须通过。

## 协作建议

- 后端改动放在 `backend/**`，前端改动放在 `frontend/**`；
- 每个功能分支只处理一个交付目标，通过 Pull Request 合并到 `main`，并保留原提交作者历史；
- 不提交 `.env`、密钥、数据库、缓存、快照、向量索引和用户数据；
- 接口有变化时同步更新 OpenAPI/Pydantic 契约和 README；
- 前端不要硬编码 Agent 节点数、执行顺序、系统 ID 或计划版本。

贡献者名单、历史分支和验收规则见 [CONTRIBUTING.md](CONTRIBUTING.md)。
