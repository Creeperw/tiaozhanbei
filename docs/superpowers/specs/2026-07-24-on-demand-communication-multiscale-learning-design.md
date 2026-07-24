# 按需通信、局部纠错与多时间尺度学习状态设计

日期：2026-07-24  
状态：已完成架构讨论，等待书面设计确认  
适用范围：`tiaozhanbei` 后端智能体框架、学习治理接口及现有前端展示

## 1. 目标

本次建设补齐两个核心部件：

1. 基于接收方职责与当前认知差距的按需通信，以及审核失败后的通用局部纠错。
2. 面向长期、短期、当日和实时行为的多时间尺度学习状态，以及先执行安全约束、再进行透明评分的路径候选生成。

最终结果必须复用现有六类业务智能体、LangGraph、MySQL/SQLite 兼容持久化、学习规划、学情监控和前端页面。不得创建一套与现有系统并行的规划或行为数据库。

## 2. 明确不在本次范围内

- 不实现“基于执行轨迹评价的弹性多智能体组织”。
- 不增加 Critic Agent。
- 不根据历史轨迹自动改变 Agent 拓扑。
- 不实现权重在线学习、强化学习或自动调参。
- 不在本次建设对照实验和消融实验平台。
- 不重写前端信息架构；只提供稳定接口并在现有页面增加必要的验证入口。

## 3. 全局约束

- 在线测试通过是最终验收标准。
- Live 验证只能在已经启动的前端运行面板中点击 Execute，不从 WSL 命令行执行 Live pytest。
- 自动化测试必须包含常规案例、边界案例、故障案例和极端案例。
- 后端接口必须版本化、向后兼容、字段含义明确，允许未来大幅重构前端而无需重新推断业务语义。
- Agent 面向用户的内容继续以自然语言为主；结构化数据仅用于执行、校验、持久化和前端渲染。
- 任何路径候选都必须先通过前置条件、时间、复习优先级、可信来源和低数据保护约束。
- 模型不能制造缺失的掌握度、难度、用时、置信度或证据。
- 自动局部修复最多执行一轮；第二次仍不通过时失败关闭或转人工。
- 局部修复不能绕过 Audit，也不能覆盖未受影响节点的有效输出。
- 用户数据必须按登录用户隔离；执行轨迹接口不能返回其他用户的通信内容或修复记录。

## 4. 总体架构

系统新增两个受控层，不增加新的业务智能体角色：

```text
用户请求
  -> Planner 选择最小业务 Agent 集合
  -> CognitiveGapAnalyzer 计算目标 Agent 缺失信息
  -> AgentHandoffBuilder 生成最小通信包
  -> LangGraph 执行业务节点
  -> Audit
       -> pass：正常结束
       -> revise：LocalRepairController 生成最小修复链
                    -> 重跑必要节点
                    -> Audit 再审一次
       -> reject / needs_human_review：失败关闭或人工处理

持久化学习行为、计划、掌握度、复习状态
  -> MultiScaleLearningStateBuilder
  -> HardConstraintGate
  -> TransparentPathScorer
  -> 合法路径候选
  -> Planner / Diagnosis 受控选择
  -> 正式计划与推荐依据
```

## 5. 部件一：认知差距驱动的按需通信

### 5.1 统一通信契约

新增 `competition_app/contracts/agent_communication.py`，提供以下稳定契约。

`EvidenceReference`

- `evidence_id`
- `source_type`
- `source_id`
- `claim`
- `quality_label`
- `retrieved_at`

`ConfirmedFact`

- `fact_id`
- `category`
- `content`
- `evidence_refs`
- `source_step_id`
- `freshness`

`UncertaintyItem`

- `uncertainty_id`
- `category`
- `description`
- `blocking`
- `resolution_action`

`DownstreamNeed`

- `field`
- `reason`
- `required`
- `accepted_source_types`

`AgentHandoffBundle`

- `schema_version`
- `handoff_id`
- `trace_id`
- `execution_id`
- `source_steps`
- `target_agent`
- `purpose`
- `confirmed_facts`
- `evidence`
- `uncertainties`
- `task_constraints`
- `downstream_needs`
- `omitted_categories`
- `generated_at`

