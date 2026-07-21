# Competition App 个性化复习卡基础框架设计

## 1. 目标

在 `competition_app` 中建设一个可运行、可观察、可扩展的模块化单体框架，先完整跑通 `personalized_review_card` 场景，以便验证六智能体、知识检索、复习调度、审核发布、MySQL 写回和训练快照导出的数据流。

第一版不是完整产品平台。它优先解决统一契约、统一编排、统一工具运行时、统一写回和统一 Trace，后续错题批改、组卷、学习路径和模拟病患均在该底座上扩展。

## 2. 已确认约束

- 第一条验收主线为个性化复习卡。
- 运行状态保存到 MySQL，同时导出 JSON 全流程快照。
- 启动时自动创建 `competition_app` 数据库，并执行版本化 migration。
- 同时提供 CLI 和 FastAPI，两者共用同一应用用例和 Orchestrator。
- 所有智能体首版统一调用 OpenAI 兼容聊天接口：
  - Base URL：`https://llm-1nvjq1o5rj1bf5yi.cn-beijing.maas.aliyuncs.com/compatible-mode/v1`
  - Model：`qwen-plus`
  - API Key 环境变量：`DASHSCOPE_API_KEY`
- 向量模型调用硅基流动 OpenAI 兼容接口：
  - Base URL：`https://api.siliconflow.cn/v1`
  - Model：`Qwen/Qwen3-Embedding-4B`
  - API Key 环境变量：`SILICONFLOW_API_KEY`
- MySQL 默认连接：`localhost:3306`、用户 `root`、数据库 `competition_app`。
- MySQL 密码只从 `MYSQL_PASSWORD` 读取，不写入代码、示例配置、日志或快照。
- 支持 `stub` 和 `live` 两种运行模式。默认测试使用确定性 Stub，真实模式调用上述模型。
- 使用现有 Conda `torch` 环境，不创建新的 Python 环境。

## 3. 架构选择

采用独立的模块化单体，在 `competition_app` 内建立统一运行底座，通过 Adapter 复用 `competition` 中已有题库、知识点、Bridge、RAG 和复习公式。

不直接扩展现有 `复习组件/pipeline.py`，因为该流程固定串行、Planner 不控制执行、数据主要来自 fixture，继续叠加会阻碍后续场景扩展。也不在第一版引入微服务、消息队列和前端，以避免基础设施掩盖业务数据流。

目录职责：

```text
competition_app/
├── api/                  # FastAPI 路由和请求/响应映射
├── cli/                  # CLI 命令
├── application/          # 个性化复习卡用例
├── contracts/            # Pydantic 权威数据契约
├── runtime/              # Orchestrator、注册表、执行状态和 Trace
├── agents/               # 六智能体适配器
├── llm/                  # 阿里云聊天客户端和确定性 Stub
├── embeddings/           # 硅基流动向量客户端和 Stub
├── tools/                # Tool Runtime 与现有能力适配器
├── review/               # 确定性复习算法和调度
├── services/             # 正式业务状态服务
├── repositories/         # MySQL Repository
├── migrations/           # 版本化 SQL
├── prompts/              # 六智能体 Prompt 模板
├── snapshots/            # 运行时生成的脱敏 JSON 快照
├── tests/                # 单元、集成和可选 Live Smoke 测试
├── config.py
├── main.py
└── README.md
```

## 4. 端到端流程

示例请求：

```json
{
  "learner_id": "learner_001",
  "topic": "四君子汤",
  "available_minutes": 15
}
```

执行步骤：

1. CLI 或 FastAPI 创建请求。
2. Planner 调用 `qwen-plus` 识别主题、风险和缺失信息，再由后端模板生成合法 `ExecutionPlan`。
3. Orchestrator 校验 DAG、Agent、工具权限和步骤依赖。
4. 记忆管理 Agent 与知识库管理 Agent 并行执行。
5. 记忆管理 Agent 压缩当前会话与本次执行已授权的历史上下文，生成运行态 `ConversationContextSummary`，并结合稳定画像和已确认长期记忆生成 `LearnerContextBrief`。
6. 知识库管理 Agent 对齐正式 `kp_id`，读取 Bridge 和教材切片，必要时用硅基流动向量补召回，生成 `EvidencePack`。
7. 学情诊断 Agent 根据上下文、复习状态和证据生成 `DiagnosisResult` 与 `DailyReviewPolicy`。
8. 确定性 Review Scheduler 创建 `ReviewTask`。
9. 专家 Agent 根据 Evidence、诊断和任务生成 `ResourceDraft`。
10. 审核裁判 Agent 输出 `pass/revise/reject/needs_human_review`。
11. `revise` 最多退回专家一次；`pass` 后才发布资源和绑定任务。
12. Agent 只生成 `WritebackIntent`，`WritebackExecutor` 校验前置条件、幂等键和版本后写入 MySQL。
13. Trace Service 保存步骤、产物、工具调用、审核历史和写回结果。
14. Snapshot Exporter 导出脱敏 JSON，供流程分析和训练数据装配。

