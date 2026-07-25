# 智能组卷：正式题库、接口与 Live 部署说明

## 1. 目的与默认行为

智能组卷通过“蓝图 → 正式题库检索 → 组卷 → 审核 → 发布”的流程生成训练试卷。自本次修改起，`competition_app` 默认运行在 `live` 模式：未提供模型密钥时服务会明确启动失败，不会静默退回到 demo/stub 题目。

`stub` 仅用于隔离测试或前端接口联调，必须显式设置：

```bash
COMPETITION_APP_MODE=stub
```

启动后先检查：

```bash
curl http://127.0.0.1:7860/health
```

正常正式模式必须包含：

```json
{
  "mode": "live",
  "knowledge_source": "formal"
}
```

如果返回 `mode: "stub"` 或 `knowledge_source: "demo"`，页面会显示固定演示题，例如“根据当前教学主题生成的补充练习题”，这不是正式题库结果。

## 2. 本次智能组卷后端修改

### 2.1 组卷链路

请求经 `POST /api/v1/review-cards` 进入：

```text
PlannerAgent
  → PaperBlueprintAgent
  → KnowledgeBaseAgent
  → PaperAssemblyAgent
  → AuditAgent
  → Workshop 试卷发布
```

- `PaperBlueprintAgent`：解析题量、题型、主题和个性化摘要，形成可检索的蓝图单元。
- `KnowledgeBaseAgent`：从正式题库筛选主题、题型匹配的候选题；按题目 ID 与题干哈希去重；记录近期已出题的冷却排除原因。
- `PaperAssemblyAgent`：优先采用正式候选；模型遗漏候选时按蓝图确定性补选正式题；练习模式仅在正式候选不足时使用审核生成补题。
- `AuditAgent`：检查题量、题型、重复和来源后发布试卷。

### 2.2 正式题优先与补题规则

1. 正式题库及当前用户已确认题优先。
2. 同题目 ID、同规范化题干不能在同一试卷重复。
3. 最近已发布题进入短期冷却，不会被模型选择、系统回填或最终保底路径重新加入。
4. 练习模式（`mode=practice`）允许正式候选不足时生成审核补题；每题标记为“审核生成补题”。
5. 测试模式（`mode=test`、`测试` 或 `考试`）不采用任何模型生成题：
   - 正式候选足够时，只发布正式题；
   - 正式候选不足时，返回可操作的题库不足错误，不用生成题凑数。
6. 若正式题命中但全部处于冷却期，接口会说明“近期已发布题”原因；不会把它误报为题库未命中。

> 例：当前正式题库中，与“四君子汤”直接相关且题型为单项选择题的可用正式题只有 3 道。因此新用户首轮请求 5 题时，预期为“3 道正式题库 + 2 道审核生成补题”；同一用户后续短期内再次请求该专题时，3 道正式题处于冷却期，练习模式会使用新的审核生成补题，测试模式则会提示题库不足。

### 2.3 题量解析修复

`exam_constraints.question_count` 是服务端硬约束，优先于自然语言解析。题量解析不再将“第 1 轮”“第 3 阶段”等编号误识别为 1 题或 3 题。

例如：

```json
{
  "user_request": "第2轮：生成四君子汤单项选择题试卷5题",
  "exam_constraints": {
    "question_count": 5,
    "question_types": ["单项选择题"],
    "mode": "practice",
    "allow_generated_fill": true
  }
}
```

生成目标始终是 5 题，而不是“第2轮”中的 2 题。

## 3. 前端与后端接口

前端开发服务器 `frontend/llm` 默认把 `/api` 代理到 `127.0.0.1:7860`。因此浏览器页面与健康检查必须连接同一个 7860 服务；不要只在其他端口启动 live 服务。

### 3.1 生成试卷

```http
POST /api/v1/review-cards
Cookie: competition_session=...
Content-Type: application/json
```

请求示例：

```json
{
  "learner_id": "当前登录用户ID",
  "user_request": "生成四君子汤单项选择题试卷5题",
  "available_minutes": 15,
  "exam_constraints": {
    "question_count": 5,
    "question_types": ["单项选择题"],
    "mode": "practice",
    "allow_generated_fill": true
  }
}
```

要点：

- 服务端使用登录会话确认用户身份；`learner_id` 不能跨用户越权。
- `mode=practice`：允许审核补题。
- `mode=test`：只允许正式题库/已确认个人题，正式候选不足则不发布。
- 响应中的 `agent_outputs` 包含 `paper_blueprint`、`question_candidate_pool`、`exam_paper_draft` 和 `audit_result`，用于查看候选数量、冷却说明和审核结果。

流式版本：`POST /api/v1/review-cards/stream`，响应类型为 `text/event-stream`。

