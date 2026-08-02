# TreeKG 接入教材上传流水线

本目录是 TreeKG 与教材上传流水线之间的稳定边界。流水线负责人无需调用
`ExplicitKG`、`HiddenKG` 内部脚本，只需在教材完成目录识别和正文切片后调用
`build_graph.py`。

## 1. 接入位置

在 `ikram/三条龙` 分支的流程中，调用位置是：

```text
TextbookImportService.import_pdf()
  → PDF 入库
  → 多模态模型识别目录
  → MarkItDown / MinerU 提取正文
  → chunk_book_by_toc() 生成 chunks
  → 调用 integration/build_graph.py       ← 接在这里
  → 读取 manifest.json
  → 保存 graph_id、状态和 artifact_path
  → 教材导入任务完成
```

不要把原始 PDF 再传给 TreeKG。TreeKG 的输入是已经按教材目录切好的 JSONL。
`textbook_chunking.py` 当前生成的包装格式可以直接使用；入口也兼容 TreeKG 原生
JSONL。上游不需要生成 `final_knowledge_points.json`，实体和关系由 TreeKG 从正文
摘要中抽取。

## 2. 调用方式

### 2.1 先做无模型验证

从 `TreeKG-main` 目录运行：

```powershell
python integration/build_graph.py `
  --request integration/examples/request.json `
  --output integration/runtime/KG_JOB_DEMO_001 `
  --validate-only
```

该命令只校验契约并规范化切片，不调用大模型，适合部署探针和 CI。

### 2.2 正式构建

```powershell
$env:TREEKG_API_KEY = "由部署平台注入"
python integration/build_graph.py `
  --request runtime\KG_JOB_001\request.json `
  --output runtime\KG_JOB_001
```

正式构建依次执行：

```text
normalize → explicit → hidden → publish → ready
```

命令退出码为 `0` 表示成功，非 `0` 表示失败。stdout 每行都是一个 JSON 进度
事件，流水线 worker 可以直接转发到已有的教材导入任务状态。

## 3. 输入契约

完整 JSON Schema：[`contracts/request.schema.json`](contracts/request.schema.json)。

最小请求：

```json
{
  "schema_version": "1.0.0",
  "job_id": "KG_JOB_001",
  "user_id": "user-123",
  "book": {
    "book_id": "UTB_a1b2c3",
    "title": "中医文化学"
  },
  "source": {
    "chunks_path": "chunks.jsonl",
    "format": "textbook_pipeline_jsonl"
  }
}
```

`chunks_path` 相对路径以请求 JSON 所在目录为基准。每行至少需要正文以及章节
路径，例如流水线现有格式：

```json
{
  "content": "教材正文……",
  "original": {
    "chunk_id": "00001",
    "metadata": {
      "heading_path": "中医文化学 > 第一章 > 第一节",
      "catalog_path": ["第一章", "第一节"]
    }
  }
}
```

入口会补齐稳定的 `chunk_uid`、顺序、教材字段和 TreeKG 所需元数据。

## 4. 输出契约

完整 JSON Schema：[`contracts/result.schema.json`](contracts/result.schema.json)。

每个任务使用独立输出目录：

```text
KG_JOB_001/
├─ manifest.json
├─ input/
│  └─ <book_id>/<book_id>.jsonl
├─ work/
│  └─ <book_id>/01_explicit_kg、02_hidden_kg
├─ artifacts/
│  ├─ final_kg.json
│  ├─ nodes.jsonl
│  └─ edges.jsonl
└─ viewer/
   ├─ meta.json
   ├─ toc_tree.json
   ├─ search_index.json
   ├─ entity_edges.json
   └─ chunks/
