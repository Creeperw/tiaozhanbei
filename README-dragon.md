# README-dragon — 「传奇三条龙」分支说明

> 面向合并方 AGENT / 队友的手册。本分支在 `ikram/三条龙` 基础上整理并重命名，包含三条上传/知识链路的完整实现。
> 目标：让接手的人**不重新读代码**也能理解「做了什么、怎么实现、数据在哪、接口有哪些、合并时注意什么」。

---

## 0. 一句话总结

把「上传考纲 / 上传教材 / 知识图谱」三条链路打通并增强：

- **上传考纲**：MinerU 提全文 → DeepSeek 文本结构化 → **向量匹配**教材切片与题库 → 前端可折叠展示匹配证据（强/中/弱分级）。
- **上传教材**：视觉模型(Qwen-VL)识别目录 → markitdown/MinerU 解析正文 → pypdf 页码跳转 → **三级目录兜底** → 可选切片(pipeline_chunks 格式)+向量化+匹配 → **TreeKG 知识图谱**；全程后台任务 + 步骤进度。
- **知识图谱**：TreeKG 流水线（DeepSeek OpenAI 协议适配 + BERT 编码）→ 图谱 API → 前端力导向可视化（拖拽平移/滚轮缩放）。

---

## 1. 主要提交（按时间）

| Commit | 内容 |
|---|---|
| `68e3fa7` | 教材 PDF / 题库上传基础流程 |
| `bf5d472` | 个人考纲上传工作流 |
| `0cdd117` | 考纲上传走 MinerU + 向量匹配 + 前端匹配展示 |
| `ae3d577` | 教材上传支持 markitdown 解析、切片匹配、实时进度 |
| `90a140d` / `883f358` | 合并队友「知识图谱接入流水线」(TreeKG-main) |
| `2fa735d` | TreeKG 接入教材上传：Anthropic→OpenAI 协议、BERT 封装、空输入降级 |
| `b05dc60` | 无目录 PDF 三级兜底（书签→标题探测→平铺） |
| `31f11c5` | 修复书签页码被正文标题误覆盖成目录页 |
| `05acf71` | 上传教材「课程目录」按钮置灰 + 知识图谱入口/可视化 + 图谱 API |
| `d0c183d` | 教材学习工作台左侧导航新增「知识图谱」入口 |
| `44f5a9d` | 图谱力导向布局改 Fruchterman-Reingold（修复 NaN/左上角） |
| `1e6db22` | 图谱拖拽平移 + 滚轮缩放 + 复位 |

---

## 2. 实现细节

### 2.1 上传考纲（`services/user_syllabus.py`）

流程：上传文件 → `import_file()` → 判定后缀：

- **PDF**：优先 **MinerU**（`tools/syllabus_matching.py` 无关；MinerU 在 `_run_mineru`，走 `knowledge_upload_pipeline/parse_question_pdf.py`，`MINERU_TOKEN` 注入）→ 产出 Markdown → **DeepSeek 文本结构化**（`reasoning_effort:"none"`、6 路并发、分块 3000 字、429/5xx 重试、单块容错、**纯代码合并**）→ `_normalize_structure` → **向量匹配**（`tools/syllabus_matching.py`）。
- 其他格式：多模态视觉方案兜底。
- 匹配结果写入 `mappings.jsonl`，`get()` 一并返回 mappings。

关键点：

- **DeepSeek 是纯文本模型**：推理模型会烧光 max_tokens → 所有 LLM 调用必须带 `reasoning_effort:"none"`。
- 合并阶段**不能再用 LLM**（输出超 8K token 截断）→ 改为按章节标题去重的代码合并。
- 向量匹配（`SyllabusVectorMatcher`）：批量 embedding（Qwen3-Embedding-4B）→ FAISS 检索**教材 chunk 索引**（按科目关键词过滤 ~25 本）+**题库索引**（9.3 万题）→ 产出 `textbook_evidence[]`、`question_evidence[]`、`match_grade`（strong/medium/weak/unmatched）。
- **FAISS 中文路径坑**：本机 `faiss.read_index` 打不开含中文路径的 index，必须 `np.frombuffer(文件字节, uint8)` + `faiss.deserialize_index`。

### 2.2 上传教材（`services/textbook_import.py`）

`import_pdf()` 主流程：

