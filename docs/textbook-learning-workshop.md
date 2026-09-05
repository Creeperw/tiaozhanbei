# 学习工坊教材学习说明

本说明对应 `ikram/新的不能再新的学习工坊丨注册页面` 分支中的最新注册流程、学习工坊教材学习功能、教材结构数据和教材封面资源。

## 变更基线

- 本分支以 GitHub `origin/main` 的最新提交为基线，不能从旧的学习工坊分支直接部署。
- 注册、登录和学习工坊都使用同一个主后端 `backend/competition_app`，开发环境端口为 `7860`。
- 前端正式工程只有 `frontend/llm`；Vite 开发服务器默认端口为 `5173`，并将 `/api` 请求代理到 `7860`。
- 认证请求使用 HttpOnly Cookie，前端请求必须携带 `credentials: 'include'`；不要再启动独立交接服务作为前端代理目标。

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

## 注册与登录数据

注册页面由 `frontend/llm/src/components/AuthPage.jsx`、
`RegistrationJourney.jsx`、`RegistrationJourneyFrame.jsx` 和
`OnboardingSurveyPanel.jsx` 组成。注册后会先创建用户，再保存首次画像问卷；问卷数据用于首页、学习计划、学习阶段和推荐教材。

主后端认证接口如下：

| 用途 | 接口 | 说明 |
| --- | --- | --- |
| 注册 | `POST /api/v1/auth/register` | 创建账号并返回用户信息 |
| 登录 | `POST /api/v1/auth/login` | 建立 HttpOnly 会话 Cookie |
| 当前用户 | `GET /api/v1/auth/me` | 恢复刷新后的登录态 |
| 登出 | `POST /api/v1/auth/logout` | 清理会话 Cookie |

注册画像的主要字段包括目标考试/课程、长期目标、短期目标、学习基础、专业或角色、学历、已学课程、每日可用时间、资源偏好和教材路线。数据库迁移位于 `backend/competition_app/migrations/`；用户资料、学习计划、学习活动、收藏和笔记属于运行时数据库数据，不应提交到 Git。

## 教材列表规则

- 默认优先展示当前用户学习计划中的教材，并保持学习计划原有顺序。
- 滚动到列表底部后，点击“展开所有教材”，会将其余教材追加到列表末尾。
- 展开前已经显示的计划教材不会重新排序或移动。
- 当前教材全集来自 `textbook_14_5` 知识图谱路线，共 83 本教材。

## 章节学习

进入教材后：

