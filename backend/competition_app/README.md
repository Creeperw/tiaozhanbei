# 时珍智训后端

本目录是时珍智训的主后端包 `competition_app`。后端采用 FastAPI、LangGraph、
MySQL/SQLite、DeepSeek 与 FAISS，负责用户认证、学习行为汇总、三层学习规划、知识讲解、
个性化学习资源、试卷生成、复习队列以及知识库管理接口。

长期规划、短期计划和当日任务是三个独立层级：下层只能基于已生效的上层生成，修改上层
会使相关下层失效。自然语言请求的层级由 Planner 模型判断；前后端规则仅提供可覆盖的
`plan_scope_hint`。

## 目录与运行位置

在整合仓库中，本包位于：

```text
backend/
├── competition_app/                    # 当前主后端
└── competition/
    └── backend-handoff-20260720/        # 已并入同一 FastAPI 进程的业务接口包
```

所有命令都从 `backend/` 目录执行。不要从 `competition_app/` 内部直接启动，否则 Python
无法按包名解析绝对导入。

## 1. 本地快速启动（Stub）

项目开发环境为 Python 3.10 和 Conda `torch`：

```bash
cd backend
conda activate torch
python -m pip install -r competition_app/requirements.txt
COMPETITION_APP_MODE=stub python -m competition_app.cli.app serve \
  --host 127.0.0.1 --port 8878
```

健康检查：

```bash
curl http://127.0.0.1:8878/health
```

Stub 模式不调用外部模型和向量服务，适合前端联调、接口契约检查和自动化测试。

> 当前配置读取系统环境变量；`.env.example` 只展示变量名，不包含任何有效密钥。
> 团队可在启动脚本中自行加载 `.env.local`，但不得提交该文件。

## 2. Live 模式

Live 模式默认使用：

- 对话模型：`deepseek-v4-flash`（阿里云兼容接口）
- Embedding：`Qwen/Qwen3-Embedding-4B`（SiliconFlow）
- 编排：LangGraph

至少设置：

```bash
export COMPETITION_APP_MODE=live
export COMPETITION_EXECUTION_ENGINE=langgraph
export DASHSCOPE_API_KEY='...'
export SILICONFLOW_API_KEY='...'
export EXA_API_KEY='...'                 # 仅网络资源检索需要
python -m competition_app.cli.app serve --host 127.0.0.1 --port 8878
```

Live 模式还需要团队共享盘中的正式知识资产。大体积题库、FAISS 索引、视频知识库和运行时
数据库不提交 Git，路径通过以下变量挂载：

```bash
export QUESTION_VECTOR_STORE_ROOT='/absolute/path/to/vdb_store'
export KNOWLEDGE_VECTOR_STORE_ROOT='/absolute/path/to/vdb_store'
export KNOWLEDGE_HANDOFF_ROOT='/absolute/path/to/知识星球视频知识库_前端交接包_2026-07-18'
export KNOWLEDGE_RUNTIME_ROOT='/absolute/path/to/知识库管理组件/runtime'
```

缺少正式资产时请使用 Stub 模式；Live 模式不会用伪数据静默降级。

## 3. MySQL 与数据隔离

后端支持无数据库 Stub 演示，但登录、多用户数据隔离、学习行为、计划、复习队列与断线恢复
应使用 MySQL：

```bash
export MYSQL_HOST=127.0.0.1
export MYSQL_PORT=3306
export MYSQL_USER=root
export MYSQL_PASSWORD='...'
export MYSQL_DATABASE=competition_app
export BACKEND_HANDOFF_MYSQL_DATABASE=competition_frontend
python -m competition_app.cli.app init-db
```

主框架与交接业务域使用同一 MySQL 实例中的两个数据库，避免同名表冲突。迁移位于
`competition_app/migrations/`，由 `init-db` 按顺序和校验和执行。

