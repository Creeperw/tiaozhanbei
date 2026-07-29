# 时珍智训后端

本目录是时珍智训的主后端包 `competition_app`。后端采用 FastAPI、LangGraph、
MySQL/SQLite、Qwen 与 FAISS，负责用户认证、学习行为汇总、三层学习规划、知识讲解、
个性化学习资源、试卷生成、复习队列以及知识库管理接口。

长期规划、短期计划和当日任务是三个独立层级：下层只能基于已生效的上层生成，修改上层
会使相关下层失效。自然语言请求的层级由 Planner 模型判断；前后端规则仅提供可覆盖的
`plan_scope_hint`。

规划正文由 Diagnosis 智能体生成并保持自然语言原文；正文必须给出具体 `《书名》`、阶段
目标、推进安排、可观察产出和验收条件。Diagnosis 内部调用不对前端展示的
`PlanContractCompilerAgent`（内部 A 智能体），把当前层正文中的执行语义提取为最小 JSON
合同。编译器只允许逐字提取和提供来源锚点，不得补期限、补节点、补教材或改写正文；缺失或
冲突时返回问题，由 Diagnosis 最多修订一次。随后 `PlanContractValidator` 对可信路线、阶段
书目和父子期限做确定性校验，系统才装配并持久化正式计划。A 智能体不是顶层工作流节点，
因此不进入前端现有智能体路径显示。

期限以结构字段为准：长期规划的 `total_duration_days` 必须等于各阶段 `duration_days` 之和；
短期规划的 `duration_days` 不得超过所属长期阶段的期限。例如长期阶段为 30 天，则该阶段的
任一短期规划最多为 30 天，31 天会被拒绝。短期推进节点和今日章节/知识点同样使用结构字段，
不再依靠正则解析或重写规划正文。

