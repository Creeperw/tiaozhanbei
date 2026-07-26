# 学习工坊教材学习说明

本说明对应 `ikram/学习工坊` 分支中的教材学习功能、教材结构数据和教材封面资源。

## 功能入口

1. 启动后端服务和前端服务。
2. 登录平台。
3. 打开左侧导航的“学习工坊”。
4. 学习工坊会读取当前登录用户的学习计划，并在顶部显示：
   - 当前学习阶段；
   - 当前应该继续学习的教材；
   - 当前学习任务章节；
   - 任务预计时长；
   - 计划教材状态或进度。
5. 点击“点击继续学习”或任意教材卡片，进入教材章节学习页面。

## 教材列表规则

- 默认优先展示当前用户学习计划中的教材，并保持学习计划原有顺序。
- 滚动到列表底部后，点击“展开所有教材”，会将其余教材追加到列表末尾。
- 展开前已经显示的计划教材不会重新排序或移动。
- 当前教材全集来自 `textbook_14_5` 知识图谱路线，共 83 本教材。

## 章节学习

进入教材后：

1. 点击章节，查看该章节的小节。
2. 点击小节，进入知识点和视频学习页面。
3. 点击带有时间戳视频的知识点时，时间戳视频会替换当前视频，页面始终只显示一个视频播放器。
4. 点击“返回上一个视频”，可以回到前一个视频。
5. 点击“返回”可以回到学习工坊。

## 数据总览

教材学习数据不是单个文件，而是由“原始切片、章节目录、切片映射、知识库索引、前端资源、学习计划”几层数据组成：

```text
原始教材 Markdown
   ↓
73,170 条标准切片 source_chunks.jsonl
   ↓ 通过 chunk_uid 关联
最终章节目录 chapter_nodes.jsonl
   ↓
最终切片映射 chunk_chapter_links.jsonl
   ↓
后端知识图谱接口 / 前端教材学习页面
```

其中 `chunk_uid` 是跨文件、跨服务关联切片的唯一键。本次发布不修改原始切片内容，也不重新编号 `chunk_uid`。

## 数据位置

### 1. 章节结构发布目录

最终教材结构数据位于：

```text
backend/competition/knowledge_atlas_chapters/2026-07-22/final/
```

主要文件：

- `chapter_nodes.jsonl`：最终发布的教材、章节和小节节点；
- `chunk_chapter_links.jsonl`：每个 `chunk_uid` 到章节和小节的映射；
- `publish-report.json`：最终发布数量和完整性报告。

同一日期目录下还保留了生成、审核和修复过程资料，例如 `reviewed-v2/`、`reviewed-v3/`、`audit_data/` 和若干处理脚本。这些是开发审计资料，不是运行时必须文件；正式消费应优先使用 `final/` 目录。

### 2. 原始教材切片

原始切片属于公共知识库资产，标准文件名为：

```text
03_pipeline_chunks/source_chunks.jsonl
```

项目中的章节文件是对这批切片的补充索引。后端通过 `chunk_uid` 将切片正文与章节目录连接起来。原始切片、知识点和题库数据不在本次章节结构修复中改写。

### 3. 教材封面

教材封面位于：

```text
frontend/llm/public/textbook-covers/
```

封面按照教材展示名称保存为 JPG 文件，前端通过 URL 编码后的教材名称读取，例如：

```text
frontend/llm/public/textbook-covers/中医学基础.jpg
```

对应浏览器资源地址为：

```text
/textbook-covers/{教材名称}.jpg
```

### 4. 前端教材学习代码

```text
frontend/llm/src/components/workshop-textbook/
├─ TextbookLibrary.jsx              教材卡片列表、计划教材优先和展开全部
├─ TextbookChapterLearning.jsx      章节/小节学习页面和单播放器
├─ textbookMetadata.js              教材名称、封面 URL 等元数据
└─ textbookChapterApi.js            章节和小节接口请求
```

主要页面入口和导航逻辑位于：

```text
frontend/llm/src/components/DashboardPage.jsx
frontend/llm/src/appShell.js
```

### 5. 后端接口和配置

章节数据由后端知识图谱接口提供：

| 用途 | 接口 |
| --- | --- |
| 查询教材、章节节点 | `GET /api/knowledge/atlas/nodes` |
| 查询某个小节的知识点和视频 | `GET /api/knowledge/atlas/section/{sectionId}` |
| 查询当前用户学习计划 | `GET /api/v1/learning-path` |
| 查询首页当前任务和计划教材 | `GET /api/v1/dashboard/home` |

后端章节数据根目录由环境变量 `KNOWLEDGE_ATLAS_CHAPTER_ROOT` 指定。未配置时，后端按项目的知识库公共资产目录和回退目录查找同名章节文件。配置示例位于：

```text
backend/competition_app/.env.example
```

后端知识资产和交付逻辑位于：

```text
backend/competition_app/tools/knowledge_assets.py
backend/competition_app/tools/knowledge_delivery.py
backend/competition_app/services/knowledge_recognition_review.py
```

