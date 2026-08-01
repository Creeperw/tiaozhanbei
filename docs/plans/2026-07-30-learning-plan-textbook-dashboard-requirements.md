# 教学资源页“学习计划”数据同步需求

## 1. 文档目的

本文定义教学资源页顶部“学习计划”区域的数据来源、同步口径、跳转行为和验收标准。目标界面以用户提供的参考图为准，本期只讨论顶部左、右两个学习计划区域，不包含下方教材列表的视觉改版。

本需求只复用现有正式数据，不允许前端硬编码学习时长、阶段名称、教材数量、章节、知识点或进度，也不新增第二套业务真相。

## 2. 已定位的现有实现

### 2.1 目标页面

- 教学资源页入口：`frontend/llm/src/App.jsx` 中 `page = practice` 的默认页面。
- 页面主体：`frontend/llm/src/components/DashboardPage.jsx`。
- 教材列表：`frontend/llm/src/components/workshop-textbook/TextbookLibrary.jsx`。
- 教材目录页：`frontend/llm/src/components/workshop-textbook/TextbookChapterLearning.jsx`。
- 学习路径页：`page = learning-path`，由 `QualificationRoutePage` 渲染。

`DashboardPage` 已经存在“当前学习计划”区域，并已接入学习路径、当前学习任务和教材入口。本次需求应在该区域上收敛数据契约，不另建一套孤立页面状态。

### 2.2 现有正式接口

| 数据能力 | 现有接口 | 关键字段 |
| --- | --- | --- |
| 终身学习统计 | `GET /api/v1/learning-statistics/overview?days=30` | `lifetime.focus_minutes` |
| 学情报告活动统计 | `GET /api/v1/learning-activity/summary?days=30&recent_limit=100` | `counters.focus_sessions.active_seconds`，仅统计窗口期 |
| 长期计划阶段 | `GET /api/v1/learning-path` | `current_node_id`、`nodes[]` |
| 阶段计划教材 | `GET /api/v1/learning-path?parent_id={stageNodeId}` | `nodes[]`，节点类型为 `book` |
| 当前正式学习任务 | `GET /api/v1/dashboard/home` | `current_learning_task` |
| 教材学习进度记录 | `GET /api/learning-activity/textbook-progress?book={book}` | `completed_section_ids`、`last_section_id`、`history` |
| 教材章节、小节目录 | 知识图谱 Atlas 节点接口 | 教材下全部章节、小节 ID |
| 次日任务负载策略 | `GET /api/v1/task-load-policy` | `recommended_minutes`、`allocation`、`reasons`、`evidence` |

## 3. 总体数据原则

1. 同一指标必须只有一个权威来源，多个页面只负责展示同一字段。
2. 学习计划层级由长期计划投影提供；教材实际完成进度由学习行为记录提供，两者不得互相伪造。
3. 前端不得显示占位百分比。数据不可用时显示明确空状态或“进度待统计”。
4. 所有数据按当前登录用户隔离，不使用前端传入的 learner ID 切换用户。
5. 第一版只组合前端现有接口完成，不新增或改造后端接口；前端通过共享 loader/selector 统一字段优先级和计算口径。
6. 后续可由后端聚合现有来源后一次返回给教学资源页；聚合层不新增数据库真相，只投影现有数据。

## 4. 左侧“学习计划概览”需求

### 4.1 累计学习时长

#### 展示

- 指标名：累计学习时长。
- 参考图以小时展示；建议：小于 60 分钟显示分钟，大于等于 60 分钟显示小时，可保留 1 位小数。

#### 权威来源

- 使用 `GET /api/v1/learning-statistics/overview?days=30` 的 `lifetime.focus_minutes`。
- 底层来源是 `learning_focus_sessions.active_seconds`，由有效心跳和可见/交互规则累计。

#### 同步要求

- 教学资源页与个人数据页“我的学情报告”必须共用 `lifetime.focus_minutes`。
- 当前学情报告的“累计学习时长”实际使用近 30 天 `active_seconds`，名称与统计口径不一致。实现时应把学情报告也切换到终身字段；如果仍需保留近 30 天指标，应改名为“近30天学习时长”，不能继续称为累计。
- 建议抽取共享前端 loader/selector，两个页面不得各自重算。

### 4.2 学习阶段

#### 权威来源

- 根路径接口：`GET /api/v1/learning-path`。
- 当前阶段优先取 `current_node_id` 对应的 `stage` 节点。
- 兼容回退：找 `status = in_progress/current` 的阶段；仍不存在时取第一个未完成阶段。
- 展示值使用阶段节点的 `title`，阶段说明使用 `description`。