## 5. Planner 与 Orchestrator

### 5.1 Planner

Planner 是受约束的智能体，不自由生成任意流程。它调用 `qwen-plus` 完成任务识别、参数提取、澄清判断和风险标记，最终步骤由后端模板注册表产生。

`personalized_review_card` 模板固定包含：

```text
memory_context
knowledge_evidence
learning_diagnosis
review_schedule
resource_generation
resource_audit
resource_binding
writeback
snapshot_export
```

Planner 不生成正式学习/复习计划，不计算掌握度，不生成资源正文，不写数据库。

### 5.2 Orchestrator

第一版 Orchestrator 支持：

- Plan Schema 与 DAG 校验；
- 拓扑执行和无依赖步骤并行；
- Agent Registry 与 Tool Registry 权限检查；
- 单步骤超时；
- 最多一次基础重试；
- `revise` 最多一次专家重生成；
- `needs_human_review` 暂停和恢复；
- 产物引用、工具调用和写回 Trace；
- 失败时保留已完成步骤，不发布未审核资源。

步骤状态限定为：`pending`、`running`、`success`、`failed`、`retrying`、`waiting_human_review`、`skipped`。

## 6. 六智能体边界

- **Planner**：输入用户请求、任务模板和可用能力；输出 `PlannerDecision` 与 `ExecutionPlan`；不得自由编排非法步骤、写库或生成资源。
- **记忆管理**：输入当前会话、已授权历史上下文、画像、确认偏好、相关记忆和临时约束；输出 `ConversationContextSummary`、`LearnerContextBrief` 和可选长期记忆候选建议；不得将运行态摘要自动写入画像，也不得修改掌握度、复习状态或公共知识。
- **学情诊断**：输入上下文、复习状态和 Evidence；输出 `DiagnosisResult` 与 `DailyReviewPolicy`；不得生成资源正文或直接落正式计划。
- **知识库管理**：输入主题、知识目录、Bridge 和检索结果；输出 `EvidencePack`；不得判定个人最终掌握度或写题库主表。
- **专家**：输入 Context、Evidence、Diagnosis 和 ReviewTask；输出 `ResourceDraft`；不得发布资源、决定复习时间或绕过审核。
- **审核裁判**：输入 ResourceDraft、Evidence 和 Diagnosis；输出 `AuditResult`；不得生成主要正文或直接修改草稿。

复习调度器是后端确定性组件，不是第七个智能体。

### 6.1 记忆管理与上下文压缩

记忆管理 Agent 同时承担两类相关但必须隔离的职责：

1. **运行态上下文压缩**：把当前会话、已完成步骤摘要和本次任务相关历史压缩为 `ConversationContextSummary`，供后续 Agent 在有限上下文窗口内使用；
2. **稳定记忆检索与候选沉淀**：读取用户已确认的长期事实、偏好和约束，形成 `LearnerContextBrief`；当发现可能长期有用的新信息时，只生成 `LongTermMemoryCandidate`，等待用户确认或服务规则批准。

`ConversationContextSummary` 是会话/执行级临时产物，不属于用户画像，不得因为被压缩就自动转为长期记忆。它必须保留输入消息或产物引用、覆盖时间范围、被保留事实、未解决问题、临时约束和压缩版本，避免摘要无法追溯。

首版压缩触发条件采用确定性策略：请求显式要求压缩，或估算上下文超过配置阈值。压缩结果由 LLM 生成，但后端校验引用、角色、时间范围和必需字段；后续 Agent 读取“原始当前请求 + 压缩摘要 + 必要原文引用”，不能只依赖无引用摘要作出高风险事实判断。

长期记忆写入遵循：

```text
运行态观察
→ LongTermMemoryCandidate
→ 冲突检查与来源校验
→ 用户确认或明确服务规则批准
→ learner_memories 新版本
```

当前任务、即时计划、未确认推断、模型归因和会话摘要不得直接写入长期画像。

## 7. 模型与运行模式

### 7.1 Live 模式