## JSONL 数据结构

### `chapter_nodes.jsonl`

文件每行一个 JSON 对象，使用 `schema_version: "1.0.0"`。节点通过 `node_id` 和 `parent_id` 组成树：

```text
book
└── chapter
      └── section
```

#### 教材节点字段

```json
{
   "schema_version": "1.0.0",
   "node_id": "BOOK_xxx",
   "parent_id": null,
   "node_type": "book",
   "book": "中医学基础_clean",
   "title": "中医学基础",
   "order": 4,
   "chunk_count": 765,
   "source_markdown": "中医学基础.md"
}
```

#### 章节节点字段

章节节点在教材节点基础上包含：

- `parent_id`：所属教材的 `node_id`；
- `node_type: "chapter"`；
- `chapter_order`：教材内章节顺序；
- `title`：章节标题；
- `chunk_count`：章节关联切片数量；
- `first_chunk_uid` / `last_chunk_uid`：章节切片范围；
- `review_status`、`detection_method`、`confidence`：识别和审核信息。

#### 小节节点字段

小节节点在章节节点基础上包含：

- `parent_id`：所属章节的 `node_id`；
- `node_type: "section"`；
- `section_order`：章节内小节顺序；
- `stage1_range_index`：原始目录阶段索引；
- `source_line_no`：来源 Markdown 行号；
- `chunk_count`、`first_chunk_uid`、`last_chunk_uid`：小节切片范围。

### `chunk_chapter_links.jsonl`

文件每行一个 JSON 对象，使用 `schema_version: "2.0.0"`。它是切片到章节树的关系表：

```json
{
   "schema_version": "2.0.0",
   "chunk_uid": "中医学基础_clean:00001",
   "book": "中医学基础_clean",
   "chapter_id": "CH_xxx",
   "chapter_name": "第一章 绪论",
   "chapter_order": 1,
   "section_id": "SEC_xxx",
   "section_name": "第一节 基础概念",
   "section_order": 1,
   "detection_method": "reviewed_markdown_catalog",
   "confidence": 0.97,
   "review_status": "resolved",
   "unresolved_identifier": null
}
```

关键约束：

- 每条原始切片对应一条映射记录；
- `chunk_uid` 必须保持原值，不得用行号或 `chunk_id` 替代；
- `chapter_id` 必须对应 `chapter_nodes.jsonl` 中的章节节点；
- `section_id` 必须对应 `chapter_nodes.jsonl` 中的小节节点；
- 无法自动确认的记录保留稳定的 `UNRESOLVED_*` 标识，不删除切片。

### `publish-report.json`

当前最终发布报告的核心统计为：

| 字段 | 数值 | 含义 |
| --- | ---: | --- |
| `books` | 83 | 教材数量 |
| `chapters` | 1,229 | 最终发布章节数量 |
| `sections` | 6,484 | 最终发布小节数量 |
| `links` | 73,170 | 切片映射数量 |
| `unique_uids` | 73,170 | 唯一 `chunk_uid` 数量 |
| `missing_nodes` | 0 | 缺失节点数量 |
| `approved_candidates` | 689 | 审核通过候选节点数 |
| `promoted_candidate_nodes` | 654 | 晋级到最终结构的候选节点数 |
| `removed_nodes` | 663 | 去重/清理移除的过程节点数 |

因此，最终发布数据保持了 **73,170 / 73,170** 条切片映射，且 `chunk_uid` 唯一。

合并在线验收时发现 `《金匮要略》`的最终节点曾被发布流程漏出，而同批次原始层级中仍保留
26 章、1,658 个已标记为 `resolved` 且置信度为 `0.99` 的小节节点。当前 `final/` 已恢复
这些节点并重新绑定其 1,717 条切片映射；发布完整性检查结果仍为 `missing_nodes: 0`。

## 开发验证

前端目录：

```powershell
Set-Location frontend/llm
npm run test:unit
npm run build
```

学习工坊相关测试：

```powershell
npm run test:unit -- src/components/DashboardTextbookPlan.test.jsx src/components/workshop-textbook/TextbookLibrary.test.jsx src/components/workshop-textbook/TextbookChapterLearning.test.jsx
```

后端识别报告定向测试需要从 `backend` 目录运行：

```powershell
Set-Location backend
python -m pytest competition_app/tests/services/test_knowledge_recognition_review.py competition_app/tests/api/test_knowledge_recognition_reports.py -q
```

## 注意事项

- 知识图谱接口需要登录态认证。
- 当前后端没有持久化教材阅读断点，因此“继续学习”依据的是当前学习计划和正式学习任务，不代表精确的上次视频播放位置。
- `final/` 是对外发布的章节数据目录；`reviewed-*`、`audit_data/`、`cjk-audit/`、Excel/报告和处理脚本属于生成与审计过程资料。
- 章节结构处理脚本、审计报告和中间文件不属于项目运行所需内容，不应提交到 Git；本功能分支只提交最终数据、前端资源和必要文档。
