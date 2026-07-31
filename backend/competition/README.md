# 外部交付与运行资产

本目录只提交可审查的后端交接代码 `backend-handoff-20260720/`。

以下大体积资产不进入 Git，请从团队共享盘获取，并优先通过绝对路径环境变量挂载：

| 资产 | 环境变量 |
|---|---|
| FAISS 题库/教材索引 `vdb_store` | `QUESTION_VECTOR_STORE_ROOT`、`KNOWLEDGE_VECTOR_STORE_ROOT` |
| 视频知识库交付包 | `KNOWLEDGE_HANDOFF_ROOT` |
| 知识库可写 runtime | `KNOWLEDGE_RUNTIME_ROOT` |
| 章节映射覆盖（可选） | `KNOWLEDGE_ATLAS_CHAPTER_ROOT` |

启用交接业务接口时设置：

```bash
export BACKEND_HANDOFF_ENABLED=true
export BACKEND_HANDOFF_ROOT="$PWD/competition/backend-handoff-20260720"
```

本地只做主框架和前端接口联调时保持 `BACKEND_HANDOFF_ENABLED=false`。

知识星球章节映射已版本化放在 `knowledge_atlas_chapters/2026-07-22/`。默认直接读取该目录；
只有替换映射版本时才需要配置 `KNOWLEDGE_ATLAS_CHAPTER_ROOT`。

---

# 教材 PDF、题库上传与 PDF 阅读交付说明

本分支只交付三个功能：

1. **教学资源 → 上传教材 PDF**：目录只由配置的多模态大模型识别，正文全部交给 MinerU。
2. **训练工坊 → 上传题库**：上传资料、解析预览、逐题修改/确认，写入当前用户的个人题库。
3. **教学资源 → PDF 阅读**：内置教材和用户上传教材均可打开 PDF，支持目录跳转、阅读状态、批注、收藏和学习笔记。

助教页面、知识星球视频、其他页面美化及其他未提交工作区改动不属于本次交付，不要一起合并。

## 1. 本地启动

在仓库根目录：

```powershell
python -m pip install -r backend/competition_app/requirements.txt
cd frontend/llm
npm install
npm run build
cd ../..
$env:COMPETITION_APP_MODE = "stub"       # 仅页面联调
$env:BACKEND_HANDOFF_ENABLED = "true"    # 题库上传需要
python -m competition_app.cli.app serve --host 127.0.0.1 --port 7860
```

也可用：

```powershell
$env:BACKEND_PYTHON = (Get-Command python).Source
powershell -ExecutionPolicy Bypass -File backend/competition/backend-handoff-20260720/run.ps1 start
```

地址：`http://127.0.0.1:7860/`；健康检查：`/health`；OpenAPI：`/docs`。

要实际导入教材，不能只用 stub，必须把 `COMPETITION_APP_MODE` 改为 `live` 并配置真实模型和 MinerU 密钥。

## 2. 环境和数据

复制 `backend/competition_app/.env.example` 为 `.env.local`。教材导入至少需要：

```dotenv
COMPETITION_APP_MODE=live
API_HOST=127.0.0.1
API_PORT=7860
RUNTIME_ROOT=backend/competition_app/runtime
FRONTEND_DIST_ROOT=frontend/llm/dist

# 目录识别：必须是支持图片输入的多模态模型
CHAT_BASE_URL=https://your-openai-compatible-endpoint/v1
CHAT_MODEL=kimi-k2.6
# 也可填写 CHAT_MODELS；它优先于 CHAT_MODEL
CHAT_MODELS=
DASHSCOPE_API_KEY=
SILICONFLOW_API_KEY=

MINERU_TOKEN=
KNOWLEDGE_HANDOFF_ROOT=competition/知识星球视频知识库_前端交接包_2026-07-18
BACKEND_HANDOFF_ENABLED=true
BACKEND_HANDOFF_ROOT=competition/backend-handoff-20260720
TEXTBOOK_PDF_ROOT=competition/textbook_pdfs
TEXTBOOK_PDF_CATALOG_PATH=backend/competition_app/data/textbook_pdfs/catalog.v1.json
QUESTION_VECTOR_STORE_ROOT=competition/vdb_store
KNOWLEDGE_VECTOR_STORE_ROOT=competition/vdb_store
```

