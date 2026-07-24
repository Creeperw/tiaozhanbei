# 前端接口参考

本文面向时珍智训正式 React 前端，描述当前 `main` 分支可用的认证、对话、学习规划、学习工坊、知识库、复习与兼容业务接口。

- 后端默认地址：`http://127.0.0.1:7860`
- 正式主接口前缀：`/api/v1`
- 迁移期业务接口前缀：`/api`
- 主 OpenAPI：`GET /openapi.json`
- Swagger UI：`GET /docs`
- 兼容业务 OpenAPI：`GET /api/v1/platform/openapi.json`，需登录且启用兼容层

本文记录的是前端集成规则和关键数据契约。字段级约束以运行中 OpenAPI 为最终依据。

环境搭建、同源部署与升级见 [部署与升级指南](deployment.md)；数据库归属、迁移和备份恢复见
[数据库运维指南](database-operations.md)。前端不得直接连接数据库或自行维护用户数据归属。

## 1. 接口分层

| 浏览器请求 | 后端归属 | 稳定性 | 使用原则 |
|---|---|---|---|
| `/api/v1/*` | `competition_app` 主后端 | 正式接口 | 新功能优先使用 |
| `/api/*` | `backend-handoff` 兼容业务域 | 迁移接口 | 仅用于尚未迁移的页面 |
| `/health` | 主后端 | 正式接口 | 无需登录的存活检查 |

前端常量定义：

```js
export const API_BASE = '/api';
export const MAIN_API_BASE = '/api/v1';
export const AUTH_API_BASE = `${MAIN_API_BASE}/auth`;
```

开发环境中 Vite 按以下方式代理：

- `/api/v1/*` 原样转发到 `http://127.0.0.1:7860`；
- `/api/*` 去掉开头的 `/api` 后转发；
- 生产环境由 FastAPI 同源托管前端，并将 `/api` 挂载到兼容业务域。

前端不要硬编码 `7860`，也不要自行去掉 `/api`。业务代码只使用相对路径。

## 2. 通用请求规则

### 2.1 认证

主后端使用名为 `competition_session` 的 HttpOnly Cookie。登录或注册成功后浏览器自动保存，前端不得把令牌写入 localStorage。

所有受保护请求必须携带 Cookie：

```js
export async function fetchWithAuth(url, options = {}) {
  const headers = { ...options.headers };
  if (options.body !== undefined && !(options.body instanceof FormData)) {
    headers['Content-Type'] = 'application/json';
  }
  return fetch(url, {
    ...options,
    headers,
    credentials: 'include',
  });
}
```

Cookie 属性：`HttpOnly`、`SameSite=Lax`、`Path=/`。HTTPS 部署时设置 `AUTH_COOKIE_SECURE=true`。

公开路径只有首页静态资源、`/health`、`/openapi.json`、`/docs` 和 `/api/v1/auth/*`。其余接口默认需要登录。

### 2.2 内容类型

| 场景 | Content-Type |
|---|---|
| 普通 JSON | `application/json` |
| 上传文件 | `multipart/form-data`，不要手动设置 boundary |
| 原始文件导入 | 按接口说明直接发送二进制 body |
| 对话流 | 响应为 `text/event-stream` |

### 2.3 用户隔离

用户身份以 Cookie 对应的服务端会话为准。

- 前端不得通过 `learner_id`、`user_id` 切换用户；
- 对话请求中的 `learner_id` 只是兼容必填字段，服务端会覆盖为当前登录用户；
- 会话、规划、知识卡、试卷和复习队列均按当前登录用户隔离；
- 访问其他用户的资源通常返回 `403` 或按不存在处理为 `404`。

### 2.4 时间、分页和版本

- 时间使用 ISO 8601 字符串，前端负责按本地时区展示；
- 列表接口通常使用 `offset`、`limit`，返回 `items`、`total`；
- 需要长期兼容的数据包包含 `schema_version`，前端应校验主版本并忽略未知字段；
- `progress`、`mastery` 等比例字段范围为 `0` 到 `1`，展示时再乘以 100。

### 2.5 错误响应

一般错误：

```json
{
  "detail": "面向用户或开发者的错误说明"
}
```

参数校验错误：

```json
{
  "detail": [
    {
      "loc": ["body", "field_name"],
      "msg": "Field required",
      "type": "missing"
    }
  ]
}
```

| 状态码 | 含义 | 前端处理 |
|---|---|---|
| `400` | 请求业务格式错误 | 展示 `detail`，保留用户输入 |
| `401` | 未登录或会话过期 | 清空本地登录态并跳转登录页 |
| `403` | 权限不足 | 禁止重试，提示无权访问 |
| `404` | 资源不存在或不属于当前用户 | 返回上一级并刷新列表 |
| `409` | 状态冲突、重复 ID 或不可执行 | 展示冲突原因，不盲目重试 |
| `410` | 一次性请求已过期或旧接口已停用 | 重新取题/重新进入新接口，不复用旧请求 |
| `413` | 请求体过大 | 提示用户缩小上传或任务内容 |
| `422` | 参数或业务校验未通过 | 定位字段或展示 `detail` |
| `429` | 请求过快 | 读取 `Retry-After` 后再允许提交 |
| `503` | 可选服务或正式知识库未启用 | 展示能力暂不可用，不伪造数据 |

## 3. 认证与会话

### 3.1 注册

`POST /api/v1/auth/register`

```json
{
  "username": "lin_student",
  "password": "minimum-8-characters",
  "display_name": "林同学"
}
```

约束：用户名 3—64 字符且不能包含空白、`< > / \\`；密码 8—128 字符。

成功返回 `201` 并设置 Cookie：

```json
{
  "user": {
    "user_id": "USER_xxx",
    "username": "lin_student",
    "display_name": "林同学",
    "role": "user",
    "status": "active",
    "onboarding_required": true,
    "created_at": "2026-07-21T12:00:00Z"
  },
  "expires_at": "2026-08-20T12:00:00Z"
}
```

用户名重复返回 `409`。

新注册普通用户的 `onboarding_required=true`。前端必须先展示注册学情调查，不能渲染首页、
学习工坊或对话页。调查顺序为：

1. `GET /api/training/onboarding/group-templates` 和
   `GET /api/v1/qualification-targets` 加载选项；
2. `POST /api/training/onboarding/survey` 保存 L0 基线、学习画像和初始学习记忆；
3. `POST /api/v1/auth/onboarding/complete` 由主后端核验调查状态并关闭门禁。

第三步成功返回 `{"user": AuthUser, "onboarding_status": {...}}`，其中
`user.onboarding_required=false`。页面刷新或重新登录时必须以
`GET /api/v1/auth/me` 返回的该字段为准；不得只在前端内存中记录“已跳过”。必填基本信息包括
用户群体、学习/考试方向、学历/专业、基础水平和每日可投入时长。长期目标由所选资格考试
确定，不再要求用户重复填写；注册阶段也不采集短期目标或自由形式的“规划输入”。

已完成调查的用户再次打开“画像与记忆 → 学情调查”时，前端必须调用
`GET /api/training/onboarding/status` 回填表单，不能显示一份空调查。核心回填字段包括
`learner_group`、`major_or_role`、`tcm_foundation`、`target_exam_or_course`、
`textbook_route_id`、`textbook_route_version` 与 `daily_available_minutes`。

注册调查只提供 `cross_professional`（跨专业进阶）和 `academic`（学历教育）两个群体。
历史 `public_interest` 数据仅保留读取兼容，不得再显示为新用户选项。

学习/考试方向必须直接使用 `/api/v1/qualification-targets` 返回的五类资格考试，不得在前端
复制名单或混入专业、职称、职业技能等级、考研和课程学习入口：

1. 中医执业医师资格考试；
2. 中医执业助理医师资格考试；
3. 中西医结合执业医师资格考试；
4. 中西医结合执业助理医师资格考试；
5. 执业药师职业资格考试（中药学类）。

保存调查时把资格身份和教材路线一起写入 `goals`：

```json
{
  "learner_group": "academic",
  "goals": {
    "target_exam_or_course": "中医执业医师资格考试",
    "target_type": "certification",
    "exam_track_id": "EXAM_2025_TCM_PHYSICIAN",
    "textbook_route_id": "textbook_tcm_physician",
    "textbook_route_version": 1
  },
  "background": {
    "education_major": "非医学专业",
    "foundation_level": "零基础"
  },
  "preferences": {
    "daily_available_minutes": 45
  }
}
```

`exam_track_id` 与 `textbook_route_id` 含义不同，前者标识官方资格考试，后者标识可复用的
教材学习顺序。前端必须原样提交接口返回的两个字段。调查保存后，后端会同时建立可信的
活动学习目标；Diagnosis 和规划智能体应直接读取该目标，不得再次追问“具体考试名称”。

资格目录响应结构：

```json
{
  "schema_version": "1.0",
  "target_kind": "qualification_exam",
  "items": [
    {
      "target_id": "integrated_assistant",
      "official_name": "中西医结合执业助理医师资格考试",
      "target_type": "certification",
      "exam_track_id": "EXAM_2025_INTEGRATED_ASSISTANT",
      "planning_route_id": "tcm_physician_standard_degree",
      "textbook_route_id": "textbook_integrated_clinical",
      "textbook_route_version": 1
    }
  ],
  "total": 5
}
```

`target_id` 仅供前端选中状态使用；落库与后续智能体判断以 `official_name`、
`target_type`、`exam_track_id` 和教材路线字段为准。

### 3.2 登录、退出和当前用户

| 方法 | 路径 | 请求 | 返回 |
|---|---|---|---|
| `POST` | `/api/v1/auth/login` | `{username, password}` | 与注册成功响应相同 |
| `POST` | `/api/v1/auth/logout` | 无 | `{"status":"logged_out"}` |
| `GET` | `/api/v1/auth/me` | 无 | `{"user": AuthUser}` |

旧接口 `/token`、`/register`、`/send-code`、`/reset-password` 已停用并返回 `410`，新前端不得调用。

#### 登录页交互契约

