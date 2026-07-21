# 时珍智训 / 司宁培训助手平台

时珍智训是一个由“健康领域多智能体聊天系统”演进而来的培训助手平台。当前版本以平台壳为入口，包含首页、智能助教、训练工坊、知识仓库、学习画像、学习规划、学情报告、题库治理和管理支持能力。

> 本仓库仍保留部分健康语义和医疗辅助能力。系统输出只能用于培训、健康管理与科普辅助，不能替代专业医疗诊断、治疗或处方建议。

## 1. 给协作队友的结论

这个项目不应简单理解为只有两个可独立复制的 `frontend/` 和 `backend/` 目录：

- 前端正式工程是 `frontend/llm/`，可以独立安装、测试和构建。
- 后端代码主体位于 `backend/`，但正式 Python 导入路径是 `APP.backend.*`。
- 当前开发机上的 `APP/backend` 是指向根目录 `backend/` 的 Junction，同一物理文件会以两个路径出现在 Git 状态中；不要删除或重建该映射，也不要把两处当成两套代码分别修改。
- 后端运行还依赖 `APP/__init__.py`、`APP/env_loader.py`、`APP/intent_reply_template.py`、根目录 `intent_reply_template.py`、`requirements.txt` 和启动脚本。
- 数据库、题库原始数据、用户文件、向量索引和模型不属于代码交付物。本 README 会说明它们应放在哪里，但交付压缩包不包含具体数据。

首次交付包位于 `deliverables/2026-07-20/`：

- `frontend-handoff-20260720.zip`：前端源码、测试、静态资源和前端依赖清单。
- `backend-handoff-20260720.zip`：可运行的 `APP.backend` 后端包、测试、脚本、环境变量模板和依赖清单。
- `MANIFEST.md`：精确的包含项、排除项、文件数和验证结果。
- `SHA256SUMS.txt`：压缩包 SHA-256，用于确认传输后文件未损坏。

压缩包只适合第一次交接。后续更新使用 Git 提交、分支和 Pull Request，不需要人工枚举修改文件，详见第 14 节。

## 2. 技术栈

| 层     | 技术                                                         | 用途                             |
| ------ | ------------------------------------------------------------ | -------------------------------- |
| 前端   | React 19、React DOM 19                                       | 页面与组件                       |
| 构建   | Vite 7                                                       | 开发服务器、代理和生产构建       |
| 样式   | Tailwind CSS 4、PostCSS、原生 CSS                            | 平台壳、业务页面和可视化样式     |
| 状态   | Zustand 5                                                    | LangGraph 流程时间线状态         |
| 内容   | react-markdown、remark-gfm、remark-math、rehype-katex、KaTeX | Markdown、公式和富文本回答       |
| 可视化 | Three.js                                                     | 知识星球和学习路径三维场景       |
| 测试   | Vitest、Testing Library、Playwright、axe-core                | 单元、组件、端到端和可访问性测试 |
| 后端   | FastAPI、Uvicorn、Pydantic                                   | HTTP API、流式响应和数据校验     |
| 数据层 | SQLAlchemy、SQLite / MySQL、PyMySQL                          | 业务数据与运行时兼容迁移         |
| 智能体 | LangGraph、HTTPX                                             | 多智能体工作流与模型调用         |
| 检索   | FAISS、sentence-transformers、NumPy                          | 可选 RAG 和题目向量检索          |
| 多模态 | python-docx、faster-whisper                                  | 文档抽取和可选语音识别           |
| 鉴权   | python-jose、passlib[argon2]                                 | JWT 与密码哈希                   |

本机当前验证环境为 Python 3.13 和 Node.js 24；项目最低建议为 Python 3.10+、Node.js 18+。团队应统一主版本，避免锁文件和原生依赖差异。

## 3. 总体架构与数据流

```text
Browser
  │
  │ React fetch('/api/...') + Bearer Token
  ▼
Vite dev proxy
  │ 移除 /api，默认转发到 http://127.0.0.1:7860
  ▼
FastAPI / APP.backend.main:app
  ├─ auth / sessions / upload
  ├─ dashboard / training / workspace
  ├─ knowledge / atlas / question workspace
  ├─ personalization / learning activity
  └─ multi-agent chat stream
       ├─ database context and learner profile
       ├─ planner / executor / reviewer
       ├─ optional RAG / search / video tools
       └─ message branch, feedback and writeback
```

登录后浏览器把 JWT 保存到 `localStorage.token`。所有受保护请求通过 `frontend/llm/src/utils/api.js` 自动加入 `Authorization: Bearer <token>`。智能助教保存最近会话标识 `localStorage.lastSessionId`，但会话和消息正文以数据库为准。

## 4. 目录架构

```text
.
├─ APP/
│  ├─ __init__.py                    # 加载项目 .env
│  ├─ env_loader.py                  # 无第三方依赖的环境变量加载器
│  ├─ intent_reply_template.py       # 转发根模板，兼容 APP.intent_reply_template
│  └─ backend/                       # 开发机 Junction -> ../backend
├─ backend/                          # FastAPI 后端真实源码目录
│  ├─ routers/                       # HTTP 路由层
│  ├─ tests/                         # unittest 测试
│  ├─ scripts/                       # 导入、修复、重建与演示种子脚本
│  ├─ official_exam_repository/      # 正式题库文件仓储代码
│  ├─ knowledge_atlas_contracts/     # 小型、可版本化的知识图谱交付契约
│  ├─ sample_data/                   # 演示种子；不进入本次代码包
│  ├─ data/                          # 公共知识/正式题库源数据；不进入代码包
│  ├─ uploads/                       # 用户上传；运行时数据，不进入代码包
│  ├─ user_knowledge/                # 个人知识库；运行时数据，不进入代码包
│  ├─ user_questions/                # 用户题目文件；运行时数据，不进入代码包
│  ├─ vdb_store/                     # FAISS/向量索引；生成物，不进入代码包
│  ├─ knowledge_atlas_assets/        # 图谱导入源素材；不进入代码包
│  ├─ knowledge_atlas_runtime/       # 图谱运行数据；生成物，不进入代码包
│  └─ knowledge_runtime/             # 知识库运行状态；生成物，不进入代码包
├─ frontend/
│  └─ llm/                           # 唯一正式前端工程
│     ├─ src/                        # React 源码
│     ├─ public/                     # 前端必需静态资源
│     ├─ e2e/                        # Playwright 端到端测试
│     ├─ package.json                # npm 脚本与直接依赖
│     ├─ package-lock.json           # 锁定依赖版本
│     ├─ vite.config.js              # 构建、测试和 /api 代理
│     ├─ playwright.config.js        # E2E 配置
│     └─ eslint.config.js            # ESLint 配置
├─ docs/                             # 架构、计划、验收和交接文档
├─ deliverables/                     # 本地交付产物；ZIP 不提交 Git
├─ .env.example                      # 无密钥的环境变量模板
├─ intent_reply_template.py          # 意图回答结构模板
├─ requirements.txt                  # Python 直接依赖
├─ run.ps1 / run.sh                  # Windows / POSIX 一键运行入口
├─ TECH_STACK.md                     # 历史技术细节和协议说明
├─ RL.md                             # GRPO 规划稿，尚未落地
└─ README.md                         # 当前交接总说明
```