通信包不包含系统密钥、其他用户数据、无关完整对话、未授权画像字段或与目标 Agent 无关的所有上游原始输出。

### 5.2 Agent 需求目录

新增 `competition_app/runtime/agent_communication.py`，用受版本控制的需求目录描述每类 Agent 的必要信息。

示例：

- Knowledge：知识查询、题目范围、资源偏好、可信来源约束。
- Diagnosis：目标、学习基础、时间、行为证据、当前计划、多时间尺度状态摘要。
- LearningPlanService：已校验的规划提案、目标层级、父计划引用、时间预算。
- ReviewScheduler：已完成并通过批改的知识点、掌握状态、复习状态。
- Expert：正式任务、知识证据、资源形式、时间和难度约束。
- Audit：待审核产物、证据映射、用户约束、生成步骤和审查规则。

需求目录负责定义“目标 Agent 应该知道什么”，不是新的路由器，也不决定是否调用该 Agent。

### 5.3 认知差距计算

`CognitiveGapAnalyzer.analyze(...)` 接收：

- 当前执行步骤。
- 目标 Agent。
- 根上下文中允许访问的字段。
- 直接依赖节点输出。
- 已经形成的事实和证据索引。
- Agent 需求目录。

输出：

- 已满足需求。
- 缺失但可从现有依赖补充的需求。
- 缺失且会阻断执行的需求。
- 应传递的最小事实和证据。
- 应省略的无关信息类别。

第一版使用确定性字段规则和已有结构化契约，不调用大模型计算差距。这样可以稳定测试、审计和复现。模型仍负责业务语义生成，不负责决定自己能否绕过必需数据。

### 5.4 兼容迁移

现有 Agent 继续获得 `dependency_outputs`，同时新增：

- `agent_handoff`
- `cognitive_gap`

新代码优先读取 `agent_handoff`；没有新字段时回退现有 `dependency_outputs`。迁移完成前不删除旧字段，避免破坏现有测试和前端实时执行链路。

### 5.5 通信轨迹

新增运行事件：

- `handoff_prepared`
- `handoff_blocked`
- `handoff_consumed`

对前端和日志只公开摘要：

- 目标 Agent。
- 事实数量。
- 证据数量。
- 阻断项数量。
- 省略类别。

原始通信内容保存在当前用户的工作流运行状态中，不通过公共实时事件完整广播。

## 6. 部件一：通用局部纠错

### 6.1 修复契约

`RepairIssue`

- `issue_id`
- `issue_type`
- `message`
- `claim_ref`
- `evidence_ref`
- `owner_step_id`
- `affected_step_ids`
- `severity`

`RepairAction`

- `action_id`
- `action_type`
- `step_id`
- `reason`
- `depends_on`
- `preserve_outputs`

`LocalRepairPlan`

- `schema_version`
- `repair_id`
- `execution_id`
- `trigger_step_id`
- `issues`
- `actions`
- `max_rounds`
- `requires_reaudit`
- `status`

### 6.2 问题定位

Audit 输出在保持现有自然语言 findings 的同时，增加可选结构化 findings。对于旧 Audit 输出，`LocalRepairController` 使用确定性分类将问题映射到以下类别：

- `missing_evidence`
- `conflicting_evidence`
- `learner_mismatch`
- `route_or_prerequisite_error`
- `content_quality`
- `paper_blueprint_mismatch`
- `unresolved`

无法确定责任节点时不得猜测，生成 `unresolved` 并转人工或失败关闭。

### 6.3 修复白名单

| 问题类别 | 允许的最小修复链 |
|---|---|
| 缺失/冲突证据 | Knowledge → 受影响生成节点 → Audit |
| 学情适配错误 | Diagnosis → 受影响的计划或生成节点 → Audit |
| 路线/前置条件错误 | Route Resolver → Diagnosis → 受影响节点 → Audit |
| 内容质量问题 | Expert 或 Paper Assembly → Audit |
| 蓝图偏离 | Paper Blueprint → Knowledge → Paper Assembly → Audit |
| 无法定位 | 不自动修复 |

修复控制器只能选择当前任务允许的节点，不能引入 Planner 未授权的业务能力。修复轮次固定为一轮。

### 6.4 LangGraph 接入