- 未登录用户首先看到公开展示页，账号和密码字段只在用户点击登录入口后出现在模态弹层中；
- “登录”“登录已有账号”打开登录模式，“开始学习”“开启智训之旅”打开注册模式；
- 登录弹层可切换至注册，注册弹层可返回登录；“返回展示页”关闭弹层并将默认模式复位为登录；
- 页面挂载时请求 `GET /health`，仅用于提示认证服务是否可达，不替代真正的登录校验；
- Vite 开发环境必须将 `/health` 原样代理到主后端，生产环境由 FastAPI 同源响应；
- 登录和注册请求继续使用 `credentials: "include"`，成功后以响应中的 `user` 更新前端状态；
- 网络不可达时显示“认证服务尚未连接”，不得伪造登录成功或回退到旧认证接口；
- 页面需适配移动端，弹层内容自身可滚动，不应产生横向页面溢出。

### 3.3 对话会话

| 方法 | 路径 | 说明 |
|---|---|---|
| `GET` | `/api/v1/conversations` | 当前用户的会话列表 |
| `POST` | `/api/v1/conversations` | 创建会话，请求 `{ "title": "新对话" }` |
| `GET` | `/api/v1/conversations/{session_id}/messages` | 获取消息 |
| `PATCH` | `/api/v1/conversations/{session_id}` | 重命名，请求 `{ "title": "方剂学习" }` |
| `DELETE` | `/api/v1/conversations/{session_id}` | 删除会话及其消息 |

消息结构：

```json
{
  "id": "MESSAGE_xxx",
  "role": "user",
  "content": "请讲解四君子汤，并给我一道题。",
  "timestamp": "2026-07-21T20:30:00"
}
```

`role` 当前使用 `user`、`assistant`。前端应忽略将来增加的消息元数据。

## 4. 多智能体对话与中断恢复

### 4.1 发起流式任务

`POST /api/v1/review-cards/stream`

同一请求也可发送到 `POST /api/v1/review-cards` 并等待完整 JSON 结果。正式对话界面应优先使用流式接口；同步接口适用于调试、脚本调用和不需要展示执行过程的场景。

最小请求：

```json
{
  "thread_id": "THREAD_由前端生成的唯一ID",
  "conversation_id": "CONV_xxx",
  "learner_id": "authenticated-user",
  "user_request": "请结合我的学习状态，给我制定一份长期学习规划。",
  "available_minutes": 60,
  "messages": [
    {
      "message_id": "MESSAGE_xxx",
      "role": "user",
      "content": "请结合我的学习状态，给我制定一份长期学习规划。"
    }
  ]
}
```

`available_minutes` 范围为 1—1440。24 小时是预算上限，不表示系统必须安排满。

`conversation_id` 是连续问答的上下文主键。服务端会合并该会话已持久化的消息，因此页面刷新后即使前端只提交当前问题，也不会丢失“这些证型”“上述内容”等指代所需的历史主题。阈值以内只向智能体提供最近对话；总字符数超过服务端阈值后，Planner 必须先编排 `memory_agent`，由记忆管理智能体生成不超过 2000 字的会话摘要，再把摘要交给后续知识检索、讲解或规划步骤。前端不得自行伪造压缩摘要。

规划调研中的简短补充或纠正（例如“零基础”“每周 4 天”“不对，我要考执业医师资格证”）会继承该会话最近一次明确的规划层级。即使检查点已失效或页面刷新后前端改为发起新请求，服务端也会继续规划链路，不会把考试目标误送到教材知识点检索。用户明确提出讲解、组卷、知识卡或练习时则视为新任务，正常切换链路。

规划层级规则：

- 用户明确说“长期规划”“短期计划”“今天的任务”时，可传强约束 `plan_scope`；
- 用户表达模糊时只传 `plan_scope_hint` 或不传，让模型判断；
- 可选值：`long_term`、`short_term`、`daily_task`、`unspecified`；
- 不要仅凭前端关键词强制设置 `plan_scope`。

规划按钮启用前先读取统一前置状态：

`GET /api/v1/planning/readiness?scope=long_term|short_term|daily_task`

服务端会返回 `status`、`can_generate`、`required_action`、`reason_codes`、需要追问的 `questions`、缺少的画像字段和上层计划状态。状态可能为 `ready`、`needs_profile`、`needs_long_term_plan`、`needs_short_term_plan`、`stale_parent_plan`。长期规划在没有任何有效个人画像时逐项追问目标、基础和可持续时间；短期计划必须有当前长期规划；当日任务必须有当前短期计划。前端提示只用于提前解释，正式执行接口还会再次校验，不能通过绕开按钮跳过。

前端不需要重复拼装用户画像、学习状态、已有计划和系统数据。登录态下服务端会读取可信数据。只有上传内容或用户刚刚明确确认、但尚未持久化的信息才需要随请求提交。

### 4.2 SSE 帧

响应头：

```text
Content-Type: text/event-stream
Cache-Control: no-cache
X-Accel-Buffering: no
```

每帧格式：

```text
data: {"event":"run_started","thread_id":"THREAD_xxx"}

```

前端必须按空行切帧，再解析以 `data: ` 开头的行；不要按单个网络 chunk 解析 JSON。

常见事件：

| 事件 | 作用 | 关键字段 |
|---|---|---|
| `run_started` | 新任务开始 | `thread_id`, `user_request` |
| `graph_compiled` | LangGraph 路径确定 | `engine`, `levels`, `nodes`, `control_edges` |
| `step_started` | 智能体步骤开始 | `step_id`, `agent` |
| `model_input` | 模型输入记录 | `agent`, `raw_input` |
| `model_delta` | 模型增量输出 | `agent`, `delta` |
| `model_transport` | 模型传输记录 | 仅技术详情使用 |
| `model_output` | 模型步骤输出 | `agent`, `output` |
| `system_output` | 确定性服务输出 | `step_id`, `output` |
| `web_search_status` | 网络检索状态 | `status`, `query`, `message` |
| `step_completed` | 智能体步骤完成 | `step_id`, `agent` |
| `graph_interrupted` | 图在追问节点暂停 | 中断节点信息 |
| `run_interrupted` | 本次流的终止事件 | `result`, `assistant_message` |
| `run_resumed` | 从检查点恢复 | `thread_id` |
| `graph_resume_requested` | 已提交恢复信息 | 检查点信息 |
| `graph_resumed` | 图恢复执行 | 节点信息 |
| `run_completed` | 成功终止事件 | `result`, `assistant_message` |
| `run_failed` | 失败终止事件 | `error_type`, `message`, `thread_id` |

前端只把六个角色展示给用户：任务规划、记忆管理、学情诊断、知识库管理、专家、审核裁判。原始 `agent`、`step_id` 和工具调用放入可展开技术详情，不直接作为第七个智能体展示。

收到 `graph_compiled` 后，前端必须立即按 `nodes[].step_id` 和 `nodes[].agent` 登记本次计划节点。节点尚未收到 `step_started` 时显示“等待执行”，不能显示“本次无需参与”；因此组卷图中的 `audit_agent` 会在审核真正开始前就明确列为参与节点。`run_completed` 到达后仍处于等待状态的计划节点统一收敛为已完成。

### 4.3 终止事件

成功：

```json
{
  "event": "run_completed",
  "result": {
    "status": "success",
    "ui_actions": []
  },
  "assistant_message": "面向用户的自然语言回答"
}
```

需要追问：

```json
{
  "event": "run_interrupted",
  "result": {
    "status": "interrupted",
    "thread_id": "THREAD_xxx",
    "interrupt": {
      "step_id": "diagnosis",
      "reason": "还需要确认学习基础",
      "questions": ["你目前是否学过中医基础理论？"]
    }
  },
  "assistant_message": "我还需要确认一点信息……"
}
```

`assistant_message` 是正式自然语言投影，应作为聊天正文；`result` 是页面跳转、持久化和结构化渲染的数据源。不要把整个 `result` 直接打印到聊天气泡。

### 4.4 恢复任务

刷新或断线后先读取：

`GET /api/v1/review-cards/runs/{thread_id}`

若状态为 `interrupted`，提交：

`POST /api/v1/review-cards/runs/{thread_id}/resume/stream`

```json
{
  "answer": "我零基础，目标是中医执业医师资格考试。",
  "profile_updates": {
    "learning_background": "零基础",
    "learning_goal": "中医执业医师资格考试"
  }
}
```

恢复必须复用原 `thread_id`，不能重新调用新任务接口。服务端会从 LangGraph 检查点继续，不重复已完成步骤。

`answer` 是恢复接口的必填事实来源，`profile_updates` 是前端已经持有明确结构化字段时的可选辅助，
不是必填项。对于只缺一个画像字段的 `profile_completion` 中断，前端可以直接提交
`{"answer":"中医执业医师考试"}`；服务端会将答案写入本次待确认的 `learning_goal`。对于
`route_resolution` 中断，Memory Agent 会从简短答案中提炼明确的目标、背景或时间事实并写入权威画像。
前端不得把原问题、旧答案和新答案拼成一段新的规划请求，也不得自行修改路线 ID。

`plan_scope` 仅在前端掌握了明确业务字段时可选传入。正常追问只提交 `answer` 即可：Diagnosis
Agent 在中断负载的 `requested_scope` 中声明要补的计划层级，恢复节点以该字段为权威依据。例如，
当日任务缺少短期计划时，用户回答“可以”会继续制定短期计划，不会因为前端未传 `plan_scope`
而回退成长期计划。前端不得通过“可以/好的”等关键词自行猜测计划层级。

当本次确认更新 `learning_goal` 或 `learning_background` 时，服务端会在同一检查点恢复过程中重新执行
上游路线解析，再让 Diagnosis Agent 继续；教材路线内部的追问也属于路线依赖。已经确认
五类受支持资格考试中的一个后，不应再次出现“请说明具体考试或专业方向”的同义追问。注册调查已经
保存活动学习目标时，即使用户只说“制定长期计划”，也应直接采用该目标。当前产品不再为中医专长、
传统医学师承、职称、职业技能、考研或纯课程目标启动默认规划。

前端收到新的 `run_interrupted` 时，应以新响应中的 `questions`、`profile_fields` 和
`interrupt_type` 覆盖旧追问卡；不要继续展示上一次中断文案。只有 `run_completed` 才清除该
`thread_id` 的待恢复状态。

前置课程追问同时接受肯定和否定答案。用户明确回答“没学过/未完成”后，该课程会作为
`unmet_prerequisite_courses` 注入规划校验；长期规划必须把它纳入起始阶段，但不得再次追问
同一课程。只有用户没有给出可判定答案时，检查点才继续保持中断。

