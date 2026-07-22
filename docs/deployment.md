# 部署与升级指南

本文用于无 Docker 环境下部署时珍智训。项目由一个 FastAPI 进程统一提供主后端、兼容业务接口和构建后的 React 页面。

## 1. 环境要求

- Python 3.10，推荐使用团队现有 Conda `torch` 环境；
- Node.js 18 或更高版本、npm；
- MySQL 8.x（需要登录、多用户隔离和持久化时）；
- Live 模式所需的模型密钥、题库、FAISS 索引和知识库交付资产。

所有后端命令从仓库的 `backend/` 目录执行。配置默认读取
`backend/competition_app/.env`、`.env.local`，操作系统环境变量优先级最高；也可通过
`COMPETITION_ENV_FILE=/absolute/path/app.env` 指定配置文件。

## 2. 首次安装

```bash
git clone https://github.com/Creeperw/tiaozhanbei.git
cd tiaozhanbei/backend
conda activate torch
python -m pip install -r competition_app/requirements.txt
cp competition_app/.env.example competition_app/.env.local

cd ../frontend/llm
npm install
npm run build
```

不要把 `.env.local`、数据库备份、用户上传文件和知识资产提交到 Git。

## 3. 配置模式

### 3.1 Stub 联调

Stub 不调用外部模型，适合检查页面、接口和认证流程。若不需要兼容业务页面，可关闭交接模块：

```dotenv
COMPETITION_APP_MODE=stub
BACKEND_HANDOFF_ENABLED=false
AUTH_COOKIE_SECURE=false
```

### 3.2 完整本地部署

复制 `.env.example` 后至少填写：

```dotenv
COMPETITION_APP_MODE=live
COMPETITION_EXECUTION_ENGINE=langgraph
DASHSCOPE_API_KEY=填写真实密钥
SILICONFLOW_API_KEY=填写真实密钥
EXA_API_KEY=填写真实密钥

MYSQL_HOST=127.0.0.1
MYSQL_PORT=3306
MYSQL_USER=competition_app
MYSQL_PASSWORD=填写数据库密码
MYSQL_DATABASE=competition_app
BACKEND_HANDOFF_MYSQL_DATABASE=competition_frontend
BACKEND_HANDOFF_ENABLED=true

SECRET_KEY=填写足够长的随机值
BACKEND_HANDOFF_SECRET_KEY=填写另一随机值
AUTH_COOKIE_SECURE=false
```

Live 模式还需配置知识资产路径。相对路径以 `backend/` 为基准；跨机器部署推荐写绝对路径：

```dotenv
QUESTION_VECTOR_STORE_ROOT=/srv/tiaozhanbei-assets/vdb_store
KNOWLEDGE_VECTOR_STORE_ROOT=/srv/tiaozhanbei-assets/vdb_store
KNOWLEDGE_HANDOFF_ROOT=/srv/tiaozhanbei-assets/知识星球视频知识库_前端交接包_2026-07-18
KNOWLEDGE_RUNTIME_ROOT=/srv/tiaozhanbei-runtime/knowledge
# 可选；不配置时读取仓库内 backend/competition/knowledge_atlas_chapters/2026-07-22
KNOWLEDGE_ATLAS_CHAPTER_ROOT=/srv/tiaozhanbei-assets/knowledge-atlas-chapters
```

生产环境通过 HTTPS 对外提供服务时设置 `AUTH_COOKIE_SECURE=true`。不要启用弱默认管理员密码；若需要初始化管理员，设置一次性强密码，登录后立即更换并从环境中移除。

## 4. 初始化数据库

正式系统使用同一 MySQL 实例中的两个独立数据库：

- `competition_app`：主认证、规划、会话、LangGraph 检查点、学习行为和复习队列；
- `competition_frontend`：题库、训练、知识卡、试卷及兼容业务域数据。

先初始化主库：

```bash
cd tiaozhanbei/backend
conda activate torch
python -m competition_app.cli.app init-db
```

该命令创建主库并按顺序执行 `competition_app/migrations/*.sql`。启用
`BACKEND_HANDOFF_ENABLED=true` 后，兼容业务库会在应用装载时创建并执行其增量结构初始化；因此首次完整启动使用的数据库账号需要建库和建表权限。

生产环境建议由 DBA 预建两个数据库，再使用迁移账号执行初始化。完成后可切换为只拥有两库日常读写权限的运行账号。详细授权、迁移、备份与恢复见
[数据库运维指南](database-operations.md)。