`frontend/0717/`、`frontend/0717_2/` 是本地历史备份，不是正式前端，禁止合并回 `frontend/llm/`。

## 5. 前端功能文件与组件

### 5.1 入口、壳层与公共模型

| 文件                                         | 责任                                                    |
| -------------------------------------------- | ------------------------------------------------------- |
| `frontend/llm/src/main.jsx`                | React DOM 挂载入口                                      |
| `frontend/llm/src/App.jsx`                 | 登录态、页面意图和一级页面装配；登录后默认进入平台首页  |
| `frontend/llm/src/appShell.js`             | 导航配置、角色可见性、页面标题和模块接口元数据          |
| `frontend/llm/src/components/AppShell.jsx` | 桌面侧栏、移动抽屉、用户菜单和主内容壳                  |
| `frontend/llm/src/pageIntent.js`           | 页面跳转意图和参数合并，避免组件直接依赖兄弟组件状态    |
| `frontend/llm/src/pageDataLoaders.js`      | 训练计划、报告、工作台、试卷、病例等 API 读取与降级数据 |
| `frontend/llm/src/utils/api.js`            | `/api` 基址、Bearer Token、JSON 容错和多接口回退      |
| `frontend/llm/src/index.css`               | 全局主题、页面布局和公共视觉样式                        |
| `frontend/llm/src/App.css`                 | 入口级遗留样式                                          |

### 5.2 认证、首页和助教

| 文件                                       | 责任                                                           |
| ------------------------------------------ | -------------------------------------------------------------- |
| `components/AuthPage.jsx`                | 登录、注册、验证码和密码重置                                   |
| `components/HomePage.jsx`                | 平台首页、今日任务、学习摘要、推荐入口和助教角色入口           |
| `homePortal.js`                          | 首页数据归一化、状态卡、推荐和动作模型                         |
| `components/ChatInterface.jsx`           | 智能助教主界面、会话树、上传、流式解析、反馈、重生成和引用展示 |
| `chatProtocol.js`                        | `<<EV>>` 等流式标记解析、可见正文与执行轨迹分离              |
| `chatSessionClient.js`                   | 助教会话列表、创建、读取和最近会话选择                         |
| `components/AgentTimeline.jsx`           | 多智能体节点、工具调用和意图识别时间线                         |
| `stores/useLangGraphStore.js`            | LangGraph 事件节点状态、耗时、日志和工具结果                   |
| `components/CompactAssistant.jsx`        | 跨页面悬浮式紧凑助教                                           |
| `assistantDockModel.js`                  | 助教问候、消息状态与停靠模型                                   |
| `components/CodeHighlighter.jsx`         | 助教回答代码高亮                                               |
| `components/CommunityLearningButton.jsx` | 社区学习入口和交互动效                                         |

### 5.3 训练工坊、首页看板与学习路径

| 文件                                                 | 责任                                             |
| ---------------------------------------------------- | ------------------------------------------------ |
| `components/DashboardPage.jsx`                     | 学习工坊入口，装配考试图谱、每日工作区和训练模块 |
| `components/dashboard/DashboardDailyWorkspace.jsx` | 每日任务指挥区、学习进度和行动入口               |
| `components/dashboard/dashboardDailyModel.js`      | 每日任务、路径节点和看板展示模型                 |
| `components/PracticePage.jsx`                      | 训练工作台任务执行、结果工件和模块分发           |
| `components/CaseTrainingPanel.jsx`                 | 病例/情景模拟对话、帮助和提交                    |
| `components/PaperGenerationPanel.jsx`              | 组卷和试卷任务入口                               |
| `components/MistakeVariationPanel.jsx`             | 错题变式来源和训练任务入口                       |
| `components/OnboardingSurveyPanel.jsx`             | 首次培训画像问卷                                 |
| `components/PlanningPage.jsx`                      | 学习规划页面                                     |
| `components/ReportsPage.jsx`                       | 学情报告页面                                     |
| `components/LearningTrendChart.jsx`                | 学习趋势图表                                     |
| `learningFocusTracker.js`                          | 专注会话开始、心跳、结束和页面生命周期           |
| `learningTrendDisplay.js`                          | 趋势数据转图表展示模型                           |
| `dashboardRouting.js`                              | 看板模块到平台页面的路由映射                     |

### 5.4 考试图谱与知识星球