```

流水线只需要消费 `manifest.json`。正式成功时关键字段如下：

```json
{
  "schema_version": "1.0.0",
  "job_id": "KG_JOB_001",
  "graph_id": "KG_...",
  "user_id": "user-123",
  "book_id": "UTB_a1b2c3",
  "owner_scope": "user",
  "status": "ready",
  "counts": {"chunks": 765, "nodes": 4737, "edges": 3025},
  "artifacts": {
    "final_kg": "artifacts/final_kg.json",
    "nodes": "artifacts/nodes.jsonl",
    "edges": "artifacts/edges.jsonl",
    "viewer_root": "viewer",
    "viewer_meta": "viewer/meta.json",
    "sha256": {}
  },
  "error": null
}
```

`nodes.jsonl` 和 `edges.jsonl` 会增加稳定的 `node_id`、`edge_id`、`source_id`、
`target_id`，便于数据库关联；大体积节点和边仍建议保存在文件或对象存储中。

## 5. 流水线 worker 示例

以下代码放在教材导入后台任务中，不要放在 HTTP 请求主线程同步等待：

```python
process = await asyncio.create_subprocess_exec(
    sys.executable,
    str(tree_kg_root / "integration" / "build_graph.py"),
    "--request", str(request_path),
    "--output", str(job_root),
    stdout=asyncio.subprocess.PIPE,
    stderr=asyncio.subprocess.STDOUT,
    env={**os.environ, "TREEKG_API_KEY": settings.tree_kg_api_key},
)

async for raw_line in process.stdout:
    event = json.loads(raw_line.decode("utf-8"))
    update_textbook_task(event["job_id"], event["stage"], event["status"], event["message"])

if await process.wait() != 0:
    raise RuntimeError(json.loads((job_root / "manifest.json").read_text())["error"])
```

建议在原教材任务中增加三个对用户可见的阶段：

| TreeKG stage | 用户提示 |
|---|---|
| `explicit` | 正在抽取知识点与显式关系 |
| `hidden` | 正在补全和去重知识关系 |
| `publish` | 正在发布知识图谱 |

## 6. 数据库回写

当前上传教材主要依赖用户目录下的 `manifest.json`，尚未完整登记图谱。流水线负责人
应在自己的事务中至少保存：

### `knowledge_graphs`

- `graph_id`：使用 TreeKG 返回值，唯一。
- `book_id`：关联上传教材。
- `owner_user_id`：必须取当前鉴权用户，不能使用前端任意传值。
- `schema_version`、`status`。
- `artifact_root`、`manifest_path`。
- `node_count`、`edge_count`、`source_sha256`。
- `created_at`、`completed_at`、`error_code`、`error_message`。

### `user_knowledge_graphs`

- `user_id`、`graph_id`、`permission`。
- 唯一键建议为 `(user_id, graph_id)`。

读取接口建议保持在主系统中：

```http
GET /api/v1/textbooks/{book_id}/knowledge-graph
```

后端先校验用户对 `book_id` 的权限，再根据 `knowledge_graphs.manifest_path` 返回状态
和资源地址。不要把本机绝对路径直接返回给浏览器。

## 7. 失败、重试和幂等

- 相同请求文件和同一输出目录已经是 `ready` 时，入口直接返回现有结果。
- 每个任务必须使用独立目录，禁止多个任务共享 TreeKG 默认 `src/output`。
- 失败时 `manifest.json.status=failed`，并包含结构化 `error.code/message`。
- 可以使用同一 `job_id` 和目录重试；已有摘要支持断点续跑。
- 主系统现有内存任务字典会在重启后丢失，生产环境应把任务状态持久化到数据库。

## 8. 部署依赖和环境变量

安装 [`requirements.txt`](requirements.txt) 中的 Python 依赖，并提供：

- `TREEKG_API_KEY`：必需，仅由服务端环境注入。
- `TREEKG_API_BASE`：可选，覆盖大模型端点。
- `TREEKG_MODEL_NAME`：可选，覆盖模型名。
- `TREEKG_API_TIMEOUT`：可选，覆盖请求超时。

隐式图谱阶段还需要部署完整的 `HiddenKG/model/` Python 包（包括 `main.py`）及其中的
`bert-base-chinese` 模型组件。
该目录体积较大且被 Git 忽略，应由镜像、模型卷或部署脚本提供，不要提交模型文件。
默认配置使用 CUDA；无 GPU 的环境需将 `HiddenKG/config/emb.yaml` 的 `DEVICE`
改为 `cpu` 并关闭 AMP。

## 9. 安全边界

- API 密钥不得出现在请求、日志、manifest 或 Git 中。
- `user_id` 必须来自已经通过鉴权的服务端会话。
- 每个用户、教材和任务使用隔离目录。
- 浏览器只能访问后端鉴权后的资源接口，不能直接访问运行目录。
- 删除教材时由主系统负责同步删除数据库关联和对应图谱目录。

## 10. 验证

```powershell
python -m unittest discover -s integration/tests -v
```

测试不调用外部模型，只验证契约、切片适配、任务隔离和错误输出。