### 3.2 试卷工作台

| 目的 | 方法与路径 |
|---|---|
| 列出当前用户试卷 | `GET /api/v1/workshop/papers?offset=0&limit=50` |
| 获取单份试卷 | `GET /api/v1/workshop/papers/{paper_id}` |
| 保存答案 | `PUT /api/v1/workshop/papers/{paper_id}/answers` |
| 暂停计时 | `POST /api/v1/workshop/papers/{paper_id}/timer/pause` |
| 恢复计时 | `POST /api/v1/workshop/papers/{paper_id}/timer/resume` |
| 交卷评分 | `POST /api/v1/workshop/papers/{paper_id}/submit` |

题目读取接口中的每题会提供学员可见来源标签：

- `正式题库`
- `个人已确认题`
- `审核生成补题`
- `历史题源待确认`

不向考生接口返回内部检索分数、完整证据 ID 或未交卷时的标准答案。

## 4. 队友拉取后的部署步骤

以下命令在仓库根目录执行，Windows 推荐使用 Git Bash。路径中有中文或空格时必须用引号包裹。

### 4.1 安装代码依赖

```bash
cd backend
python -m pip install -r competition_app/requirements.txt
```

正式题库在关闭向量检索时使用流式 JSON 解析，建议确认已安装：

```bash
python -m pip install ijson
```

若 `EMBEDDING_MODE=enabled` 且使用本地/FAISS 索引，按 `requirements.txt` 安装 `faiss-cpu`；Windows 环境如无可用 wheel，可先设置 `EMBEDDING_MODE=disabled`，正式题库仍会通过 BM25 流式检索工作，但不使用向量召回。

### 4.2 准备大体积正式资产

以下资产不提交 Git，必须从团队共享盘挂载、复制或建立目录链接：

```text
知识星球视频知识库_前端交接包_2026-07-18/
└── 知识库管理组件/
    └── data/backend_delivery/
        ├── 01_question_bank/formatted_questions.json
        ├── 03_pipeline_chunks/source_chunks.jsonl
        ├── 04_knowledge_points/final_knowledge_points.json
        └── 08_exam_learning_path_2025/

vdb_store/                         # 启用向量检索时需要
backend-handoff-20260720/          # 已在当前仓库中，用于训练工坊发布/答题接口
```

启动时必须能读到 `formatted_questions.json`，否则 live 服务会报“知识库交接包不完整”。不得将题库、FAISS 索引、运行时数据库或用户数据提交到 Git。

### 4.3 创建本地配置

复制模板，所有密钥只写入本机 `.env.local`，不要提交：

```bash
cp backend/competition_app/.env.example backend/competition_app/.env.local
```

最小 live 配置：

```dotenv
COMPETITION_APP_MODE=live
COMPETITION_EXECUTION_ENGINE=langgraph
API_HOST=127.0.0.1
API_PORT=7860

DASHSCOPE_API_KEY=从项目负责人或团队密钥管理服务获取
SILICONFLOW_API_KEY=从项目负责人或团队密钥管理服务获取
BACKEND_HANDOFF_SECRET_KEY=由密钥管理服务生成的高熵随机值

# 本地live最小持久化；生产环境可改为MySQL/DATABASE_URL
USE_SQLITE=true

KNOWLEDGE_HANDOFF_ROOT=正式知识库交付包的绝对路径
KNOWLEDGE_RUNTIME_ROOT=正式知识库交付包/知识库管理组件/runtime
QUESTION_VECTOR_STORE_ROOT=可选，vdb_store绝对路径
KNOWLEDGE_VECTOR_STORE_ROOT=可选，vdb_store绝对路径

BACKEND_HANDOFF_ENABLED=true
BACKEND_HANDOFF_ROOT=仓库内backend/competition/backend-handoff-20260720的绝对路径
BACKEND_HANDOFF_RUNTIME_ROOT=本机可写runtime/frontend_backend绝对路径
```

模型密钥用途：

| 变量 | 是否启动live必需 | 用途 |
|---|---:|---|
| `DASHSCOPE_API_KEY` | 是 | 蓝图、组卷、审核和补题模型调用；当前兼容模型由 `CHAT_BASE_URL` / `CHAT_MODEL` 指定 |
| `SILICONFLOW_API_KEY` | 是 | Embedding 服务；即使当前关闭向量召回，live 启动配置仍要求该变量存在 |
| `EXA_API_KEY` | 否 | 网络视频、参考资料和外部题目线索检索；未配置时不影响本地正式题库组卷 |
| `MINERU_TOKEN` | 否 | PDF/图片知识或题目导入；常规组卷不需要 |
| `MYSQL_PASSWORD` | 生产建议 | MySQL 持久化；本地可使用 `USE_SQLITE=true` |
| `DATABASE_URL` | 生产可选 | 可替代MySQL变量的完整数据库连接串 |
| `USE_SQLITE` | 本地live必需（三选一） | 设为 `true` 使用本地SQLite；live还可通过 `DATABASE_URL` 或 `MYSQL_PASSWORD` 选择持久化存储 |
| `BACKEND_HANDOFF_SECRET_KEY` | 是（交接业务域启用时） | 交接业务域会话/JWT签名密钥；live模式下留空会拒绝启动，不要与其他环境复用 |
| `SECRET_KEY` | 生产必需 | 主应用认证与签名密钥 |