| 文件                                                         | 责任                                       |
| ------------------------------------------------------------ | ------------------------------------------ |
| `components/exam-atlas/ExamAtlas.jsx`                      | 考试大纲/学习路径图谱容器                  |
| `components/exam-atlas/examAtlasApi.js`                    | 考试轨道、节点、知识点和学习状态接口       |
| `components/exam-atlas/examAtlasModel.js`                  | 图谱节点、关系和状态归一化                 |
| `components/exam-atlas/examAtlasPageContext.js`            | 图谱页面参数和导航上下文                   |
| `components/exam-atlas/ExamAtlasCanvas.jsx`                | 考试图谱画布                               |
| `components/exam-atlas/ExamAtlasDetailDrawer.jsx`          | 节点详情抽屉                               |
| `components/exam-atlas/AtlasPracticePanel.jsx`             | 图谱内下一题和判题面板                     |
| `components/knowledge-atlas/KnowledgeAtlas.jsx`            | 知识星球主容器                             |
| `components/knowledge-atlas/knowledgeAtlasApi.js`          | 图谱状态、路径、节点、详情和上下文解析接口 |
| `components/knowledge-atlas/knowledgeAtlasModel.js`        | 图谱数据归一化和布局输入                   |
| `components/knowledge-atlas/knowledgeAtlasFeature.js`      | 功能开关与可用性判定                       |
| `components/knowledge-atlas/useKnowledgeAtlasCanvas.js`    | Canvas 绘制、缩放、命中测试和动效          |
| `components/knowledge-atlas/KnowledgeAtlasDetail.jsx`      | 知识点详情、图片和关联题目                 |
| `components/knowledge-atlas/KnowledgeWorkspaceNav.jsx`     | 知识仓库内部导航                           |
| `components/learning-tree/KnowledgePlanetScene.jsx`        | Three.js 知识星球场景                      |
| `components/learning-tree/knowledgePlanetModel.js`         | 星球节点布局和边关系                       |
| `components/learning-tree/KnowledgeTreeDrilldown.jsx`      | 学习树层级钻取                             |
| `components/learning-tree/LearningPathOverview.jsx`        | 学习路径总览和语义缩放                     |
| `components/learning-tree/LearningPathTrainingModules.jsx` | 路径关联训练模块                           |
| `components/learning-tree/LearningPlanRail.jsx`            | 学习计划侧轨                               |
| `components/learning-tree/learningTreeModel.js`            | 依赖树、鱼骨图边和布局算法                 |

### 5.5 知识、题库、画像与管理

| 文件                                      | 责任                                                                     |
| ----------------------------------------- | ------------------------------------------------------------------------ |
| `components/KnowledgePage.jsx`          | 知识仓库总页，装配知识星球、文件、目录和题库入口                         |
| `components/AdminKnowledgePage.jsx`     | 管理员知识库维护                                                         |
| `knowledgePageState.js`                 | 知识范围提示、检索状态和空态模型                                         |
| `components/QuestionWorkspacePage.jsx`  | 题库导入、人工审核、确认、驳回、停用和索引重建                           |
| `components/PersonalizationHubPage.jsx` | 个性化数据中枢，切换画像和记忆任务                                       |
| `components/PersonalizationPage.jsx`    | 学习画像、记忆、候选记忆和导出                                           |
| `components/ProfileConflictList.jsx`    | 画像冲突和候选项展示                                                     |
| `profileConflictList.js`                | 冲突分组展示模型                                                         |
| `systemDataDisplay.js`                  | 系统学习数据指标格式化                                                   |
| `components/SettingsPage.jsx`           | 用户设置                                                                 |
| `components/AdminFeedbackPage.jsx`      | 管理员反馈查询、编辑、删除和导出                                         |
| `components/HomeButton.jsx`             | 返回首页按钮                                                             |
| `components/ui/index.jsx`               | Button、IconButton、SegmentedControl、Badge、Skeleton、EmptyState、Modal |
| `components/ui/useModalFocus.js`        | 弹窗焦点约束和键盘可访问性                                               |

同目录的 `*.test.js`、`*.test.jsx` 对应单元/组件测试；`frontend/llm/e2e/*.spec.js` 对应平台、训练工坊和知识图谱的浏览器测试。

## 6. 后端功能文件

### 6.1 基础设施

| 文件                       | 责任                                                                                |
| -------------------------- | ----------------------------------------------------------------------------------- |
| `backend/main.py`        | FastAPI 应用、CORS、路由注册、管理员初始化和题目导入后台工作线程                    |
| `backend/config.py`      | 数据库、模型、RAG、语音、上传、密钥和采样参数；敏感项只读环境变量                   |
| `backend/database.py`    | SQLAlchemy 模型、Session、SQLite/MySQL 初始化和`ensure_runtime_schema()` 兼容迁移 |
| `backend/auth.py`        | 密码哈希、JWT、当前用户和管理员依赖                                                 |
| `backend/schemas.py`     | 鉴权与上传等基础 Pydantic 模型                                                      |
| `backend/store.py`       | 上传文件元数据内存映射和持久化                                                      |
| `backend/file_utils.py`  | 文件路径和安全文件处理                                                              |
| `backend/email_utils.py` | 验证码生成与邮件发送                                                                |
| `backend/time_utils.py`  | UTC 时间工具                                                                        |
| `backend/faiss_io.py`    | FAISS 索引原子读写辅助                                                              |

### 6.2 智能助教与多智能体

| 文件                              | 责任                                                                 |
| --------------------------------- | -------------------------------------------------------------------- |
| `health_workflow.py`            | LangGraph 主工作流：上下文、规划、工具、执行、审核、重生成和记忆更新 |
| `health_llm.py`                 | API/local 模式模型客户端和角色路由                                   |
| `health_memory.py`              | 用户画像、短长期记忆、候选记忆、摘要和上下文压缩                     |
| `health_tools.py`               | RAG、网络、视频等工具注册                                            |
| `health_utils.py`               | 工作流通用文本和状态工具                                             |
| `health_prompts.py`             | 健康工作流提示词兼容入口                                             |
| `agent_contracts.py`            | 智能体输入、输出和事件契约                                           |
| `agent_registry.py`             | 智能体注册表                                                         |
| `agent_runtime.py`              | 智能体运行时上下文                                                   |
| `agent_prompts.py`              | 培训智能体提示词                                                     |
| `agent_orchestrator_service.py` | 培训智能体编排和执行轨迹                                             |
| `planner_agent_service.py`      | 学习任务规划                                                         |
| `expert_agent_service.py`       | 专家执行代理                                                         |
| `diagnosis_agent_service.py`    | 学情诊断和训练入口判断                                               |
| `audit_agent_service.py`        | 输出审核                                                             |
| `cross_validation_service.py`   | 多结果交叉验证                                                       |
| `memory_agent_service.py`       | 记忆智能体能力                                                       |
| `knowledge_agent_service.py`    | 知识检索/证据包智能体能力                                            |
| `document_ingestion_service.py` | 文档解析和知识导入                                                   |
| `vision_parse_service.py`       | 图片/视觉输入解析                                                    |
| `tool_runtime.py`               | 工具调用运行时                                                       |
| `search_tool.py`                | Exa 网络搜索                                                         |
| `reminder_tool.py`              | 提醒工具                                                             |

