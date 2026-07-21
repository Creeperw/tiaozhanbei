# 前端接口参考

本文面向时珍智训正式 React 前端，描述当前 `main` 分支可用的认证、对话、学习规划、学习工坊、知识库、复习与兼容业务接口。

- 后端默认地址：`http://127.0.0.1:7860`
- 正式主接口前缀：`/api/v1`
- 迁移期业务接口前缀：`/api`
- 主 OpenAPI：`GET /openapi.json`
- Swagger UI：`GET /docs`
- 兼容业务 OpenAPI：`GET /api/v1/platform/openapi.json`，需登录且启用兼容层

本文记录的是前端集成规则和关键数据契约。字段级约束以运行中 OpenAPI 为最终依据。

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
    "created_at": "2026-07-21T12:00:00Z"
  },
  "expires_at": "2026-08-20T12:00:00Z"
}
```

用户名重复返回 `409`。

### 3.2 登录、退出和当前用户

| 方法 | 路径 | 请求 | 返回 |
|---|---|---|---|
| `POST` | `/api/v1/auth/login` | `{username, password}` | 与注册成功响应相同 |
| `POST` | `/api/v1/auth/logout` | 无 | `{"status":"logged_out"}` |
| `GET` | `/api/v1/auth/me` | 无 | `{"user": AuthUser}` |

旧接口 `/token`、`/register`、`/send-code`、`/reset-password` 已停用并返回 `410`，新前端不得调用。

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

规划层级规则：

- 用户明确说“长期规划”“短期计划”“今天的任务”时，可传强约束 `plan_scope`；
- 用户表达模糊时只传 `plan_scope_hint` 或不传，让模型判断；
- 可选值：`long_term`、`short_term`、`daily_task`、`unspecified`；
- 不要仅凭前端关键词强制设置 `plan_scope`。

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
  "plan_scope": "long_term",
  "profile_updates": {
    "learning_background": "零基础",
    "learning_goal": "中医执业医师资格考试"
  }
}
```

恢复必须复用原 `thread_id`，不能重新调用新任务接口。服务端会从 LangGraph 检查点继续，不重复已完成步骤。

SSE 断开不代表任务停止。断线后轮询运行状态，不要立即创建同内容的新任务。

## 5. 学习状态、首页和学习路径

### 5.1 核心接口

| 方法 | 路径 | 用途 |
|---|---|---|
| `GET` | `/api/v1/dashboard/home` | 首页摘要、今日任务、复习任务和继续学习 |
| `GET` | `/api/v1/learning-context` | 当前用户画像、行为、计划、复习队列和能力状态 |
| `GET` | `/api/v1/agent-data-capabilities` | 智能体可读写数据权限清单 |
| `POST` | `/api/v1/learning-tasks/current/complete` | 完成当前当日任务 |
| `GET` | `/api/v1/learning-path` | 获取长期规划的阶段层 |
| `GET` | `/api/v1/learning-path?parent_id={node_id}` | 获取指定阶段的教材层 |
| `GET` | `/api/v1/learning-path/nodes?parent_id={node_id}` | 等价的显式子节点接口 |

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
      "description": "集中完成练习批改、案例训练和错题变式。",
      "enabled": true,
      "recommended": false,
      "capabilities": ["practice_grading", "case_training", "mistake_variation"]
    }
  ],
  "endpoints": {}
}
```

正式模块键只有：`question_training`、`knowledge_cards`、`paper_workspace`。前端不要恢复已移除的“讲义生成”入口。

### 6.2 知识卡片

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

知识卡只保存已完成学习或明确生成的知识点。到期复习卡不能因“生成完成”直接进入复习队列；复习队列准入以用户完成配套题目为准。

### 6.3 试卷

| 方法 | 路径 | 说明 |
|---|---|---|
| `GET` | `/api/v1/workshop/papers?offset=0&limit=50` | 试卷列表 |
| `GET` | `/api/v1/workshop/papers/{paper_id}` | 试卷、题目、计时和答题状态 |
| `PUT` | `/api/v1/workshop/papers/{paper_id}/answers` | 保存草稿答案 |
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
  "expired": false
}
```

题目类型：`single_choice`、`multiple_choice`、`fill_blank`、`short_answer`、`case_quiz`。答案提交后才形成学习行为，进而更新掌握度和复习队列。

### 6.4 训练任务兼容接口

尚未完全迁移的题目训练和组卷入口使用：

| 方法 | 浏览器路径 | 说明 |
|---|---|---|
| `GET` | `/api/training/workspace/modules` | 训练模块能力 |
| `POST` | `/api/training/workspace/tasks` | 创建训练任务 |
| `GET` | `/api/training/workspace/tasks/{task_id}` | 获取训练结果 |
| `GET` | `/api/training/workspace/mistake-variations/sources` | 可变式错题来源 |
| `GET` | `/api/training/workspace/papers/{paper_id}` | 兼容试卷读取 |
| `PUT` | `/api/training/workspace/papers/{paper_id}/answers` | 兼容答案保存 |
| `POST` | `/api/training/workspace/papers/{paper_id}/submit` | 兼容试卷提交 |

创建任务：

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

当前 UI 使用的 `task_type`：`practice_grading`、`knowledge_card_generation`、`paper_generation`、`mistake_variation`。案例训练使用独立会话接口，不通过此字段伪装。

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

正式前端知识图谱当前使用 `/api/knowledge/atlas/*`：

- `GET /api/knowledge/atlas/status`
- `GET /api/knowledge/atlas/routes`
- `GET /api/knowledge/atlas/nodes`
- `GET /api/knowledge/atlas/detail/{kp_id}`
- `GET /api/knowledge/atlas/images/{filename}`
- `POST /api/knowledge/atlas/warm`
- `GET /api/knowledge/atlas/resolve-context`
- `GET /api/knowledge/atlas/questions/search`

新页面优先使用 `/api/v1/knowledge/*`；兼容层仅保留现有知识图谱交互。

## 8. 复习队列

| 方法 | 路径 | 说明 |
|---|---|---|
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

队列准入规则：完成知识点题目的瞬间才创建或更新记忆单元。只打开知识卡、只生成复习卡或未提交答案都不算完成。

## 9. 兼容业务接口索引

以下接口仍由 `backend-handoff` 提供。浏览器统一加 `/api` 前缀；表中均为浏览器最终请求路径。

| 页面/能力 | 主要接口 |
|---|---|
| 首页兼容数据 | `/api/dashboard/home` |
| 个性数据 | `/api/personalization/overview`、`/learner-profile`、`/learning-trends` |
| 学习目标 | `/api/personalization/learning-target` |
| 记忆管理 | `/api/personalization/memories`、`/candidates` 及其子资源 |
| 学习设置 | `/api/personalization/learner-settings` |
| 新用户问卷 | `/api/training/onboarding/status`、`/group-templates`、`/survey` |
| 题目练习 | `/api/training/practice/next`、`/practice/grade` |
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
| 学习路径 | `/api/v1/learning-path*` | 教材知识点展开可进入知识图谱兼容层 |
| 学习工坊 | `/api/v1/workshop*` | 训练任务、案例训练暂用 `/api/training*` |
| 知识仓库 | `/api/v1/knowledge*` | 现有三维图谱暂用 `/api/knowledge/atlas*` |
| 个性数据 | `/api/v1/learning-context` | 画像、记忆编辑暂用 `/api/personalization*` |

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
