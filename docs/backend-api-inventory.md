# 后端接口盘点与前端取数建议

更新时间：2026-07-26

本文用于前后端继续扩展页面时快速选择接口。浏览器默认访问主后端
`/api/v1/*`；`/api/*` 下的兼容接口由主后端统一挂载并执行登录用户映射，
前端不应直连交接后端端口，也不应提交 `user_id` 改变数据归属。

完整字段契约见 [`frontend-api-reference.md`](frontend-api-reference.md)，运行时定义以
`GET /api/v1/platform/openapi.json` 为准。

## 一、稳定主接口

| 领域 | 接口 | 主要用途 |
|---|---|---|
| 认证 | `POST /api/v1/auth/register` | 注册并进入强制学情调查 |
| 认证 | `POST /api/v1/auth/login`、`POST /api/v1/auth/logout` | 登录与退出 |
| 认证 | `GET /api/v1/auth/me` | 当前用户、角色和 onboarding 状态 |
| 会话 | `GET/POST /api/v1/conversations` | 会话列表与创建 |
| 会话 | `GET /api/v1/conversations/{session_id}/messages` | 对话历史和持久动作 |
| 智能体 | `POST /api/v1/review-cards/stream` | 启动六智能体任务 |
| 智能体 | `POST /api/v1/review-cards/runs/{thread_id}/resume/stream` | 中断追问后恢复 |
| 智能体 | `GET /api/v1/review-cards/runs/{thread_id}` | 查询持久运行状态 |
| 智能体 | `GET /api/v1/executions/{execution_id}/coordination` | 执行协调与轨迹摘要 |
| 规划 | `GET /api/v1/planning/readiness` | 长期、短期、当日任务前置条件 |
| 规划 | `GET /api/v1/learning-context` | 权威画像、三层规划、复习队列与能力发现 |
| 路径 | `GET /api/v1/learning-path` | 当前用户阶段—教材—知识点路径 |
| 路径 | `GET /api/v1/learning-path/nodes` | 学习路径懒加载 |
| 路径 | `GET /api/v1/qualification-targets` | 五类受支持资格考试 |
| 路径 | `GET /api/v1/learning-routes` | 非个性化经典教材路线目录 |
| 路径 | `GET /api/v1/learning-routes/{route_id}` | 经典路线阶段与教材详情 |
| 首页 | `GET /api/v1/dashboard/home` | 今日任务、计划摘要、签到和页面动作 |
| 今日任务 | `POST /api/v1/learning-tasks/current/complete` | 完成正式今日任务 |
| 今日任务 | `POST /api/v1/learning-tasks/current/refresh` | 24 小时到期轮换 |
| 签到 | `GET/POST /api/v1/checkin` | 签到状态与幂等签到 |

## 二、学情、行为与成果统计

| 接口 | 定位 | 前端适合展示 |
|---|---|---|
| `GET /api/v1/learning-monitoring/snapshot?days=7` | Diagnosis Agent 的正式监测输入 | 样本量、数据新鲜度、可观测指标 |
| `GET /api/v1/learning-metrics/overview?days=30` | 统一、可审计的监测指标合同 | 登录事件/天数、签到、专注、今日任务、题目、试卷、错题、掌握和复习；每项带公式与来源 |
| `GET /api/v1/learning-activity/summary?days=30` | 行为事件与任务执行 | 登录、专注、任务完成、资源点击 |
| `GET /api/v1/learning-activity/trends?days=30` | 按日行为趋势 | 折线图、日历热力图 |
| `GET /api/v1/learning-statistics/overview?days=30` | 正式学习成果累计与时间窗聚合 | 已完成题目、不同题目数、得分率、试卷、错题、复习、掌握度 |
| `GET /api/v1/learning-state/multiscale` | 微观/中观/宏观学习状态 | 多尺度学情可视化 |
| `GET /api/v1/learning-insights` | 可解释学情诊断 | 薄弱点、掌握热图、证据质量；到期数、通知和规划复盘统一使用 canonical 复习投影 |
| `GET /api/v1/resource-match-report` | 当前计划的资源匹配 | 匹配分项与数据来源 |
| `GET /api/v1/task-load-policy` | 次日任务负载策略 | 建议分钟数、复习/补弱/新学分配及证据来源 |
| `POST /api/v1/resource-recommendations/events` | 资源反馈写入 | 依次记录真实展示、点击和用户确认完成；校验展示凭证与当前用户 |
| `GET /api/v1/resource-effectiveness?days=30` | 资源效果闭环 | 展示—点击—完成漏斗、后测正确率与掌握度变化 |
| `GET /api/v1/interventions` | 主动干预 | 干预卡片与反馈 |
| `GET /api/v1/notifications` | 通知中心 | 未读通知、干预和计划复盘提醒 |
| `GET /api/v1/plan-reviews` | 自动规划复盘 | 建议、依据与接受/拒绝 |
| `POST /api/v1/learning-automation/run` | 统一反馈闭环 | 运行监控复盘，并幂等推送一项尚无资源的到期复习 |
| `GET /api/v1/learning-automation/status` | 异步派发状态 | 当前用户最近一次复习资源推送的运行、成功或失败原因 |

智能助教的泛化规划请求采用“现有计划优先”策略：Planner 返回 `plan_action`，
已有有效长期规划、短期计划或当日任务且用户未明确要求修改时，由
LearningPlanService 直接返回正式版本。响应中的 `reused_existing`、
`replan_review` 和 `force_replan_prompt` 供前端区分“复用”与“新生成”。
复盘建议通过 `/api/v1/notifications` 和 `/api/v1/plan-reviews` 同步读取。

