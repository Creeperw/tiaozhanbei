# 学情监测与资源匹配口径

本文档对应 `GET /api/v1/learning-metrics/overview`、`GET /api/v1/learning-insights`
与 `GET /api/v1/resource-match-report`。
所有指标必须能够回溯到当前登录用户的持久化记录；系统不把模型猜测、页面停留或未完成题目当作掌握证据。

## 1. 统一约定

- 行为窗口仅允许 7、30、90 天，按 `Asia/Shanghai` 展示，服务端使用响应中的 `window.start_at` 和 `window.end_at` 查询。
- `overview.confidence` 是数据覆盖度，不是统计置信区间，也不是测验信度。
- 当前掌握状态是累计作答形成的快照；任务执行、练习得分率、登录规律、资源点击率和错因分布严格使用所选窗口。
- 掌握度对外统一为 `0..1`；权威写回表 `knowledge_mastery_states.mastery_score` 的存储单位是 `0..100`，返回报告时除以 100。
- 数据不足时显示空状态或观察值，不使用默认值伪造学习结论。

## 2. 监测指标来源

| 指标 | 持久化来源 | 采集动作 | 计算公式 | 适用窗口 |
|---|---|---|---|---|
| 知识掌握 | `knowledge_mastery_states`，兼容回退 `learner_knowledge_mastery` | 已完成题目经过批改、审核并成功写回 | 各知识点当前掌握度的算术平均 | 当前状态 |
| 复习保持 | `learner_kp_review_states` | 完成知识点题目后建立或更新复习状态 | `R=exp(-elapsed_seconds/stability_seconds)`，缺少复习时间或稳定度则不计入平均 | 当前时刻 |
| 任务执行 | `daily_task_instances`、`daily_task_items` | 已发布每日任务物化原子项；原子项完成状态由绑定练习/视频的服务端证据派生 | `completed_non_cancelled_daily_items/non_cancelled_published_daily_items` | 7/30/90 天 |
| 练习得分率 | `grading_result_records`、`learning_attempts`、`audit_result_records` | 普通练习或试卷逐题完成评分且审核通过 | 审核通过的普通练习与试卷逐题总得分 ÷ 对应总分；不含 AI 病患案例 | 7/30/90 天 |
| 学习规律 | `learning_activity_records` | 登录成功或主动签到 | 有登录/签到记录的不同日期数 ÷ 窗口天数 | 7/30/90 天 |
| 登录事件数 | `learning_activity_records` | 注册后自动登录或登录成功 | `activity_type=login` 的事件数；同日多次登录分别计数 | 7/30/90 天 |
| 实际登录天数 | `learning_activity_records` | 注册后自动登录或登录成功 | 登录事件按 `Asia/Shanghai` 日期去重 | 7/30/90 天 |
| 活跃天数 | `learning_activity_records` | 登录成功或主动签到 | 登录日期与签到日期的并集天数；兼容字段 `login_frequency` 采用此口径 | 7/30/90 天 |
| 资源使用 | `learning_activity_records` | 服务端记录一次推荐展示；用户点击时携带该展示 ID | 已点击且确实展示过的资源数 ÷ 展示资源数 | 7/30/90 天 |
| 有效学习分钟 | `learning_focus_sessions` | 开始、心跳、暂停、完成专注会话 | 每日已确认 `active_seconds` 求和后除以 60 | 7/30/90 天 |
| 错因分布 | `mistake_records` | 错题写回；客观题完成错因调研后更新，主观题采用审核后的批改归因 | 按 `error_type` 计数 | 7/30/90 天 |
| 到期复习数 | `review_memory_units`（`canonical_review_memory`） | 题目完成、批改与审核通过后建立或更新 canonical 记忆单元 | 已准入记忆单元中 `next_review_at<=calculated_at` 的数量 | 当前时刻 |
| 已完成题目数 | `learning_attempt_items`、`grading_result_records`、`audit_result_records`、`paper_submissions` | 提交题目并完成批改、审核；历史试卷按最新完成提交兼容 | 正式非试卷题项数 + `max(正式试卷题项数, 最新完成试卷内题项数)` | 累计及 7/30/90 天 |
| 不同题目数 | `question_version_records` | 正式完成题目 | 审核通过题目版本解析到稳定 `question_id` 后去重 | 累计及 7/30/90 天 |
| 同题重试次数 | `learning_attempt_items`、`grading_result_records`、`audit_result_records` | 同一题目版本再次完成正式批改且审核通过 | `sum(max(同一 question_version_id 的正式完成次数-1, 0))` | 7/30/90 天 |