#### 同步要求

- 教学资源页与学习路径页必须消费同一份 `/learning-path` 数据。
- 前端不得按数组下标写死“中医基础与文化语言”等阶段文案。
- 用户描述为五个阶段，但当前前端演示模型包含六个默认阶段，后端测试也允许动态的 4～6 个阶段。正式界面必须以后端已发布长期计划为准。如果产品规则必须固定五阶段，应由长期规划/路线契约在生成和审核时保证五阶段，而不是由本页面截断或补齐。

### 4.3 计划教材

#### 指标定义

- 本卡片默认统计“当前学习阶段”的计划教材，而不是整个长期计划的全部教材。
- 总数：当前阶段子节点中 `node_type = book` 的数量；可用当前阶段 `child_count` 快速展示，但最终以子节点实际数量为准。
- 已完成数：教材正式进度达到 100% 的数量。

#### 权威来源

- 教材集合：`GET /api/v1/learning-path?parent_id={currentStageId}`。
- 完成状态：每本教材的正式教材进度记录与正式小节目录共同计算。

#### 完成判定

```text
教材进度 = 已完成且仍属于当前教材目录的小节数 / 当前教材全部小节数
教材已完成 = 教材小节总数 > 0 且教材进度 = 100%
```

- 不允许直接使用当前学习路径 book 节点的 `status` 统计完成数。现有后端投影主要用于规划顺序，尚未把教材学习记录聚合成真实 book 状态。
- 已删除或不属于当前教材目录的历史 section ID 不计入分子。

## 5. 右侧“当前在学”需求

### 5.1 当前书名

按以下优先级确定：

1. `dashboard/home.current_learning_task.learning_chapter.book`。
2. 当前阶段计划教材中，最近存在教材学习记录的教材。
3. 当前阶段中 `status = in_progress/current` 的教材。
4. 当前阶段第一本未完成教材。

显示时统一去掉数据值中的 `《》`，由视图层添加书名号。

### 5.2 当前章节名

按以下优先级确定：

1. `current_learning_task.learning_chapter.title`。
2. 当前教材进度接口 `history` 中最后一条记录的 `chapter_name`。
3. 当前教材目录中的第一个未完成章节。

当前任务接口已经在后端将 `LearningTask.learning_chapter` 解析为 `book + title`，前端不应再次用正则拆解自然语言任务。

### 5.3 下一个知识点

按以下优先级确定：

1. `current_learning_task.items[]` 中第一个 `status != completed` 且带 `kp_name` 的原子任务。
2. `current_learning_task.focus_knowledge_points[0]`。
3. `current_learning_task.knowledge_cards[0].title`。
4. 若没有正式当日任务，则从当前教材下一个未完成小节的知识点列表中取第一个知识点。

界面文案为“下一个知识点：{名称}”。无可用知识点时显示“完成当前章节后生成下一学习重点”，不展示伪造内容。

### 5.4 学习进度条

- 参考图中的百分比表示当前教材学习进度，不表示当日任务完成率。
- 数据来源与教材目录页的“课程学习进度”完全一致：教材全部小节 + `completed_section_ids`。
- 教学资源页和教材目录页应共用同一进度计算函数或后端聚合字段。
- 禁止沿用当前 `DashboardPage` 在无进度时显示 `12%` 的视觉占位逻辑。
- 当小节目录或进度记录任一来源加载失败时，不显示百分比，显示“进度待统计”。

### 5.5 今日建议学习时长

#### 责任归属

- 计算责任：后端确定性的任务负载策略服务，不由前端计算，也不由大模型自由生成。
- 正式来源：`GET /api/v1/task-load-policy` 的 `recommended_minutes`。
- 该服务已经结合近期原子任务完成率、有效专注时长、练习正确率、掌握度和到期复习量，并提供 `allocation`、`evidence`、`reasons`，结果可审计。
- Diagnosis Agent 在生成/刷新当日任务时应采用该值，并可解释原因，但不得重新计算或覆盖它；Audit 负责检查计划是否遵守时间预算。

#### 与当前任务时长的区别

- “今日建议学习时长”：`task-load-policy.recommended_minutes`，表示系统建议的今日/下一任务负载。
- “当前任务预计时长”：`dashboard/home.current_learning_task.estimated_minutes`，表示已经发布的当前任务预算。
- 参考图只展示“今日建议学习时长”。如两者不同，可在提示信息中说明“当前任务预计 X 分钟”，不能静默混用。

### 5.6 右侧绿色学习任务卡片背景