1. **目录识别**：多模态 `_locate_toc`（缩略图定位目录页）→ `_extract_toc`（逐字识别章节+印刷页码）。
   - 视觉模型用 **Qwen-VL**（`VISION_API_*` 配置）——DeepSeek 不收图片。
2. **三级目录兜底**（无目录不失败）：
   - `toc_status="extracted"`：视觉目录成功；
   - `toc_status="outline"`：改用 **PDF 内嵌书签**（pypdf `reader.outline`，支持章→节嵌套）；**书签页码权威，不再被正文标题覆盖**；
   - `toc_status="headings"`：正则探测正文每页的「第X章/第X节」；
   - `toc_status="flat"`：全书平铺一章。
3. **正文解析**：有文本层 → **markitdown**（本地）；扫描版 → **MinerU**（云端 OCR）；>200MB 且未确认 → `TEXTBOOK_TOO_LARGE`。
4. **页码映射**：pypdf 逐页文本 + 目录标题匹配（去编号核心标题重试），`toc.json` 每章/节带 `pdf_page` 供前端跳转。
5. **切片+匹配**（`match_local=true` 时）：`tools/textbook_chunking.py` 按目录页码区间切 chunk（**pipeline_chunks 同格式**）→ embedding → `chunks/index.faiss + metadata.jsonl` → 匹配题库+知识点 → `chunk_matches.jsonl`。
6. **知识图谱**（`treekg_root` 配置后）：生成 request.json → subprocess 调 `TreeKG-main/integration/build_graph.py`（用 `TREEKG_PYTHON` 的 venv python）→ 转发 stdout 进度事件 → `treekg/` 产物 → 记入 manifest（`kg_status/kg_graph_id/...`）。
7. **后台任务 + 进度**：POST 返回 `task_id`，GET 轮询状态；步骤：upload→toc→extract→chunk→embed→match→graph→done。

### 2.3 TreeKG 知识图谱（`TreeKG-main/`）

- 入口：`TreeKG-main/integration/build_graph.py --request <json> --output <dir> [--validate-only]`。
- 阶段：`normalize → explicit(LLM 摘要+实体/关系抽取) → hidden(BERT 向量化+去重+边预测) → publish(viewer) → ready`。
- **已适配 DeepSeek**：`api_client.py` + HiddenKG 的 `Conv/Aggr/Dedup/Pred.py` 从「火山方舟 Anthropic 协议」改为「OpenAI 协议」(`Authorization: Bearer`、`/chat/completions`、`reasoning_effort:none`)。
- **补了缺失文件**：`TreeKG-main/src/HiddenKG/model/main.py`（BERT 编码封装 `get_encoder`）；`bert-base-chinese` 需本地下载到 `TreeKG-main/src/HiddenKG/model/bert-base-chinese`（ModelScope，已 gitignore）。
- **修了队友代码 bug**：Aggr.py 断行语法错误、Dedup/Pred 空实体输入崩溃（改优雅降级/写空结果）、统一 EOL。
- `PYTHONUTF8=1` 必须在 subprocess env（emoji 打印在 GBK 控制台会崩）。

### 2.4 前端

- 上传考纲结果页（`UserSyllabusPage.jsx`）：章节折叠、匹配徽章（强/中/弱/未匹配）、题目数、证据展开。
- 上传教材弹窗（`TextbookLibrary.jsx`）：勾选框「是否匹配本地数据库（耗时较长）」、>200MB 确认弹窗、步骤进度列表。
- 教材阅读器（`TextbookPdfReader.jsx`）：上传的书「课程目录」返回按钮置灰。
- 教材学习工作台（`TextbookChapterLearning.jsx`）：左侧导航新增「知识图谱」按钮（笔记本上方）。
- 图谱面板（`KnowledgeGraphPanel.jsx`，新建）：书选择器 + 力导向 SVG（FR 布局、类型着色、悬停详情、拖拽平移、滚轮缩放、复位）。

---

## 3. 数据结构与目录

### 3.1 教材 run 目录（`backend/competition_app/runtime/textbook_uploads/<owner>/<UTB_id>/`）