SSE 断开不代表任务停止。断线后轮询运行状态，不要立即创建同内容的新任务。

## 5. 学习状态、首页和学习路径

### 5.1 核心接口

| 方法 | 路径 | 用途 |
|---|---|---|
| `GET` | `/api/v1/dashboard/home` | 首页摘要、今日任务、复习任务和继续学习 |
| `GET` | `/api/v1/learning-context` | 当前用户画像、行为、完整长短期计划正文、复习队列和能力状态 |
| `GET` | `/api/v1/agent-data-capabilities` | 智能体可读写数据权限清单 |
| `POST` | `/api/v1/learning-tasks/current/complete` | 完成当前当日任务 |
| `POST` | `/api/v1/learning-tasks/current/refresh` | 到期时幂等轮换当前当日任务；未到期只返回现状 |
| `GET` | `/api/v1/learning-path` | 获取长期规划的阶段层 |
| `GET` | `/api/v1/learning-path?parent_id={node_id}` | 获取指定阶段的教材层 |
| `GET` | `/api/v1/learning-path/nodes?parent_id={node_id}` | 等价的显式子节点接口 |
| `GET` | `/api/v1/learning-routes?status=approved&q=` | 获取非个性化经典路线目录 |
| `GET` | `/api/v1/learning-routes/{route_id}` | 获取一条经典路线的阶段、教材和来源 |
| `GET` | `/api/v1/qualification-targets` | 获取注册与规划可选择的五类资格考试及其教材路线映射 |
| `GET` | `/api/v1/learning-activity/summary?days=30&recent_limit=20` | 当前用户行为指标、计数器和最近事件 |
| `GET` | `/api/v1/learning-activity/trends?days=30` | 当前用户学习趋势 |

长期规划的结构化阶段位于 `learning-context.long_term_plan.stages`，元素固定为 `{ "stage": 1, "book": ["《教材》"], "goal": "阶段目标" }`。长期规划更新时，正文、`stages`、`planning_route`、版本号及 `/api/v1/learning-path` 投影会作为同一次写入一起变化；前端不得从规划正文二次解析阶段。流式对话的长期规划完成消息会由系统附加同源的 `long_term_plan_stages` JSON 小块，供即时渲染，不是模型自由生成字段。

注册调查不是只供前端展示的数据。`learning-context.user_profile` 会将已持久化的调查转换为
规划智能体可直接使用的可信事实，包括：

- `learning_goal`：优先使用所选经典路线的 `target_exam_or_course`；
- `learning_background`：基础水平、专业背景和已学课程的自然语言摘要；
- `daily_available_minutes`、`user_major_or_profession`、`completed_courses`；
- `goals.goal_name`、`goals.goal_type`、`goals.textbook_route_id` 和路线版本；
- `user_preference`：学习时段、资源形式和难度偏好。

同一响应的 `onboarding` 字段保留完整 `survey_answers`、`field_sources` 与 `l0_baseline`，
供画像页回显和数据来源说明。Diagnosis、路线解析和规划准备度统一读取服务端组装后的
`user_profile`，不得要求用户重复提供其中已经存在的目标、基础、专业或时间信息；只有报考
途径等调查中确实不存在、且会改变路线的事实才允许继续追问。

个性数据中的“今日任务卡”只读取 `learning-context.learning_task`。映射字段为：`task_id -> key`、`task_content -> title`、`learning_chapter -> 今日章节`、`focus_knowledge_points -> 重点知识点`、`estimated_minutes -> duration_min`、`completion_criteria -> reason`，并可保留 `expected_output` 与 `status`。旧规划摘要中的 `daily_tasks` 不再覆盖正式当日任务；页面也不再展示独立的“本周计划卡”。当 `learning_task=null` 时，前端应说明需先制定短期计划，再生成今日任务。

今日任务采用服务端管理的滚动 24 小时窗口。任务本体返回 `refresh_started_at`、`refresh_due_at`；`learning-context` 与 `dashboard/home` 同时返回 `daily_task_timer`：

```json
{
  "policy": "rolling_24h",
  "interval_hours": 24,
  "auto_refresh_enabled": true,
  "available": true,
  "state": "active",
  "server_time": "2026-07-23T08:00:00+00:00",
  "refresh_started_at": "2026-07-23T07:30:00+00:00",
  "refresh_due_at": "2026-07-24T07:30:00+00:00",
  "remaining_seconds": 84600,
  "refreshed": false,
  "current_task_id": "TASK_xxx"
}
```

前端以 `server_time` 和 `remaining_seconds` 展示倒计时，到零调用刷新接口并重新读取页面数据。截止判断必须服从服务端，禁止用浏览器本地日期自行换任务。刷新接口可以由多个标签页同时调用：未到期时不换，到期后只会把当前任务推进一次。用户在截止时离线也不会丢失轮换；下一次读取上述任一页面接口时，服务端会先补做过期轮换。轮换仅使用当前短期计划的 `task_blocks`，不会静默修改长期或短期计划；没有短期计划时返回 `available=false`、`reason=short_term_plan_required`。

已有任务在升级前没有刷新字段时，第一次读取会从该时刻开始获得完整 24 小时窗口，避免部署升级直接覆盖尚未完成的学习内容。字段存放在计划 JSON 中，无需新增数据库表或迁移。

学习工坊右栏使用 `GET /api/v1/dashboard/home` 的 `current_learning_task`，不要自行从任务正文解析章节或知识点。后端会以知识仓库为准把模型给出的可读知识点名称解析为正式 ID，并返回可执行知识卡动作：

```json
{
  "current_learning_task": {
    "task_id": "TASK_xxx",
    "title": "学习四君子汤的组成、功用和配伍意义",
    "duration": "25 分钟",
    "refresh_started_at": "2026-07-23T07:30:00+00:00",
    "refresh_due_at": "2026-07-24T07:30:00+00:00",
    "learning_chapter": {
      "book": "方剂学",
      "title": "补益剂·补气",
      "source": "knowledge_repository"
    },
    "focus_knowledge_points": ["四君子汤"],
    "knowledge_cards": [
      {
        "kp_id": "KP_xxx",
        "title": "四君子汤",
        "book": "方剂学",
        "chapter": "补益剂·补气",
        "action": {
          "action_type": "navigate",
          "label": "学习知识卡",
          "destination": "workshop.knowledge_card",
          "params": { "kp_id": "KP_xxx" }
        }
      }
    ]
  }
}
```

`current_learning_task=null` 表示当前没有未完成的正式今日任务。点击知识卡时按 `action.destination` 白名单跳转，并把 `params.kp_id` 交给知识卡模块；知识卡模块会复用 `/api/v1/workshop/knowledge-cards/resolve` 完成生成或更新。

资格考试必须单选。当前只允许五类受支持目标进入新规划：四类中医/中西医结合医师资格与
执业药师职业资格考试（中药学类）。前端不得再提交中医专长、传统医学师承、职称、职业技能、
考研或纯课程路线。历史记录仍可读取，但不会出现在新用户目录，也不能被模型选为新的默认规划。
若会话中断追问后，用户给出五类中的唯一考试名称，后端将该回复作为恢复答案直接解析并继续，
不会再次提出相同问题。

四类医师资格分别保留独立 `exam_track_id`；中医执业/助理医师复用
`textbook_tcm_physician`，中西医结合执业/助理医师复用
`textbook_integrated_clinical`。执业药师使用 `textbook_tcm_pharmacy`。这意味着考试身份
不会因复用教材而合并，长期规划正文与 `long_term_plan_stages` 则按对应教材路线生成。

### 5.2 学习路径数据

```json
{
  "schema_version": "1.0",
  "learner_id": "USER_xxx",
  "plan_ref": {
    "plan_id": "LP_LONG_xxx",
    "plan_version": 1,
    "route_id": "textbook_tcm_physician",
    "route_version": 1
  },
  "parent_id": null,
  "parent_type": null,
  "current_node_id": "stage-1",
  "nodes": [
    {
      "node_id": "stage-1",
      "node_type": "stage",
      "parent_id": null,
      "title": "中医基础与文化语言",
      "order": 1,
      "status": "in_progress",
      "progress": 0.2,
      "mastery": null,
      "has_children": true,
      "child_count": 4,
      "description": "建立中医基础概念和医古文阅读基础。",
      "source_refs": ["user-textbook-routes-json-2026-07-19"],
      "navigation": {
        "action": "expand",
        "parent_id": "stage-1"
      }
    }
  ],
  "offset": 0,
  "limit": 100,
  "total": 1,
  "has_more": false
}
```

`node_type`：`stage`、`book`、`knowledge_point`。

阶段标题优先取教材路线的阶段名；没有教材路线但存在已确认规划路线时，回退取 `planning_route.phases[].name`，不能只显示无语义的“第 N 阶段”。

`status`：`completed`、`in_progress`、`next`、`locked`、`unassessed`。

`navigation.action`：

- `expand`：继续请求子节点；
- `open_knowledge_atlas`：按 `route_id`、`book` 打开知识图谱；
- `open_knowledge_point`：按 `kp_id` 打开知识点。

未制定长期规划时仍返回 `200`，不要把它当异常：

```json
{
  "schema_version": "1.0",
  "plan_ref": null,
  "nodes": [],
  "availability": "requires_long_term_plan",
  "message": "请先完成长期学习规划，再生成阶段、教材和知识点路径。"
}
```

此时页面显示空状态和“去制定长期规划”按钮，不回退为未经用户确认的默认路径。

### 5.3 非个性化经典路线

经典路线是系统提供的参考路线，不等同于用户已确认的长期规划。前端可在学习路径中提供“我的学习路径 / 经典路线”切换，但不得把经典路线的阶段标记为用户已完成或进行中。

学习工坊的“经典路线”下拉框必须调用 `GET /api/v1/qualification-targets`，展示其中五项
`official_name`，以 `target_id` 作为选择值。选中后使用该项的 `textbook_route_id` 调用
`GET /api/v1/learning-routes/{textbook_route_id}` 加载阶段和教材。医师与助理医师可以复用
同一教材路线，但在下拉框中仍是两个独立的官方考试入口。