## 5. 启动方式

完成前端构建后，从 `backend/` 启动：

```bash
conda activate torch
python -m competition_app.cli.app serve --host 0.0.0.0 --port 7860
```

验证：

```bash
curl --fail http://127.0.0.1:7860/health
curl --fail http://127.0.0.1:7860/openapi.json >/dev/null
```

浏览器入口为 `http://127.0.0.1:7860/`。登录后还可检查：

- `GET /api/v1/platform/status`：主框架、兼容业务域和持久化装配状态；
- `GET /api/v1/platform/openapi.json`：兼容业务接口契约；
- `GET /docs`：主 FastAPI Swagger 文档。

前后端分离开发时，后端仍运行在 `7860`，另一个终端执行：

```bash
cd frontend/llm
npm run dev
```

访问 `http://127.0.0.1:5173`。Vite 已代理 `/api/v1`、`/api` 和 `/health`，前端业务代码只使用相对 URL。

## 6. 生产进程建议

仓库不依赖 Docker。服务器上可用 systemd 或团队已有进程管理器托管。systemd 的关键配置如下，路径按实际机器修改：

```ini
[Service]
WorkingDirectory=/srv/tiaozhanbei/backend
Environment=COMPETITION_ENV_FILE=/etc/tiaozhanbei/app.env
ExecStart=/opt/conda/envs/torch/bin/python -m competition_app.cli.app serve --host 127.0.0.1 --port 7860
Restart=on-failure
RestartSec=5
```

由 Nginx 或现有网关终止 HTTPS，并把同一域名反向代理到 `127.0.0.1:7860`。SSE 对话接口需要关闭代理缓冲并提高读取超时；Cookie 登录要求网关保留 `Set-Cookie`。

运行目录、上传目录和知识库 runtime 应放在持久磁盘，并授予服务账号读写权限；源代码目录只需读取权限。

## 7. 升级流程

1. 备份两个数据库，并确认备份文件非空；
2. 拉取目标提交，记录旧、新 commit SHA；
3. 重新安装后端依赖并执行 `npm install`、`npm run build`；
4. 执行 `python -m competition_app.cli.app init-db`；
5. 重启应用，检查 `/health`、平台状态、登录、对话和关键业务页面；
6. 发生不可恢复问题时停止新流量，恢复两个数据库备份并回退代码版本。

主库没有自动向下迁移。回滚以“代码版本 + 同一时刻的两个数据库备份”为一个整体，不要只恢复其中一个数据库。

## 8. 常见问题

- `MYSQL_PASSWORD is required`：正式持久化初始化未配置数据库密码；
- `migration checksum changed`：已执行的 SQL 迁移被修改，应还原旧文件并新增下一编号迁移；
- 知识星球或学习工坊 404：确认 `BACKEND_HANDOFF_ENABLED=true`，再看平台状态中的 `mounted`；
- 页面仍是旧版本：重新执行 `npm run build`，检查 `FRONTEND_DIST_ROOT`；
- 登录成功后仍返回 401：请求需带 `credentials: 'include'`，HTTPS 环境检查安全 Cookie 与代理头；
- SSE 无增量输出：检查反向代理缓冲和读取超时配置；
- Live 资源检索失败：检查资产绝对路径、目录权限、模型密钥及索引版本。
- 教材下没有章节或知识点：检查 `/api/knowledge/atlas/status` 返回的 `chapter_root`，确认其中包含 `chapter_nodes.jsonl` 和 `chunk_chapter_links.jsonl`；默认应指向仓库内置的 `2026-07-22` 映射。

接口联调细节见 [前端接口参考](frontend-api-reference.md)。

## 9. 自动治理与中断恢复部署检查

- 正式环境必须启用主数据库，否则 LangGraph 会明确降级为仅进程内恢复；
- `python -m competition_app.cli.app init-db` 必须执行到 `008_langgraph_persistent_checkpoints.sql`；
- 主应用和兼容业务模块必须连接共享持久数据库，不能把 SQLite 放在临时目录；
- 反向代理重连 SSE 时保留原 `thread_id`，不要把同一答案重新创建为新任务；
- 学情洞察读取会执行幂等自动检查，通知、干预和每周复盘分别通过去重键、24 小时冷却和周期键防重复；
- 升级后抽查 `/api/v1/learning-insights`、`/api/v1/notifications`、`/api/v1/plan-reviews`，并实际完成一次“中断—重启服务—恢复”验证。