接口响应的 `dimensions[].source_ids`、`formula`、`evidence_count` 和 `window_days` 是前端展示及审计的正式来源；
`data_sources[]` 给出表名、字段和时间字段。

## 3. 数据覆盖度

数据覆盖度只回答“当前是否有足够的多类记录支撑谨慎干预”，不回答“结论有多大统计概率为真”。

```text
coverage =
  0.50 × min(窗口内作答数 / 5, 1)
+ 0.30 × min((登录天数 + 有效专注会话数) / 4, 1)
+ 0.20 × min(有掌握状态的知识点数 / 3, 1)
```

主动干预门槛为：`coverage >= 0.60`、窗口内至少 3 次作答且至少存在 1 个知识点掌握状态。
这是透明的工程安全门槛，不是经过常模验证的心理测量阈值。冷启动期间只能展示观察数据。

T 阶段行为窗口同样只使用上述权威来源：每日任务完成率来自已发布原子任务，登录变化来自两段
连续 7 日窗口内的实际登录天数，专注变化来自有效 `active_seconds`，重试次数只统计审核通过的
正式题目结果。没有已发布每日任务时，任务完成率标记为不可用并在 T 阶段规则中按中性值处理，
不能用 0% 推断用户怠惰。

## 4. 掌握度与保持度

完成并通过审核的题目按知识点写回。当前兼容写回公式为：

```text
M_t = 0.65 × M_(t-1) × exp(-lambda × delta_days) + 0.35 × q_t
```

其中 `q_t` 是本次得分比例；结果在数据库中按 `0..100` 保存。`lambda` 根据近期错误和连续独立答对次数调整。
这是版本化的工程模型 `ebbinghaus_classic_hybrid_v1_1`，不是 BKT、IRT 或标准化考试分数。

保持度只在存在 `last_review_at` 与正数 `stability_seconds` 时动态计算。历史导入记录若只有明确的
`retention_estimate`，响应会标记为 `persisted_legacy_estimate`；没有证据则返回 `null`，不会按 0% 处理。

## 5. 资源匹配

### 5.1 候选与目标

- 目标知识点：学情报告中的薄弱知识点，加上当前正式今日任务的知识点。
- 候选资源：当前用户已保存的 `knowledge_card_records`、启用的 `teaching_resources`、启用的 `question_bank_items`。
- 没有目标知识点时返回空推荐，不使用随机或默认资源填充。
- 本接口只排序已持久化资源，不在请求期间进行网络检索；网络补充资源须先经过知识库流程入库。

### 5.2 评分

```text
知识点覆盖 0.45
资源质量   0.20
形式偏好   0.20
时间适配   0.15
```

知识点覆盖是资源知识点与目标知识点交集占目标知识点的比例。质量读取资源或题库的持久化质量分；
知识卡没有质量证据时使用明确标注的中性值 0.5。形式偏好来自用户画像。题目耗时优先使用当前用户近30天真实响应时间，
没有记录时才使用题型默认值。当前不采集或推断题目难度，资源匹配不包含难度分项；缺少某项可用特征时从分母中移除该项并重新归一化。

当前权重属于可解释的工程基线。正式宣称推荐有效前，应以真实用户反馈计算 `Precision@K`、`Recall@K`、`NDCG@K`，
并验证任务完成率和后测学习增益。

### 5.3 资源效果闭环

资源报告生成用户所有的 `recommendation_view_id`，但 GET 本身只写入待展示凭证，不计曝光。
前端实际渲染列表后先写入 `impression`，才能用这个 ID 写入点击或完成事件。系统按
`展示资源数 → 点击资源数 → 完成资源数` 计算漏斗；完成事件保存当时的知识点掌握度，
后续再与当前掌握度以及相关知识点的正式作答结果比较。样本不足时，学习增益保持
`null/insufficient_evidence`，不以 0 代替，也不自动修改推荐权重。

