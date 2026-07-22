# 时珍智训（挑战杯项目）

本仓库用于前后端协作与统一部署。当前提交包含完整后端与可直接体验的 React 前端。

## 系统能力

- 长期规划、短期规划和当日任务分层生成，并投影为“阶段 → 教材 → 知识点”学习路径；
- 正式对话界面支持 LangGraph 流式执行、中断恢复、引用展示和六智能体执行轨迹；
- 学习工坊包含题目训练、知识卡片和计时试卷，知识卡可聚合讲解、教材切片、视频与题目；
- 学习行为、掌握度、复习队列和资源推送按登录用户隔离并持久化；
- 知识库优先使用本地可信资料，资源不足时保留网络检索与专家补题能力；
- Cookie 会话认证、注册登录、管理员权限和前后端同源部署已经接入。

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
Set-Location "D:\A学业\赛事\小挑\揭榜挂帅\code design\code design v2\tiaozhanbei\backend"

$env:COMPETITION_APP_MODE = "stub"
D:\miniforge3\python.exe -m competition_app.cli.app serve
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

1. 开发服务器将 `/api` 代理到 `http://127.0.0.1:7860`。
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
迁移期 `/api/*` 去掉 `/api` 前缀后交给兼容业务路由：

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

## Live 环境与大体积数据

Live 模式使用 `deepseek-v4-flash`、`Qwen/Qwen3-Embedding-4B` 和正式知识库。下列内容
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
```

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

当前基线：后端非 Live 测试 611 项、前端单元与组件测试 207 项。若测试数量随功能增加，以 CI/本地最新输出为准。

## 协作建议

- 后端改动放在 `backend/**`，前端改动放在 `frontend/**`；
- 每个功能分支只处理一个交付目标，通过 Pull Request 合并到 `main`；
- 不提交 `.env`、密钥、数据库、缓存、快照、向量索引和用户数据；
- 接口有变化时同步更新 OpenAPI/Pydantic 契约和 README；
- 前端不要硬编码 Agent 节点数、执行顺序、系统 ID 或计划版本。