#### 设计目标

在保持现有绿色渐变背景和信息层级不变的前提下，为卡片增加低透明度的中医文化线稿纹理。纹理只承担氛围装饰作用，不表达业务状态，不参与交互，也不能影响课程标题、章节、进度和按钮的阅读。

#### 视觉元素

背景纹理可组合以下中医文化元素，但单张卡片不要求全部同时出现，应控制元素密度：

- 草药植物纹理；
- 药碾、药杵、药罐；
- 古籍书卷；
- 阴阳太极元素；
- 经络穴位示意纹样。

#### 设计风格

- 新中式医疗科技风；
- 极简线稿，不使用写实照片或高细节实物图；
- 线稿颜色使用浅绿色或白色；
- 最终合成透明度控制在 5%～15%；
- 不使用高对比描边、实心大色块、强阴影或高饱和颜色；
- 视觉质感应符合高级教育平台 UI，不做传统药铺式复古堆砌。

#### 布局要求

- 纹理主体集中在卡片右侧 30%～40% 区域；
- 左侧信息区保持纯净，保障书名、章节、进度、建议时长和操作按钮的对比度；
- 装饰可以从右下或右中向内延伸，但不得进入左侧主要文字安全区；
- 靠近文字区的一侧使用透明渐变蒙版自然渐隐；
- 纹理与绿色渐变背景融合，不形成独立插画卡片或明显边框；
- 小屏幕下应进一步降低透明度、裁切到右侧，必要时隐藏次要纹样。

#### 素材与实现约束

- 优先使用透明背景 SVG 或高分辨率透明 PNG，便于适配不同尺寸和深浅主题；
- 装饰层必须设置 `pointer-events: none`，不能遮挡按钮或产生焦点；
- 装饰层应位于内容层下方，内容区保持稳定的可读对比度；
- 同一张底图应通过裁切和渐隐适配桌面端，不在前端重复叠加多个高成本大图；
- 素材需确认可商用或由项目自行生成，禁止直接使用来源不明的网络图片。

#### 视觉验收

1. 第一眼先看到课程信息和操作按钮，随后才感知到背景纹理。
2. 在 100% 缩放和常见低亮度显示器上，所有文字仍清晰可读。
3. 纹理没有覆盖进度条、百分比、建议时长和按钮轮廓。
4. 桌面端纹理占据右侧约 30%～40%，左侧没有明显图案干扰。
5. 移动端不因纹理产生横向滚动、布局抖动或点击遮挡。

## 6. 按钮与导航

### 6.1 继续学习

点击后进入当前教材目录：

```js
{
  page: 'practice',
  params: {
    view: 'textbook-chapters',
    route: currentBookRoute || 'textbook_14_5',
    lv1: currentBookName,
    source: 'learning-plan'
  }
}
```

- 当前教材目录页会读取 `last_section_id`，用户可在目录页继续最近学习位置。
- 当前书名为空时按钮禁用，并提示“尚未生成可学习教材”。

### 6.2 查看完整计划

点击后跳转：

```js
{ page: 'learning-path', params: {} }
```

目标为顶部导航中的正式学习路径页，不是内部的 `learning-path-tasks` 页面。

## 7. 第一版接口组合与后续聚合

### 7.1 第一版：只使用现有接口

第一版不等待后端增加聚合字段，由教学资源页组合以下现有接口：

1. `GET /api/v1/learning-statistics/overview?days=30`：读取终身累计学习时长。
2. `GET /api/v1/learning-path`：读取当前阶段。
3. `GET /api/v1/learning-path?parent_id={currentStageId}`：读取当前阶段计划教材。
4. `GET /api/v1/dashboard/home`：读取当前任务、书名、章节和知识点。
5. `GET /api/learning-activity/textbook-progress?book={book}`：读取当前教材已完成小节和最近位置。
6. Atlas 教材目录接口：读取当前教材的章节、小节总量并计算真实进度。
7. `GET /api/v1/task-load-policy`：读取今日建议学习时长。

第一版实现要求：

- 在前端新增共享的数据加载与归一化层，页面组件不直接散落七套字段判断；
- 学情报告与教学资源页复用累计时长 selector；
- 教材目录页与教学资源页复用教材进度计算函数；
- 并行请求互不依赖的数据；阶段教材请求须等待当前阶段确定后再发起；
- 使用请求取消或版本标识，避免用户切换页面后旧响应覆盖新状态；
- 单个接口失败时保留其他可用数据，并按本文空状态规则降级。

### 7.2 后续：推荐的页面聚合契约