`GET /api/v1/learning-routes` 返回完整的经典教材参考目录，供管理、检索或其他非资格入口
使用，不应直接作为学习工坊的资格考试下拉选项。其目录响应示例：

```json
{
  "schema_version": "1.0",
  "route_kind": "classic_reference",
  "personalized": false,
  "items": [
    {
      "route_id": "textbook_tcm_physician",
      "route_version": 1,
      "status": "approved",
      "goal_name": "中医执业医师",
      "aliases": [],
      "stage_count": 5,
      "book_count": 18,
      "source_refs": ["user-textbook-routes-json-2026-07-19"],
      "detail_endpoint": "/api/v1/learning-routes/textbook_tcm_physician"
    }
  ],
  "total": 7
}
```

详情响应中的 `route.stages` 已按 `order` 排列，每个阶段包含 `stage_id`、`name`、`objective`、`books`、`exit_evidence` 和 `source_refs`。`sources` 提供可展示的来源说明；`navigation.atlas_route_id` 用于从教材继续进入知识图谱。经典路线教材节点统一使用 `unassessed`，不伪造个性化进度。

### 5.4 学习行为监控

`/api/v1/learning-activity/summary` 只聚合当前登录用户，`days` 仅支持 `7`、`30`、`90`。响应包含：

- `system_data`：完成率、正确率、专注度、资源点击率、掌握度等已计算指标；
- `trends`：按日趋势序列；
- `counters`：学习任务、专注会话和行为事件的原始计数；
- `recent_activities`：最近可追溯事件；
- `collection`：每类指标对应的采集来源说明。

汇总响应示例：

```json
{
  "schema_version": "1.0",
  "window_days": 30,
  "calculated_at": "2026-07-22T09:30:00+08:00",
  "system_data": {
    "time_data": {
      "login_frequency": {"value": 6, "unit": "days"},
      "focus_time_period": {"value": "20:00-20:59", "unit": "hour_slot"}
    },
    "task_completion_rate": {"value": 0.75, "unit": "ratio"},
    "resource_click_rate": {"value": 0.4, "unit": "ratio"},
    "calculation_version": "system-data-v2"
  },
  "trends": {
    "days": 30,
    "series": [
      {"date": "2026-07-22", "login_days": 1, "focus_minutes": 35, "task_completion_rate": 1.0}
    ]
  },
  "counters": {
    "learning_tasks": {"total": 4, "by_status": {"completed": 3, "pending": 1}},
    "focus_sessions": {"total": 2, "active_seconds": 2100, "by_status": {"completed": 2}},
    "activities": {"total": 8, "by_type": {"question_attempt": 3}}
  },
  "recent_activities": [
    {
      "activity_id": 42,
      "activity_type": "question_attempt",
      "resource_type": "question",
      "resource_id": "FORMAL_Q_1",
      "completion_status": "completed",
      "score": 100.0,
      "duration_minutes": 0,
      "created_at": "2026-07-22T01:28:00"
    }
  ],
  "collection": {
    "task_completion": "learning_tasks",
    "focus_time": "learning_focus_sessions heartbeat",
    "resource_click": "dashboard recommendation view and click",
    "graded_learning": "question, paper and case submission activities"
  }
}
```

`GET /api/v1/learning-activity/trends?days=30` 只返回 `schema_version`、`days`、`series` 和 `calculated_at`，适合图表按需刷新。`system_data` 中单项指标还可能包含 `window_start`、`window_end`；前端必须允许服务端增加字段。

行为写入仍由兼容层承担：学习任务创建/完成、专注会话心跳/结束、题目与试卷提交、案例训练提交，以及首页推荐曝光和点击。首页只有真实展示推荐后才会产生曝光记录，用户点击后调用 `POST /api/dashboard/recommendations/click`；不能用页面访问代替资源点击。

`task_completion_rate` 的当前统计口径是最近时间窗内全部正式 `LearningTask` 的完成数除以非取消任务数，不只统计今日任务。今日任务、题目练习、试卷与案例任务只要写入正式学习任务，都会进入该口径；按日趋势中的 `task_completion_rate` 则只统计对应日期。前端如需“今日任务完成率”，应使用今日任务接口的任务块状态单独计算，不要把它与本字段混用。

`login_frequency` 表示时间窗内发生过登录或签到的去重活跃天数，同一用户同一天多次登录、重复签到只计 1 天。

### 5.5 每日签到

| 方法 | 路径 | 说明 |
|---|---|---|
| `GET` | `/api/v1/checkin?days=7` | 获取当前用户签到状态、连续天数和日历 |
| `POST` | `/api/v1/checkin` | 当前用户当日签到，重复调用幂等 |

`GET` 响应包含 `today`、`checked_in_today`、`streak`、`total_checkins` 和 `calendar_days`。`POST` 额外返回 `already_checked_in`、`message`、更新后的 `status` 以及刷新后的 `system_data`。签到只用于记录真实活跃日，不等同于完成学习任务，也不会直接提高任务完成率。首页 `GET /api/v1/dashboard/home` 同时返回同结构的 `checkin_status`，供首屏直接渲染。

前端展示指标时应同时保留时间窗口和空样本状态。没有事件时显示“暂无数据”，不要把空样本渲染成 0 分能力结论。

## 6. 学习工坊

### 6.1 工坊入口

`GET /api/v1/workshop`

```json
{
  "schema_version": "1.0",
  "default_module": "question_training",
  "modules": [
    {
      "key": "question_training",
      "label": "题目训练",
      "description": "完成客观题、案例简答、AI 病患模拟和错题变式训练。",
      "enabled": true,
      "recommended": false,
      "capabilities": ["practice_grading", "case_training", "mistake_variation"],
      "practice_modes": [
        "objective_practice",
        "case_short_answer",
        "ai_patient_simulation",
        "mistake_history"
      ]
    }
  ],
  "endpoints": {}
}
```

正式模块键只有：`question_training`、`knowledge_cards`、`paper_workspace`。前端不要恢复已移除的“讲义生成”入口。

### 6.2 题目训练

题目训练页固定提供四种模式：客观题、案例简答、AI 病患模拟、错题变式。前三者完成提交后都写入当前用户的学习行为；答错结果进入统一错题记录。AI 病患模拟沿用病例会话接口，不删除、不降级为普通简答题。

| 方法 | 路径 | 说明 |
|---|---|---|
| `GET` | `/api/v1/workshop/practice/next?mode=objective&scope=public&topic=四君子汤` | 从正式题库获取一道未泄露答案的练习题 |
| `POST` | `/api/v1/workshop/practice/grade` | 提交并批改已签发题目 |
| `GET` | `/api/v1/workshop/practice/mistakes?status=all&offset=0&limit=50` | 当前用户全部错题记录 |
| `GET` | `/api/v1/workshop/practice/mistakes/{mistake_id}` | 当前用户单条错题详情 |
| `POST` | `/api/v1/workshop/practice/mistakes/{mistake_id}/answer-context` | 客观错题生成变式前补充当时的作答情况 |

`mode`：

- `objective`：`single_choice`、`multiple_choice`、`fill_blank`、`true_false`；
- `case`：`short_answer`、`case_quiz`；
- `all`：兼容调用，不限制题型。

`scope`：`public` 为正式题库，`user` 为当前用户导入题库，`all` 为两者。`kp_id` 可选；不传时由后端从当前范围中选题。`next` 响应中的 `request_id` 必须原样带入 `grade`，且只可消费一次。前端不得提交或展示标准答案字段。

取题成功响应：

```json
{
  "available": true,
  "kp_id": "050122",
  "question": {
    "question_id": "FORMAL_Q_1",
    "question_type": "multiple_choice",
    "stem": "四君子汤的组成包括哪些药物？",
    "options": [
      {"option_id": "A", "content": "人参"},
      {"option_id": "B", "content": "白术"}
    ],
    "kp_ids": ["050122"],
    "kp_names": ["四君子汤的组成与配伍"],
    "difficulty": 2,
    "difficulty_source": "formal_question_bank",
    "request_id": "6f718df8-72cf-4af8-90ec-5739216c59dd",
    "source_scope": "formal_question_bank"
  }
}
```

无匹配题时返回 `200`：

```json
{"available": false, "kp_id": "050122", "question": null}
```

正式题批改请求只信任服务端保存的题目、答案和知识点快照。虽然兼容模型仍接收下列字段，前端不得填写 `standard_answer`、`rubric` 或自行改写知识点：

```json
{
  "question_id": "FORMAL_Q_1",
  "question_type": "multiple_choice",
  "stem": "四君子汤的组成包括哪些药物？",
  "student_answer": "A, B",
  "request_id": "6f718df8-72cf-4af8-90ec-5739216c59dd"
}
```

批改响应：

```json
{
  "grading": {
    "question_id": "FORMAL_Q_1",
    "question_type": "multiple_choice",
    "score": 0.0,
    "is_correct": false,
    "analysis": "本题考查四君子汤的组成与配伍。多选题含错误选项，按规则计 0 分。错因暂不自动下结论，请到错题变式中补充当时的作答把握和判断过程。",
    "question_explanation": "四君子汤由人参、白术、茯苓、炙甘草组成，配伍重在益气健脾。",
    "explanation_source": "generated_on_first_attempt",
    "error_type": "待结合作答情况分析"
  },
  "attempt_id": "ATTEMPT_xxx",
  "attempt_item_id": "ITEM_xxx",
  "writeback": {
    "status": "applied",
    "receipt_id": "RECEIPT_xxx",
    "mistake_ids": ["18"],
    "review_task_ids": []
  }
}
```

受控练习的响应不会返回 `standard_answer`。`request_id` 有效期为 30 分钟且只能成功消费一次：未签发或不属于当前用户返回 `400`，重复提交返回 `409`，过期返回 `410`，答案为空返回 `422`。该提交不是可任意重放的幂等请求：前端提交期间应禁用按钮；若响应在网络中断时丢失，先刷新错题/学习行为确认是否已写入，再决定重新取题，不能生成新的 `request_id` 冒充原题。

`kp_names` 是前端唯一可展示的知识点标签，`kp_ids` 仅用于接口联动，不得直接渲染。`difficulty` 使用 `D1—D5`；`difficulty_source=formal_question_bank|question_bank_snapshot` 表示题库标注或题目快照，`system_default` 表示源题缺少有效难度后明确回退到 `D2`。资源匹配报告中的 `1-|题目难度-目标难度|/4` 只计算难度匹配度，不生成题目难度。