密钥由项目负责人或团队的密钥管理服务发放；不要通过 Git、聊天记录、截图或提交历史传递。若密钥疑似泄露，应立即在供应商控制台轮换。

### 4.4 启动顺序

后端：

```bash
cd backend
python -m competition_app.cli.app serve
```

前端开发：

```bash
cd frontend/llm
npm install
npm run dev -- --host 127.0.0.1
```

前端 Vite 服务会将 `/api` 代理到 `7860`。启动后必须按顺序检查：

```bash
curl http://127.0.0.1:7860/health
curl http://127.0.0.1:5173/api/health
```

两个接口都必须显示：

```json
{"mode":"live","knowledge_source":"formal"}
```

如果 7860 是 `stub/demo`，不要继续用页面验收组卷；先检查启动进程环境、`.env.local` 是否生效，以及是否有系统环境变量把 `COMPETITION_APP_MODE` 覆盖为 `stub`。

## 5. 验收清单

1. 使用新注册账号登录，避免近期题冷却影响首轮结果。
2. 请求“四君子汤单项选择题 5 题，练习模式”。
3. 首轮预期：3道“正式题库” + 2道“审核生成补题”。
4. 再生成同一专题，正式题不应重复；页面应显示冷却/补题结果，而不是固定 demo 文案。
5. 请求同专题 `mode=test`：正式题不足时应返回题库不足，不应生成审核补题。
6. 浏览器 Network 中确认请求经过 `/api/v1/review-cards`，且 `GET /api/health` 返回 `live/formal`。
7. 检查 `GET /api/v1/workshop/papers/{paper_id}`：题目来源标签应为“正式题库”或“审核生成补题”，不能是固定占位题。

## 6. 常见故障

| 现象 | 原因 | 处理 |
|---|---|---|
| 页面显示固定“根据当前教学主题生成的补充练习题” | 前端连接的是 stub 服务 | 检查 7860 和 5173 `/api/health`；必须重启实际7860为live |
| 服务启动报缺少 `DASHSCOPE_API_KEY` 或 `SILICONFLOW_API_KEY` | 默认live已生效但本机未配置密钥 | 在 `.env.local` 补齐变量；不要改回stub掩盖问题 |
| 服务报“知识库交接包不完整” | `KNOWLEDGE_HANDOFF_ROOT` 指向错误或资产未同步 | 挂载完整交付包，确认题库JSON与知识点文件存在 |
| 全部为审核生成补题 | 正式候选不足，或近期正式题全在冷却期 | 查看 `question_candidate_pool` 的 warnings；换主题、等待冷却期或缩小题型范围 |
| 本地内存不足或FAISS导入失败 | 大索引/本地向量依赖不可用 | 安装 `ijson`；临时设 `EMBEDDING_MODE=disabled` 使用BM25正式检索 |
| 前端是5173、后端live却仍显示demo | Vite代理连接到了旧7860进程 | 结束旧7860进程，以live环境重新启动7860，再刷新浏览器 |

## 7. 相关代码位置

| 位置 | 职责 |
|---|---|
| `backend/competition_app/config.py` | live/stub默认值、密钥校验、路径解析 |
| `backend/competition_app/application/container.py` | live组件装配、正式知识仓库与交接业务域挂载 |
| `backend/competition_app/tools/knowledge_delivery.py` | 正式题库BM25/向量检索与流式JSON读取 |
| `backend/competition_app/agents/paper_blueprint.py` | 题量、题型、个性化蓝图约束 |
| `backend/competition_app/agents/knowledge_base.py` | 候选检索、主题/题型过滤、冷却去重 |
| `backend/competition_app/agents/paper_assembly.py` | 正式候选优先、补题、测试模式限制 |
| `backend/competition_app/api/app.py` | review-cards与workshop papers接口 |
| `frontend/llm/src/pageDataLoaders.js` | 前端组卷请求和约束传递 |
| `frontend/llm/src/components/SmartPaperPanel.jsx` | 组卷配置与生成过程 |
| `frontend/llm/src/components/PaperGenerationPanel.jsx` | 试卷答题、题源标签与提交 |