1. 点击章节，查看该章节的小节。
2. 点击小节，进入知识点和视频学习页面。
   - 章节和小节优先按照标题中的“第 X 章 / 第 X 节”数字排序；
   - 原始 `chapter_order` / `section_order` 仅在标题没有序号时作为回退。
   - 如果原始章节内出现“第三节、第四节、第一节、第二节”这类跨章错挂，且全书按“第一节”切分出的组数与章节数严格一致，后端会按切片顺序重新挂接父章节；当前修正 122 个错挂小节。
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
assets/knowledge-atlas/chapters/releases/2026-07-22/final/
```

主要文件：

- `chapter_nodes.jsonl`：最终发布的教材、章节和小节节点；
- `chunk_chapter_links.jsonl`：每个 `chunk_uid` 到章节和小节的映射；
- `publish-report.json`：最终发布数量和完整性报告。
- `section_video_matches.jsonl`：视频分 P 到教材小节完整视频的正式映射。

同一版本目录下还可保留生成、审核和修复过程资料，例如 `reviewed-v2/`、`reviewed-v3/`、`audit_data/` 和若干处理脚本。运行时章节服务优先选择配置的章节根目录；未配置时从统一资产目录选择 `reviewed-v3/`，再回退到 `final/`。旧 `backend/competition/knowledge_atlas_chapters/2026-07-22` 只作迁移期兼容来源。部署时不能只复制前端代码而遗漏章节文件。

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

教材目录接口的层级约定如下：

| `level` | 节点 | 必填上下文 |
| --- | --- | --- |
| `1` | 教材 | `route` |
| `2` | 章节 | `route`、`lv1` |
| `3` | 小节 | `route`、`lv1`、`chapter` 或 `chapterId` |
| `4` | 知识点 | `route`、`lv1`、`chapter`、`chapterId`、`lv2` 或 `sectionId` |

当前章节学习页会在搜索时读取 level 3 和 level 4，并对单个章节或小节请求失败进行容错；搜索可以匹配章节、小节和知识点名称。小节详情仍通过 `/knowledge/atlas/section/{sectionId}` 读取正文、视频和相关学习数据。

后端章节数据根目录由环境变量 `KNOWLEDGE_ATLAS_CHAPTER_ROOT` 指定。未配置时，后端按项目的知识库公共资产目录和回退目录查找同名章节文件。配置示例位于：

```text
backend/competition_app/.env.example
```

`KNOWLEDGE_ATLAS_CHAPTER_ROOT` 可以指向直接包含发布文件的 `final/`，也可以指向以 `final/` 为子目录的上一级发布目录。后端会自动选择正式发布目录。部署后的目标目录至少应包含：

```text
chapter_nodes.jsonl
chunk_chapter_links.jsonl
section_video_matches.jsonl
publish-report.json
```

小节完整视频映射优先读取当前视频发布目录：

```text
{KNOWLEDGE_ATLAS_VIDEO_ROOT}/{active-release}/ocr_section_matches/section_video_matches.jsonl
```

运行目录未部署该文件时，后端回退读取章节正式发布目录中的同名文件：

```text
assets/knowledge-atlas/chapters/releases/2026-07-22/final/section_video_matches.jsonl
```

`GET /api/knowledge/atlas/status` 的 `coverage` 字段分别报告教材切片映射、知识点视频和小节完整视频覆盖情况；缺少可选的小节视频映射时同时写入 `warnings`，不再伪装成已有完整视频覆盖。

加载优先级如下：

1. 视频运行目录中的 OCR 映射，适用于独立发布新视频资产；
2. `KNOWLEDGE_ATLAS_CHAPTER_ROOT` 中的正式映射；
3. 统一资产目录中的 `2026-07-22/final/section_video_matches.jsonl`。

因此，合并本分支后不需要在服务器上手工寻找作者本机文件；只要仓库内置正式发布目录存在，小节完整视频就能加载。知识点时间戳视频仍依赖 `KNOWLEDGE_ATLAS_VIDEO_ROOT` 下的 `full_batch_results`。

### 6. 不提交到 Git 的运行时数据

以下目录可能在本机或部署机存在，但不是分支交付内容：

```text
backend/competition_app/data/                 # 用户和业务运行数据
backend/competition/vdb_store/                # 向量索引生成物
backend/platform_backend/.run/  # 本地运行状态
runtime/                                      # PID、日志和临时状态
frontend/llm/node_modules/                    # 前端依赖安装目录
```

知识图谱正式章节数据、封面静态资源、源代码和测试属于交付内容；用户账号、密码哈希、Cookie、模型密钥、上传文件和本地日志不得提交。

## 验收清单

启动后依次验收：

1. 新用户注册成功，刷新页面后仍保持登录态；错误邮箱、重复账号和错误密码能显示明确错误。
2. 登录后首页能加载学习阶段、学习计划和推荐教材。
3. 打开学习工坊，教材列表、计划教材优先、展开全部教材和教材搜索均正常。
4. 打开教材后，章节可以展开/收起，小节可以进入，搜索“一”和“辨证施护”均能返回匹配结果。
5. 进入小节后，知识点、正文、完整视频和时间戳视频能加载；视频切换与返回上一个视频正常。
6. 训练工坊、知识图谱、学习报告和退出登录不受教材页面改动影响。
7. 前端构建、单元测试和后端测试全部通过，且浏览器 Network 中的后端请求全部指向集成服务。

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
| `chapters` | 1,203 | 最终发布章节数量 |
| `sections` | 4,826 | 最终发布小节数量 |
| `links` | 73,170 | 切片映射数量 |
| `unique_uids` | 73,170 | 唯一 `chunk_uid` 数量 |
| `missing_nodes` | 0 | 缺失节点数量 |
| `approved_candidates` | 689 | 审核通过候选节点数 |
| `promoted_candidate_nodes` | 654 | 晋级到最终结构的候选节点数 |
| `removed_nodes` | 663 | 去重/清理移除的过程节点数 |

因此，最终发布数据保持了 **73,170 / 73,170** 条切片映射，且 `chunk_uid` 唯一。

### `section_video_matches.jsonl`

当前正式映射共 5,227 行，来源为 `ocr-section-1-3-5-v7`。在当前章节树和知识点数据上：

- 5,176 行能够解析到具体小节；
- 51 行暂时无法解析；
- 覆盖 1,436 个有完整分 P 视频的小节。

没有完整分 P 视频的小节仍可回退展示知识点时间戳推荐视频，但两类覆盖率必须分别统计。

每行至少使用以下字段：

```json
{
  "pipeline_version": "ocr-section-1-3-5-v7",
  "bvid": "BVxxxxxxxxxx",
  "aid": 123,
  "cid": 456,
  "page": 1,
  "duration": 520,
  "video_title": "视频标题",
  "part_title": "分P标题",
  "kp_lv1": "教材名称",
  "kp_lv2": "小节名称",
  "match_mode": "title_core",
  "match_source": "ocr_1_3_5",
  "matched_text": "匹配文本"
}
```

后端解析规则：

1. 如果记录含 `section_id`，优先直接关联；
2. 否则使用 `kp_lv1 + kp_lv2` 匹配章节树；
3. 章节树标题不一致时，使用现有知识点到切片的映射反查小节；
4. 同名小节仍有多个候选时，结合 `part_title`、`video_title` 和 OCR 文本识别章节；
5. 无法可靠确定的记录计入 `unmatched_rows`，不强行挂接。

小节接口返回：

- `section_videos`：小节完整分 P 视频，最多返回一个；
- `recommended_videos`：没有完整视频时的知识点时间戳推荐；
- `resource_state: "exact"`：存在小节完整视频；
- `resource_state: "recommended"`：仅存在推荐片段；
- `resource_state: "empty"`：没有可播放资源。

### 更新小节视频映射

更新视频资产时执行：

1. 生成或取得新的 `section_video_matches.jsonl`；
2. 将正式文件放入 `assets/knowledge-atlas/chapters/releases/2026-07-22/final/`；
3. 更新 `publish-report.json` 中的小节视频行数、解析数、未匹配数、覆盖小节数和 SHA-256；
4. 调用状态接口确认 `coverage.section_full_videos.mapping_file_available=true`；
5. 运行后端接口测试和学习工坊前端测试。

校验文件：

```powershell
Get-FileHash -Algorithm SHA256 assets/knowledge-atlas/chapters/releases/2026-07-22/final/section_video_matches.jsonl
```

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

学习工坊后端接口测试需要从交接后端目录运行：

```powershell
Set-Location backend/platform_backend
python -m pytest APP/backend/tests/test_knowledge_atlas_service.py APP/backend/tests/test_knowledge_atlas_routes.py APP/backend/tests/test_knowledge_atlas_video_pipeline.py APP/backend/tests/test_knowledge_atlas_asset_import.py -q
```

## 注意事项

- 知识图谱接口需要登录态认证。
- 当前后端没有持久化教材阅读断点，因此“继续学习”依据的是当前学习计划和正式学习任务，不代表精确的上次视频播放位置。
- `final/` 是对外发布的章节数据目录；`reviewed-*`、`audit_data/`、`cjk-audit/`、Excel/报告和处理脚本属于生成与审计过程资料。
- 章节结构处理脚本、审计报告和中间文件不属于项目运行所需内容，不应提交到 Git；本功能分支只提交最终数据、前端资源和必要文档。