提交答案后，后端优先使用题库已有解析；没有解析时由 Expert 题目讲解模型依据服务端题干、
参考答案、评分要点和知识点生成 `question_explanation`，再由独立审核模型核验。解析生成过程
不读取学习者答案，因而“评分分析”和“题目解析”是两个独立字段。模型或审核不可用时会基于
同一组可信题目字段生成保底解析，不影响交卷。

首次生成的解析会在当前批改事务中写回可用的权威题目记录：
`QuestionVersionRecord.analysis`、`QuestionBankItem.analysis` 或
`LearningQuestion.explanation`；用户私有题写入 `UserQuestionItem.analysis`。再次遇到同一道题
时直接读取缓存，不重复请求模型。`explanation_source` 可能为
`generated_on_first_attempt`、`question_version_cache`、`question_bank_cache`、
`learning_question_cache` 或 `user_question_cache`。前端应分别展示“本次批改”和“题目解析”。

公共练习题直接来自知识库交付包的只读正式题库：`01_question_bank/formatted_questions.json`，当前基线为 93,111 道；语义候选可使用同一交付包对应的题库 FAISS。前端传入 `topic` 或 `kp_id` 后由后端检索并筛选题型，不能用业务数据库中已缓存的题数判断正式题库是否完整。业务数据库只按需保存本次签发题目的权威快照、一次性凭证、作答、评分和错题记录，不批量复制或改写公共题库。正式题响应使用 `source_scope=formal_question_bank`。

客观题由后端按服务端标准答案确定性判分；多选题只要包含错误选项即为 `0` 分。主观题（`short_answer`、`case_quiz`）必须经过 Expert Agent 批改，并在返回的 `agent_trace` 中保留 `expert_agent` 记录。

错题列表与“可生成变式的错题”不是同一集合。`mistakes` 返回所有归属当前用户的错题。客观错题还必须先完成作答情境调研，之后才可生成变式；主观题由 Expert Agent 直接归因，不要求该调研。不能变式的错题仍必须展示，并使用 `variation_reason` 说明原因。同一题再次答错会更新活动中的错题及最近作答证据，不因无法生成变式而丢弃记录。

错题列表响应：

```json
{
  "schema_version": "1.0",
  "items": [
    {
      "mistake_id": 18,
      "status": "active",
      "question_id": "FORMAL_Q_1",
      "question_version_id": "FORMAL_Q_1",
      "attempt_item_id": "ITEM_xxx",
      "stem": "四君子汤的组成包括哪些药物？",
      "question_type": "multiple_choice",
      "difficulty": 2,
      "kp_ids": ["050122"],
      "error_type": "待结合作答情况分析",
      "summary": "错因暂不自动下结论。",
      "student_answer": "A, B",
      "score": 50.0,
      "max_score": 100.0,
      "feedback": "答案不完整。",
      "answer_context_required": true,
      "answer_context_completed": false,
      "answer_context": null,
      "variation_available": false,
      "variation_reason": "请先补充当时的作答把握和判断过程",
      "created_at": "2026-07-22T01:28:00",
      "updated_at": "2026-07-22T01:28:00"
    }
  ],
  "total": 1,
  "offset": 0,
  "limit": 50,
  "has_more": false
}
```

详情接口返回 `{ "schema_version": "1.0", "mistake": MistakeItem }`。`status=all` 不过滤；其他值按错题状态原样过滤。`limit` 为 `1—100`。错题 ID 不属于当前用户时返回 `404`，不得跨用户回退查询。

客观错题作答情境请求：

```json
{
  "answer_state": "犹豫后作答",
  "reason": "审题遗漏",
  "notes": "当时只注意了症状，没有看清题目要求选全部正确项。"
}
```

`answer_state` 可选：`确定后作答`、`犹豫后作答`、`排除后猜测`、`完全猜测`、`误读题意`；`reason` 可选：`概念混淆`、`审题遗漏`、`记忆不清`、`选项辨析困难`、`操作失误`、`其他`。保存后响应返回更新后的 `mistake`，前端以新的 `variation_available` 决定是否开放变式按钮。

AI 病患模拟使用：

- `GET /api/training/cases/types`
- `POST /api/training/case-sessions`
- `GET /api/training/case-sessions/{session_id}`
- `POST /api/training/case-sessions/{session_id}/messages`
- `POST /api/training/case-sessions/{session_id}/help`
- `POST /api/training/case-sessions/{session_id}/submit`

病例评分审核通过但答案不完整时，同样写入统一错题历史；病例错题当前只保留记录，不自动生成普通题变式。

### 6.3 知识卡片

| 方法 | 路径 | 说明 |
|---|---|---|
| `GET` | `/api/v1/workshop/knowledge-cards?offset=0&limit=50` | 已学习知识卡列表 |
| `GET` | `/api/v1/workshop/knowledge-cards/{card_id}` | 完整知识卡 |
| `POST` | `/api/v1/workshop/knowledge-cards/resolve` | 按知识点聚合资源并保存 |

聚合请求：

```json
{
  "kp_id": "050122",
  "question_limit": 10,
  "source_execution_id": "THREAD_xxx"
}
```

完整知识卡的 `resource_bundle`：

```json
{
  "schema_version": "1.0",
  "bundle_id": "BUNDLE_xxx",
  "knowledge_point": {},
  "explanation": {},
  "textbook_slices": [],
  "videos": [],
  "questions": [],
  "coverage": {
    "knowledge_point": true,
    "explanation": true,
    "textbook_slices": true,
    "videos": true,
    "questions": true,
    "fallback_used": ["video", "question"]
  },
  "provenance": []
}
```

`fallback_used` 表示本地资源不足后使用过网络补充。前端应标注来源，不应隐藏或改写为本地教材证据。

知识卡详情默认只渲染 `explanation`。教材切片、视频和题目分别作为可切换资源入口，用户点击后再展示；不要把四类资源同时铺在首屏。

知识卡只保存已完成学习或明确生成的知识点。到期复习卡不能因“生成完成”直接进入复习队列；复习队列准入以用户完成配套题目为准。

### 6.4 试卷

| 方法 | 路径 | 说明 |
|---|---|---|
| `GET` | `/api/v1/workshop/papers?offset=0&limit=50` | 试卷列表 |
| `GET` | `/api/v1/workshop/papers/{paper_id}` | 试卷、题目、计时和答题状态 |
| `PUT` | `/api/v1/workshop/papers/{paper_id}/answers` | 保存草稿答案 |
| `POST` | `/api/v1/workshop/papers/{paper_id}/timer/pause` | 暂停服务端计时 |
| `POST` | `/api/v1/workshop/papers/{paper_id}/timer/resume` | 从剩余时长继续计时 |
| `POST` | `/api/v1/workshop/papers/{paper_id}/submit` | 幂等提交并评分 |

保存答案：

```json
{
  "answers": {
    "ITEM_1": "A",
    "ITEM_2": "人参、白术、茯苓、炙甘草"
  }
}
```

提交：

```json
{
  "request_id": "paper-由前端生成的UUID"
}
```

同一次提交重试必须复用 `request_id`，防止重复计分和重复写入学习行为。

计时结构：

```json
{
  "duration_minutes": 60,
  "started_at": "2026-07-21T20:00:00Z",
  "expires_at": "2026-07-21T21:00:00Z",
  "remaining_seconds": 3540,
  "expired": false,
  "paused": false,
  "paused_at": null
}
```

题目类型：`single_choice`、`multiple_choice`、`fill_blank`、`short_answer`、`case_quiz`。答案提交后才形成学习行为，进而更新掌握度和复习队列。

试卷读取响应顶层包含 `total_score`，各题包含 `max_score`、`kp_names`、`difficulty` 与 `difficulty_source`。未声明总分时保留新试卷明确给出的题目分值；对历史遗留的“每题错误写成 100 分”数据，读取时自动归一化为整卷 100 分。前端只能使用服务端的 `total_score`/`max_score`，不得用题数乘固定分值。

交卷响应的 `items[]` 固定包含 `is_correct`、`score`、`max_score`、`submitted_answer`、
`standard_answer`、`explanation`、`grading_analysis`、`mistake_ids`。其中 `explanation`
使用与单题练习相同的“首次生成并持久化、后续直接复用”机制。答错题在发布时已建立
`PaperItem -> QuestionVersion -> QuestionKPLink` 权威链，审核通过后会写入统一错题记录，
并出现在错题变式来源中；不能只保存页面上的对错状态。

`short_answer`、`case_quiz` 必须由 Expert Agent 进行语义评分，再由 Audit Agent 独立复核。批改响应增加：

- `grading.grading_source=expert_agent_model`：真实模型批改；
- `grading.dimension_scores`：各评分维度结果；
- `audit.decision`、`audit.reason`：独立审核结论；
- `writeback.status`：只有 `audit.decision=pass` 才允许更新掌握度、错题和复习队列。

模型或审核不可用时，服务端可以返回 `grading_source=rule_fallback` 供页面临时展示，但 Audit 必须为 `needs_human_review`，并返回 `writeback.status=withheld_pending_audit` 或 `skipped`。前端不得把这种结果显示成“Expert 批改成功”。

作答页固定按“单选题、多选题、填空题、简答题”分组展示；`case_quiz` 归入简答题区并保留自身题型标识。暂停与继续必须调用服务端计时接口，不能只停浏览器定时器。暂停后的剩余时长由服务端保存，刷新、离开页面或断线重连后仍保持暂停；继续后服务端基于保存的剩余秒数生成新的截止时间。交卷成功后倒计时立即停止并显示已交卷状态。

### 6.5 训练任务兼容接口

尚未完全迁移的训练入口使用：

