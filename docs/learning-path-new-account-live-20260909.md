# 新账号学习路径生产验收（2026-09-09）

## 结论

未通过。新账号可注册、选择考试、保存学情调研并触发真实规划，但长期规划转人工复核，短期计划和今日任务未生成，不能进入预期的学习任务流程。

## 测试条件

- 已启动生产前端，health确认mode=live、status=ok；未运行命令行Live pytest。
- 账号：path_live_20260909_01；显示名：学习路径验收0909。随机测试密码未记录；浏览器保留登录。
- 用户：USER_5303c3e61f954a6297b97202cd3b11b6。
- 中医执业医师资格考试；零基础、无历史课程和做题记录；每天学习与复习合计最多35分钟；晚间、讲义后少量分阶测试、不安排视频。
- 注册后个性化路径为空、今日任务0，未沿用旧账号数据。

## 运行证据

- 会话：CONV_a254a10f08f745c4bd0178e90ee754be。
- 执行：EXE_b45580bcb52fa7569c27fdc48788bab7。
- 运行：THREAD_17889613611244be62327ae74c。
- 复核：HR_EXE_b45580bcb52fa7569c27fdc48788bab7。
- 数据库创建13:42:38.895118 UTC，终态更新时间13:51:07 UTC，约8分28秒。
- 终态waiting_human_review；knowledge、memory、route_resolution、diagnosis、audit步骤完成。
- current/context确认long_term_plan、short_term_plan、learning_task均为null。未重提规划、确认发布或写入修复数据。

## 阻塞链路

1. 审核报告称可信路线标识和版本未明确，且引用compiled_contract.trusted_route。实际持久化Diagnosis的trusted_plan_route已包含route_id=textbook_tcm_physician、route_version=1。
2. 与生产SHA256一致的audit.py明确将compiled_contract和trusted_route作为并列输入发送，非上述嵌套字段。因此审核报告所称字段缺失与已保存产物不符。
3. audit_findings_compiler最终ModelResponseError，error_reason=business_schema_invalid；两次供应商请求HTTP200、finish_reason=stop，不是连接超时。原始输入/响应未完整留存，不能进一步断言具体哪个字段非法。
4. 结构化审核问题PLAN_MODEL_ISSUE_COMPILATION、issue_type=unresolved、policy_id=audit:invalid_compilation、blocking=true；owner_step_id与origin_step_id为空。
5. repair_trace状态stopped、rerun_step_ids为空，没有实际重跑Diagnosis；系统安全转人工，后续两层计划未启动。

模型阶段耗时：Diagnosis约225秒、合同编译约84秒、审核约27秒、审核问题编译约79秒。不能将所有耗时归为网络问题。

## 覆盖边界与后续建议

- 本轮验证到审核阻塞为止，未完成成功路径、今日任务打开和成功计划刷新恢复。
- 审核弹窗以长文本显示内部字段，只提供暂时关闭。后续关闭与刷新后路径切换曾被页面元素拦截，浏览器快照亦发生变化；记录交互异常，不据此断言稳定的CSS根因。
- 未修改业务源码、模型配置、代理或任何旧账号数据。
- 建议先修正审核字段语义和事实核验，再补审核问题编译失败的安全诊断与受控恢复；不得删除审核或对无法定位的问题直接放行。修改前须获得用户批准。

## 继续排查：纠错与诊断缺口（2026-09-09）

已核对生产与发行源码的模型客户端、审核问题编译器、审核编译合同和规划审核提示，四文件SHA256均一致。

### 已复现的机制

使用stub环境和httpx.MockTransport，构造缺少source_anchors的审核问题响应，两次均返回HTTP200，不调用真实模型或数据库：