```
textbook.pdf
manifest.json          # 教材清单（见下）
toc.json               # { chapters:[{id,title,printed_page,pdf_page,page_match,sections}], source_pdf_pages }
markitdown/            # markitdown 全文（文字版）
mineru/                # MinerU 全文（扫描版）
chunks/                # match_local=true 时
  index.faiss
  metadata.jsonl       # pipeline_chunks 格式
chunk_matches.jsonl    # 每 chunk 的 kp/question 匹配
treekg/                # 知识图谱产物
  request.json
  manifest.json        # {status, graph_id, counts, artifacts}
  artifacts/final_kg.json   # { nodes:[{name,type,description,level}], edges:[{source,target,type}] }
  artifacts/nodes.jsonl
  artifacts/edges.jsonl
  viewer/              # 前端可视化按需数据
```

`manifest.json` 关键字段：`toc_status`(extracted/outline/headings/flat)、`content_parser`(markitdown/mineru)、`match_local`、`chunk_count`、`chunk_matched_kp`、`chunk_matched_question`、`kg_status`、`kg_graph_id`、`kg_node_count`、`kg_edge_count`、`kg_manifest_relative_path`。

### 3.2 考纲 run 目录（`runtime/user_syllabi/<owner>/<USY_id>/`）

```
source.pdf
manifest.json          # processing_status: processing/success/failed
structured.json        # {title, subject, exam_type, sections:[{section_id,title,requirements:[...]}]}
requirements.jsonl     # 每条 {requirement_id, section_id, title, mastery_level, details, source_pages, confidence}
mappings.jsonl         # 匹配结果（见下）
extraction_report.json
mineru/ 或 rendered_pages/
```

`mappings.jsonl` 关键字段：`match_status`、`match_grade`(strong/medium/weak/unmatched)、`kp_name`、`confidence`、`channels`、`textbook_evidence[]`(book/heading/kp_lv2/snippet/score)、`question_evidence[]`(question_id/stem/type/source/score)、`question_count`。

### 3.3 pipeline_chunks 格式（与知识库管线一致）

```json
{"type":"json_field","source":"<book>_chunks.jsonl","record_id":0,"field":"text","chunk_id":0,
 "content":"...",
 "original":{"chunk_id":"00001","text":"...","char_count":N,"heading_path":["书","章","节"],
   "metadata":{"book":"...","heading_path":"书 > 章 > 节","chunk_index":0,"total_chunks":N,
     "prev_chunk_id":null,"next_chunk_id":"00002","images":[],"kp_Lv1":"书","kp_Lv2":"节",
     "catalog_path":["章","节"],"marker_id":"...","pdf_pages":[1,2]}}}
```

### 3.4 向量库（`backend/competition/vdb_store/indexes/`）

- `题库/`：题库向量索引（metadata 有 `题目id/题目内容/题型/题目大来源/...`）。
- 其余每目录 = 一本教材的 chunk 索引（`index.faiss` + `metadata.jsonl`）。
- 读取必须用 `deserialize_index(bytes)`（中文路径坑）。

---

## 4. 接口清单（`backend/competition_app/api/app.py`）

### 4.1 教材

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/v1/textbooks/import` | 上传教材，**202 返回 `{task_id}`**。Form: file,title,description,category,new_category,cover,`match_local`,`allow_large` |
| GET | `/api/v1/textbooks/import/{task_id}` | 任务状态 `{status, step, step_label, error, book}` |
| GET | `/api/v1/textbooks/categories` | 分类列表 |
| GET | `/api/v1/textbooks/pdfs/catalog` | 教材目录 |
| GET | `/api/v1/textbooks/pdfs/resolve?book=` | 按书名解析 |
| GET | `/api/v1/textbooks/pdfs/{book_id}` | 教材元数据（含 origin/toc_status/kg_*） |
| GET | `/api/v1/textbooks/pdfs/{book_id}/file` | PDF 文件流 |
| GET | `/api/v1/textbooks/pdfs/{book_id}/cover` | 封面 |
| GET/PUT | `/api/v1/textbooks/pdfs/{book_id}/pages/{page}/annotations` | 批注 |
| GET/PUT | `/api/v1/textbooks/pdfs/{book_id}/reading-state` | 阅读进度 |
| GET | `/api/v1/textbooks/knowledge-graphs` | 列出有图谱的上传教材 `{items:[{book_id,title,graph_id,node_count,edge_count}]}` |
| GET | `/api/v1/textbooks/knowledge-graphs/{book_id}` | 图谱数据 `{nodes,edges,node_count,edge_count,book_title}` |

### 4.2 考纲

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/v1/user-syllabi` | 上传考纲（同步，返回完整 run 数据，含 mappings） |
| GET | `/api/v1/user-syllabi` | 列表 |
| GET | `/api/v1/user-syllabi/{syllabus_id}` | 详情（manifest+structured+mappings） |
| GET | `/api/v1/user-syllabi/{syllabus_id}/requirements` | 要求列表 |
| GET | `/api/v1/user-syllabi/{syllabus_id}/mappings` | 匹配列表 |
| PUT | `/api/v1/user-syllabi/{syllabus_id}/activate` | 设为激活考纲 |
| POST | `/api/v1/user-syllabi/{syllabus_id}/reprocess` | 重新处理 |
| DELETE | `/api/v1/user-syllabi/{syllabus_id}` | 删除 |

