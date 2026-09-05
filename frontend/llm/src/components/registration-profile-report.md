# 注册、用户画像、学情报告与复习面板说明

本文说明当前正式前端中的注册流程、注册后用户画像、学情报告和复习掌握界面。相关代码集中在当前目录。

## 功能入口

未登录用户在展示页点击“开始学习”或“开启智训之旅”后进入注册流程。已创建账号但 `onboarding_required=true` 的用户登录后也会继续进入同一流程。

注册和学情调查合并为 8 步：

1. 创建账号；
2. 选择用户群体；
3. 选择考试或课程目标；
4. 选择学历或专业背景；
5. 选择当前中医药基础；
6. 选择每日可投入时长；
7. 选择常用学习时段，可跳过；
8. 选择资源偏好，可跳过。

必填项没有选择时不能继续。第二步开始可以返回上一步；账号创建后返回第一步只显示已创建账号摘要，不重复提交账号和密码。

## 前端文件

```text
frontend/llm/src/
├─ App.jsx
└─ components/
   ├─ AuthPage.jsx
   ├─ RegistrationJourney.jsx
   ├─ RegistrationJourneyFrame.jsx
   ├─ RegistrationJourney.css
   ├─ OnboardingSurveyPanel.jsx
   ├─ PersonalizationPage.jsx
   ├─ LearningInsightsReportPage.jsx
   ├─ LearningTrendDualAxisChart.jsx
   ├─ LearningActivityHeatmap.jsx
   ├─ MasteryHeatmap.jsx
   └─ ReviewDashboardPanel.jsx
```

文件职责：

- `AuthPage.jsx`：展示页、登录弹层以及新注册流程入口；
- `RegistrationJourney.jsx`：账号创建和注册阶段切换；
- `RegistrationJourneyFrame.jsx`：8 步共用外框、进度条、李时珍向导和退出按钮；
- `RegistrationJourney.css`：固定问卷和李时珍位置、桌面端与移动端布局；
- `OnboardingSurveyPanel.jsx`：加载问题、恢复答案、必填校验、跳过、返回和最终提交；
- `App.jsx`：识别 `onboarding_required`，阻止未完成初始化的账号直接进入工作台；
- `PersonalizationPage.jsx`：以只读卡片展示注册结果，用户通过“编辑画像”弹窗修改、逐字段锁定和保存；
- `LearningInsightsReportPage.jsx`：消费正式学情洞察和行为汇总，展示能力雷达、学习趋势、活跃度与薄弱知识点；
- `MasteryHeatmap.jsx`、`ReviewDashboardPanel.jsx`：展示知识点掌握、可信度、练习次数、复习保持率和复习队列。

## 注册与学情接口

| 用途 | 请求 |
| --- | --- |
| 创建账号 | `POST {AUTH_API_BASE}/register` |
| 获取用户群体模板 | `GET {API_BASE}/training/onboarding/group-templates` |
| 获取考试目标 | `GET {MAIN_API_BASE}/qualification-targets` |
| 获取已保存调查 | `GET {API_BASE}/training/onboarding/status` |
| 保存调查 | `POST {API_BASE}/training/onboarding/survey` |
| 完成账号初始化 | `POST {AUTH_API_BASE}/onboarding/complete` |

创建账号请求：

```json
{
  "username": "learning_user",
  "password": "至少八位密码",
  "display_name": "选填显示名"
}
```

调查数据按以下分组提交：

```json
{
  "learner_group": "cross_professional",
  "target_type": "qualification_exam",
  "exam_track_id": "考试路线 ID",
  "background": {
    "education_major": "非医学专业",
    "foundation_level": "了解基础术语"
  },
  "preferences": {
    "daily_available_minutes": 45,
    "preferred_time_slot": "晚间",
    "resource_preference": "知识卡片"
  },
  "goals": {
    "target_exam_or_course": "中医执业医师资格考试",
    "textbook_route_id": "教材路线 ID",
    "textbook_route_version": "教材路线版本"
  }
}
```

接口基础地址由 `frontend/llm/src/utils/api.js` 统一提供。注册请求使用 Cookie 会话；其余请求使用 `fetchWithAuth`。

## 用户画像

用户画像入口位于“个性数据 → 用户画像”。页面从以下接口合并数据：

| 用途 | 请求 |
| --- | --- |
| 个性数据概览 | `GET {API_BASE}/personalization/overview` |
| 学习画像及锁定字段 | `GET/PUT {API_BASE}/personalization/learner-profile` |
| 已确认学习上下文 | `GET {MAIN_API_BASE}/learning-context` |
| 基础画像保存 | `PUT {API_BASE}/personalization/profile` |

页面分为“基础信息”和“学习偏好”两列，默认以只读方式呈现。点击“编辑画像”后：

- 弹窗加载当前画像，不使用占位演示值覆盖用户数据；
- 可编辑专业背景、当前基础、学习目标、可投入时间和偏好字段；
- 点击字段锁定按钮后将其加入 `locked_fields`，再次点击可解除；
- 点击“保存画像”后，字段值、锁定列表和 `lock_reason` 一起提交。

锁定只是保护后续推荐和智能体更新时不要覆盖用户确认值，不代表数据库字段不可修改；用户主动解锁后仍可编辑。

## 学情报告与复习统计

- 学情报告通过 `/api/v1/learning-insights?days=30&run_automation=false` 获取能力维度、趋势和薄弱点（报告页只读，不触发自动化推送，自动化由答题提交等真实学习事件触发）；
- 累计学习时长、完成练习和活跃天数补充读取 `/api/v1/learning-activity/summary?days=30&recent_limit=100`；
- 学习趋势使用 `focus_minutes` 和 `task_completion_rate`，双坐标轴根据真实数据动态缩放；
- 雷达图和薄弱点没有可靠证据时显示数据不足，不生成预览成绩；
- 复习面板通过正式复习与掌握接口展示热力图、到期队列、知识点明细和历史变化；
- 复习队列仍只接纳完成知识点题目并通过批改的记录，知识卡生成本身不会入队。

所有百分比均来自后端正式字段；缺失值渲染为“—”或数据不足，不按零分处理。

## 布局约束

- 李时珍固定在右侧，不随不同问题高度上下跳动；
- 问卷卡片、进度条和操作按钮使用固定基线；
- 1280×720、浏览器 100% 缩放下应完整显示；
- 左上角不显示独立书本图标；
- 移动端改为纵向布局，不能产生横向滚动。

## 开发验证

在项目根目录运行：

```powershell
npm --prefix frontend/llm run test:unit -- `
  src/components/AuthPage.test.jsx `
  src/components/OnboardingSurveyPanel.test.jsx `
  src/components/PersonalizationPage.test.jsx `
  src/components/ReportsPage.test.jsx `
  src/components/ReviewDashboardPanel.test.jsx

npm --prefix frontend/llm run build
```

浏览器验收至少覆盖：

1. 新账号完成全部 8 步并进入工作台；
2. 必填题未选时不能继续；
3. 第七、八步可以跳过；
4. 返回第一步不会重复创建账号；
5. `onboarding_required=true` 的已有账号能继续调查；
6. 用户画像锁定后不能输入，解锁后可以输入；
7. 学情报告的四项摘要、能力雷达、趋势、薄弱点和活跃度均来自当前用户数据；
8. 复习面板的热力图、到期队列、掌握明细与历史变化按当前用户隔离；
9. 1024px 以上用户画像充分使用工作区，窄屏可纵向滚动且无横向溢出。
