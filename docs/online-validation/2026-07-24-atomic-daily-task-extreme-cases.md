# 每日原子任务极端场景验证记录

- 计划：`docs/superpowers/plans/2026-07-24-atomic-daily-task-auto-completion.md`
- 本地验证日期：2026-07-25
- 当前阶段：本地非 Live 回归已执行；Live 场景全部待在已启动的前端运行台中点击 Execute
- 结论：**本地证据通过但带关注项；不得据此标记在线验收通过**

## 验证边界

本轮遵守以下约束：

- 未从 WSL 命令行运行任何 Live pytest。
- 仅运行主应用非 Live pytest、handoff 聚焦/回归、前端单测和生产构建。
- 保留现有脏工作树，未执行 reset、revert、clean 或 commit。
- 只有出现服务端最终状态和可复核响应/截图后，在线场景才允许标记通过。

## 本地回归证据

| 验证项 | 命令摘要 | 结果 | 判定 |
| --- | --- | ---: | --- |
| 主应用回归 | `python -m pytest -q competition_app/tests` | `800 passed, 3 skipped, 1 warning`，114.52 秒 | 通过；3 个 skip 未冒充通过 |
| handoff 首次回归 | Task 9 指定的 6 个测试文件 | `108 passed, 3 failed` | 3 个失败均为 Task 6 新口径下的旧断言：无每日原子项时应为 `available=false, value=null`，不是生产逻辑回退理由 |
| handoff 修复后回归 | 同上 | `111 passed, 6 warnings`，30.71 秒 | 通过 |
| 前端默认 Vitest（修复前） | `npm run test:unit` | 259 个断言通过；11 个原生 `node:test` 文件被 Vitest 误收集；2 个 forks worker 启动超时 | 配置/WSL worker 问题，不是 259 个断言失败 |
| 前端默认 Vitest（配置修复后） | `npm run test:unit` | `51 files / 261 tests passed`；另有 1 个 forks worker 启动超时 | 业务断言全部通过；命令整体不能记为通过 |
| worker 超时复验 | `ReviewDashboardPanel.test.jsx`，threads、单 worker | `1 file / 1 test passed`，55.74 秒 | 超时归为 WSL forks worker 启动环境故障 |
| Tasks 7–8 前端聚焦复验 | 7 个相关文件逐文件、threads、单 worker | `45 passed`（4+19+6+7+5+3+1），全部通过 | 通过；逐文件执行规避 WSL 批量 worker 启动不稳定 |
| 原生 Node 前端测试 | 11 个 `node:test` 文件 | `63 passed, 1 failed` | 唯一失败是既有管理员知识库路由预期不一致，与每日原子任务无关；不在本任务修复范围 |
| 前端生产构建 | `npm run build` | 成功，3277 modules transformed，137 秒 | 通过；仅有 Browserslist 过期和 chunk size 警告 |

## 本轮最小修复

只进行一次集中修复波次：

1. 将自由工作台练习、自由普通练习、自由试卷的 3 个旧断言迁移到 Task 6 的每日原子项口径：无已发布每日原子项时完成率不可用且 `value=null`。
2. 收窄 Vitest 的 `*.test.js` 收集范围，只纳入真实 Vitest 文件；既有原生 `node:test` 文件仍由 Node 自带运行器执行。

未修改生产完成率逻辑，未把自由练习或自由试卷重新计入每日任务完成率。

## 十个在线场景：本地证据与待执行 Live 操作

以下所有场景的在线状态均为 **PENDING LIVE**。本地自动化只用于说明已有覆盖，不能替代真实前端、API、数据库最终状态与截图。