| 方法 | 浏览器路径 | 说明 |
|---|---|---|
| `GET` | `/api/training/workspace/modules` | 训练模块能力 |
| `POST` | `/api/training/workspace/tasks` | 创建训练任务 |
| `GET` | `/api/training/workspace/tasks/{task_id}` | 获取训练结果 |
| `GET` | `/api/training/workspace/mistake-variations/sources` | 可变式错题来源 |
| `GET` | `/api/training/workspace/mistakes` | 全部错题记录（稳定接口的兼容路径） |
| `GET` | `/api/training/workspace/mistakes/{mistake_id}` | 单条错题详情（兼容路径） |
| `GET` | `/api/training/workspace/papers/{paper_id}` | 兼容试卷读取 |
| `PUT` | `/api/training/workspace/papers/{paper_id}/answers` | 兼容答案保存 |
| `POST` | `/api/training/workspace/papers/{paper_id}/timer/pause` | 兼容暂停计时 |
| `POST` | `/api/training/workspace/papers/{paper_id}/timer/resume` | 兼容继续计时 |
| `POST` | `/api/training/workspace/papers/{paper_id}/submit` | 兼容试卷提交 |

兼容创建任务示例：

```json
{
  "task_type": "paper_generation",
  "title": "训练试卷",
  "query": "围绕四君子汤组卷",
  "inputs": {
    "topic": "四君子汤",
    "difficulty": 1,
    "question_count": 25,
    "types": ["fill_blank"],
    "distribution": {"fill_blank": 25}
  },
  "options": {"need_audit": true}
}
```

学习工坊的正式“生成试卷”按钮不再调用上述兼容 `paper_generation`，而是同步调用 `POST /api/v1/review-cards`，提交自然语言组卷要求及 `exam_constraints.question_count`、`question_types`、`question_type_distribution`。这样题库不足时仍可继续网络检索或由 Expert 补题，并强制经过 Audit；前端从 `ui_actions` 中查找 `destination=workshop.paper` 的 `params.paper_id`，再调用 `/api/v1/workshop/papers/{paper_id}` 打开计时答题页。页面不提供难度选择，难度由智能体结合学习状态确定。

对话组卷成功时，`assistant_message` 只包含“组卷并通过审核”的提示，不包含试卷正文、答案或解析；试卷内容仅由答题页按 `paper_id` 读取。当前 UI 继续通过兼容任务接口使用的类型为 `knowledge_card_generation`、`mistake_variation`。普通客观题和案例简答直接使用 `/api/v1/workshop/practice/*`；AI 病患模拟使用独立病例会话接口，不通过此字段伪装。

只有下列条件同时满足时才展示任务产物：

```text
status == "completed" && audit.decision == "pass"
```

审核拒绝时保留错误摘要，不得把未通过试卷当作正式试卷跳转。

## 7. 知识库

### 7.1 正式主接口

| 方法 | 路径 | 说明 |
|---|---|---|
| `GET` | `/api/v1/knowledge/routes` | 教材路线 |
| `GET` | `/api/v1/knowledge/nodes` | 教材、章节或知识点节点 |
| `GET` | `/api/v1/knowledge/points/{kp_id}` | 知识点完整详情 |
| `GET` | `/api/v1/knowledge/images/{filename}` | 教材图片 |
| `POST` | `/api/v1/knowledge/warm` | 预热正式知识后端 |
| `POST` | `/api/v1/knowledge/questions/search` | 检索题目 |
| `POST` | `/api/v1/knowledge/questions/import-markdown` | 导入 Markdown 题目 |
| `POST` | `/api/v1/knowledge/questions/import-file` | 导入题目文件 |
| `POST` | `/api/v1/knowledge/content/import-text` | 导入用户文本资料 |
| `POST` | `/api/v1/knowledge/content/import-file` | 导入用户文件 |

题目检索：

```json
{
  "query": "四君子汤组成和配伍意义",
  "kp_ids": ["050122"],
  "limit": 10,
  "scope": "all"
}
```

`scope`：`all`、`public`、`user`。用户导入内容必须写入个人域，不能修改公共知识库。

PDF、图片导入不再要求浏览器传 MinerU 密钥。`MINERU_TOKEN`（兼容
`MINERU_API_KEY`）由服务端 `.env.local` 读取；客户端若发送 `x-mineru-token` 只作为旧版兼容，
不得把密钥保存到前端。PDF 使用交接包 `knowledge_upload_pipeline/parse_question_pdf.py`
解析为规范 Markdown，再进入知识点/题目抽取和 Embedding。

当前知识资料工作台使用兼容上传接口：

| 方法 | 路径 | 说明 |
|---|---|---|
| `POST` | `/api/knowledge/upload?scope=personal` | `multipart/form-data`，字段名 `files`，普通用户仅可写个人库 |
| `GET` | `/api/knowledge/files?scope=all` | 同时返回公共和当前用户个人资料 |
| `GET` | `/api/knowledge/status?scope=personal` | 返回文档、切片、处理进度和 Embedding 状态 |
| `POST` | `/api/knowledge/rebuild?scope=personal` | 重建当前范围索引 |

PDF 上传成功响应中的 `pdf_processing[]` 包含
`source_filename`、`stored_filename` 和 `parser=mineru_precision`。PDF 原件不会直接进入
重复索引；检索库保存 MinerU 生成的 `.mineru.md`。Embedding 默认
`EMBEDDING_MODE=enabled`：有本地 `EMBEDDING_MODEL_PATH` 时使用本地模型，否则使用主后端注入的
远程 Embedding 配置。活动题库向量目录必须提供与 `index.faiss`、`metadata.jsonl` 配套的
`index_manifest.json`，用于校验模型标识、维度和向量归一化契约；文档索引和个人索引仍按用户
隔离保存。

### 7.2 考试路线

| 方法 | 路径 |
|---|---|
| `GET` | `/api/v1/knowledge/exams/tracks` |
| `GET` | `/api/v1/knowledge/exams/tracks/{track_id}/stages` |
| `GET` | `/api/v1/knowledge/exams/tracks/{track_id}/catalog` |
| `GET` | `/api/v1/knowledge/exams/stages/{stage_id}/requirements` |
| `GET` | `/api/v1/knowledge/exams/requirements/{node_id}/matches` |
| `GET` | `/api/v1/knowledge/exams/catalog/{catalog_node_id}/knowledge-points` |
| `GET` | `/api/v1/knowledge/exams/knowledge-points/{kp_id}/matches` |
| `GET` | `/api/v1/knowledge/exams/review-queue` |
| `GET` | `/api/v1/knowledge/exams/validation-summary` |
| `POST` | `/api/v1/knowledge/exams/query` |
| `POST` | `/api/v1/knowledge/exams/import-markdown` |
| `POST` | `/api/v1/knowledge/exams/import-file` |

官方路线读取接口不要求把用户 ID放进 URL；用户导入和查询仍由登录态隔离。

### 7.3 知识图谱兼容接口

正式前端知识图谱当前使用 `/api/knowledge/atlas/*`。知识星球页面固定呈现教材目录，不展示或切换不同学习路线；后端的经典路线接口仍供“学习路径”等其他页面使用：

- `GET /api/knowledge/atlas/status`
- `GET /api/knowledge/atlas/routes`
- `GET /api/knowledge/atlas/nodes`
- `GET /api/knowledge/atlas/detail/{kp_id}`
- `GET /api/knowledge/atlas/images/{filename}`
- `POST /api/knowledge/atlas/warm`
- `GET /api/knowledge/atlas/resolve-context`
- `GET /api/knowledge/atlas/questions/search`

新页面优先使用 `/api/v1/knowledge/*`；兼容层仅保留现有知识图谱交互。

知识图谱固定按“教材 → 章节 → 小节 → 知识点”四级下钻。目录和画布必须使用同一套顺序：教材、章节、小节按后端 `order_index` 升序，知识点按后端 `order` 升序。顺序视图采用单列纵向排布。前端筛选只移除节点，不得按标题或资源数量重新排序。

当前知识星球节点请求示例：

```text
GET /api/knowledge/atlas/nodes?level=2&route=textbook_14_5&lv1=中医学基础
GET /api/knowledge/atlas/nodes?level=3&route=textbook_14_5&lv1=中医学基础&chapter_id=CHAPTER_ID
GET /api/knowledge/atlas/nodes?level=4&route=textbook_14_5&lv1=中医学基础&chapter_id=CHAPTER_ID&section_id=SECTION_ID
```

节点响应：

```json
{
  "ok": true,
  "level": 2,
  "nodes": [
    {
      "id": "稳定章节标识",
      "name": "第一章 绪论",
      "count": 33,
      "children_count": 4,
      "order_index": 1,
      "review_status": "resolved"
    }
  ],
  "count": 36,
  "stats": {"lv1": 83, "lv2": 1282, "lv3": 5186, "lv4": 73777},
  "route": "textbook_14_5"
}
```

前端进入教材时传 `lv1=name`；进入章节时传 `chapter_id=id`（也可同时传 `chapter=name`）；进入小节时传 `section_id=id`（兼容调用可传 `lv2=name`）；第四级节点的 `id` 才是详情接口所需的 `kp_id`。`resolve-context` 也会返回 `chapter`、`chapter_id`、`section_id`，前端应原样保留以恢复钻取位置。

## 8. 复习队列

| 方法 | 路径 | 说明 |
|---|---|---|
| `GET` | `/api/v1/review-queue?limit=50` | 推荐：按登录态读取当前用户复习队列 |
| `GET` | `/api/v1/review-dashboard?limit=50&history_limit=100` | 当前用户复习队列、知识点掌握度、复习状态和掌握历史的聚合接口 |
| `GET` | `/api/v1/learners/{learner_id}/review-queue?limit=50` | 当前用户复习队列 |
| `POST` | `/api/v1/learners/{learner_id}/review-queue/dispatch` | 为下一个到期知识点生成复习资源 |
| `POST` | `/api/v1/review-tasks/{review_task_id}/attempts` | 提交复习结果 |

调度请求：

```json
{
  "available_minutes": 15
}
```

复习结果：

```json
{
  "learner_id": "USER_xxx",
  "outcome": "independent_correct",
  "hint_used": false,
  "answered_at": "2026-07-21T21:00:00Z",
  "attempt_id": "ATTEMPT_xxx"
}
```

`outcome`：`independent_correct`、`hinted_correct`、`wrong`、`skipped`。

队列准入规则：完成知识点题目且批改结果被接受的瞬间才创建或更新记忆单元。只打开知识卡、只生成复习卡、浏览资源或未提交答案都不算完成；答对和答错都会入队，答错会更早复习。响应中的 `admission_policy=completed_graded_kp_question_v1` 和 `projection_source=canonical_review_memory` 可用于前端展示规则说明。