### 6.3 培训、训练和评价

| 文件                                  | 责任                               |
| ------------------------------------- | ---------------------------------- |
| `dashboard_service.py`              | 首页学习摘要、今日任务、推荐和公告 |
| `training_service.py`               | 训练计划、报告、练习和入门流程     |
| `training_workspace_service.py`     | 训练工作台模块、任务编排和结果工件 |
| `training_orchestration_adapter.py` | 工作台任务到智能体编排器的适配层   |
| `deep_training_service.py`          | 知识对齐、选题、诊断和交叉验证     |
| `checkin_service.py`                | 每日签到                           |
| `learner_profile_service.py`        | 学习者画像服务                     |
| `learning_plan_service.py`          | 学习计划构建                       |
| `learning_target_service.py`        | 学习目标读写                       |
| `learning_task_activity_service.py` | 学习任务和专注会话记录             |
| `learning_writeback_service.py`     | 训练结果写回掌握度、错题和计划     |
| `grading_application_service.py`    | 判题应用服务                       |
| `review_formula.py`                 | 复习优先级计算                     |
| `onboarding_template_service.py`    | 入门问卷分组模板                   |
| `case_training_models.py`           | 病例训练请求/状态模型              |
| `case_training_state.py`            | 病例训练状态                       |
| `case_training_service.py`          | 病例会话、帮助和提交               |
| `case_patient_orchestration.py`     | 模拟患者编排                       |
| `case_repository.py`                | 病例数据访问                       |
| `paper_generation_service.py`       | 组卷                               |
| `paper_submission_service.py`       | 草稿、交卷和评分                   |
| `mistake_variation_service.py`      | 错题变式任务                       |
| `variation_repository.py`           | 变式版本和评分规则仓储             |

### 6.4 知识库、题库与正式内容

| 文件                                       | 责任                                       |
| ------------------------------------------ | ------------------------------------------ |
| `rag_core.py`                            | 公共/个人知识库构建、联合检索和权限边界    |
| `rag_text.py`                            | 文本切分、向量化和相似度检索               |
| `knowledge_atlas_service.py`             | 知识图谱状态、节点、详情、上下文和题目搜索 |
| `knowledge_atlas_video_pipeline.py`      | 图谱视频链接解析和校验                     |
| `data_import_service.py`                 | 通用数据导入                               |
| `delivery_import_service.py`             | 队友交付物导入                             |
| `formal_content_import_service.py`       | 正式课程内容导入                           |
| `question_repository.py`                 | 关系型题库读写                             |
| `official_exam_repository/repository.py` | JSONL 正式题库仓储                         |
| `official_exam_repository/jsonl_io.py`   | JSONL 原子读写                             |
| `question_bank_import_service.py`        | 正式题库批量导入                           |
| `pdf_question_ingestion_service.py`      | PDF 题目提取                               |
| `question_ingestion_service.py`          | 单题解析、校验和入库                       |
| `question_ingestion_task_service.py`     | 异步导入任务状态                           |
| `question_ingestion_worker.py`           | 导入任务后台线程                           |
| `question_workspace_service.py`          | 人工审核、确认、驳回、停用和历史           |
| `question_index_v2_service.py`           | 用户隔离的题目索引 v2                      |
| `question_index_search_service.py`       | 题目索引检索                               |
| `core_learning_service.py`               | 核心学习数据 facade                        |
| `exam_learning_service.py`               | 考试轨道、节点、知识点和学习状态           |
| `system_data_service.py`                 | 登录、学习行为和系统数据聚合               |

### 6.5 路由和脚本

`backend/routers/` 中每个文件只负责 HTTP 输入输出和权限依赖，业务逻辑应留在对应 service：

| 路由文件                         | 领域                                     |
| -------------------------------- | ---------------------------------------- |
| `auth_routes.py`               | 注册、登录、验证码、重置密码、当前用户   |
| `file_routes.py`               | 上传                                     |
| `voice_routes.py`              | 语音转写                                 |
| `vl_chat_routes.py`            | 会话树、消息、聊天流和重生成             |
| `dashboard_routes.py`          | 平台首页                                 |
| `training_routes.py`           | 签到、练习、问卷、诊断、计划和报告       |
| `training_workspace_routes.py` | 训练任务、试卷和错题变式                 |
| `case_training_routes.py`      | 病例训练                                 |
| `deep_training_routes.py`      | 深度训练                                 |
| `agent_routes.py`              | 智能体上下文、计划、诊断、轨迹和编排     |
| `knowledge_routes.py`          | 知识文件、检索、知识点、证据包和题目导入 |
| `knowledge_atlas_routes.py`    | 知识图谱                                 |
| `exam_learning_routes.py`      | 考试学习路径                             |
| `question_workspace_routes.py` | 题库导入与审核工作台                     |
| `personalization_routes.py`    | 学习目标、画像、记忆、候选和趋势         |
| `learning_activity_routes.py`  | 学习任务和专注会话                       |
| `feedback_routes.py`           | 用户反馈与管理员导出                     |

`backend/scripts/` 包含：正式学习内容导入、正式题库导入、知识图谱素材导入、题目索引重建、图谱题目对账、题型修复、视频链接更新，以及演示数据 seed。脚本运行前必须先备份数据库并确认输入目录；交付包不含它们依赖的实际题库/图谱数据。

## 7. 数据库模型和数据位置

### 7.1 主要表族