- SiliconFlow 地址使用 `SILICONFLOW_API_KEY`；DashScope/阿里兼容地址使用 `DASHSCOPE_API_KEY`，不要混用。
- MinerU 管线必须存在：`backend/competition/知识星球视频知识库_前端交接包_2026-07-18/知识库管理组件/knowledge_upload_pipeline`。
- 题库结构化兜底模型读取交接包配置：`backend/competition/backend-handoff-20260720/.env.example` 中的 `LLM_MODE`、`LLM_API_KEY`、`LLM_API_BASE_URL`、`LLM_API_MODEL`。
- 密钥、数据库、FAISS/向量索引、PDF、用户上传文件都不提交 Git。

## 3. 教学资源：上传教材 PDF

入口：**教学资源 → 上传**。字段：`file`（PDF，最大 512 MB）、`title`、`description`、`category`、`new_category`、`cover`。不上传封面时取 PDF 首页；不填写介绍时根据目录生成一行摘要。

接口：

```text
GET  /api/v1/textbooks/categories
POST /api/v1/textbooks/import
GET  /api/v1/textbooks/pdfs/catalog
GET  /api/v1/textbooks/pdfs/{book_id}
GET  /api/v1/textbooks/pdfs/{book_id}/file
GET  /api/v1/textbooks/pdfs/{book_id}/cover
```

固定处理顺序：

```text
候选目录页 → 多模态模型识别目录/层级 → toc.json
整本 PDF → MinerU → Markdown/页面产物
目录印刷页码 → 映射为真实 PDF 页码 → 阅读器跳转
```

目录提取失败返回 HTTP 422 和稳定错误码：`TEXTBOOK_TOC_EXTRACTION_FAILED`。不能用 MinerU 正文冒充目录，不能由人工补目录；正文不交给多模态模型。超过 180 页的 PDF 自动拆分后交给 MinerU，再合并结果。

用户数据目录：

```text
backend/competition_app/runtime/textbook_uploads/<owner>/<book_id>/
├── textbook.pdf
├── cover.*
├── manifest.json
├── toc.json
└── mineru/
```

按登录用户隔离，其他用户不可读取。该目录是运行数据，不提交 Git。

## 4. 训练工坊：上传题库

入口：**训练工坊 → 上传题库**。支持 `.pdf`、`.png`、`.jpg`、`.jpeg`、`.webp`、`.bmp`、`.tif`、`.tiff`、`.md`、`.txt`；单文件最大 10 MiB。

流程：

```text
上传 → PDF/图片由 MinerU 解析
     → Markdown/TXT 直接解析
     → 结构化题目解析/LLM 兜底
     → 预览 → 逐题修改确认或全部确认
     → 激活到当前用户个人题库
```

接口（交接包路由已挂载到 7860）：

```text
POST /api/question-workspace/imports
GET  /api/question-workspace/imports
GET  /api/question-workspace/imports/{job_id}
GET  /api/question-workspace/imports/{job_id}/items
PATCH /api/question-workspace/items/{question_id}
POST /api/question-workspace/items/{question_id}/confirm
POST /api/question-workspace/items/{question_id}/reject
POST /api/question-workspace/imports/{job_id}/confirm
POST /api/question-workspace/questions/{question_id}/deactivate
POST /api/question-workspace/index/rebuild
GET  /api/question-workspace/questions
```

文件和上传记录位于：`backend/competition/backend-handoff-20260720/APP/backend/user_questions/uploads/<user_id>/`。确认时只写当前用户范围；个人向量索引同步失败时返回 `rebuild_required`，不能阻断题目确认。不得删除 `owner_user_id`，不得写入公共正式题库或公共向量索引。

## 5. PDF 阅读和类别字段

内置教材索引：`backend/competition_app/data/textbook_pdfs/catalog.v1.json`；内置 PDF 大文件目录：`backend/competition/textbook_pdfs/`。PDF 不提交 Git，队友需从共享盘补齐，并保证 `TEXTBOOK_PDF_ROOT` 与索引中的 `relative_path` 一致。

阅读器支持翻页、页码跳转、缩放、章/节目录跳转、页面批注、阅读断点、收藏和按 PDF 页码关联的学习笔记。旧的“教材 → 章节 → 小节 → 视频”链路必须保留，PDF 阅读是新增入口。

阅读接口：

```text
GET/PUT /api/v1/textbooks/pdfs/{book_id}/pages/{page}/annotations
GET/PUT /api/v1/textbooks/pdfs/{book_id}/reading-state
```

