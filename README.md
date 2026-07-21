# 时珍智训（挑战杯项目）

本仓库用于前后端协作与统一部署。当前提交包含完整后端；前端负责人可在仓库根目录新增
`frontend/`，不要复制或改写后端状态机。

## 仓库结构

```text
tiaozhanbei/
├── backend/
│   ├── competition_app/                 # FastAPI + LangGraph 主后端
│   └── competition/
│       └── backend-handoff-20260720/     # 已装配到主进程的业务接口包
├── frontend/                             # 前端团队接入位置（待加入）
├── .gitignore
└── README.md
```

后端完整环境、数据库、接口、SSE、中断恢复和测试说明见
[backend/competition_app/README.md](backend/competition_app/README.md)。

## 后端快速启动

项目不要求 Docker，统一使用 Python 3.10 与已有 Conda `torch` 环境：

```bash
cd backend
conda activate torch
python -m pip install -r competition_app/requirements.txt
COMPETITION_APP_MODE=stub python -m competition_app.cli.app serve \
  --host 127.0.0.1 --port 8878
```

打开：

- 健康检查：`http://127.0.0.1:8878/health`
- OpenAPI：`http://127.0.0.1:8878/docs`
- 正式对话页：`http://127.0.0.1:8878/chat/`
- 实时执行链路页：`http://127.0.0.1:8878/demo/`

Stub 模式不需要外部模型、向量库或数据库，适合前端先完成接口联调。
需联调前端交接业务域时，再设置 `BACKEND_HANDOFF_ENABLED=true`；其余时间保持
默认的 `false`。

## 前端接入要点

1. 开发服务器将 `/api`、`/token`、`/health` 代理到 `http://127.0.0.1:8878`。
2. Cookie 会话请求设置 `credentials: 'include'`。
3. 以 `/api/v1/platform/openapi.json` 与 `/docs` 为接口真源。
4. 对话执行使用 `POST /api/v1/review-cards/stream`，按 SSE `event` 字段消费。
5. 收到 `run_interrupted` 后保存 `thread_id`，调用
   `POST /api/v1/review-cards/runs/{thread_id}/resume/stream` 恢复。
6. 普通自然语言输入只可提交 `plan_scope_hint`；只有用户明确选择长期、短期或当日层级时
   才提交强约束 `plan_scope`。
7. 用户和学习数据以服务端登录身份隔离，前端不得用请求体中的 `learner_id` 切换用户。

## Live 环境与大体积数据

Live 模式使用 `deepseek-v4-flash`、`Qwen/Qwen3-Embedding-4B` 和正式知识库。下列内容
不进入 Git，由项目共享盘提供并通过环境变量指向绝对路径：

- 题库与知识点原始交付包；
- FAISS `vdb_store`；
- 视频知识库与知识库管理组件 runtime；
- MySQL 数据、本地 SQLite、用户上传文件和运行快照。

需要的环境变量名称已列在 `backend/competition_app/.env.example`，其中没有有效密钥。

## 数据库

正式联调建议使用同一 MySQL 实例中的两个数据库：

- `competition_app`：认证、主规划、复习队列和 LangGraph 运行状态；
- `competition_frontend`：前端交接业务域。

```bash
cd backend
export MYSQL_PASSWORD='本机密码'
python -m competition_app.cli.app init-db
```

## 测试

```bash
cd backend
conda run -n torch python -m pytest -q competition_app/tests \
  --ignore=competition_app/tests/integration/test_learning_plan_live_flow.py
```

不要从 WSL 命令行运行 Live pytest；Live 验收在已启动前端运行面板点击 Execute。

## 协作建议

- 后端改动放在 `backend/**`，前端改动放在 `frontend/**`；
- 每个功能分支只处理一个交付目标，通过 Pull Request 合并到 `main`；
- 不提交 `.env`、密钥、数据库、缓存、快照、向量索引和用户数据；
- 接口有变化时同步更新 OpenAPI/Pydantic 契约和 README；
- 前端不要硬编码 Agent 节点数、执行顺序、系统 ID 或计划版本。