现有 DAG 仍是主图。Audit 返回 `revise` 时进入受控修复子图：

1. `repair_plan` 生成最小修复计划。
2. `repair_execute` 按修复计划运行白名单节点。
3. `repair_audit` 再审核一次。
4. 合并修复节点输出，保留所有未受影响输出。

修复过程中出现中断追问时继续使用当前 LangGraph thread 和检查点。页面刷新或进程恢复后不得重复已经成功的修复节点。

### 6.5 持久化

通信摘要、认知差距和修复记录写入现有 `workflow_run_states.payload_json` 和最终执行结果 metadata。第一版不新增数据库表。

持久化内容包括：

- `communication_trace`
- `repair_trace`
- `repair_round`
- `preserved_step_ids`
- `rerun_step_ids`
- `final_audit_decision`

这样可以复用当前 SQL/内存双实现，并避免为派生执行数据引入第二套迁移。

## 7. 部件二：多时间尺度学习状态

### 7.1 状态契约

新增稳定响应 `MultiScaleLearningState`：

- `schema_version`
- `state_id`
- `learner_id`
- `generated_at`
- `macro`
- `meso`
- `micro`
- `data_quality`
- `hard_constraints`
- `source_refs`
- `state_digest`

宏观层 `macro`：

- 资格考试目标。
- 已批准经典路线。
- 当前长期阶段。
- 阶段教材。
- 前置课程。
- 阶段验收证据。

中观层 `meso`：

- 当前短期计划。
- 当前当日任务。
- 计划知识点。
- 薄弱知识点。
- 到期复习知识点。
- 近 7/30 日任务完成率和规律性。

微观层 `micro`：

- 最近答题记录。
- 正确率。
- 响应用时。
- 已确认错因。
- 最近专注时长。
- 当前任务负荷。
- 最近使用的题目和资源。

数据质量 `data_quality`：

- 数据覆盖度。
- 样本量。
- 数据新鲜度。
- 可用指标。
- 不可用指标及原因。
- 是否允许谨慎调整路径。

### 7.2 构建与持久化策略

状态由现有持久化数据派生：

- 长期、短期和当日计划。
- 用户画像和注册学情调查。
- 学习行为记录。
- 答题记录。
- 知识点掌握度。
- 复习状态。
- 错题记录。
- 专注计时。
- 默认教材路线和前置条件。

不复制这些源数据。工作流运行记录保存实际使用的状态摘要、`state_digest` 和来源引用；正式规划继续保存 recommendation trace。这样既能复核一次决策，又不会产生两套相互漂移的学习状态。

### 7.3 安全硬约束

候选路径在评分前必须依次检查：

1. 路线与资格目标一致。
2. 当前阶段前置条件已满足，或候选明确补齐该前置条件。
3. 当日任务总时长不超过用户可用时间。
4. 已到期复习优先进入候选集合。
5. 中医事实性内容具有可信教材、正式题库或已审核证据。
6. 数据覆盖不足时不得生成高难度跳级、长期路线重写或临床高风险判断。
7. 候选知识点和教材必须能够映射到已批准阶段。
8. 不得用占位教材、占位知识点或系统内部 ID 代替展示名称。

未通过硬约束的候选可以返回给调试/解释界面，但必须标记 `eligible=false`，且不能提交给 Diagnosis 作为可选路径。

### 7.4 透明候选评分

通过硬约束后计算：

```text
total_score =
  0.30 * learning_gain
  + 0.20 * retention_benefit
  + 0.20 * knowledge_coverage
  + 0.10 * time_fit
  + 0.10 * difficulty_fit
  + 0.10 * autonomy_support
  - 0.10 * repetition_penalty
  - 0.15 * uncertainty_risk
```

所有分项归一化到 `[0, 1]`，总分最终限制到 `[0, 1]`。

分项规则：