聊天客户端和向量客户端使用 OpenAI 兼容 HTTP 协议，均设置超时、有限重试和结构化错误。聊天输出要求 JSON，解析失败只允许一次 JSON 修复，不将未经验证文本保存为正式 Agent 产物。

### 7.2 Stub 模式

Stub 按 Agent 角色和任务返回确定性且契约完整的结构化数据，用于：

- 单元和集成测试；
- 离线调试；
- 不消耗 API 额度的流程演示；
- 数据结构与快照回归。

Stub 和 Live 必须实现相同接口并产生相同契约类型。配置项为 `COMPETITION_APP_MODE=stub|live`。

## 8. 数据契约

契约遵循 `competition/system_data_design.md` 的四类数据分离原则：事实、确定性计算状态、智能体建议、正式业务状态。

第一版运行对象：

- `UserRequest`
- `ExecutionPlan`
- `ExecutionStep`
- `ExecutionRun`
- `StepTrace`
- `AgentEnvelope`
- `ArtifactReference`
- `ToolCallRecord`
- `WritebackIntent`

第一版智能体产物：

- `PlannerDecision`
- `ConversationContextSummary`
- `LongTermMemoryCandidate`
- `LearnerContextBrief`
- `EvidencePack`
- `DiagnosisResult`
- `DailyReviewPolicy`
- `ResourceDraft`
- `AuditResult`

第一版复习和资源对象：

- `LearnerKPReviewState`
- `ReviewDueCandidate`
- `ReviewTask`
- `ReviewResourceBinding`
- `ReviewAttempt`
- `ResourceVersion`
- `ResourceClaim`
- `ResourceCitation`

所有 Agent 产物通过 Envelope 保存 `artifact_id`、`case_id`、`trace_id`、`execution_id`、`producer`、`input_refs`、`version`、`schema_version`、`confidence`、`status` 和 `created_at`。

## 9. MySQL 与 Migration

启动流程：

```text
连接 MySQL Server
→ 若 competition_app 不存在则创建
→ 创建 schema_migrations
→ 按版本执行 SQL
→ 记录已执行版本和校验和
```

第一版表：

- 用户状态：`learners`、`learner_profiles`、`learner_memories`、`memory_candidates`、`knowledge_mastery_states`、`learner_kp_review_states`；
- 运行态上下文：`conversation_sessions`、`conversation_messages`、`context_summaries`；
- 运行时：`execution_runs`、`execution_steps`、`artifacts`、`tool_calls`、`writeback_intents`；
- 知识引用：`knowledge_catalogs`、`knowledge_point_refs`、`evidence_packs`；
- 复习和资源：`review_tasks`、`resource_versions`、`audit_results`、`review_resource_bindings`；
- 合成导出：`snapshot_exports`、`training_case_exports`。

第一版不把现有约九万题和七万知识点整体复制到 MySQL，只注册现有交付包目录、版本和引用。Repository 使用参数化 SQL、事务和显式提交；写回依赖唯一幂等键。

测试使用独立测试数据库或按运行生成的测试库名，禁止清空开发数据库。

## 10. 知识检索

知识检索顺序：

```text
主题
→ 正式知识点文本/BM25 对齐 kp_id
→ strict Bridge 回查教材切片
→ 题目 Bridge 与考纲关系补充
→ 硅基流动向量模型语义补召回
→ 去重、权威分层和冲突标记
→ 知识库管理 Agent 压缩为 EvidencePack
```

证据优先级：正式知识点和 strict Bridge、教材切片、题库 Bridge、向量补召回。`similarity` 层只能作为弱证据，不能单独支撑高风险专业声明。

现有交付包和检索模块通过 Adapter 接入，不修改其核心数据文件。

## 11. 复习计算

复习状态唯一主体是 `learner_id + kp_id`。

保持率：

$$
R(t)=e^{-t/S}
$$

其中 `t = now - last_review_at`。`next_review_at` 只用于到期判断，不能作为保持率时间原点。

阶段间隔为：即时回忆、20 分钟、1 小时、9 小时、1 天、2 天、6 天、31 天。

状态不存在时创建即时回忆状态；到期时创建系统推荐任务；未到期但用户主动请求时创建 `user_requested` 任务；缺失或损坏时间字段进入初始化或修复状态，不能静默视为普通到期。

学情诊断可调整任务容量、资源形式和目标难度，但不能改写确定性复习时钟。

## 12. 审核、发布与写回

- `pass`：发布 `ResourceVersion` 并创建 `ReviewResourceBinding`；
- `revise`：携带审核意见退回专家，最多一次；
- `reject`：终止，不发布；
- `needs_human_review`：执行暂停，等待人工结论。