前端不得把页面加载、路由跳转或停留时长自动视作资源完成。“打开资源”只产生点击；
完成仅来自用户在资源卡上的显式确认，或者未来由具体学习资源返回的服务端完成证据。
同一展示凭证上的重复事件由后端幂等处理，伪造或跨用户资源 ID 会被拒绝。

### 5.4 次日任务负载

`GET /api/v1/task-load-policy` 返回确定性的 `next-day-load-v1`：

- 基准分钟数来自当前今日任务；没有当前任务时使用 60 分钟工程基准；
- 已发布原子任务完成率低于 50% 时缩减到基准的 65%，50%—80% 缩减到 85%；
- 完成率至少 90%，且已有练习得分率不低于 70% 时，最多小幅增加到 110%；
- 有效专注时长明显不足时不允许加量；
- 到期复习和低得分/低掌握只改变任务组成，分别预留复习、补弱时间，不会因此增加总量；
- 缺失指标按中性处理，建议范围为 10—1440 分钟；1440 分钟只是上限，不代表必须排满。

模型只接收该策略的精简结果并解释原因；`recommended_minutes`、分配和证据可用性均由
后端计算，Compiler 不重新推断这些数值。24 小时今日任务刷新和对话生成今日任务使用
同一策略。

## 6. 参考依据及边界