服务端始终以登录会话中的用户 ID 作为数据权限边界；请求体中的 `learner_id` 不能覆盖
登录身份。生产环境还需设置：

```bash
export BACKEND_HANDOFF_SECRET_KEY='a-long-random-secret'
export AUTH_COOKIE_SECURE=true            # HTTPS 环境
```

## 4. 前端整合约定

推荐生产环境让前端和 API 同源；开发环境由 Vite/Nginx 将以下路径代理到 `8878`：

```text
/api/*
/token
/health
/docs
```

Cookie 会话请求必须启用凭据：

```js
fetch('/api/v1/auth/me', {
  credentials: 'include',
});
```

主要入口：

| 场景 | 接口 |
|---|---|
| 健康检查 | `GET /health` |
| 注册/登录/退出 | `POST /api/v1/auth/register`、`/login`、`/logout` |
| 当前用户 | `GET /api/v1/auth/me` |
| 普通执行 | `POST /api/v1/review-cards` |
| 流式执行 | `POST /api/v1/review-cards/stream` |
| 查询运行状态 | `GET /api/v1/review-cards/runs/{thread_id}` |
| 中断后恢复 | `POST /api/v1/review-cards/runs/{thread_id}/resume/stream` |
| 复习队列 | `GET /api/v1/learners/{learner_id}/review-queue` |
| 平台装配状态 | `GET /api/v1/platform/status` |
| 完整交接接口契约 | `GET /api/v1/platform/openapi.json` |
| FastAPI 文档 | `GET /docs` |

流式接口返回 `text/event-stream`。前端应按 `event` 字段处理节点开始、模型增量、执行图、
中断、完成和失败事件，不要依赖日志文本。收到 `run_interrupted` 后保存 `thread_id`，使用
恢复接口继续；已完成节点由 LangGraph 检查点复用。

学习规划请求支持两个层级字段：

- `plan_scope`：用户在 UI 中明确选择的层级，属于强约束；
- `plan_scope_hint`：前端对自然语言的高置信提示，Planner 模型可覆盖。

可选层级为 `long_term`、`short_term`、`daily_task`、`unspecified`。普通自然语言输入不应
由前端强行填写 `plan_scope`。

## 5. 已包含的业务能力

- LangGraph 动态 Agent DAG、事件流、中断追问和刷新/断线恢复；
- 用户注册、登录、Cookie/JWT 兼容和跨用户数据隔离；
- 长期规划、短期计划、当日任务的独立生成、版本和失效传播；
- 学习行为、答题表现、掌握度、专注时长和学情诊断；
- 教材/题库/网络证据检索、知识讲解及配套练习题；
- 个性化学习卡、整卷蓝图、候选题补充、专家改题和审核；
- 完成题目后才进入的复习知识点队列、到期调度和资源推送；
- 前端负责人交接包中的训练、题库工作台、知识图谱、文件、语音等后端接口。

## 6. 测试与验收

```bash
cd backend
conda run -n torch python -m pytest -q competition_app/tests \
  --ignore=competition_app/tests/integration/test_learning_plan_live_flow.py
```

不要从 WSL 命令行运行 Live pytest。Live 验收应在已启动前端的运行面板点击 Execute，
避免并发模型与向量进程压垮本地环境。

提交前至少检查：

```bash
python -m pytest -q competition_app/tests/services/test_plan_scope.py
curl http://127.0.0.1:8878/health
```

## 7. 禁止提交

- `.env`、`.env.local`、API Key、数据库密码和 Cookie Secret；
- `__pycache__/`、`.pytest_cache/`、`snapshots/`、日志和本地数据库；
- FAISS 索引、视频、题库原始包和其他大体积知识资产；
- 用户上传文件、答题记录导出或含个人信息的运行快照。

遇到接口不一致时，以 `/api/v1/platform/openapi.json` 和 Pydantic 请求/响应模型为准，
不要让前端复制后端状态机或重新生成系统 ID。