- 原始JSON Schema校验可准确产生`/issues/*/source_anchors`、`required`。
- `openai_compatible.py`仅对planner_agent和plan_contract_compiler收集structured_issues；audit_findings_compiler不在其中，最终异常的validation_issues为空。
- 该角色第二次请求走通用纠错提示，要求面向学习者的content；既没有具体字段错误，也没有前一次输出。此要求与审核问题编译合同不符。
- 两次无效后直接抛ModelResponseError；异常发生在AuditFindingsCompilerAgent的Pydantic校验与compilation_feedback之前，该层同稿纠错无法处理此类客户端异常。
- 这证明存在纠错职责和可观测性缺陷；构造响应不是线上原始响应，不能声称线上也缺source_anchors，亦不能证明提示冲突必然导致模型失败。

生产只读检查：debug_trace_enabled=false、debug_trace_allow_live=false；该thread/execution均无对应独立诊断目录。未启用采样、重启服务或再次发起Live运行。

### 待批准最小上下文映射

| 文件 | 拟处理内容 |
| --- | --- |
| `backend/competition_app/prompt_skills/audit_agent/learning_plan.md` | 明确trusted_route与compiled_contract并列、分别由谁提供及核验范围；不要求向合同加入schema未定义字段，不以确定性通过替代其他语义审核 |
| `backend/competition_app/llm/openai_compatible.py` | 为固定枚举audit_findings_compiler增加符合来源提取职责的纠错反馈，保留字段级脱敏错误；维持最多两次请求，不放宽schema或自动发布 |
| `backend/competition_app/llm/validation_diagnostics.py` | 复用现有schema字段白名单和规则脱敏，预计无需改动 |
| `backend/competition_app/agents/audit_findings_compiler.py`、`contracts/audit_compilation.py` | 保留来源逐字引用、位置目录及完整性门禁，预计无需改动 |
| `backend/competition_app/tests/llm/`、`tests/agents/test_audit_findings_compiler.py`、`tests/llm/test_prompt_skills.py` | 增加实际HTTP模拟的缺字段后正确修复、连续失败、脱敏反馈、来源不可伪造与提示合同回归；假模型成功不代表Live成功 |

本方案不改公共API、不迁移数据库、不更改模型配置；需警惕共享客户端影响其它角色，因此按系统固定角色精确限定。业务修复、部署及新的Live请求尚未执行。

## 再次只读排查：首次尝试的证据限制

- 检查13:49—13:52 UTC服务journal，共11条记录，无审核编译角色、结构校验错误或异常堆栈。
- 精确按本thread查询langgraph_checkpoints、langgraph_checkpoint_blobs、langgraph_checkpoint_writes，均无记录；审核失败案例表仅有最终决定、问题与返修摘要，无首次响应。
- 再读运行记录：首次响应1318字符、HTTP200、finish_reason=stop、done_received=true、body_read_completed=true；第二次652字符，同样完整结束。两次response_format均为json_object，不是供应商严格json_schema模式。
- **更正此前口头表述**：最终error_reason=business_schema_invalid只证明最终尝试结构校验失败。客户端内部attempt_failures未进入该持久化记录；首次既可能是invalid_json/ambiguous_json，也可能是business_schema_invalid，不能据最终异常断言首次已通过JSON解析但字段错误。
- Schema摘要与本地重建值完全一致：973507a5511289122677c33329325ff862ec4daa3b1ced18f25f49d9f73c2591。离线MockTransport使用生产相同json_object模式，合法compiled与needs_revision两分支均单次通过原始JSON Schema及Pydantic校验，排除“合同两分支均不可满足”的确定性问题；不证明真实首次响应合法。
- 原始输入审核finding长度391字符、report长度859字符，未发现仅凭这些长度即可确定超过单条message/source_quote的2000字符上限；不能把长度约束列为现场根因。
- 当前剩余必要证据是每次尝试的解析/校验失败类别及脱敏字段规则。若继续在线取证，应先申请仅诊断变更及单次前端复测，不改审核/纠错提示，不改变原有重试策略，不开启全量正文/思考采集。若首轮失败后第二轮成功，也必须能保留首轮诊断。该取证尚未实施。

## 获批仅诊断部署与单次复测

用户同意继续仅诊断方案后，精确修改并部署两个运行文件：

