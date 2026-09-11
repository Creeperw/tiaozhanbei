# 长期规划编译失败取证（2026-09-07）

## 结论与用户影响

本次真实请求在编译阶段失败，长期计划未保存，短期计划未执行。已确认首个阶段引用拒绝是校验策略误判：把阶段正文中正常提及下一阶段的 ID，等同于引用跨越了阶段边界。同时，真实合同第二阶段安排摘要存在非逐字的压缩拼接错误。这两个问题相互独立，不能只修改第一个错误便宣称规划已修复。

16:08 UTC 取证轮只部署诊断，以下第 1–5 节保留该轮事实。用户随后批准业务修复；修复与后续验证状态见文末更新。

## 证据标识

- 模式：`COMPETITION_APP_MODE=live`；模型：`deepseek-v4-flash`。
- thread：`THREAD_17887970582137b7a5ea6eea6a`。
- execution：`EXE_3bf21ac8af7951eb1c6ec0b80ab8ffc7`。
- 开始：2026-09-07 16:04 UTC；失败：约 16:08 UTC。
- 服务器证据目录：`/srv/tiaozhanbei/runtime/competition_app/evaluation/compiler-failure-evidence/`。
- `failure-1.json`：真实首次合同及正文，28,681 字节。
- `failure-2.json`：同稿重提取返回的拒绝结果，13,963 字节。
- `failure-3.json`：整稿返修后的正文及栏目拒绝结果，14,186 字节。
- 三份均 `sanitization_changed_evidence=false`；目录权限 0700、证据文件 0600。
- 采样配置已改名 `config.disabled.json`，不会继续采集。
- 初稿 source digest：`55fd48a8a3d06eb96e18656b18f6062c7b80ea8b197a1e601fe7a4f170207109`。
- 初稿 route digest：`e19a46e5bec31b9d1f4c00bbeceaca551ef9d78e9186675ec0248344a147491f`。

## 1. 第一阶段：阶段归属误判（已证实）

位置：`backend/competition_app/agents/plan_contract_compiler.py:1850–1853`（当前诊断版本）。

该条件对引文全文搜索其他阶段 ID；只要出现就拒绝。它没有区分“另一个阶段的标题/内容”与“本阶段说明中提到下一阶段”。

真实第一阶段引文：

- 长度 518 字，原文位置 745，结束位置 1263。
- `source_field`、可信阶段、逐字匹配、顺序、本阶段 ID、全部教材、摘要、时长检查均为 true。
- 唯一 false：`no_other_stage_id`。
- 引文内无其他阶段标题；有本阶段自己的晋级说明：“该验收通过后才应把剩余精力整体转移到 stage-2”。
- `prompt_skills/plan_contract_compiler/compile_plan_contract.md:33` 同时要求“完整连续原文段”和“不含其他阶段ID”。遇到上述合法晋级说明，这两个要求发生冲突。

因果对照：原样重放稳定复现 `/stages/0/stage_id`；只缩短该引文至本阶段验收原句结束，不改正文或学习决策，第一阶段通过，错误转移到第二阶段。

## 2. 第二阶段：摘要压缩拼接（已证实）

位置：`plan_contract_compiler.py:1848`，要求 `schedule_summary` 连续逐字存在于阶段引文。

真实阶段引文本身逐字正确，但模型输出的 156 字安排摘要把前置说明、当前复习主线、四个节点压缩拼成一段。例：摘要含“按节点一至节点四推进，依次完成”，正文原样列出四个独立节点，没有这段连续措辞。`summary_position_in_document=-1`，`summary_in_quote=false`。

对照：在第一阶段引文已缩短的基础上，只把第二阶段摘要及其锚点换为原阶段中从前置说明到节点四的连续原文，原生产编译器返回 `compiled`、`issues=[]`。

该对照未调用真实模型、未保存计划。它证明原稿可以被正确提取并编译，不证明选书、前置条件等后续业务校验通过。

## 3. 后续失败：提取错误转成整稿返修（已证实路径）

1. 首次合同：模型声明 `compiled`，本地阶段门禁拒绝。
2. 同稿重提取：模型直接返回 `needs_revision / schema_invalid@/field_anchors`。该错误是模型声明，不是已证实的 JSON Schema 库异常。
3. `diagnosis.py:900–951` 进入整稿返修。
4. 返修稿把原 `【最终目标】` 改成 `【当前主目标】`，其他五栏仍在。
5. `contracts/route_binding.py:23` 只接受长期固定栏目 `最终目标`，因此报 `missing_required_field@/plan_document/final_goal`；在调用编译模型之前即失败。
6. 仅在内存中恢复该标题，栏目检查返回空问题列表。不能据此认定后续全部校验通过。