为避免教学资源页并发请求每本教材、重复计算并出现短暂不一致，建议由现有 `GET /api/v1/dashboard/home` 增加一个只读聚合字段 `learning_plan_overview`。该字段只聚合现有正式来源，不持久化新状态。

示例：

```json
{
  "learning_plan_overview": {
    "total_focus_minutes": 5160,
    "current_stage": {
      "node_id": "plan:LP_1:stage:foundation",
      "title": "中医基础与文化语言"
    },
    "planned_textbooks": {
      "scope": "current_stage",
      "total": 4,
      "completed": 1
    },
    "current_learning": {
      "book": "中医学基础",
      "route_id": "textbook_14_5",
      "chapter": "第3章 阴阳五行学说",
      "next_knowledge_point": "五行生克关系",
      "progress": 0.28,
      "last_section_id": "SEC_xxx"
    },
    "recommended_minutes_today": 25,
    "current_task_estimated_minutes": 25,
    "source_refs": {
      "focus": "learning-statistics.lifetime.focus_minutes",
      "plan": "learning-path",
      "progress": "textbook-progress + atlas-catalog",
      "duration": "task-load-policy"
    }
  }
}
```

该聚合契约不属于第一版交付范围；第一版先按 7.1 的现有接口组合落地。后续接口上线时，展示组件应保持不变，仅替换数据适配层。

## 8. 加载、空状态与刷新

### 8.1 加载

- 两侧卡片使用骨架态，不先展示 `0`、`12%` 或默认教材名。
- 基础聚合完成后一次性替换关键数据，避免阶段、教材和当前任务分别闪动。

### 8.2 空状态

- 未生成长期计划：左侧显示“等待生成学习计划”，右侧提供“去制定计划”。
- 有计划但无教材：显示“当前阶段教材待规划”。
- 有教材但无学习记录：进度为 0%，当前章节取第一章；不得假定用户已学习。
- 当前任务为空：用教材最近学习记录回退，不使用过期任务。

### 8.3 刷新时机

- 页面首次进入。
- 用户从教材目录页返回教学资源页。
- 完成教材小节后。
- 当前任务完成、刷新或重新规划后。
- 页面重新获得焦点时可做轻量刷新，避免另一标签页学习后数据不同步。

## 9. 当前实现缺口

1. 学情报告将近 30 天专注时长标为“累计学习时长”，与终身口径不一致。
2. `DashboardPage` 的当前教材进度读取学习路径 book 节点 `progress`，但正式后端尚未把教材完成记录投影到该字段。
3. `DashboardPage` 在无进度时绘制固定 `12%`，会产生假数据。
4. 当前教材目录进度需要“Atlas 小节总数 + textbook-progress 完成记录”组合，缺少面向多本教材的聚合接口。
5. 当前前端存在六阶段默认演示数据，不能据此认定正式业务一定是五阶段。
6. `dashboard/home.current_learning_task` 已具备书名、章节、知识点、任务进度和预计时长，是右侧卡片的首选当前任务来源，但当前页面尚未展示下一个知识点。

## 10. 验收标准

1. 教学资源页与“我的学情报告”的累计学习时长数值一致，且均来自终身统计。
2. 教学资源页当前阶段与学习路径页高亮阶段一致。
3. 教学资源页计划教材数量与当前阶段展开后的教材数量一致。
4. 完成一本教材最后一个未完成小节后，两个页面的教材进度均变为 100%，已完成教材数加 1。
5. 当前任务存在时，书名、章节和下一个知识点与 `dashboard/home.current_learning_task` 一致。
6. 当前任务不存在时，能按最近教材记录稳定回退；完全无记录时进入第一本未完成计划教材。
7. 今日建议学习时长与 `task-load-policy.recommended_minutes` 一致，前端不自行重算。
8. “继续学习”进入当前教材目录，“查看完整计划”进入正式学习路径页。
9. 任一接口失败时不展示伪造值，其他可用字段仍正常展示。
10. 阶段数改变时页面自适应，不依赖固定五阶段或六阶段数组。

## 11. 建议测试范围

- 单元测试：字段优先级、小时格式化、阶段选择、教材完成计算、知识点回退、空状态。
- 组件测试：两个按钮导航参数、接口部分失败、无计划、有计划无记录、教材完成边界。
- 接口测试：聚合字段用户隔离、终身时长、当前阶段、教材进度、负载策略来源引用。
- 集成测试：教材小节完成后返回教学资源页，进度和计划教材完成数同步更新。
- 回归测试：个人数据页、学习路径页、教材目录页继续使用同一正式数据源。