`review-dashboard` 返回 `summary`、`queue`、`mastery`、`review_states`、`review_tasks` 和 `mastery_history`。知识点对用户显示时优先使用 `kp_name`，不得用内部 `kp_id` 替代名称。掌握度使用 0–100，保持率使用 0–1。

## 8.1 Memory Agent 权威画像

对话中用户明确表达昵称、用户群体、学习目标、学习基础或时间条件时，Memory Agent 会先提炼稳定事实并写入现有权威画像；规划、推荐、学习工坊和前端均通过 `GET /api/v1/learning-context` 的 `user_profile` 读取，不应各自从原始对话重复抽取。

关键字段为 `display_name`、`learner_group`、`learning_goal`、`learning_background`、`time_constraints`。低置信度或未明确表达的字段不写入；画像锁定字段不会被自动覆盖。兼容 `/api/personalization/learner-profile` 主要用于设置和历史页面，不应覆盖 `learning-context.user_profile` 中的已确认事实。

规划追问中的短回答同样进入上述权威画像。系统会把“中医执业医师考试”规范为
`learning_goal=中医执业医师资格考试`，把“我是零基础，计算机专业，每周学习4天，每天2小时”拆分为
`learning_background=零基础，计算机专业` 与
`time_constraints=每周学习4天，每天2小时`。路线解析同时兼容结构化 `goals[]` 和直接
`learning_goal` 字段，前端不需要为了兼容旧格式重复写两份目标。

## 8.2 学习监控快照

`GET /api/v1/learning-monitoring/snapshot?days=7`

这是学情诊断的正式数据依赖，返回 `sample_counts`、可空的 `metrics`、`evidence_status`、`freshness_status`、`calculated_at` 和 `reason_codes`。零样本时 `evidence_status=insufficient`，准确率、完成率等不可观测指标返回 `null`，前端不得显示为 100% 或“状态稳定”。Diagnosis Agent 同样读取这份快照；证据不足时可以结合画像制定起步规划，但必须降低置信度，不能虚构薄弱点。

## 9. 兼容业务接口索引

以下接口仍由 `backend-handoff` 提供。浏览器统一加 `/api` 前缀；表中均为浏览器最终请求路径。

| 页面/能力 | 主要接口 |
|---|---|
| 首页兼容数据 | `/api/dashboard/home` |
| 学习画像与记忆 | `/api/personalization/overview`、`/learner-profile`、`/memories`、`/candidates`、`/learning-trends` |
| 学习目标 | `/api/personalization/learning-target` |
| 记忆管理 | `/api/personalization/memories`、`/candidates` 及其子资源 |
| 学习设置 | `/api/personalization/learner-settings` |
| 新用户问卷 | `/api/training/onboarding/status`、`/group-templates`、`/survey` |
| 题目练习 | 首选 `/api/v1/workshop/practice/*`；兼容 `/api/training/practice/next`、`/practice/grade` |
| 案例训练 | `/api/training/cases/types`、`/api/training/case-sessions` 及其消息、帮助、提交接口 |
| 考试图谱 | `/api/exam-learning/tracks` 及其节点、知识点、掌握度接口 |
| 题库工作区 | `/api/question-workspace/imports`、`/items`、`/questions`、`/index/rebuild` |
| 文件上传 | `/api/upload` |
| 反馈 | `/api/feedback`；管理员使用 `/api/feedback/admin*` |
| 语音转写 | `/api/voice/transcribe`，当前阶段可能禁用 |

兼容层是否可用：

`GET /api/v1/platform/status`

完整兼容 OpenAPI：

`GET /api/v1/platform/openapi.json`

若 `enabled=false` 或 `mounted=false`，前端应隐藏依赖兼容层的入口或展示明确空状态。

## 10. 智能体返回的页面动作

智能体完成知识卡或试卷任务后，可在结构化 `result.ui_actions` 中返回：

```json
{
  "action_type": "navigate",
  "label": "进入试卷作答",
  "destination": "workshop.paper",
  "params": {
    "paper_id": "PAPER_xxx"
  }
}
```

合法目标：

- `workshop.question_training`
- `workshop.knowledge_card`
- `workshop.paper`

前端必须维护目标到内部页面的白名单映射，不直接把 `destination` 当 URL：

```js
const destinations = {
  'workshop.question_training': { page: 'practice', view: 'workspace', taskType: 'question_training' },
  'workshop.knowledge_card': { page: 'practice', view: 'workspace', taskType: 'knowledge_cards' },
  'workshop.paper': { page: 'practice', view: 'workspace', taskType: 'paper_workspace' },
};
```

## 11. 页面到接口映射

| 正式页面 | 首选主接口 | 兼容接口 |
|---|---|---|
| 登录/注册 | `/api/v1/auth/*` | 不允许回退旧认证 |
| 智能助教 | `/api/v1/conversations*`、`/api/v1/review-cards*` | 文件、反馈等暂用 `/api/*` |
| 平台首页 | `/api/v1/dashboard/home` | 无数据时可读取 `/api/dashboard/home`，不得混合覆盖可信字段 |
| 学习路径 | `/api/v1/learning-path*`、`/api/v1/learning-routes*` | 个性化路径和非个性化经典路线分开展示；教材可进入知识图谱 |
| 学习工坊 | `/api/v1/workshop*` | 训练任务、案例训练暂用 `/api/training*` |
| 知识仓库 | `/api/v1/knowledge*` | 现有三维图谱暂用 `/api/knowledge/atlas*` |
| 个性数据 | `/api/v1/learning-context`、`/api/v1/learning-monitoring/snapshot`、`/api/v1/learning-activity/*` | 画像、记忆编辑暂用 `/api/personalization*` |
| 学情洞察与资源匹配 | `/api/v1/learning-insights`、`/api/v1/resource-match-report` | 旧 `/api/agent/diagnosis/report` 仅作无数据降级 |
| 通知与主动干预 | `/api/v1/notifications*`、`/api/v1/notification-preferences`、`/api/v1/interventions*` | 设置页也可通过 `/api/personalization/learner-settings` 一次保存通知偏好 |
| 规划自动复盘 | `/api/v1/plan-reviews*` | 调整提案必须由用户确认，长期规划不得静默覆盖 |
| 规划入口 | `/api/v1/planning/readiness`、`/api/v1/review-cards*` | readiness 只做预检，生成接口仍会强制校验 |
| 复习队列 | `/api/v1/review-queue` | 带 learner_id 的旧接口仅作兼容 |

## 12. 前端实现约束

1. 只使用相对路径，并统一通过 `fetchWithAuth`。
2. `401` 触发全局退出流程；其他错误由页面就地处理。
3. 取消请求使用 `AbortController`，不要把用户主动取消显示成系统错误。
4. SSE 断线后先查询 `run` 状态，禁止直接重复创建任务。
5. `assistant_message` 渲染自然语言；结构化结果只用于执行、校验和页面卡片。
6. `schema_version` 不兼容时显示升级提示，禁止猜字段。
7. 列表按服务端 `total` 和 `has_more` 分页，不用当前数组长度推断总数。
8. 试卷提交复用 `request_id`；按钮提交期间禁用，防止双击。
9. 用户 ID 从 `/auth/me` 获取，只作展示和当前用户 URL 占位，不允许手工切换。
10. OpenAPI 或 Pydantic 契约变化时，同一个提交中更新本文档和前端适配测试。

## 13. 联调检查清单

```bash
# 服务存活
curl http://127.0.0.1:7860/health

# 主 OpenAPI
curl http://127.0.0.1:7860/openapi.json

# 前端检查
cd frontend/llm
npm run test:unit
npm run lint
npm run build
```

Live 验收不要从 WSL 命令行运行 Live pytest；应在已启动前端运行面板点击 Execute。

## 14. 学情洞察、自动治理与通知

### 14.1 学情洞察

`GET /api/v1/learning-insights?days=30&run_automation=true`

`days` 只允许 `7`、`30`、`90`。响应中的稳定字段包括：

- `overview`：当前 T 阶段、诊断摘要、可信度、到期复习数量；
- `dimensions`：知识掌握、复习保持、任务执行、练习正确、学习规律和资源使用；
- `activity_trends.series`：按日登录、有效学习分钟和任务完成率；
- `mastery_heatmap`、`weak_points`、`mistake_distribution`；
- `data_quality`：样本量、数据来源及是否足以触发主动干预；
- `automation`：本次幂等检查得到的干预与规划复盘结果。

前端必须展示 `data_quality`。数据不足时不得把空值渲染成确定性诊断。

每个 `dimensions[]` 同时返回 `source_ids`、`formula`、`evidence_count` 和 `window_days`；
顶层 `data_sources[]` 与 `methodology` 是正式审计契约。`overview.confidence` 和
`data_quality.confidence` 表示数据覆盖度，不是统计置信区间。完整口径见
[学情监测与资源匹配口径](learning-monitoring-methodology.md)。

### 14.2 资源匹配报告

`GET /api/v1/resource-match-report?limit=12`

每个 `matches[]` 包含 `resource_id`、`resource_type`、`title`、`score`、
`estimated_minutes`、`components`、`reasons` 和白名单 `action`。`components` 当前包含知识点覆盖、
质量、资源形式、时间和难度匹配。前端用 `action.type` 做受控跳转，不自行拼接外部 URL。

资源项还包含 `component_sources`、`quality_basis`、`estimated_minutes_basis` 和原始 `source`。
缺少难度等特征时对应 component 为 `null`，服务端排除该特征并重新归一化权重；前端不得把
`null` 渲染为 0 分。没有薄弱知识点和今日任务知识点时，服务端返回空 `matches`，禁止前端补默认推荐。

### 14.3 通知

- `GET /api/v1/notifications?status=all|unread|read|dismissed&limit=50`
- `PATCH /api/v1/notifications/{notification_id}`，请求体：`{"status":"read"}` 或 `dismissed`
- `GET /api/v1/notification-preferences`
- `PUT /api/v1/notification-preferences`

偏好请求示例：

```json
{
  "in_app_enabled": true,
  "categories": {
    "review_due": true,
    "intervention": true,
    "plan_review": true
  },
  "digest_frequency": "realtime",
  "quiet_hours": {"start": "22:00", "end": "07:00"}
}
```