`learning_plan.md` 中通用排版示例确实有 `【当前主目标】`，但未验证它导致本次模型改标题的因果关系，不把“模型照抄示例”列为已证实根因。

## 4. 纠错与诊断边界（已离线验证）

固定路线编译请求没有接入客户端 `_result_validator`。`openai_compatible.py` 在 JSON Schema 合法后返回；来源校验发生在调用返回之后。MockTransport 全链验证：非逐字的验收字段仍只调用一次，客户端与模型轨迹记录 `compiled`，然后本地编译器拒绝。

`model_trace.py:213` 的成功记录早于本地门禁；普通轨迹只保留模型的 status/issues，不保留原始合同。这解释了此前为什么无法从历史记录确定实际失败文字，以及为什么模型轨迹的 compiled 不等于整体编译通过。

另一个既有路径：`diagnosis.py:1049–1065` 的业务返修编译只有一次，失败直接抛错。该路径解释上一轮验收字段错误的传播，本次最终失败走的是前述初稿编译返修路径，二者不得混淆。

## 5. 验证、部署与数据边界

- 纯离线诊断及原有编译测试：82 passed。
- AST 对照：去掉新增观察包装与 raw 引用赋值后，原业务实现与部署前完全一致。
- 部署仅两个文件：Compiler、新 `llm/compiler_failure_evidence.py`。
- 备份：`/srv/tiaozhanbei-backups/compiler-failure-evidence-20260907/{before,deploy}.tgz`。
- 当前服务 PID：120437；active；NRestarts=0。
- Compiler SHA：`c9db2631be3fae7887b8dea43f13080806b7bde15e55451697758ece869bcee8`。
- 新诊断 SHA：`53d96d42c2941b2e4cbf543c7c45b56de9885bc08230845196b896c2469cebc7`。
- 当前账号在两张计划状态表中均 0；在途运行 0。
- question_attempts：116，SHA `918d8dab810a70ae07298348d80130d90f34458ba76a08bca5338af0d0acb436`。
- daily_task_items：44，SHA `5dd8e80f410441d06de552f843715f3dab15e49b824d675da93dd407cbb91bbe`。
- learning_activity_records：235，SHA `b7c74ea038421c25d39be2d1a8cbd4de426d5d3d373a3f59850378bf47e0c6e5`。
- 三表哈希与原基线完全相同；未操作其他账号、教材完成记录或今日任务。

## 取证时提出的修复边界

1. 使用实际阶段段落边界判断引用归属，不以引文中出现其他阶段 ID 直接拒绝；仍拒绝跨段借用教材、时长或安排。
2. 把来源校验结果接入有界的同稿提取纠错，区分提取错误与正文缺失；不允许通过模糊匹配接受改写字段。
3. 提取纠错不驱动规划作者重写已完整的正文；确需业务返修时保持当前层固定栏目。
4. 保留失败现场诊断能力的默认关闭与权限限制；不要全局开启原始模型或思考日志。

## 证据限制

本次已获得阶段引用错误和摘要拼接错误的真实因果证据。上一轮 `THREAD_1788795887727132977aca96eb8` 的验收文字未保存，不能用本次证据声称已还原其具体字符差异。

## 批准后修复更新（2026-09-07 16:25 UTC）

用户批准修复后，仅修改 release/source 内文件，未修改原工作区、其他账号或学习数据。

### 已部署修复

- `contracts/route_binding.py`：按明确阶段标题、标题层级和下一固定栏目确定边界。允许本阶段晋级句提及下一阶段；重复、缺失、倒序阶段标题及跨段引用拒绝。旧无标题正文保持保守规则。
- `agents/plan_contract_compiler.py`：固定路线所有编译入口统一首次提取加最多一次同稿重提取。来源检查仍为严格逐字匹配；不通过时给同稿反馈，不修改原文。第二次若 JSON/结构异常，保留首次失败；传输异常不掩盖。
- `agents/diagnosis.py`：固定路线不再叠加外层提取重试。只有本地六栏检查确认正文缺失时才允许编译失败驱动作者返修，模型自行声明的错误路径不能授权改稿。后续 PlanningValidator 的业务返修仍保留。
- `llm/anchor_diagnostics.py`：增加真实阶段边界检查结果，原“出现其他 ID”仅作观察，不再作为拒绝门禁。
- 编译提示升级 `1.6.1`：明确阶段内引用规则，安排与验收直接摘录连续原文，不压缩拼接。