审核至少验证专业声明的 Evidence 引用、引用存在性、证据外内容、难度适配、教学用途声明、真实诊疗风险和复习卡结构。

Agent 不直接写库。每个写操作生成 `WritebackIntent`，由 `WritebackExecutor` 检查目标服务、审核前置条件、幂等键和目标版本后执行。

## 13. JSON 快照和训练数据

每次执行导出到：

```text
competition_app/snapshots/<case_id>/<execution_id>.json
```

快照包含请求、计划、执行前状态引用、会话消息引用、上下文压缩摘要、步骤、Agent 产物、工具调用、写回意图、审核历史、执行后状态引用、最终资源和质量标签。

快照必须移除 API Key、MySQL 密码、Authorization、数据库连接串和未授权个人信息。

快照后续可以转换成单 Agent SFT、多 Agent 工具轨迹、审核修订对、Planner 计划样本、复习状态转移样本和端到端训练案例。第一版只保证快照完整和可追溯，不直接修改现有 `tcm_synth`、`tcm_training` 发布逻辑。

## 14. CLI 与 FastAPI

CLI 命令：

- `init-db`
- `seed-demo`
- `run-review-card`
- `show-run`
- `export-snapshot`
- `serve`

FastAPI 接口：

- `POST /api/v1/review-cards`
- `GET /api/v1/executions/{execution_id}`
- `GET /api/v1/executions/{execution_id}/trace`
- `GET /api/v1/executions/{execution_id}/snapshot`
- `POST /api/v1/executions/{execution_id}/human-review`
- `GET /health`

两种入口只负责协议转换，共同调用 `PersonalizedReviewCardUseCase`。

## 15. 错误处理与安全

- 缺少必要环境变量时，Live 模式启动失败并明确指出变量名，不回显变量值。
- 外部模型超时或暂时错误按有限策略重试；耗尽后步骤失败并保留 Trace。
- Agent JSON 不符合契约时执行一次修复；仍失败则步骤失败。
- 上下文压缩必须保留来源引用；引用不存在、越权读取或时间范围不一致时不得发布压缩摘要。
- 数据库 migration 使用互斥和事务，校验和变化视为错误。
- 未审核资源不得发布或绑定。
- Snapshot 导出采用字段级脱敏和密钥模式扫描。
- 所有中医药内容标记教学用途，不输出真实诊疗结论。

## 16. 测试与验收

### 16.1 单元测试

- 配置加载和秘密脱敏；
- 上下文压缩触发、来源引用、临时摘要与长期记忆隔离；
- Plan Schema、DAG 和权限校验；
- Agent/Tool Registry；
- 保持率、到期判断和状态初始化；
- 审核分支和最多一次 revise；
- Writeback 幂等性；
- JSON 快照完整性和脱敏。

### 16.2 集成测试

使用 Stub 和独立 MySQL 测试库跑通：

```text
请求
→ 六智能体
→ 知识证据
→ 复习调度
→ 审核
→ MySQL 写回
→ JSON 快照
```

验收条件：

1. 执行记录包含全部预期步骤和依赖；
2. 六智能体均产生可验证 Envelope；
3. 记忆管理 Agent 产生可追溯的上下文摘要，且摘要不会自动进入长期画像；
4. 复习任务以 `learner_id + kp_id` 为主体；
5. `pass` 后生成资源版本和绑定，其他审核状态不发布；
6. 重复执行同一写回幂等键不会产生重复业务记录；
7. MySQL 状态和 JSON 快照引用一致；
8. 快照不含任何密钥或密码。

### 16.3 Live Smoke Test

Live 测试不属于默认测试套件。配置两个 API Key 后分别验证一次聊天接口、一次 embedding 接口和一次完整四君子汤复习卡流程，并保存脱敏 Trace。

## 17. 第一版非目标

第一版不实现完整错题批改、智能组卷、正式学习路径版本、L3 行为采集、主动通知、用户资料在线处理、模拟病患、多模态批改、前端、消息队列、微服务和 MARCH 强化学习。

这些能力只预留通过新场景模板、Agent 任务、工具和 Service 扩展的接口，不提前实现空壳业务逻辑。

## 18. 后续扩展顺序

个性化复习卡稳定后，依次扩展：

1. 错题批改与补救；
2. 个性化知识讲解；
3. 智能组卷；
4. 学习路径与行为干预；
5. 用户知识资料异步导入；
6. 状态化 AI 模拟病患；
7. 快照到 `tcm_synth/tcm_training` 的正式装配适配器。