| # | 场景 | 已有本地证据 | Live 必须记录的输入与最终证据 | 在线状态 |
| ---: | --- | --- | --- | --- |
| 1 | 1 个 HTML5 视频 + 3 个知识点题集，父进度 0/4→4/4 | 主应用计划物化、dashboard 4 项展示、handoff 父状态派生分别有测试；尚无完整端到端串联 | 用户、task ID/version、4 个 item ID、每个题集冻结题数、逐项 API 状态、父进度每次变化、最终 4/4 截图/响应 | PENDING LIVE |
| 2 | 每个知识点冻结 3 题；2/3、不绑定自由第 4 题、冻结第 3 题 | 绑定下一题只发冻结快照、全部终态才完成、伪造题拒绝有测试；缺精确 2/3→自由题→3/3 串联 | item ID、3 个 question version ID、2/3 响应、自由题 attempt ID 及父进度不变、3/3 最终响应 | PENDING LIVE |
| 3 | 冻结题答错但审核终态，任务完成且得分率下降并产生错题/补救 | “答错且 revise 仍完成”和“答错生成错题/诊断”分别有测试；尚未同一绑定提交串联 | 错答内容、audit decision/status、item/parent 最终状态、提交前后练习得分率、错题和补救记录 ID | PENDING LIVE |
| 4 | `needs_human_review` 时进行中，转终态后完成 | 非终态不完成有服务测试 | 同一 snapshot 的 pending 响应、人工转终态操作、终态 audit ID、item/parent 完成响应 | PENDING LIVE |
| 5 | 20 道自由练习 + 1 份自由试卷不改变每日完成率 | handoff 系统数据污染隔离测试精确覆盖每日项与自由活动分离；本轮旧断言也已迁移 | 操作前后每日完成率分子/分母/value、20 个自由 attempt 摘要、paper ID、服务端 system-data 响应 | PENDING LIVE |
| 6 | HTML5 重播/跳播不能伪造 90%，真实覆盖 90% 自动完成 | 前端区间合并、seek、隐藏页、89/90 边界及服务端区间合并有测试 | item ID、每次 watched intervals、服务端 merged intervals/coverage、89% pending、真实 90% completed、父进度 | PENDING LIVE |
| 7 | iframe 专注阈值 + 用户确认双条件 | 89% 确认 409、90% 未确认 pending、满足双条件完成有服务测试；前端按钮门槛有测试 | item ID、active seconds、阈值、未确认响应、阈值不足确认的 409、最终确认响应和父进度 | PENDING LIVE |
| 8 | handoff 故障恢复，outbox 重试且不重抽/不重复 | 主应用 outbox pending→retry→delivered 与 handoff 同版本重放复用快照分别有测试 | event ID、attempt count、last error 摘要、恢复后 delivered_at、重试前后 item ID 与 question version 集合对比 | PENDING LIVE |
| 9 | 24 小时轮换保留旧历史，新 item ID 和新冻结题集 | 主应用轮换新 task/item ID、版本历史保留有测试；尚无完整跨库冻结题集比较 | 旧/新 task ID/version、旧历史可读响应、新旧 item ID、新旧冻结 question version 集合、轮换时间 | PENDING LIVE |
| 10 | 其他用户不能读取、提交或确认 item | 他人读取绑定下一题和视频上报返回 404 有测试 | 两个用户 ID、读取/练习提交/HTML5 上报/iframe 确认四类请求状态，确保无跨用户记录写入 | PENDING LIVE |

## 待 Live 前关注项

### 1. 生产 resolver 接线需先确认

当前主应用生产容器构造 `LearningPlanService` 和 `DailyTaskRefreshService` 时未显式传入正式知识点 resolver 与可信视频 resolver。按照 Task 1 的 fail-closed 语义，未解析的知识点/视频会降级为 `recall`/`reading`。因此，在执行场景 1 前必须先确认实际运行台的数据/依赖注入能够生成“真实 HTML5 视频 + 3 个正式 `knowledge_practice`”原子项；否则该场景会被阻断，不能用手工伪造 payload 冒充生产链通过。

### 2. `reading`/`recall` 降级项完成路径需确认

handoff 当前核心完成状态机以冻结题集和视频证据为主。若生产计划发布了降级 `reading`/`recall` 项，需要确认其服务端可验证完成路径；没有最终状态证据时父任务不能标记完成。

### 3. 前端 WSL worker 故障

默认 forks 池偶发 `Timeout waiting for worker to respond`。单文件改用 threads、单 worker 后通过，故本地记录为环境故障。在线测试仍须在已启动的前端运行台点击 Execute，不能把 WSL 中未完成的默认命令写成通过。

### 4. 与本任务无关的既有失败

原生 Node 前端测试中 `adminKnowledgeRouting.test.js` 仍有 1 个管理员知识库路由期望不一致（预期 `admin-knowledge`，实际 `knowledge`）。该失败不涉及每日原子任务，未在本轮顺带修改。

## Live 执行清单

在已启动的前端运行台中：

1. 点击 Execute 运行本计划的 Live 场景，不从 WSL 调用 pytest。
2. 按上表顺序完成 1–10；每项保留 task/item/question/attempt/audit/event ID。
3. 保存 API 状态码、关键响应摘要和截图；涉及指标时保存操作前后分子、分母、完成率与练习得分率。
4. 只有服务端出现预期最终状态后把对应行从 `PENDING LIVE` 改为 `PASS`；任何缺失最终状态或服务端证据的场景保持 pending/failed。
5. 若场景 1 无法产生预期 4 个正式原子项，先记录 resolver/降级项阻断，不得改用手工完成父任务。
