# 注册、用户画像与学情报告说明

本文说明 `ikram/新注册丨新学习工坊` 分支中的新注册流程、注册后用户画像和学情报告界面。相关代码集中在当前目录。

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
   ├─ LearningTrendDualAxisChart.jsx
   ├─ LearningActivityHeatmap.jsx
   ├─ ReportsPage.jsx
   └─ ReviewDashboardPanel.jsx
```

文件职责：

- `AuthPage.jsx`：展示页、登录弹层以及新注册流程入口；
- `RegistrationJourney.jsx`：账号创建和注册阶段切换；
- `RegistrationJourneyFrame.jsx`：8 步共用外框、进度条、李时珍向导和退出按钮；
- `RegistrationJourney.css`：固定问卷和李时珍位置、桌面端与移动端布局；
- `OnboardingSurveyPanel.jsx`：加载问题、恢复答案、必填校验、跳过、返回和最终提交；
- `App.jsx`：识别 `onboarding_required`，阻止未完成初始化的账号直接进入工作台；
- `PersonalizationPage.jsx`：注册结果对应的用户画像编辑、逐字段锁定和保存；
- `ReportsPage.jsx`、两张图表组件：学情报告首屏、雷达图、学习趋势和活跃度；
- `ReviewDashboardPanel.jsx`：复习与掌握统计。

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

页面分为“学习基础与目标”和“偏好与约束”两列。字段默认可输入；点击输入框右侧锁图标后：

- 字段加入 `locked_fields`；
- 输入框被禁用并变灰；
- 再次点击解锁后恢复输入；
- 点击“保存用户画像”后，字段值、锁定列表和 `lock_reason` 一起提交。

锁定只是保护后续推荐和智能体更新时不要覆盖用户确认值，不代表数据库字段不可修改；用户主动解锁后仍可编辑。

## 学情报告与复习统计

- 学习趋势使用 `focus_minutes` 和 `task_completion_rate`；
- 有效学习时长为绿色，任务完成率为橙色；
- 左右坐标轴根据当前最大数据动态缩放，不固定在不合适的量程；
- 学习活跃度综合有效学习时长、任务完成率和登录状态；
- 雷达图无真实数据时暂用预览值展示后期形态；
- 复习统计的“已评估知识点”等名称显示在卡片标题位置。

后端真实数据接入后，应删除雷达图临时预览值，不能把演示值当成用户成绩。

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
7. 学情报告第二行指标在首屏可见；
8. 趋势图颜色、动态坐标轴和复习统计标题正确。