| 表族           | 代表表                                                                                                                   | 用途                       |
| -------------- | ------------------------------------------------------------------------------------------------------------------------ | -------------------------- |
| 用户与认证     | `users`、`verification_codes`                                                                                        | 用户、角色、验证码         |
| 对话树         | `sessions`、`messages`                                                                                               | 会话、父消息、激活分支叶子 |
| 个性化         | `user_profiles`、`personalization_memories`、`memory_candidates`、`memory_summaries`                             | 画像和记忆                 |
| 智能体追踪     | `agent_events`                                                                                                         | 执行事件                   |
| 学习活动       | `learning_activity_records`、`training_task_records`、`learning_focus_sessions`                                    | 行为和任务                 |
| 试卷           | `paper_instances`、`paper_items`、`paper_answers`、`paper_submissions`                                           | 试卷生命周期               |
| 掌握度         | `learner_knowledge_mastery`、`knowledge_mastery_states`、`mastery_history_records`                                 | 知识掌握历史               |
| 练习与判题     | `learning_attempts`、`learning_attempt_items`、`grading_result_records`、`audit_result_records`                  | 作答、判题、审核           |
| 计划与干预     | `learning_plan_records`、`review_tasks`、`learning_intervention_records`                                           | 学习计划和复习             |
| 知识与题库     | `knowledge_points`、`question_bank_items`、`question_versions`、`question_kp_link_records`                       | 知识点和题目版本           |
| 错题与变式     | `question_attempts`、`mistake_records`、`variation_sets`、`variation_question_versions`、`variation_rubrics`   | 错题和变式                 |
| 证据资源       | `teaching_resources`、`evidence_pack_records`、`evidence_pack_items`                                               | 教学资源和证据包           |
| 核心学习新结构 | `kp`、`question`、`user_profile`、`learning_profile`、`long_term_plan`、`short_term_plan`、`learning_task` | 平台语义学习数据           |
| 系统数据       | `system_data`                                                                                                          | 统一系统指标               |

数据库新增字段或表必须通过 `backend/database.py::ensure_runtime_schema()` 做向后兼容迁移，不要只调用 `Base.metadata.create_all()`。

### 7.2 运行数据边界

| 数据           | 默认位置                                             | 是否打包 | 说明                                   |
| -------------- | ---------------------------------------------------- | -------- | -------------------------------------- |
| SQLite 数据库  | `./health_agent.db`                                | 否       | 可用 MySQL 替代                        |
| 公共知识源     | `backend/data/`                                    | 否       | 仅管理员维护，所有用户可检索           |
| 正式考试题库源 | `backend/data/official_exam_2025/`                 | 否       | 由环境变量指定                         |
| 个人知识库     | `backend/user_knowledge/{data,indexes}/{user_id}/` | 否       | 严格按用户隔离                         |
| 用户题目       | `backend/user_questions/`                          | 否       | 用户级文件数据                         |
| 上传文件       | `backend/uploads/`                                 | 否       | 元数据在`backend/file_metadata.json` |
| 公共向量库     | `backend/vdb_store/`                               | 否       | 可重新构建                             |
| 图谱导入源     | `backend/knowledge_atlas_assets/`                  | 否       | 大体积素材                             |
| 图谱运行数据   | `backend/knowledge_atlas_runtime/`                 | 否       | 由导入脚本生成                         |
| 知识运行状态   | `backend/knowledge_runtime/`                       | 否       | 运行生成                               |
| 模型权重       | `models/`                                          | 否       | 本地模式另行部署                       |

代码交接后，后端队友应自行创建这些空目录或运行导入/重建脚本。不能用真实用户数据库作为联调样例；需要联调时使用 `backend/sample_data/` 或脱敏数据。

## 8. API 约定

### 8.1 通用约定

- 前端统一请求 `/api/...`。
- Vite 把 `/api` 移除后转发到 `VITE_API_TARGET`，默认后端应为 `http://127.0.0.1:7860`。
- 除注册、登录、验证码等公开接口外，请求头使用 `Authorization: Bearer <JWT>`。
- `POST /token` 使用 `application/x-www-form-urlencoded` 的 `username`、`password`，其他 JSON 接口通常使用 `application/json`。
- 文件上传使用 `multipart/form-data`，不要手动设置 `Content-Type` 边界。
- FastAPI 运行后可在 `http://127.0.0.1:7860/docs` 查看实时 OpenAPI；合并时以源码生成的 OpenAPI 为最终准绳。

### 8.2 接口总表