新用户和新规划只支持五类教材型资格目标：中医执业医师、中医执业助理医师、
中西医结合执业医师、中西医结合执业助理医师及执业药师职业资格考试（中药学类）。
注册页从 `/api/v1/qualification-targets` 获取官方名称、考试轨道和教材路线映射，保存后同时
建立可信活动学习目标。规划链路直接复用该目标，不再追问已经选择过的考试名称。中医专长、
传统医学师承、职称、职业技能、考研与纯课程路线仅保留历史读取兼容，不能再被选作新默认规划。

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
COMPETITION_APP_MODE=stub python -m competition_app.cli.app serve
```

健康检查：

```bash
curl http://127.0.0.1:7860/health
```

Stub 模式不调用外部模型和向量服务，适合前端联调、接口契约检查和自动化测试。

> 当前配置按 `.env`、`.env.local`、系统环境变量的顺序覆盖读取；`.env.example`
> 只展示可用变量，不包含任何有效密钥。

## 2. Live 模式

Live 模式默认按以下顺序使用对话模型：

- `qwen3.7-flash`
- `qwen3.7-max-preview`
- `glm-5.2`
- `qwen3.7-flash-2026-07-15`
- `qwen-plus`
- Embedding：`Qwen/Qwen3-Embedding-4B`（SiliconFlow）
- 编排：LangGraph

模型顺序由 `CHAT_MODELS` 配置。当前模型遇到配额/限流、模型不存在、接口不兼容、持续服务
异常、空响应或无法修复的结构化输出时，会将该模型移出本进程的活动位置并按顺序切到下一项；
成功切换后，后续请求继续使用新的活动模型。系统不会为了“用完额度”发送无业务意义的请求。
`CHAT_MODEL` 仅用于兼容旧部署；配置 `CHAT_MODELS` 后以候选列表为准。

Planner 负责所有正常用户输入的意图判定，包括问候、感谢和能力询问。纯闲聊的用户可见回复
也由 Planner 模型生成；规划信息不足时，是否追问及具体问题由 Planner 结合当前话语、最近
对话和已有计划决定。确定性规则只负责 schema、依赖、安全边界和模型失败后的错误控制，
不能在正常路径中替代智能体回复。

至少设置：

```bash
export COMPETITION_APP_MODE=live
export COMPETITION_EXECUTION_ENGINE=langgraph
export CHAT_MODELS='qwen3.7-flash,qwen3.7-max-preview,glm-5.2,qwen3.7-flash-2026-07-15,qwen-plus'
export DASHSCOPE_API_KEY='...'
export SILICONFLOW_API_KEY='...'
export EXA_API_KEY='...'                 # 仅网络资源检索需要
python -m competition_app.cli.app serve
```

Live 模式还需要团队共享盘中的正式知识资产。大体积题库、FAISS 索引、视频知识库和运行时
数据库不提交 Git，路径通过以下变量挂载：

```bash
export QUESTION_VECTOR_STORE_ROOT='/absolute/path/to/vdb_store'
export KNOWLEDGE_VECTOR_STORE_ROOT='/absolute/path/to/vdb_store'
export KNOWLEDGE_HANDOFF_ROOT='/absolute/path/to/知识星球视频知识库_前端交接包_2026-07-18'
export KNOWLEDGE_RUNTIME_ROOT='/absolute/path/to/知识库管理组件/runtime'
```

如已有旧项目资产，可直接使用上述绝对路径；也可将 `vdb_store` 和知识库交付包软链接到
`backend/competition/` 下。不要复制或提交 FAISS 索引，后端会把解析后的本地路径投影给
交接业务模块。

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

启用交接业务域后，`competition_frontend` 会在模块装载时完成建库、缺失表初始化和增量结构修复。生产部署、最小授权、迁移约束、备份与恢复流程见
[部署与升级指南](../../docs/deployment.md) 和
[数据库运维指南](../../docs/database-operations.md)。

服务端始终以登录会话中的用户 ID 作为数据权限边界；请求体中的 `learner_id` 不能覆盖
登录身份。生产环境还需设置：

```bash
export BACKEND_HANDOFF_SECRET_KEY='a-long-random-secret'
export AUTH_COOKIE_SECURE=true            # HTTPS 环境
```

## 4. 前端整合约定

生产环境先在 `frontend/llm` 执行 `npm run build`，主 FastAPI 会在 `/` 同源托管构建结果。
开发环境由 Vite 将以下路径代理到 `7860`：

```text
/api/v1/*       # 主 API，不改写路径
/api/*          # 尚未迁移的兼容业务接口，去掉 /api 前缀
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
| --- | --- |
| 健康检查 | `GET /health` |
| 注册/登录/退出 | `POST /api/v1/auth/register`、`/login`、`/logout` |
| 当前用户 | `GET /api/v1/auth/me` |
| 对话会话 | `GET/POST /api/v1/conversations` |
| 会话消息 | `GET /api/v1/conversations/{conversation_id}/messages` |
| 首页摘要 | `GET /api/v1/dashboard/home` |
| 今日任务到期轮换 | `POST /api/v1/learning-tasks/current/refresh` |
| 当前任务可信资源物化/旧任务修复 | `POST /api/v1/learning-tasks/current/materialize-resources` |
| 当前完整规划与通过门禁 | `GET /api/v1/learning-plans/current` |
| 已完成任务绑定阶段证据 | `POST /api/v1/learning-plans/current/stages/{stage}/evidence` |
| 普通执行 | `POST /api/v1/review-cards` |
| 流式执行 | `POST /api/v1/review-cards/stream` |
| 查询运行状态 | `GET /api/v1/review-cards/runs/{thread_id}` |
| 中断后恢复 | `POST /api/v1/review-cards/runs/{thread_id}/resume/stream` |
| 多时间尺度学情 | `GET /api/v1/learning-state/multiscale` |
| 规划路径候选 | `GET /api/v1/learning-state/path-candidates` |
| 执行协调摘要 | `GET /api/v1/executions/{execution_id}/coordination` |
| 复习队列 | `GET /api/v1/learners/{learner_id}/review-queue` |
| 平台装配状态 | `GET /api/v1/platform/status` |
| 完整交接接口契约 | `GET /api/v1/platform/openapi.json` |
| FastAPI 文档 | `GET /docs` |

`GET /api/v1/dashboard/home` 的 `current_learning_task` 是学习工坊右栏的正式今日任务投影，包含 `learning_chapter`、`focus_knowledge_points` 与 `knowledge_cards[].action`。知识点 ID 由知识仓库解析，前端不得从自然语言任务正文自行生成 ID；点击动作后使用现有知识卡解析接口打开对应内容。

正式今日任务自生成起使用滚动 24 小时刷新窗口。`dashboard/home` 和 `learning-context` 返回服务端 `daily_task_timer`，前端倒计时归零后调用刷新接口；如果用户离线，下一次读取时自动补做轮换。轮换从当前短期计划任务块中取下一项，接口幂等且按登录用户隔离。刷新时间随现有计划 JSON 持久化，不需要数据库迁移。

每日任务按“章节视频学习 + 重点知识点题目训练”物化为原子项。视频只能使用知识仓库中
已发布、可核验的片段；找不到时不伪造链接，题目训练仍可继续。
模型给出的自然语言知识点会先按教材和章节映射到知识图谱；系统仅把本次需要的规范知识点
及至少 3 道配套题按需登记到个性化执行库，并冻结题目版本，不在启动时全量复制公共题库。
无法形成可核验完成路径的模型标签不会伪装成正式训练项。
每日任务的重点知识点、预期产出和验收标准最终由已经冻结的原子项反向校准，避免计划文字要求
4 个知识点、实际只有 3 个可执行资源时出现完成率与验收口径冲突。
`dashboard/home.current_learning_task.recommended_resources` 直接返回已绑定的章节视频和冻结题集入口。

`GET /api/v1/learning-plans/current` 是规划页面的稳定读取接口，同时返回长期/短期正文、
结构化字段、当前任务和验收门禁。长期阶段遵循 `all_exit_evidence_verified`：只有精确匹配
批准路线 `exit_evidence`、且来源为本人已完成服务端任务的证据才能满足指标。

每个 `conversation_id` 表示一个可包含多轮消息的正式会话；每次 LangGraph 执行使用独立
`thread_id`。中断恢复复用该次 `thread_id`，不能把会话 ID 当作所有轮次共用的检查点 ID。

流式接口返回 `text/event-stream`。前端应按 `event` 字段处理节点开始、模型增量、执行图、
中断、完成和失败事件，不要依赖日志文本。收到 `run_interrupted` 后保存 `thread_id`，使用
恢复接口继续；已完成节点由 LangGraph 检查点复用。

规划追问恢复只要求提交自然语言 `answer`；`profile_updates` 仅在调用方已经掌握明确字段时选填。
单字段画像追问会把答案写入对应字段，路线追问会由 Memory Agent 提炼后写入画像。确认目标或专业背景后，
LangGraph 会刷新上游路线解析，包括教材路线内部的追问，不会继续复用中断前的临时路线结果。非医学背景
确认“中医执业医师考试”后仍可能继续询问规定学历、专长或师承途径，这是报考路线单选，不是重复询问目标。

学习规划请求支持两个层级字段：

- `plan_scope`：用户在 UI 中明确选择的层级，属于强约束；
- `plan_scope_hint`：前端对自然语言的高置信提示，Planner 模型可覆盖。

可选层级为 `long_term`、`short_term`、`daily_task`、`unspecified`。普通自然语言输入不应
由前端强行填写 `plan_scope`。

## 5. 已包含的业务能力

- LangGraph 动态 Agent DAG、事件流、中断追问，以及刷新、断线和服务重启后的数据库检查点恢复；
- 智能体按需通信、认知缺口阻断，以及 Audit 触发的一轮局部修复；协调接口只返回事实数量、证据数量、步骤、状态和审核结论等安全摘要；
- 宏观、计划与任务、作答与掌握三层只读学情快照，以及先执行固定硬约束、再进行透明评分的规划候选；
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
curl http://127.0.0.1:7860/health
```

Live 页面还需用全新账号验证一次追问恢复：先提供基础和可持续时间但不提供目标；系统询问目标后仅回答
“中医执业医师考试”。下一步必须进入报考途径确认或直接生成已批准路线，不能再次询问考试/专业方向；
完成后长期规划应包含经典路线的五个阶段与真实教材，不能出现“待确认教材”。

## 7. 禁止提交

- `.env`、`.env.local`、API Key、数据库密码和 Cookie Secret；
- `__pycache__/`、`.pytest_cache/`、`snapshots/`、日志和本地数据库；
- FAISS 索引、视频、题库原始包和其他大体积知识资产；
- 用户上传文件、答题记录导出或含个人信息的运行快照。

遇到接口不一致时，以 `/api/v1/platform/openapi.json` 和 Pydantic 请求/响应模型为准，
不要让前端复制后端状态机或重新生成系统 ID。