- `learning_gain`：薄弱知识点差距、当前阶段目标和待完成验收证据的组合；没有预测模型时使用公开规则值。
- `retention_benefit`：到期程度和预计保持率；没有复习状态时标记不可用。
- `knowledge_coverage`：候选知识点与薄弱点、计划点和到期点的加权覆盖。
- `time_fit`：估算时长不超过预算时为 1，超出预算的候选已在硬约束阶段淘汰。
- `difficulty_fit`：题目/资源难度与当前掌握度、近期正确率和用户偏好的差距。
- `autonomy_support`：用户明确选择、资源偏好和已确认反馈的满足程度；没有反馈时使用中性值并标注来源。
- `repetition_penalty`：近期重复题目、资源和知识点所占比例。
- `uncertainty_risk`：数据缺失、样本不足、证据过期和来源不足的组合。

缺失分项不伪造为满分。响应同时返回 `available=false` 和缺失原因；计算时对可用正向分项重新归一化，风险项独立扣减。

### 7.5 规划接入

Planner 只读取多时间尺度状态摘要，用于判断任务层级和最小 Agent 集合。

Diagnosis 获得：

- 合法路径候选。
- 每个候选的评分分解。
- 被阻断候选及原因。
- 当前多时间尺度状态摘要。

Diagnosis 可以在合法候选中灵活选定阶段、教材、知识点和任务形式，但输出必须再次经过 `PlanningValidator`。系统固定字段、ID、版本和状态仍由 LearningPlanService 拼接。

## 8. 稳定接口

### 8.1 多时间尺度状态

`GET /api/v1/learning-state/multiscale`

查询参数：

- `window_days=7|30|90`，默认 `30`。
- `include_recent_events=true|false`，默认 `false`。

响应保持第 7.1 节契约。`schema_version` 首版为 `1.0`。

### 8.2 路径候选

`GET /api/v1/learning-state/path-candidates`

查询参数：

- `scope=long_term|short_term|daily_task`
- `limit=1..30`，默认 `10`
- `include_blocked=true|false`，默认 `true`

每个候选返回：

- `candidate_id`
- `scope`
- `stage`
- `books`
- `knowledge_points`
- `estimated_minutes`
- `eligible`
- `blocked_reasons`
- `hard_constraint_results`
- `score`
- `score_components`
- `evidence_refs`
- `source_refs`
- `recommended_action`

### 8.3 执行协调轨迹

`GET /api/v1/executions/{execution_id}/coordination`

只允许当前执行所属用户访问。响应：

- `schema_version`
- `execution_id`
- `communication_summary`
- `repair_summary`
- `final_audit_decision`

默认不返回原始通信内容。为未来前端执行图提供稳定节点、边、修复边和状态，不要求前端解析模型自然语言。

### 8.4 兼容规则

- 现有接口和字段不删除、不重命名。
- 新对象都带 `schema_version`。
- 枚举增加新值时，前端必须能够回退显示原值。
- 业务展示名与内部 ID 同时返回，前端默认使用展示名。
- 空数据使用空数组、空对象或带原因的 `null`，不返回含义不明的占位文本。
- 所有时间使用带时区 ISO 8601。
- 所有分数明确单位和范围。
- 列表接口预留 `limit`，大型事件明细默认不返回。

## 9. 前端验证入口

现有前端只做增量展示：

- 学情报告增加宏观/中观/微观状态摘要和数据质量说明。
- 规划页面展示当前候选为什么可用、为什么被阻断，以及各评分项。
- 实时执行链路展示“按需通信”和“局部修复”控制边，但不展示敏感原始内容。
- 前端读取稳定接口对象，不从自然语言规划正文反向解析阶段、教材、知识点或修复节点。

这些入口用于在线验收，不作为最终前端信息架构约束。

## 10. 错误处理

- 通信包缺少必需信息：节点不调用模型，返回结构化阻断原因。
- 上游依赖输出格式错误：记录责任步骤和契约错误，不把原始异常文本伪装成业务答案。
- Audit finding 无法定位：不自动重跑整条链，转人工或失败关闭。
- 修复后仍不通过：保留两次 Audit 结果，停止自动修复。
- 多尺度状态源数据读取失败：返回明确缺失来源；规划继续使用现有可验证数据，但不生成依赖缺失指标的高风险候选。
- 没有长期计划时：短期和当日候选标记为被前置条件阻断。
- 没有短期计划时：当日候选标记为被前置条件阻断。
- 用户可用时间为零：不生成主学习任务，只返回复习提醒或无任务状态。
- 数据库暂时不可用：不得用跨用户缓存或演示数据回退。