- `llm/openai_compatible.py`：仅针对固定角色audit_findings_compiler，在每次尝试结束时记录status、允许的failure_reason和由原始schema生成的脱敏validation_issues。观察异常不影响业务。没有改变提示、纠错输入、重试预算、校验决策或异常。
- `llm/response_diagnostics.py`：现有诊断通道增加最多两条structured_attempts白名单，只保留尝试序号、状态、失败类别、字段路径和规则，不复制响应、异常原文、思考或用户文本。
- 新增离线`tests/llm/test_audit_attempt_diagnostics.py`；与原有响应诊断、JSON Schema、Planner/Compiler纠错、供应商能力和审核提取回归合计93 passed。覆盖流式/非流式、JSON无效/多对象歧义/字段缺失、首败后成功、连续失败和其它角色不受影响。
- 对照关闭观察块的同组模拟，HTTP请求体、返回结果、最终异常和原last_error_details相同。移除诊断块和诊断参数后，客户端AST与部署前生产版本完全相同。
- 生产部署前running/pending计数0，mode=live。备份`/srv/tiaozhanbei-releases/backups/audit-attempt-diagnostics-20260909/{before,deploy}.tgz`。
- 语法检查通过，本地/线上SHA一致：客户端`c13d49cdce5807354dd054cae9e4e18965df401358ed21fa4fba24943d5b8a7b`；诊断过滤器`e48ed744fb5f2552f2e31b486518843c3254f06d9fd78128a91f85c7c023866a`。
- 服务PID163127、active、NRestarts=0；浏览器health HTTP200、ok、live。未启用full capture或debug trace配置。

### 唯一前端复测（已终态：三层生成完成，但验收未通过）

- 原新账号path_live_20260909_01，问卷low/35分钟/晚间/讲义与分阶题/不视频以及自定义原文均保持不变。
- 鼠标点击入口被页面元素拦截，使用标准按钮聚焦加Enter完成问卷；没有force click、脚本直接点击、CSS修改或接口代提交。鼠标交互可用性仍未通过。
- 会话`CONV_392d4852879c40468e3bb399af25eb89`。三个阶段全部completed：长期`EXE_b536c3f61f9e402cdcf4c73bdd1cd533`（14:59:07—15:04:28）、短期`EXE_308047f3fd110872d736d389c2a83b1d`（15:04:35—15:10:52）、日任务`EXE_bacb57f35b912b2923fdca1550e93258`（15:10:59—15:14:31）。持久化为`LP_LONG_2a44ca15527049a3ad0c7fbc1c892eb5`、`LP_SHORT_fbc3b71539d742ffbb00a4b83d4254ad`、`TASK_430405d0a5df47978b7bd9be63d3630f`。
- 本轮审核未输出needs_human_review，因此没有调用审核问题编译器，**新增诊断未被触发**；首次编译失败证据仍未取得，不能据此宣称该问题已修复。
- **本轮未通过的原因转移到日任务**：①任务含视频项“观看《中医学基础》绪论章节视频”4分钟，与用户“不安排视频”以及短期计划正文自述“不安排视频”直接矛盾；②三项合计59.5分钟（4+4.5+51），超出每日35分钟上限，调度记录target_minutes=60、new_learning_minutes=60。
- 代码定位（本地与生产SHA一致，尚未修改）：`services/learning_plan.py` `a8694bb5df3ae90d9340f8b580b1f01a39c5a80d1123dde2dcb5ece03a61edc6`；`agents/learning_plan_service.py` `ebd876585d8474997c9af9297fe479e3baeee08f73029ef129ae4b7765146507`。`materialize_daily_task`以R=min(recommended_minutes, available_minutes)取预算，available_minutes缺失时`task_minutes=max(10,min(1440,budget))`直接采用task_load_policy的recommended值；`materialize_daily_task_items`在无视频原子且存在视频解析器时**无条件**插入video_section，未读取“不视频”偏好。
- 复测结束时已跨到9月10日（日历显示9月10日为今天且今日任务未完成，9月9日为已学习）；任务创建时间15:14:30Z仍属9月9日，不是今日新增。未执行或完成任何任务，未修改业务数据。