1. [1EdTech Caliper Analytics 1.2](https://www.imsglobal.org/spec/caliper/v1p2/)：用于学习事件、测验、媒体和资源交互的统一语义。它不规定本系统的计算公式或权重。
2. [Properties of the Bayesian Knowledge Tracing Model](https://jedm.educationaldatamining.org/index.php/JEDM/article/view/35)：支持根据知识组件及连续作答更新掌握状态的研究方向。当前系统仍是显式工程公式，并未声称实现 BKT。
3. [The Cold Start Problem and Interpretation of Knowledge Tracing Models' Predictive Performance](https://educationaldatamining.org/EDM2021/virtual/poster_paper126.html)：说明首次少量练习下的知识追踪解释风险，因此系统在证据不足时禁止主动干预。
4. [A systematic literature review on educational recommender systems](https://pubmed.ncbi.nlm.nih.gov/36124004/)：支持教育推荐采用多维信息并验证对学习过程的实际效果；它不为当前固定权重背书。

## 7. 可审计性与版本

- 学情方法版本：`learning-monitoring-v4-auditable-window`。
- 行为窗口聚合版本：`learning-window-v3-auditable`；持久化 30 日兼容快照版本为 `system-data-v4-auditable-window`。
- `GET /api/v1/learning-metrics/overview` 是面向新前端的统一只读指标合同；每个指标同时返回
  `value`、`unit`、`scope`、`available`、`unavailable_reason`、`formula` 和 `sources`。
- `GET /api/v1/learning-activity/summary` 的 `system_data` 按请求的 7/30/90 日窗口即时计算；
  旧的固定 30 日持久化快照仅在 `compatibility_snapshot_30d` 中保留，不能用于其他窗口。
- 任务执行只统计已物化到 `daily_task_instances` 的非取消每日原子项；自由练习、自由试卷、案例、活动日志和泛 `learning_task` 均不进入分子或分母。取消项排除，窗口内已发布但到期未完成的原子项仍保留在分母。窗口内没有可统计原子项时，任务完成率返回 `available=false`、`value=null` 和 `unavailable_reason=no_planned_daily_task_items`，不以 0% 代替缺失值。
- 专注时长只累计页面可见、最近 300 秒内存在用户交互且相邻上报间隔不超过 300 秒的区间；
  开始会话视为一次明确交互，结束会话前会结算最后一个有效区间。跨窗口会话因当前表只保存累计
  `active_seconds`，按会话观测时长比例裁剪到窗口；这是当前数据模型的明确限制。
- 掌握与复习公式版本随每条状态及历史记录保存。
- `/api/v1/learning-insights`、到期提醒、主动干预和规划复盘均由宿主层注入同一个
  `canonical_review_memory` 投影；`overview.review_projection_source` 用于核验来源。
  `learner_kp_review_states` 只保留保持度等兼容数据，不能覆盖 canonical 到期数量。
- 低完成率连续天数只检查最近连续、且存在正式每日原子任务的自然日：
  `daily_atomic_task_completion_rate = 当日已完成原子项 / 当日全部非取消原子项`。
  从最新日期向前遇到完成率大于等于 50%、缺少正式任务或证据缺失即停止累计；连续 3 天
  低于 50% 才提出需要用户确认的短期重规划请求，不自动覆盖正式计划。
- `POST /api/v1/learning-automation/run` 将上述监控复盘与 canonical 复习队列放在同一调用中；
  每次最多为一个“已完成过题目、已到期、尚未绑定资源”的知识点生成资源，重复调用幂等。
  知识检索可以解析出内部题库知识点 ID，但资源调度与绑定始终保留 canonical 队列原始
  `kp_id`，避免别名或旧数据标识导致“生成成功但队列未绑定”。
- 推荐项返回 `components`、`component_sources`、`quality_basis`、`estimated_minutes_basis` 和资源 `source`。
- 修改公式、阈值或数据源时必须同步修改本文件、接口文档和测试，不允许只改前端文案。

## 8. 多时间尺度学情与路径候选

### 8.1 三个尺度及来源

- `macro`：已批准资格路线、当前阶段、阶段教材、先修条件和阶段验收证据。来源为当前长期计划、
  已批准路线和确认后的学习画像，不从规划正文反向提取。
- `meso`：当前短期计划、日任务、计划/薄弱/到期复习知识点、任务完成率和学习规律度。来源为
  `short_term_plan`、`learning_task`、`learner_kp_review_states` 及窗口内学习活动。
- `micro`：近期正式作答、正确率、平均响应时间、平均掌握度、知识点掌握、已确认错因、
  专注分钟和当前任务负载。来源为 `question_attempt`、`knowledge_mastery_states`、
  `mistake_records`、`learning_focus_sessions` 和未完成任务。

聚合窗口只允许 7、30、90 日。计划和路线等当前状态不随窗口裁剪；任务、作答、错因和专注按窗口统计。
过期或失效的父计划不能作为合法父链。近期事件默认从接口响应清空，只有显式请求时返回。

### 8.2 硬约束

候选先按固定顺序执行硬约束，再计算排序分数：

1. `goal_route_alignment`
2. `parent_plan_exists`
3. `prerequisite_satisfied`
4. `time_budget`
5. `due_review_priority`
6. `trusted_source`
7. `low_data_protection`
8. `approved_stage_mapping`

任一硬约束失败即 `eligible=false`，高分不能覆盖阻断。可用时间单位为整数分钟，范围
`0..1440`；1440 分钟只表示上限，不要求任务把时间排满。

### 8.3 固定评分和缺失值

正向权重为：学习增益 `0.30`、保持收益 `0.20`、知识覆盖 `0.20`、时间适配 `0.10`、
难度适配 `0.10`、自主偏好支持 `0.10`。近期重复惩罚权重为 `0.10`，数据不确定性惩罚权重为
`0.15`。

每个分项使用 `available`、`value`、`unit`、`source_refs` 和 `unavailable_reason`。
缺少掌握、时间、资源难度或其他证据时，该分项保持 `value=null`，从可用正向分项分母中移除并重归一化；
不得用 0 代替缺失值。低覆盖度通过 `low_data_protection` 限制高风险调整，并通过不确定性分项降低排序。

### 8.4 解释边界

- 低数据用户只能得到保守候选或明确阻断，不能把注册调查当作真实练习成效。
- 数据新鲜度、冲突来源和父计划状态会限制候选；`state_digest` 用于证明候选对应同一源状态，
  不是长期保存的学习结论。
- 当前固定权重是透明工程基线，尚未用真实学习增益、后测提升或随机对照数据校准。
- 宣称推荐有效前，除排序指标外还必须验证完成率、延迟后测和不同学习群体的增益与公平性。