## 11. 测试设计

### 11.1 单元测试

按 TDD 为以下行为先编写失败测试：

- 认知差距只选择目标 Agent 必需字段。
- 无关的大体积上游输出不进入通信包。
- 阻断需求缺失时不调用目标 Agent。
- 敏感字段和其他用户字段不能进入通信包。
- Audit 缺证据时生成 Knowledge → Expert → Audit 修复链。
- 内容质量问题只重跑 Expert → Audit。
- 无法定位问题时不自动修复。
- 自动修复只执行一轮。
- 修复保留未受影响节点输出。
- 修复中断恢复后不重复成功节点。
- 多尺度状态正确组合宏观、中观和微观数据。
- 缺失指标保持不可用状态，不自动补成 100%。
- 硬约束在评分前执行。
- 未满足前置条件的高分候选仍不可用。
- 时间超限候选仍不可用。
- 到期复习进入候选优先集合。
- 重复资源产生惩罚。
- 数据不足增加不确定性风险并禁止高风险调整。

### 11.2 API 和用户隔离测试

- 未登录请求返回 401。
- 用户不能读取其他用户的多尺度状态和协调轨迹。
- 三个新接口的 `schema_version`、空值和分数范围稳定。
- 内部知识点 ID 始终同时返回可读名称；前端默认不显示裸 ID。
- 旧接口响应不因新增能力删除字段。

### 11.3 极端案例

- 全新用户：只有注册调查，没有计划、答题或掌握记录。
- 只有长期计划，没有短期计划。
- 有短期计划但父长期计划已失效。
- 每日可用时间为 0 分钟。
- 每日可用时间为 1440 分钟，但系统不强制排满。
- 一次出现 100 个到期复习知识点。
- 最近连续大量答错，且所有答题集中在同一知识点。
- 所有知识点已掌握，没有薄弱点。
- 资源全部缺少难度字段。
- 行为数据全部超过有效时间窗。
- 上游输出包含超大无关文本。
- 两个可信证据互相冲突。
- Audit 同时报告证据、学情和内容质量三类问题。
- 修复节点再次失败。
- 修复过程中页面刷新或服务重启。
- 数据库连接中断后恢复。
- 恶意请求尝试读取其他用户执行 ID。

### 11.4 在线端到端验收

在已经启动的前端运行面板点击 Execute，至少验证：

1. 新用户数据不足时生成保守长期规划候选，不伪造掌握度。
2. 有长期规划后才允许生成短期候选。
3. 有短期规划后才允许生成当日候选。
4. 到期复习存在时，候选解释明确显示复习优先依据。
5. 时间预算很大时任务不会被强制排满。
6. 模拟缺证据 Audit 返修时，执行图只重跑必要节点。
7. 第二次 Audit 仍失败时系统停止修复并显示可解释错误。
8. 页面刷新后可恢复中断和修复状态。
9. 学情报告能读取三个时间尺度、来源和不可用指标。
10. 协调轨迹接口不泄露原始敏感通信内容。

在线测试结果是最终验收依据；单元测试、组件测试和构建通过不能替代在线验收。

## 12. 文档同步

实施完成时同步更新：

- `README.md`：新增能力概览与启动依赖。
- `backend/competition_app/README.md`：运行机制、配置和测试命令。
- `docs/frontend-api-reference.md`：三个新接口、字段、示例和兼容规则。
- `docs/database-operations.md`：说明复用工作流运行状态的持久化字段和清理策略。
- `docs/deployment.md`：说明 LangGraph 检查点、工作流状态和交接后端的部署要求。
- `docs/learning-monitoring-methodology.md`：多时间尺度指标、评分公式、限制和数据来源。

## 13. 完成定义

同时满足以下条件才算完成：

- 两个核心部件接入真实主流程，而不是独立演示代码。
- 正常、边界、故障和极端自动化测试通过。
- 前端相关单元/组件测试通过。
- 后端非 Live 回归测试通过。
- 已启动前端运行面板中的在线测试全部通过。
- 新接口文档、数据库文档、部署文档和监测方法文档同步。
- 接口保持向后兼容，现有前端功能无回归。
- 最终报告明确区分已验证事实、限制和未实现的弹性多智能体组织。
