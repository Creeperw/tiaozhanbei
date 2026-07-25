# 模拟病患 API 集成说明

## 使用的后端接口

| 方法 | 路径 | 用途 |
|------|------|------|
| POST | `/api/v1/simulated-patient` | 统一入口，通过 `action` 字段分发 |
| GET | `/api/v1/simulated-patient/stats/{user_id}` | 快捷获取统计（未使用） |
| GET | `/api/v1/simulated-patient/mistakes/{user_id}` | 快捷获取错题（未使用） |

## 前端 API 调用清单

所有调用均使用 `fetchWithAuth` + `readJsonResponse`（已有工具函数），自动携带 HttpOnly Cookie。

| 功能 | action | 额外参数 | 响应关键字段 |
|------|--------|---------|------------|
| 开始问诊 | `start` | — | `patient_reply`, `patient_info`, `session_id` |
| 对话 | `dialogue` | `user_input` | `patient_reply`, `turn_count`, `help_available` |
| 援助 | `help` | `help_type: "question"\|"interpretation"` | `question` 或 `interpretation` |
| 提交诊断 | `submit` | `diagnosis: {syndrome, prescription, composition, notes}` | `grading_report`, `history_id` |
| 清空对话 | `reset` | — | `patient_reply` |
| 统计 | `stats` | — | `total`, `rate` |
| 对话历史 | `dialog_history` | `limit` | `list[{session_id, turn, role, content}]` |
| 收藏列表 | `collections` | — | `list[{case_id, case_name}]` |
| 错题列表 | `mistakes` | — | `list[{mistake_id, case_name, score}]` |
| 历史详情 | `history_detail` | `history_id` | `full_dialogue`, `grading_report` |

## 前端 Workaround（后端功能缺口）

### 1. 收藏夹无法持久化到服务端

**原因**：`SimulatedPatientEngine` 的 `data_provider` 有 `add_collection()` / `remove_collection()` 方法，但未被任何 `action` 接线。

**前端处理**：收藏夹存储在 `localStorage` (`sp-collections`)，仅在本浏览器可见。

**建议后端修复**：在 `engine.py` 的 `execute()` 中增加 `add_collection` 和 `remove_collection` action。

### 2. 历史记录缺患者人口统计

**原因**：`_handle_submit()` 保存 `history_record` 时不包含 `gender`、`age_range`、`body_type`。

**前端处理**：`start` 时把 `patient_info` 存入 `localStorage` (`sp-session-meta`)，侧栏展示时拼合数据。

**建议后端修复**：在 `_handle_submit()` 的 `history_record` 中加入 `basic_info` 字段。

### 3. Session 与 History 无关联

**原因**：`history_record` 不包含 `session_id`，无法从历史记录回链到原始会话。

**前端处理**：`localStorage` (`sp-session-meta`) 维护 `{session_id: {history_id, ...}}` 映射。

**建议后端修复**：在 `_handle_submit()` 的 `history_record` 中加入 `session_id` 字段。

## 不涉及的修改

- 后端所有文件 **未修改**
- 智能助教、ChatInterface、AppShell 等所有其他前端组件 **未修改**
- 仅新增 `SimulatedPatientChat.jsx` 并在 `PracticePage.jsx` 和 `QuestionTrainingPanel.jsx` 中各改 2 行引用