每本书必须有 `category`。内置索引根节点默认值为：

```json
{"default_category":"中医药"}
```

书籍对象至少包含：

```json
{"book_id":"TBPDF_...","title":"教材名","category":"中医药"}
```

用户上传的 `manifest.json` 至少包含：

```json
{
  "book_id":"TBUP_...",
  "title":"用户填写或自动识别的书名",
  "description":"一行摘要",
  "category":"中医药",
  "origin":"user_upload"
}
```

类别当前不要求在教材卡片展示，但必须保存在后端。队友已有书籍筛选前端时，只把返回对象的 `category` 接入筛选，不要重写筛选逻辑，也不要用 `stage_title` 替代它。

## 6. 合并给 Agent 的文件边界

### A. 教材上传 + PDF 阅读

后端文件：

```text
backend/competition_app/api/app.py
backend/competition_app/application/container.py
backend/competition_app/services/textbook_import.py
backend/competition_app/services/textbook_pdf.py
backend/competition_app/data/textbook_pdfs/catalog.v1.json
backend/competition_app/requirements.txt
```

前端文件：

```text
frontend/llm/src/components/DashboardPage.jsx
frontend/llm/src/components/workshop-textbook/TextbookLibrary.jsx
frontend/llm/src/components/workshop-textbook/TextbookChapterLearning.jsx
frontend/llm/src/components/workshop-textbook/TextbookPdfReader.jsx
frontend/llm/src/components/workshop-textbook/textbookPdfApi.js
frontend/llm/src/components/workshop-textbook/textbookLibrary.css
frontend/llm/src/components/workshop-textbook/textbookPdfReader.css
frontend/llm/src/index.css
```

测试：`backend/competition_app/tests/services/test_textbook_import.py`、`test_textbook_pdf.py`、`test_textbook_route_repository.py`，以及对应的教材前端测试（若目标分支已有）。

### B. 题库上传

```text
backend/competition/backend-handoff-20260720/APP/backend/contracts/question.py
backend/competition/backend-handoff-20260720/APP/backend/mineru_pdf_service.py
backend/competition/backend-handoff-20260720/APP/backend/question_workspace_service.py
backend/competition/backend-handoff-20260720/APP/backend/routers/question_workspace_routes.py
backend/competition/backend-handoff-20260720/APP/backend/tests/test_mineru_pdf_service.py
backend/competition/backend-handoff-20260720/APP/backend/tests/test_question_workspace_routes.py
frontend/llm/src/components/PracticePage.jsx
frontend/llm/src/components/QuestionWorkspacePage.jsx
frontend/llm/src/components/QuestionWorkspacePage.test.jsx
frontend/llm/src/index.css
```

`backend/competition/backend-handoff-20260720/APP/backend/main.py` 应注册 `question_workspace_routes.router`；缺失时只补这一行。题库前端与 `index.css` 是共享文件，冲突时保留队友页面结构，只合并上传类型、解析预览、导入记录和确认逻辑。

### C. 冲突和排除规则

- `api/app.py`、`container.py`、`requirements.txt`、`DashboardPage.jsx`、`QuestionWorkspacePage.jsx`、`index.css` 是共享文件，禁止整文件覆盖；按上面语义逐段合并。
- 新增的 `textbook_import.py` 及题库服务/路由文件直接保留。
- 保留旧教材视频学习链路，不要把 PDF 阅读改成替换旧页面。
- 不合并助教工作区、美观优化、Bilibili/知识星球、数据库、`.env.local`、密钥、向量索引、PDF 和 runtime。

## 7. 最小验证

```powershell
python -m compileall backend/competition_app
pytest backend/competition_app/tests/services/test_textbook_import.py backend/competition_app/tests/services/test_textbook_pdf.py
pytest backend/competition/backend-handoff-20260720/APP/backend/tests/test_mineru_pdf_service.py backend/competition/backend-handoff-20260720/APP/backend/tests/test_question_workspace_routes.py
cd frontend/llm
npm run test:unit
npm run build
```

验收点：教材目录缺失返回 `TEXTBOOK_TOC_EXTRACTION_FAILED`；书库能看到当前用户上传教材；目录章/节能跳到真实 PDF 页码；题库有上传记录、预览、确认和个人隔离；旧视频链路仍可用；所有书籍对象保留 `category`。