预算说明：每份固定路线正文最多两次 `complete_json`，客户端已有单次最多两轮结构纠错保持不变；并非整个工作流最多两次 HTTP 请求。没有接入 `_result_validator`；同稿纠错由编译器在完整来源检查之后统一管理。模型轨迹 `compiled` 仍不等于最终合同通过。

### 离线证据

- 定向测试合计 **214 passed**，包含阶段边界、提取纠错、Diagnosis 调用链、前置门禁和接口能力；仅一条第三方弃用预警。
- 新测试验证：正常晋级提及通过，真实跨段拒绝，重复/缺失/倒序标题拒绝，`stage-1`/`stage-10` 不混淆，最后阶段不能跨至下一栏目；摘要/验收改写同稿纠错，预算耗尽不调用作者，模型伪报缺栏目也不触发改稿。
- 真实 `failure-1.json` 仅只读载入离线内存：原始第一阶段引用不改即可通过；错误准确转到第二阶段非逐字摘要。仅更正第二阶段摘要与其锚点后，合同 `compiled`，正文 source digest 与原现场完全一致。
- 该人工假模型对照证明校验策略正确，不代表真实模型必然成功或后续业务门禁已通过。

### 精确部署及正在进行的在线验证

- 仅上述五个运行文件；与线上差异逐项核查，没有夹带工作树其他改动。
- 备份：`/srv/tiaozhanbei-backups/fixed-route-repair-20260907/{before,deploy}.tgz`。
- 本地/线上五文件 SHA256 全部一致；语法检查通过。
- 服务 PID **122048**，active，NRestarts=0；health **live / deepseek-v4-flash / formal / langgraph**。
- 部署前全局在途运行 0；zjf 三类学习记录计数与 SHA 均同第 5 节基线，计划两表仍 0。
- 前端确认 zjf、中医执业医师考试，仅发送一次原长期请求：`THREAD_17887983239329987e2986160d8` / `EXE_7b7ded1c9287a4500a41e680896c5185`。
- 本条记录时状态 running，尚未宣称计划保存成功；短期、教材完成和今日任务均未执行。失败采样配置仍停用，历史证据未删除。

### 在线终态更新（16:41 UTC）

该唯一请求最终 **failed**，失败步骤为 `learning_plan`，不是 `diagnosis` 或合同编译；长期计划仍未保存。

1. 16:33:09 初稿 5421 字，编译通过；后续 PlanningValidator 拒绝未确认前置条件的选书，触发原有业务返修。
2. 返修正文 6110 字，第一次提取的第一阶段摘要非逐字：`summary_position_in_document=-1`，阶段边界检查为 true。16:38:18 进入新增加的同稿提取纠错。
3. 此后执行进入审核与 `learning_plan_service`，说明返修合同及前置业务校验已通过；没有再次触发作者整稿重写。
4. 最终计划服务抛出医疗教育安全边界错误。正式规划正文中的免责声明为：“本规划为中医药教学与备考语境下的长期学习安排，方剂与证候内容仅作为考试知识训练，不构成对任何真实患者的个体化诊疗、处方或疗效承诺”。
5. `services/learning_plan.py:530–541` 匹配到“真实患者”和“疗效承诺”，但第 63 行否定规则只涵盖“不得、不应、禁止、不要、不能”，不识别“不构成”。以该原句直接离线调用 `validate_medical_education_safety`，稳定复现同一异常。该文件本地与线上 SHA 均为 `7ed985f3e5198e36df3ba3c96d1022a05da51ac162ac6136430b2c8d57a94244`。

这是新的安全免责声明误判，未在本次编译修复中修改。不能通过关闭安全检查解决；后续需审查否定语义作用范围，并测试免责声明、真实诊疗指令及二者混合句。

最终 uid6 两张计划表仍各 0；全局在途运行 0；question_attempts 116、daily_task_items 44、learning_activity_records 235，三表 SHA 与原基线全部一致。短期、教材完成、今日任务和其他账号未操作。浏览器等待已结束，无待完成请求。

## 7. 医疗免责声明误判修复（2026-09-08 本地 / 09-07 UTC）

用户批准先处理医疗安全误判，提示词分散整理仅记录待办，本轮不重构 Diagnosis 提示词。

### 实现和边界