| 领域         | 方法与路径                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                    |
| ------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 认证         | `POST /send-code`、`POST /register`、`POST /token`、`POST /reset-password`、`GET /users/me`                                                                                                                                                                                                                                                                                                                                                                                                         |
| 文件/语音    | `POST /upload`、`POST /voice/transcribe`、`GET /files/image/{file_id}`、`GET /video/thumbnail`                                                                                                                                                                                                                                                                                                                                                                                                        |
| 会话         | `GET/POST /sessions`、`PATCH/DELETE /sessions/{session_id}`、`GET /sessions/{session_id}/messages`、`POST /sessions/{session_id}/branch`                                                                                                                                                                                                                                                                                                                                                              |
| 智能助教     | `POST /chat/{session_id}`、`POST /chat/{session_id}/messages/{message_id}/regenerate`                                                                                                                                                                                                                                                                                                                                                                                                                     |
| 首页         | `GET /dashboard/home`、`POST /dashboard/recommendations/click`                                                                                                                                                                                                                                                                                                                                                                                                                                            |
| 基础训练     | `POST /training/checkin`、`POST /training/difficulty-feedback`、`POST /training/interventions/{id}/feedback`                                                                                                                                                                                                                                                                                                                                                                                            |
| 练习         | `GET /training/practice/next`、`POST /training/practice/grade`                                                                                                                                                                                                                                                                                                                                                                                                                                            |
| 入门/报告    | `GET /training/onboarding/group-templates`、`POST /training/onboarding/survey`、`POST /training/onboarding/dismiss`、`GET /training/onboarding/status`、`GET /training/diagnosis/summary`、`GET /training/plan/summary`、`GET /training/report`                                                                                                                                                                                                                                                 |
| 训练工作台   | `GET /training/workspace/modules`、`POST /training/workspace/tasks`、`GET /training/workspace/tasks/{task_id}`、`GET /training/workspace/mistake-variations/sources`                                                                                                                                                                                                                                                                                                                                  |
| 试卷         | `GET /training/workspace/papers/{paper_id}`、`PUT /training/workspace/papers/{paper_id}/answers`、`POST /training/workspace/papers/{paper_id}/submit`                                                                                                                                                                                                                                                                                                                                                   |
| 病例训练     | `GET /training/cases/types`、`POST /training/case-sessions`、`GET /training/case-sessions/{id}`、`POST /training/case-sessions/{id}/messages`、`POST /training/case-sessions/{id}/help`、`POST /training/case-sessions/{id}/submit`                                                                                                                                                                                                                                                               |
| 深度训练     | `POST /deep-training/knowledge/align`、`POST /deep-training/questions/select`、`POST /deep-training/diagnosis`、`POST /deep-training/cross-validate`、`GET /deep-training/demo`                                                                                                                                                                                                                                                                                                                     |
| 智能体       | `GET /agent/context/brief`、`POST /agent/plan/generate`、`GET /agent/plan/summary`、`GET /agent/diagnosis/report`、`GET /agent/trace/recent`、`POST /agent/orchestrate`、`POST /agent/cross-validate`                                                                                                                                                                                                                                                                                           |
| 知识库       | `GET /knowledge/status`、`GET /knowledge/files`、`GET /knowledge/catalog`、`POST /knowledge/upload`、`DELETE /knowledge/files/{filename}`、`POST /knowledge/rebuild`、`POST /knowledge/search_test`                                                                                                                                                                                                                                                                                             |
| 知识入库     | `POST /knowledge/points/align`、`POST /knowledge/ingest`、`POST /knowledge/evidence-pack`、`POST /knowledge/questions/ingest`、`GET /knowledge/questions`                                                                                                                                                                                                                                                                                                                                           |
| 异步题目导入 | `POST/GET /knowledge/admin/question-ingestion-tasks`、`GET /knowledge/admin/question-ingestion-tasks/{id}`、`POST /knowledge/admin/question-ingestion-tasks/{id}/retry`、`POST /knowledge/admin/question-ingestion-pdf-upload`、`POST /knowledge/admin/question-ingestion-pdf-tasks`                                                                                                                                                                                                                |
| 知识图谱     | `GET /knowledge/atlas/status`、`GET /knowledge/atlas/routes`、`GET /knowledge/atlas/nodes`、`GET /knowledge/atlas/detail/{kp_id}`、`GET /knowledge/atlas/images/{filename}`、`POST /knowledge/atlas/warm`、`GET /knowledge/atlas/resolve-context`、`GET /knowledge/atlas/questions/search`                                                                                                                                                                                                    |
| 考试学习路径 | `GET /exam-learning/tracks`、`GET /exam-learning/tracks/{track_id}/nodes`、`GET /exam-learning/tracks/{track_id}/nodes/{membership_id}`、`GET /exam-learning/tracks/{track_id}/nodes/{membership_id}/knowledge-points`、`GET /exam-learning/tracks/{track_id}/nodes/{membership_id}/learner-summary`、`POST /exam-learning/tracks/{track_id}/nodes/learner-states`、`GET /exam-learning/knowledge-points/{kp_id}/learner-state`、`GET /exam-learning/requirements/{node_id}/knowledge-points` |
| 题库工作台   | `POST/GET /question-workspace/imports`、`GET /question-workspace/imports/{job_id}`、`GET /question-workspace/imports/{job_id}/items`、`PATCH /question-workspace/items/{question_id}`、`POST /question-workspace/items/{question_id}/reject`、`POST /question-workspace/items/{question_id}/confirm`、`POST /question-workspace/questions/{question_id}/deactivate`、`POST /question-workspace/index/rebuild`、`GET /question-workspace/questions`                                          |
| 个性化       | `GET/PUT /personalization/learning-target`、`GET/PUT /personalization/profile`、`GET/PUT /personalization/learner-profile`、`GET/PUT /personalization/learner-settings`、`GET /personalization/learning-trends`、`GET /personalization/overview`、`GET /personalization/export`                                                                                                                                                                                                                 |
| 记忆         | `GET/POST /personalization/memories`、`POST /personalization/memories/upload-md`、`PUT/DELETE /personalization/memories/{id}`、`PATCH /personalization/memories/{id}/restore`、`PATCH /personalization/memories/{id}/promote`、`POST /personalization/memories/cleanup`                                                                                                                                                                                                                           |
| 候选记忆     | `GET/POST /personalization/candidates`、`PUT/DELETE /personalization/candidates/{id}`、`PATCH /personalization/candidates/{id}/ignore`、`PATCH /personalization/candidates/{id}/promote`                                                                                                                                                                                                                                                                                                              |
| 学习活动     | `POST /learning-activity/tasks`、`POST /learning-activity/tasks/{id}/complete`、`POST /learning-activity/focus-sessions`、`POST /learning-activity/focus-sessions/{id}/heartbeat`、`POST /learning-activity/focus-sessions/{id}/end`                                                                                                                                                                                                                                                                |
| 反馈管理     | `POST /feedback`、`GET /feedback/admin`、`PATCH/DELETE /feedback/admin/items/{id}`、`GET /feedback/admin/export`                                                                                                                                                                                                                                                                                                                                                                                      |

### 8.3 智能助教流式协议

`POST /chat/{session_id}` 是流式响应，不是普通 JSON。前端 `chatProtocol.js` 和 `useLangGraphStore.js` 识别以下标记：

| 标记                   | 含义                                |
| ---------------------- | ----------------------------------- |
| `<<STATUS:...>>`     | 当前状态文本                        |
| `<<EV:{...}>>`       | 智能体时间线事件                    |
| `<think>...</think>` | 执行轨迹/思考块；不能当最终正文显示 |
| `<<REFS:...>>`       | RAG 或网络引用                      |
| `<<VIDEOS:...>>`     | 视频搜索结果                        |
| `<<ROLLBACK:...>>`   | 审核失败，撤回本轮可见回答并重生成  |

修改后端流式输出时，必须同步更新前端解析测试。新增工作流节点时，必须同步更新 `stream_health_workflow_events`，否则时间线不会显示新节点。