通知使用服务端 `dedupe_key` 防重复，前端不得按标题自行合并。所有读写均以 Cookie 当前用户为边界。

### 14.4 主动干预

- `GET /api/v1/interventions?limit=30`
- `POST /api/v1/interventions/{intervention_id}/feedback`

反馈 `action` 可为 `accept`、`postpone`、`not_relevant`、`too_easy` 或 `too_hard`。
系统仅在 `data_quality.is_sufficient_for_intervention=true` 时创建干预，并执行 24 小时冷却。

### 14.5 规划自动复盘

- `GET /api/v1/plan-reviews?limit=30`
- `POST /api/v1/plan-reviews/run`
- `POST /api/v1/plan-reviews/{review_id}/decision`，请求体为 `{"decision":"accept"}` 或 `reject`

`outcome` 可能为 `on_track`、`daily_adjustment_suggested`、`short_replan_suggested` 或
`long_replan_requires_confirmation`。每日层调整只能处于现有短期计划范围；短期和长期提案需确认后才能写入正式计划。

### 14.6 LangGraph 重启恢复

当主库启用时，中断检查点和恢复上下文写入数据库。页面刷新、连接中断或服务重启后仍使用原
`thread_id` 调用 `/api/v1/review-cards/runs/{thread_id}/resume/stream`。恢复前可读取
`GET /api/v1/review-cards/runs/{thread_id}`；若状态不是 `interrupted`，前端不得重复提交恢复答案。

### 14.7 多时间尺度学情

`GET /api/v1/learning-state/multiscale`

- 鉴权：当前登录用户的 HttpOnly Cookie；未登录返回 `401`。
- 查询：`window_days=7|30|90`，默认 `30`；`include_recent_events=true|false`，默认 `false`。
- 隔离：只读取服务端登录身份，不接受客户端学习者 ID。
- 版本：`schema_version` 当前为 `"1.0"`。

完整响应示例：

```json
{
  "schema_version": "1.0",
  "state_id": "MSLS_01",
  "learner_id": "USER_01",
  "generated_at": "2026-07-24T08:00:00+00:00",
  "macro": {
    "qualification_goal": {"name": "中医执业医师资格考试"},
    "approved_route": {"route_id": "ROUTE_01"},
    "current_stage": {"phase_id": "P1", "name": "中医基础与文化语言"},
    "stage_books": [{"name": "《中医基础理论》"}],
    "prerequisites": [],
    "acceptance_evidence": []
  },
  "meso": {
    "current_short_term_plan": {},
    "current_daily_tasks": [],
    "planned_knowledge_points": [],
    "weak_knowledge_points": [],
    "due_review_knowledge_points": [],
    "task_completion_rate": {
      "available": true, "value": 0.75, "unit": "ratio_0_1",
      "source_refs": ["learning_task:TASK_01"], "unavailable_reason": null
    },
    "learning_regularity": {
      "available": false, "value": null, "unit": "ratio_0_1",
      "source_refs": [], "unavailable_reason": "no_learning_activity_in_window"
    }
  },
  "micro": {
    "recent_attempts": [],
    "question_accuracy": {
      "available": false, "value": null, "unit": "ratio_0_1",
      "source_refs": [], "unavailable_reason": "no_question_attempts"
    },
    "average_response_time": {
      "available": false, "value": null, "unit": "seconds",
      "source_refs": [], "unavailable_reason": "no_question_attempts"
    },
    "average_mastery": {
      "available": false, "value": null, "unit": "ratio_0_1",
      "source_refs": [], "unavailable_reason": "no_mastery_observations"
    },
    "mastery_by_knowledge_point": [],
    "confirmed_mistake_reasons": [],
    "recent_focus_minutes": {
      "available": false, "value": null, "unit": "minutes",
      "source_refs": [], "unavailable_reason": "no_focus_sessions"
    },
    "current_task_load": {
      "available": false, "value": null, "unit": "minutes",
      "source_refs": [], "unavailable_reason": "no_pending_tasks"
    },
    "recent_question_ids": [],
    "recent_knowledge_point_ids": [],
    "recent_resource_ids": []
  },
  "data_quality": {
    "window_days": 30,
    "window_start": "2026-06-24T08:00:00+00:00",
    "window_end": "2026-07-24T08:00:00+00:00",
    "coverage": 0.1429,
    "sample_counts": {"tasks": 1, "question_attempts": 0, "mastery_points": 0, "review_states": 0, "mistakes": 0, "focus_sessions": 0},
    "available_metrics": 1,
    "unavailable_metrics": 6,
    "allow_cautious_path_adjustment": false,
    "limitations": ["缺失指标保持不可用，不参与正向评分。"]
  },
  "hard_constraints": [
    {"key": "low_data_protection", "passed": false, "reason": "insufficient_data_for_high_risk_adjustment", "source_refs": []}
  ],
  "source_refs": [
    {"source_id": "learning_task:TASK_01", "source_type": "database_row", "table": "learning_task", "record_id": "TASK_01", "window_days": 30}
  ],
  "state_digest": "aaaaaaaaaaaaaaaaaaaaaaaa"
}
```

`MetricValue.value` 是 `number|null`。`ratio_0_1` 表示 `0..1` 比例，`seconds`、`minutes`
分别为秒、分钟。`available=false` 时 `value` 必须为 `null` 且 `unavailable_reason` 非空；
前端显示“不可用”和原因，不得转成 `0` 或 `0%`。默认响应会清空近期事件和原始 ID 数组。

### 14.8 路径候选

`GET /api/v1/learning-state/path-candidates`

- 鉴权和用户隔离同上。
- 查询：必填 `scope=long_term|short_term|daily_task`；`limit=1..30`，默认 `10`；
  `include_blocked=true|false`，默认 `true`。
- 版本：`schema_version` 当前为 `"1.0"`。

完整响应示例：

```json
{
  "schema_version": "1.0",
  "learner_id": "USER_01",
  "scope": "daily_task",
  "generated_at": "2026-07-24T08:00:00+00:00",
  "state_digest": "aaaaaaaaaaaaaaaaaaaaaaaa",
  "items": [
    {
      "candidate_id": "PATH_01",
      "scope": "daily_task",
      "stage": {"phase_id": "P1", "name": "基础阶段"},
      "books": [{"book_id": "BOOK_01", "name": "《中医基础理论》"}],
      "knowledge_points": [{"kp_id": "KP_01", "name": "阴阳学说"}],
      "estimated_minutes": 20,
      "eligible": true,
      "blocked_reasons": [],
      "hard_constraint_results": [
        {"key": "time_budget", "passed": true, "reason": "time_budget_satisfied", "source_refs": ["profile:USER_01"]}
      ],
      "score": 0.8,
      "score_components": {
        "learning_gain": {
          "available": true, "value": 0.7, "unit": "ratio_0_1",
          "source_refs": ["knowledge_mastery_states:STATE_01"], "unavailable_reason": null
        },
        "difficulty_fit": {
          "available": false, "value": null, "unit": "ratio_0_1",
          "source_refs": [], "unavailable_reason": "resource_difficulty_missing"
        }
      },
      "evidence_refs": ["knowledge_mastery_states:STATE_01"],
      "source_refs": ["learning_task:TASK_01"],
      "recommended_action": "continue_task"
    }
  ],
  "counts": {"returned": 1, "eligible": 1, "blocked": 0, "due_reviews_considered": 0},
  "scoring_policy": {
    "hard_constraint_order": ["goal_route_alignment", "parent_plan_exists", "prerequisite_satisfied", "time_budget", "due_review_priority", "trusted_source", "low_data_protection", "approved_stage_mapping"],
    "positive_weights": {"learning_gain": 0.3, "retention_benefit": 0.2, "knowledge_coverage": 0.2, "time_fit": 0.1, "difficulty_fit": 0.1, "autonomy_support": 0.1},
    "repetition_weight": 0.1,
    "uncertainty_weight": 0.15,
    "missing_positive_components": "renormalize_available_only",
    "risk_components": "independent_deductions"
  }
}
```

`estimated_minutes` 是整数分钟，`score` 和可用评分分项为 `0..1`。硬约束失败时，即使高分也归入
“被阻断候选”。不可用分项显示“未纳入”，不显示 `0%`。展示使用 `stage.name`、
`books[].name`、`knowledge_points[].name`，默认不显示裸 ID。前端不得从规划正文推导这些字段。

### 14.9 执行协调摘要

`GET /api/v1/executions/{execution_id}/coordination`

- 鉴权：必须登录；无查询参数。
- 隔离：只有执行所属用户可读；不存在和其他用户的执行都返回 `404`。
- 版本：`schema_version` 当前为 `"1.0"`。

完整响应示例：

```json
{
  "schema_version": "1.0",
  "execution_id": "EXE_01",
  "communication_summary": {
    "total": 1,
    "items": [
      {
        "schema_version": "1.0",
        "handoff_id": "HANDOFF_01",
        "step_id": "diagnosis",
        "target_agent": "diagnosis_agent",
        "fact_count": 2,
        "evidence_count": 1,
        "blocking_field_count": 0,
        "omitted_categories": ["raw_conversation"],
        "status": "consumed",
        "created_at": "2026-07-24T08:00:00+00:00"
      }
    ]
  },
  "repair_summary": {
    "total": 1,
    "items": [
      {
        "repair_id": "REPAIR_01",
        "trigger_step_id": "audit",
        "issue_types": ["missing_evidence"],
        "rerun_step_ids": ["diagnosis"],
        "preserved_step_ids": ["planner", "memory"],
        "round": 1,
        "status": "completed",
        "final_audit_decision": "pass",
        "created_at": "2026-07-24T08:01:00+00:00"
      }
    ]
  },
  "final_audit_decision": "pass"
}
```

`total` 是非负整数，`items` 空时为 `[]`；无最终结论时 `final_audit_decision=null`。接口不返回
已确认事实正文、原始对话、提示词或完整证据正文。前端只映射
`handoff_prepared`（按需通信）、`handoff_blocked`（通信信息不足）、`repair_planned`
（已生成局部修复链）、`repair_step_started`（局部修复执行中）、`repair_completed`
（局部修复完成）、`repair_stopped`（局部修复已停止），只展示摘要、步骤和状态。