Planner 按交付目标组合能力，而不是把包含“学习”“最近”等词的输入一律归为规划。
“最近学了什么”读取已完成记录；“最近需要学什么/接下来该学什么”同时读取当前计划、
掌握度与复习状态、近期完成记录，返回只读的下一步重点，不创建计划。若用户明确要求
根据薄弱点推荐题目或资源，则进入学情、知识/题目检索、专家资源和审核链路；只有明确
要求制定、安排或修改计划时才进入规划发布链路。

“梳理某本教材某一章节的学习要点、阅读重点或学习方法”使用
`general_learning_support`，由 Knowledge、Expert、Audit 共同处理。该类别允许自然语言
自由组织，不强制套知识讲解的固定段落，也不会创建或修改学习计划；单个概念、原理和
辨析问题仍使用 `knowledge_explanation`。

今日任务的全部原子项完成后，执行协调器会自动写入短期任务块进度；同一短期计划的
全部任务块完成后，短期计划状态自动变为 `completed`。长期阶段只在当前阶段全部
`exit_evidence` 已取得服务端核验证据时推进；推进后旧短期计划与今日任务失效，
下一次规划必须基于新的阶段重新生成。阶段推进事件同步写入通知中心。

三个统计入口不能混用：

- `learning-metrics/overview` 是新前端需要一次读取多类数字时的统一入口；
- `learning-activity/summary` 回答“用户做了什么”；
- `learning-statistics/overview` 回答“用户完成了多少正式学习成果”；
- `learning-insights` 回答“这些证据反映了什么学情”。

## 三、学习工坊

| 能力 | 接口 |
|---|---|
| 工坊总览 | `GET /api/v1/workshop` |
| 下一道练习 | `GET /api/v1/workshop/practice/next` |
| 知识卡列表/详情 | `GET /api/v1/workshop/knowledge-cards`、`GET /api/v1/workshop/knowledge-cards/{card_id}` |
| 解析并保存知识卡 | `POST /api/v1/workshop/knowledge-cards/resolve` |
| 试卷列表/详情 | `GET /api/v1/workshop/papers`、`GET /api/v1/workshop/papers/{paper_id}` |
| 保存试卷答案 | `PUT /api/v1/workshop/papers/{paper_id}/answers` |
| 试卷计时 | `POST /api/v1/workshop/papers/{paper_id}/timer/pause`、`.../resume` |
| 交卷 | `POST /api/v1/workshop/papers/{paper_id}/submit` |
| 收藏夹 | `GET/POST /api/v1/workshop/favorite-folders` |
| 收藏 | `GET/POST /api/v1/workshop/favorites` |
| 笔记 | `GET/POST /api/v1/workshop/notes` |
| 资格真题套卷 | `/api/v1/qualification-papers/*` |

客观题、案例简答、错题变式以及用户题库的兼容训练接口仍位于 `/api/training/*`，
接口返回的正式批改、审核和写回结果会进入学习成果统计。只打开题目或保存草稿不会计数。

## 四、知识库与教材

| 能力 | 接口 |
|---|---|
| 教材/章节节点 | `GET /api/knowledge/atlas/nodes` |
| 小节知识点与视频 | `GET /api/knowledge/atlas/section/{section_id}` |
| 知识点资源详情 | `GET /api/v1/knowledge/points/{kp_id}` |
| 知识点/题目检索 | `POST /api/v1/knowledge/questions/search` |
| 知识资产预热 | `POST /api/v1/knowledge/warm` |
| 上传教材/资料 | `POST /api/v1/knowledge/content/import-text`、`.../import-file` |
| 结构识别报告 | `GET /api/v1/knowledge/content/recognition-reports` |
| 考纲路线 | `/api/v1/knowledge/exams/*` |

视频资源当前有两类独立覆盖率：知识点时间戳和小节完整视频。前端不得把前者显示为
“完整视频”；后续收到 `section_video_matches.jsonl` 后再启用小节完整视频覆盖统计。

## 五、复习组件

| 接口 | 用途 |
|---|---|
| `GET /api/v1/review-queue` | 当前用户待复习队列 |
| `GET /api/v1/review-dashboard` | 队列、掌握度、复习状态、任务和历史 |
| `POST /api/v1/review-tasks/{review_task_id}/attempts` | 提交复习题结果 |
| `POST /api/v1/learners/{learner_id}/review-queue/dispatch` | 调度下一项复习资源 |
| `POST /api/v1/learning-automation/run` | 运行反馈闭环并推送一项缺失资源的到期复习 |

只有完成知识点题目且正式批改写回成功，知识点才进入复习队列。
`canonical_review_memory` 是到期数量的唯一权威来源；学情展示、到期提醒、自动/手动规划复盘不得再读取旧复习状态表的到期计数。
统一自动化接口每次最多生成一个复习资源；已经绑定资源的复习任务不会再次生成。

## 六、建议继续补充的只读接口

当前优先级如下：

1. 知识库资产覆盖报告：教材切片、知识点题目、时间戳视频、完整小节视频分别统计；
2. 学习成果明细分页：为统计卡片提供可追溯题目/试卷/复习记录；
3. 教材阅读进度：保存用户最后阅读小节、视频位置和完成状态；
4. 前端组合页 BFF：当首页或学情页确定最终布局后，按页面一次返回所需数据。

上述接口应继续遵守“真实写入才统计、空样本返回不可用、用户数据服务端隔离”的原则。