### 8.4 会话分支

消息不是线性列表：`messages.parent_id` 指向父消息，`sessions.active_leaf_message_id` 指向当前分支叶子。重生成只能使用目标用户消息之前的历史，不能把旧助手回答作为新答案依据。前端只渲染激活叶子到根的路径，同一用户消息下有多个助手回答时显示分支切换控件。

## 9. 环境变量与安全

复制模板但不要提交真实值：

```powershell
Copy-Item .env.example .env
```

关键变量：

| 组       | 变量                                                                                                                                      |
| -------- | ----------------------------------------------------------------------------------------------------------------------------------------- |
| 数据库   | `USE_SQLITE`、`SQLITE_PATH`、`DATABASE_URL`、`MYSQL_HOST`、`MYSQL_PORT`、`MYSQL_USER`、`MYSQL_PASSWORD`、`MYSQL_DATABASE` |
| API 模型 | `LLM_MODE`、`LLM_API_KEY`、`LLM_API_BASE_URL`、`LLM_API_MODEL`                                                                    |
| 本地模型 | `PLANNER_EXECUTOR_BASE_URL`、`PLANNER_EXECUTOR_MODEL`、`MANAGER_REVIEWER_BASE_URL`、`MANAGER_REVIEWER_MODEL`                      |
| 可选能力 | `EMBEDDING_MODE`、`EMBEDDING_MODEL_ID`、`EMBEDDING_MODEL_PATH`、`VOICE_MODE`、`VISION_API_*`、`EXA_API_KEY`                   |
| 鉴权     | `SECRET_KEY`、`ADMIN_USERNAME`、`ADMIN_EMAIL`、`ADMIN_DEFAULT_PASSWORD`                                                           |
| 邮件     | `MAIL_USERNAME`、`MAIL_PASSWORD`、`MAIL_FROM`、`MAIL_PORT`、`MAIL_SERVER`、`MAIL_STARTTLS`、`MAIL_SSL_TLS`                  |
| 数据输入 | `OFFICIAL_EXAM_DATA_DIR`、`KNOWLEDGE_ATLAS_ENABLED`、`KNOWLEDGE_ATLAS_ASSET_VERSION`、`KNOWLEDGE_ATLAS_CONTRACT_PATH`             |
| 前端代理 | `VITE_API_TARGET`                                                                                                                       |

安全注意：仓库早期提交曾包含疑似真实的模型、搜索和邮件凭据。当前源码已改为只从环境变量读取，但删除当前文本不能使旧凭据恢复安全。负责人必须在对应服务控制台撤销/轮换旧凭据；建立新共享仓库时使用净化后的基线，不要直接公开旧历史。

## 10. 安装与运行

### 10.1 Windows 一键开发

```powershell
Copy-Item .env.example .env
# 填写至少 SECRET_KEY；需要模型时再填写对应 API Key
pwsh run.ps1 deps
pwsh run.ps1
```

默认后端端口 `7860`，前端端口 `5173`。停止：

```powershell
pwsh run.ps1 stop
```

### 10.2 手动启动后端

必须从仓库根运行，`APP.backend` 导入才稳定：

```powershell
python -m pip install -r requirements.txt
python -m uvicorn APP.backend.main:app --host 0.0.0.0 --port 7860
```

默认 `USE_SQLITE=true`、`LLM_MODE=api`、`EMBEDDING_MODE=disabled`、`VOICE_MODE=disabled`。不需要 GPU 即可启动基础平台；聊天需要有效的模型配置。

### 10.3 手动启动前端

```powershell
Set-Location frontend/llm
npm ci
$env:VITE_API_TARGET='http://127.0.0.1:7860'
npm run dev -- --host 0.0.0.0
```

生产构建：

```powershell
npm run build
npm run preview
```

## 11. 依赖

### 11.1 Python 直接依赖

`requirements.txt` 当前包含：`fastapi`、`uvicorn[standard]`、`python-multipart`、`pydantic`、`email-validator`、`sqlalchemy`、`pymysql`、`python-jose[cryptography]`、`passlib[argon2]`、`httpx`、`langgraph`、`fastapi-mail`、`exa-py`、`numpy`、`torch`、`faiss-cpu`、`sentence-transformers`、`faster-whisper`、`python-docx`。

其中 Torch、FAISS、sentence-transformers、faster-whisper 体积较大；关闭 Embedding 和 Voice 后运行基础 API 时仍可能被安装，但不会加载模型。团队后续可单独拆分基础/可选依赖，本次不改依赖结构。

### 11.2 前端直接依赖

运行依赖：`react`、`react-dom`、`zustand`、`lucide-react`、`three`、`react-markdown`、`react-syntax-highlighter`、`remark-gfm`、`remark-math`、`rehype-katex`、`katex`。

开发依赖：Vite、React 插件、Tailwind/PostCSS、ESLint、Vitest、Testing Library、jsdom、Playwright、axe-core 和 React 类型包。具体版本以 `frontend/llm/package-lock.json` 为准，队友应优先运行 `npm ci`，不要随意重新生成锁文件。

## 12. 测试与验证

后端完整测试：

```powershell
python -m unittest discover -s backend/tests -p "test_*.py"
```

后端快速导入和 OpenAPI 自检：

```powershell
python -c "from APP.backend.main import app; print('routes', len(app.routes))"
```

前端：

```powershell
Set-Location frontend/llm
npm run test:unit
npm run build
npm run test:e2e
```

`npm run lint` 可能仍受到历史备份目录或旧 hook 警告影响；交付验收至少要求新增测试、单元测试和生产构建通过。不要用已有全局 lint 问题掩盖本次新增错误。

## 13. 前后端合并步骤

1. 后端队友先启动 API，打开 `/docs`，导出或核对最新 OpenAPI。
2. 前端队友在 `frontend/llm/.env.local` 或启动命令中设置 `VITE_API_TARGET`；该文件不要提交。
3. 先联调 `POST /token` 与 `GET /users/me`，确认 Token、角色和 401 行为。
4. 联调 `GET /dashboard/home`，确认登录后首页可独立加载；某个推荐/报告不可用时不能拖垮平台壳。
5. 联调训练、知识和画像 JSON 接口，检查字段名、空数组、404/422 和权限错误。
6. 最后联调上传、聊天流、分支切换、重生成和流式标记；这部分协议最容易因普通 JSON 客户端而损坏。
7. 任何接口变更必须同一分支更新：后端路由/Schema、前端 API 调用、测试和本 README/OpenAPI 说明。
8. 合并前运行第 12 节回归，并使用无真实用户数据的 SQLite 临时库。

