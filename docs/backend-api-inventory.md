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
| `GET /api/v1/learning-activity/summary?days=30` | 行为事件与任务执行 | 登录、专注、任务完成、资源点击 |
| `GET /api/v1/learning-activity/trends?days=30` | 按日行为趋势 | 折线图、日历热力图 |
| `GET /api/v1/learning-statistics/overview?days=30` | 正式学习成果累计与时间窗聚合 | 已完成题目、不同题目数、得分率、试卷、错题、复习、掌握度 |
| `GET /api/v1/learning-state/multiscale` | 微观/中观/宏观学习状态 | 多尺度学情可视化 |
| `GET /api/v1/learning-insights` | 可解释学情诊断 | 薄弱点、掌握热图、证据质量 |
| `GET /api/v1/resource-match-report` | 当前计划的资源匹配 | 匹配分项与数据来源 |
| `GET /api/v1/interventions` | 主动干预 | 干预卡片与反馈 |
| `GET /api/v1/notifications` | 通知中心 | 未读通知、干预和计划复盘提醒 |
| `GET /api/v1/plan-reviews` | 自动规划复盘 | 建议、依据与接受/拒绝 |

三个统计入口不能混用：

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

只有完成知识点题目且正式批改写回成功，知识点才进入复习队列。

## 六、建议继续补充的只读接口

当前优先级如下：

1. 知识库资产覆盖报告：教材切片、知识点题目、时间戳视频、完整小节视频分别统计；
2. 学习成果明细分页：为统计卡片提供可追溯题目/试卷/复习记录；
3. 教材阅读进度：保存用户最后阅读小节、视频位置和完成状态；
4. 前端组合页 BFF：当首页或学情页确定最终布局后，按页面一次返回所需数据。

上述接口应继续遵守“真实写入才统计、空样本返回不可用、用户数据服务端隔离”的原则。
