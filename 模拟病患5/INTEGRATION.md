# 模拟病患组件 — 后端集成说明

## 一、API 接口

### 统一入口

**`POST /api/v1/simulated-patient`**

所有功能通过请求体中的 `action` 字段分发。需要登录 Cookie。

#### 请求体 `SimulatedPatientRequest`

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `user_id` | str | 是 | 用户唯一标识符，最大 128 字符 |
| `session_id` | str | 是 | 会话唯一标识符，最大 128 字符 |
| `action` | str | 是 | 操作类型，见下方 [Action 枚举](#action-枚举) |
| `user_input` | str | 否 | 用户输入文本，`dialogue` 时必填，最大 2000 字符 |
| `case_id` | str | 否 | 案例 ID，不传则系统自动匹配 |
| `diagnosis` | dict | 否 | 诊断内容，`submit` 时必填 |
| `help_type` | str | 否 | 援助类型，`help` 时必填：`"question"` 或 `"interpretation"` |
| `history_id` | str | 否 | 历史记录 ID，`history_detail` 时必填 |
| `limit` | int | 否 | 返回条数限制，默认 100（1-500） |

#### 响应体

| 字段 | 类型 | 说明 |
|------|------|------|
| `session_id` | str | 会话标识符 |
| `action` | str | 对应的操作类型 |
| `success` | bool | 是否执行成功 |
| `data` | any | 成功时返回的数据，结构因 action 而异 |
| `error` | str\|null | 失败时的错误信息 |
| `turn_count` | int | 当前对话轮次 |
| `help_available` | bool | 是否可获取援助（10 轮后为 true） |
| `is_complete` | bool | 是否已完成（提交后为 true） |

#### Action 枚举

| Action | 描述 | 必填参数 |
|--------|------|----------|
| `start` | 开始问诊，初始化会话并返回开场白 | `user_id`, `session_id` |
| `dialogue` | 进行对话轮次，返回患者回复 | `user_id`, `session_id`, `user_input` |
| `help` | 请求援助（10 轮后可用） | `user_id`, `session_id`, `help_type` |
| `submit` | 提交诊断，返回六维评分报告 | `user_id`, `session_id`, `diagnosis` |
| `stats` | 查询学习统计（总数/正确/错误/正确率/待复习） | `user_id` |
| `mistakes` | 查询错题库 | `user_id` |
| `collections` | 查询收藏夹 | `user_id` |
| `dialog_history` | 查询对话历史 | `user_id` |
| `history_detail` | 查询历史详情 | `user_id`, `history_id` |
| `history` | 查询当前会话历史 | `user_id`, `session_id` |
| `reset` | 重置当前会话（保留案例，重新开始） | `user_id`, `session_id` |
| `clear` | 清除当前会话 | `user_id`, `session_id` |

### 快捷接口

**`GET /api/v1/simulated-patient/stats/{user_id}`** — 获取用户学习统计（需登录）

**`GET /api/v1/simulated-patient/mistakes/{user_id}`** — 获取用户错题库（需登录）

---

## 二、集成修改说明

### 修改原则

- **不修改前后端其他部分** — 所有修改均为新增文件，主 app.py 仅新增 6 行注册代码
- **保持组件功能不变** — `simulated_patient/` 核心代码除 Agent 工厂外未修改
- **复用项目基础设施** — LLM 调用使用项目 `.env.local` 中已有的 DashScope 配置

### 新增文件

| 文件 | 路径 | 说明 |
|------|------|------|
| 组件核心模块 | `backend/competition_app/simulated_patient/` | 从 `模拟病患5/simulated_patient/` 完整复制 |
| LLM 适配器 | `simulated_patient/llm_adapter.py` | 新增。实现组件 `LLMProvider` 接口，同步调用项目配置的 DashScope 模型 |
| API 路由 | `api/simulated_patient_routes.py` | 新增。FastAPI 路由，包含 `POST /api/v1/simulated-patient` 统一入口及两个快捷 GET 接口 |
| 案例数据 | `data/clinical_cases.json` | 从 `模拟病患5/tests/clinical_cases.json` 复制，2 个临床案例 |

### 修改文件

#### 1. `backend/competition_app/api/app.py`（+6 行）

```python
# 新增 import
from competition_app.api.simulated_patient_routes import router as sp_router, init_engine as sp_init_engine

# 在 create_app() 中新增注册（static mount 之后、middleware 之前）
try:
    sp_init_engine(llm_timeout_seconds=120.0)
    app.include_router(sp_router)
except Exception:
    _logger.warning("模拟病患模块初始化失败", exc_info=True)
```

#### 2. `backend/competition_app/simulated_patient/agent_clients.py`（修改 Agent 工厂）

**修改原因**：原组件在 `test_data=None`（生产环境）时抛出 `NotImplementedError`，无法启动。

**修改内容**：新增两个生产环境桩类，Agent 工厂在无 test_data 时返回桩实例而非抛异常。

```python
# 新增
class ProductionMemoryAgent(MemoryAgent):
    def get_learner_context(self, user_id): ...
        # 返回空的用户上下文

class ProductionDiagnosisAgent(DiagnosisAgent):
    def get_diagnosis_result(self, user_id): ...
        # 返回空的学情诊断

# AgentFactory 中修改
# 之前: raise NotImplementedError("生产环境请实现...")
# 之后: return ProductionMemoryAgent() / ProductionDiagnosisAgent()
```

### 未修改的文件

以下文件保持原样，功能不变：

- `simulated_patient/engine.py` — 核心引擎
- `simulated_patient/schemas.py` — 请求/响应数据结构
- `simulated_patient/prompts.py` — LLM 提示词模板
- `simulated_patient/case_adapter.py` — CMB 案例适配器
- `simulated_patient/data_provider.py` — 数据持久化
- `simulated_patient/session_manager.py` — 会话管理
- `simulated_patient/constants.py` — 系统常量
- `simulated_patient/default_providers.py` — 原始 DeepSeek 提供者（未被调用，保留备用）
- 前端所有文件 — 无任何改动
- 后端其他路由/服务 — 无任何改动

---

## 三、LLM 调用链路

```
POST /api/v1/simulated-patient
  → simulated_patient_routes.py
    → asyncio.to_thread(engine.execute)
      → engine._handle_start / _handle_dialogue / _handle_submit
        → expert_agent.generate_patient_reply / grade_answer
          → llm_provider.chat(messages, temperature, max_tokens)
            → ProjectLLMProvider (llm_adapter.py)
              → httpx.Client.post(dashscope_url)
```

使用的模型和密钥来自 `.env.local`：
- `CHAT_BASE_URL` — 当前：`https://dashscope.aliyuncs.com/compatible-mode/v1`
- `CHAT_MODEL` — 当前：`qwen3.7-max-2026-05-20`
- `DASHSCOPE_API_KEY` — 阿里云百炼 API Key

---

## 四、运行时数据

模拟病患的持久化数据（错题库、收藏夹、统计数据、对话历史）存储在：

```
backend/competition_app/runtime/simulated_patient/
├── mistakes.json      # 错题库
├── collections.json   # 收藏夹
├── stats.json         # 学习统计
├── history.json       # 提交历史
└── dialog_{user_id}.json  # 对话记录
```