后端新增业务能力应采用 `router -> service -> database/repository`；前端新增页面应通过 `pageIntent`、显式 props、API helper 或轻量 store 接入。禁止模块通过兄弟组件内部 DOM、隐式全局变量或可变单例耦合。

## 14. Git 协作与增量同步

### 14.1 推荐：一个私有 monorepo

前后端放在同一个私有仓库，前端队友主要维护 `frontend/llm/`，后端队友主要维护 `backend/`、`APP/`。接口文档和测试跟随功能提交。不要建立两个长期分叉的仓库，否则跨端接口提交难以原子合并。

首次克隆：

```powershell
git clone <private-repository-url>
Set-Location <repository-directory>
git switch work/platform-restructure
```

### 14.2 每个功能使用短期分支

```powershell
git switch work/platform-restructure
git pull --ff-only
git switch -c feat/knowledge-atlas-filter

# 修改和验证
git add <明确的文件或目录>
git commit -m "feat: add knowledge atlas filter"
git push -u origin feat/knowledge-atlas-filter
```

在 GitHub/Gitee 创建 Pull Request，审查后合并。不要长期共用一个“某某同学分支”，也不要直接在共享主分支上堆未审查代码。

### 14.3 获取队友更新

空闲时：

```powershell
git switch work/platform-restructure
git pull --ff-only
```

正在自己的分支开发时：

```powershell
git fetch origin
git rebase origin/work/platform-restructure
```

若 rebase 冲突，Git 会列出文件。解决冲突后：

```powershell
git add <已解决文件>
git rebase --continue
```

不确定时不要执行 `reset --hard` 或强制推送，先保留冲突现场并讨论接口语义。

### 14.4 只同步某个功能

发送方只需给出提交号：

```text
请合并提交 a12bc34：知识图谱筛选和接口适配。
```

接收方：

```powershell
git fetch origin
git cherry-pick a12bc34
```

Git 会自动携带这个提交新增、修改、删除的全部文件，因此不需要人工枚举文件。

### 14.5 离线同步

没有远程仓库时可以发送补丁：

```powershell
git format-patch <上次双方共有的提交>..HEAD -o patches
```

接收方按编号执行 `git am`。补丁同样记录增删改、作者和提交说明，比手工复制文件安全；但长期协作仍应使用私有远程仓库。

### 14.6 提交粒度

推荐按可独立回滚的功能提交，而不是简单按“前端/后端”一次塞入所有文件：

```text
feat(backend): add knowledge atlas endpoints
feat(frontend): connect knowledge atlas workspace
test: cover knowledge atlas integration
docs: document knowledge atlas contract
```

接口跨端变化可以在同一个功能分支包含多个提交。后端队友可 cherry-pick 后端提交，集成分支可合并全部提交。

## 15. 打包边界和交付检查

前端包包含：`frontend/llm/src`、`public`、`e2e`、HTML、npm 清单/锁文件、Vite/Tailwind/PostCSS/ESLint/Playwright 配置，以及本 README。

后端包包含：`APP/__init__.py`、`APP/env_loader.py`、`APP/intent_reply_template.py`、物理复制到 `APP/backend/` 的 Python 源码、路由、测试、脚本、`official_exam_repository`、`knowledge_atlas_contracts`、根意图模板、`requirements.txt`、启动脚本、`.env.example`、本 README 和技术文档。

两个包都排除：

- `.env`、`.env.local` 和任何真实密钥；
- `*.db`、`*.sqlite*`；
- `node_modules`、`dist`、Python 虚拟环境和缓存；
- `test-results`、`playwright-report`；
- `uploads`、`file_metadata.json`、`user_knowledge`、`user_questions`；
- `data`、`sample_data`、正式题库原始数据；
- `vdb_store`、`knowledge_atlas_assets`、`knowledge_atlas_runtime`、`knowledge_runtime`；
- 模型权重、设计备份、本地工具缓存和历史前端备份。

收到压缩包后先验证 `SHA256SUMS.txt`，再解压到新目录；不要直接覆盖队友已有代码。正确做法是在独立 Git 分支解压/导入，查看差异，运行测试，然后合并。

## 16. 角色与默认账号

角色为 `user`、`admin`。启动钩子会确保默认管理员存在，用户名、邮箱和密码由 `ADMIN_*` 环境变量控制。示例模板中的默认密码只用于本地首次启动，生产环境必须覆盖并立即修改。系统保留用户名为 `admin`、`root`、`system`。

## 17. 当前限制

- GRPO 强化学习仍只有 `RL.md` 设计稿，尚未落地。
- 底层数据库仍混有健康语义；当前阶段通过 service/API/前端文案映射为培训语义，不做全量物理改名。
- `ChatInterface.jsx` 较大；新增独立能力优先拆小组件和协议 helper。
- 图谱、题库和 RAG 在无实际数据时应返回局部空态，不能导致首页或智能助教壳层崩溃。
- 当前 CORS 为开发期宽松配置，生产部署必须限制可信来源。

## 18. 交接负责人检查单

- [ ] 已轮换早期历史中出现过的模型、搜索、邮箱和数据库凭据。
- [ ] `.env`、数据库和所有运行数据未进入 Git/ZIP。
- [ ] 后端导入/OpenAPI 自检通过。
- [ ] 前端单元测试和生产构建通过。
- [ ] 压缩包 SHA-256 与接收端一致。
- [ ] 两位队友都从同一个私有远程仓库协作。
- [ ] 每个接口变更都有 Schema、前端调用、测试和文档。
- [ ] 合并时没有把 `APP/backend` 和 `backend` 当两套独立实现。

如需开源，请先补充许可证文件并完成历史密钥清理；在此之前仓库应保持私有。