- 规划专用 `PlanAuditModelOutput` 增加必填 `medical_safety`：safe / unsafe / uncertain。审核读取完整 proposal 和合同；解释对象、用途、否定范围及上下文，免责声明不能替同文实际诊疗指令免责。
- 未明确 safe、协议无效、安全矛盾或 unresolved 均不得发布；整体 pass 和返修次数不能压过安全阻断。资源/试卷原审核 schema 不变。
- `services/plan_safety.py` 签发进程内 HMAC 凭据，绑定完整 proposal 摘要、账号、scope、审核 ID及一小时有效期。模型不生成凭据。发布前验证，正文变化、跨账号/范围、缺失、伪造、过期均拒绝；重启后需重新审核。
- 实际前端长短期适配层强制凭据，并保留原合同摘要、审核类型、父计划及并发校验；服务在有效凭据下不再用关键词重新推翻语义审核。未审核的旧服务直调保留原保守正则，日任务和导入路径未改。
- 当前启动命令为单服务进程 Uvicorn；LangGraph 在同进程执行和恢复 `AuditResult`。凭据不是跨进程共享审批机制，也不证明模型语义判断必然正确。

### 验证和部署

- **241 项离线通过**，覆盖新增 28 项安全凭据/审核/适配层/长短期服务测试，以及原医疗边界、资源/试卷审核、固定路线编译和 LangGraph 回归。JSON 审核结果往返仍保留凭据。测试使用 stub，不是 Live 模型准确率证明。
- 本轮七个运行文件编辑器诊断无错，限定 diff whitespace 检查通过；逐文件与线上比较，仅本次安全修复差异。
- 精确部署七文件：`services/plan_safety.py`、`contracts/resource.py`、`llm/schemas.py`、`agents/audit.py`、`services/learning_plan.py`、`agents/learning_plan_service.py`、`prompt_skills/audit_agent/learning_plan.md`。本地 Stub 合同兼容更新及测试文件未部署。
- 备份：`/srv/tiaozhanbei-backups/medical-safety-review-20260908/{before,deploy}.tgz`。若回滚，恢复 before 六个原文件并移除本次新增 `services/plan_safety.py`，再重启；不得覆盖其他改动。
- 线上语法检查通过，七文件 SHA 与本地一致；PID **123992**，active、NRestarts=0；health **live / deepseek-v4-flash / formal / langgraph**。
- 部署前全局在途 0，uid6 三表计数及 SHA 原样，计划两表各 0。

### 单次前端 Live（进行中）

2026-09-07 17:00:54 UTC，已确认 zjf / 中医执业医师考试后，前端发送一次原长期计划请求：

- thread：`THREAD_17888004559546ee2536069408`
- execution：`EXE_00d9447569a2ab794d495eef4a6df860`
- 当前 running，未宣称计划已保存或全部验收完成。短期、教材完成、今日任务均未执行，其他账号未操作。

### 终态更新：等待前置课程确认，非医疗安全拒绝

- 17:12:13 UTC，状态为 `interrupted`。持久化 `payload_json.interrupt`：agent=diagnosis_agent、step_id=diagnosis、interrupt_type=planning_prerequisite、prerequisite_kind=course_status_confirmation、required_prerequisite_courses=[中医诊断学]。
- 正式问题：“你是否已完成或能够通过以下前置课程验收：中医诊断学？如果学过但忘了，请说明目前能回忆或应用到什么程度。” 原因：“模型选择的阶段需要尚未确认的强前置课程。”
- 已完成节点仅 knowledge、memory、route_resolution；**尚未进入新版医疗安全审核或计划发布**，因此本次不能视为医疗安全修复的在线通过，也不是旧免责声明误判复发。
- 日志显示初稿 3389 字合同成功编译；17:05:29 和 17:12:13 两次 PlanningValidator 均返回 `prerequisite_unconfirmed@/selected_books`。`planning_validator.py` 校验所选阶段/教材的强前置是否确认或已纳入选书；`diagnosis.py` 有界返修后转为真实状态追问。不能替用户声明课程已完成或已掌握。
- 17:12:19 前端还报告 `ERR_INCOMPLETE_CHUNKED_ENCODING`。业务中断此前已持久化，服务 PID123992 未变、NRestarts=0；不能把断连当作业务中断根因，传输异常未另行修复。
- 最终两张计划表各 0，全局 running/pending=0；三类保护数据仍 116/44/235，SHA 与原基线逐一相同。没有第二次提交，浏览器等待全部结束；当前会话等待真实前置状态回答。
