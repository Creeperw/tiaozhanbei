# 学情监测与资源匹配口径

本文档对应 `GET /api/v1/learning-insights` 与 `GET /api/v1/resource-match-report`。
所有指标必须能够回溯到当前登录用户的持久化记录；系统不把模型猜测、页面停留或未完成题目当作掌握证据。

## 1. 统一约定

- 行为窗口仅允许 7、30、90 天，按 `Asia/Shanghai` 展示，服务端使用响应中的 `window.start_at` 和 `window.end_at` 查询。
- `overview.confidence` 是数据覆盖度，不是统计置信区间，也不是测验信度。
- 当前掌握状态是累计作答形成的快照；任务执行、答题正确率、登录规律、资源点击率和错因分布严格使用所选窗口。
- 掌握度对外统一为 `0..1`；权威写回表 `knowledge_mastery_states.mastery_score` 的存储单位是 `0..100`，返回报告时除以 100。
- 数据不足时显示空状态或观察值，不使用默认值伪造学习结论。

## 2. 监测指标来源

| 指标 | 持久化来源 | 采集动作 | 计算公式 | 适用窗口 |
|---|---|---|---|---|
| 知识掌握 | `knowledge_mastery_states`，兼容回退 `learner_knowledge_mastery` | 已完成题目经过批改、审核并成功写回 | 各知识点当前掌握度的算术平均 | 当前状态 |
| 复习保持 | `learner_kp_review_states` | 完成知识点题目后建立或更新复习状态 | `R=exp(-elapsed_seconds/stability_seconds)`，缺少复习时间或稳定度则不计入平均 | 当前时刻 |
| 任务执行 | `learning_task` | 创建正式学习/练习任务；完成接口更新 `status=completed` | 完成的非取消任务数 ÷ 全部非取消任务数 | 7/30/90 天 |
| 练习正确 | `question_attempt` | 客观题、案例或其他正式练习提交成功 | 正确作答数 ÷ 全部作答数 | 7/30/90 天 |
| 学习规律 | `learning_activity_records` | 登录成功或主动签到 | 有登录/签到记录的不同日期数 ÷ 窗口天数 | 7/30/90 天 |
| 资源使用 | `learning_activity_records` | 服务端记录一次推荐展示；用户点击时携带该展示 ID | 已点击且确实展示过的资源数 ÷ 展示资源数 | 7/30/90 天 |
| 有效学习分钟 | `learning_focus_sessions` | 开始、心跳、暂停、完成专注会话 | 每日已确认 `active_seconds` 求和后除以 60 | 7/30/90 天 |
| 错因分布 | `mistake_records` | 错题写回；客观题完成错因调研后更新，主观题采用审核后的批改归因 | 按 `error_type` 计数 | 7/30/90 天 |
| 到期复习数 | `learner_kp_review_states` | 题目完成后进入复习队列，后续作答更新计划 | `status=active AND next_review_at<=now` | 当前时刻 |

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
知识点覆盖 0.40
资源质量   0.20
形式偏好   0.15
时间适配   0.15
难度适配   0.10
```

知识点覆盖是资源知识点与目标知识点交集占目标知识点的比例。质量读取资源或题库的持久化质量分；
知识卡没有质量证据时使用明确标注的中性值 0.5。形式偏好来自用户画像。题目耗时优先使用当前用户近30天真实响应时间，
没有记录时才使用题型默认值。只有题库存在明确难度字段时才计算难度适配；缺少某项特征时从分母中移除该项并重新归一化，
不会伪造固定难度分。

当前权重属于可解释的工程基线。正式宣称推荐有效前，应以真实用户反馈计算 `Precision@K`、`Recall@K`、`NDCG@K`，
并验证任务完成率和后测学习增益。

## 6. 参考依据及边界

1. [1EdTech Caliper Analytics 1.2](https://www.imsglobal.org/spec/caliper/v1p2/)：用于学习事件、测验、媒体和资源交互的统一语义。它不规定本系统的计算公式或权重。
2. [Properties of the Bayesian Knowledge Tracing Model](https://jedm.educationaldatamining.org/index.php/JEDM/article/view/35)：支持根据知识组件及连续作答更新掌握状态的研究方向。当前系统仍是显式工程公式，并未声称实现 BKT。
3. [The Cold Start Problem and Interpretation of Knowledge Tracing Models' Predictive Performance](https://educationaldatamining.org/EDM2021/virtual/poster_paper126.html)：说明首次少量练习下的知识追踪解释风险，因此系统在证据不足时禁止主动干预。
4. [A systematic literature review on educational recommender systems](https://pubmed.ncbi.nlm.nih.gov/36124004/)：支持教育推荐采用多维信息并验证对学习过程的实际效果；它不为当前固定权重背书。

## 7. 可审计性与版本

- 学情方法版本：`learning-monitoring-v2`。
- 行为窗口聚合版本：`learning-window-v1`。
- 掌握与复习公式版本随每条状态及历史记录保存。
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
缺少难度、掌握、时间或其他证据时，该分项保持 `value=null`，从可用正向分项分母中移除并重归一化；
不得用 0 代替缺失值。低覆盖度通过 `low_data_protection` 限制高风险调整，并通过不确定性分项降低排序。

### 8.4 解释边界

- 低数据用户只能得到保守候选或明确阻断，不能把注册调查当作真实练习成效。
- 数据新鲜度、冲突来源和父计划状态会限制候选；`state_digest` 用于证明候选对应同一源状态，
  不是长期保存的学习结论。
- 当前固定权重是透明工程基线，尚未用真实学习增益、后测提升或随机对照数据校准。
- 宣称推荐有效前，除排序指标外还必须验证完成率、延迟后测和不同学习群体的增益与公平性。