> 除标注外均需登录（HttpOnly Cookie，`credentials:'include'`）。

---

## 5. 配置与环境

`.env.local`（**已 gitignore，勿提交**）新增：

```dotenv
# 视觉目录识别（DeepSeek 纯文本，必须另配）
VISION_API_BASE_URL=https://api.siliconflow.cn/v1
VISION_API_MODEL=Qwen/Qwen3-VL-8B-Instruct   # 目录识别质量一般，建议 32B
VISION_API_KEY=<siliconflow key>

# TreeKG 知识图谱
TREEKG_API_KEY=<deepseek key>
TREEKG_API_BASE=https://api.deepseek.com
TREEKG_MODEL_NAME=deepseek-v4-flash
TREEKG_PYTHON=D:/1---TZB/tiaozhanbei/TreeKG-main/.venv/Scripts/python.exe
```

依赖/模型：

- `pip install markitdown`（主后端 python）。
- TreeKG 独立 venv：`TreeKG-main/.venv`，装 `TreeKG-main/integration/requirements.txt`（torch/transformers 等；**当前是 CPU 版 torch**，要 GPU 提速需自行装 CUDA 版）。
- `bert-base-chinese`：从 ModelScope 下载到 `TreeKG-main/src/HiddenKG/model/bert-base-chinese`（约 400MB，已 gitignore）。
- 本仓库已设 `core.autocrlf=false`，请保持，避免行尾问题。

---

## 6. 给合并者的注意事项

1. **新增文件**（合并时别漏）：`tools/textbook_chunking.py`、`tools/syllabus_matching.py`、`TreeKG-main/`（整目录）、`TreeKG-main/src/HiddenKG/model/main.py`、`frontend/.../KnowledgeGraphPanel.jsx`、`frontend/.../knowledgeGraphPanel.css`。
2. **密钥不入库**：`.env.local`、TreeKG venv、bert 模型权重均已 gitignore。
3. **行尾/EOL**：不要用 `Path.read_text()` + `.replace("\n","\r\n")` + `Path.write_text()` 处理本仓库文件（Windows 下会双重转义成 `\r\r\n`，Python 续行会崩）。改文件请保持 LF，或读字节后显式归一化。
4. **DeepSeek 相关**：所有 LLM 调用加 `reasoning_effort:"none"`；DeepSeek 不收图片（目录识别必须走 VISION_API_*）。
5. **FAISS 中文路径**：一律 `deserialize_index(np.frombuffer(bytes, uint8))`。
6. **MinerU 下载**：后端进程必须能访问 `cdn-mineru.openxlab.org.cn`（沙箱/代理环境会失败）。
7. **测试**：
   - 后端：`pytest backend/competition_app/tests/services/test_textbook_import.py`（含无目录平铺兜底用例）。
   - 前端：`npm --prefix frontend/llm run test:unit`；改 PracticePage 工具卡片顺序会连带 `PracticePage.test.jsx`。
8. **已知取舍**：Qwen-VL-8B 目录识别对复杂书精度一般（换 32B 可提升）；TreeKG 图谱质量依赖目录质量；torch 为 CPU 版。

---

## 7. 运行

```powershell
# 后端（工作目录 backend/）
D:\anaconda3\python.exe -m competition_app.cli.app serve --host 127.0.0.1 --port 7860
# 前端构建
npm --prefix frontend/llm run build
```