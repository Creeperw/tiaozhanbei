# TreeKG JSONL 知识图谱生成流程

## 总览

```
输入: {book}.jsonl + final_knowledge_points.json
  │
  ├─ Phase 1: TOC重建（正则推断层级）
  ├─ Phase 2: LLM摘要（自底向上并发）
  ├─ Phase 3: 实体/关系抽取
  ├─ Phase 4: 显式KG组装
  └─ Phase 5: 隐式KG扩展 → final_kg.json
```

---

## Phase 1: TOC 层级重建

**目标**: 从扁平的 JSONL 元数据中恢复 4 层树结构。

### 1.1 数据结构

JSONL 每行:
```json
{
  "chunk_uid": "中医学基础_clean:00001",
  "chunk_id": "00001",
  "text": "中医学是中国人民几千年来...",
  "kp_Lv2": "绪论",
  "metadata": {
    "heading_path": "绪论",
    "catalog_path": ["绪论"],
    "chunk_index": 0
  }
}
```

### 1.2 章边界推断

```
kp_Lv2 按 chunk_index 排序 → 遇"第一节"信号 → 切出新章
```

36 个 kp_Lv2 → 11 个章（"第一节"出现 10 次 + 绪论）

### 1.3 章标题自动推断

在每个章的 heading_path 中查找 `第X章` 正则匹配 → 找到就用，找不到就编号 `第N章`

### 1.4 节内子树

每个 kp_Lv2 内部的 heading_path 按 chunk_index 排序后，用正则分类建树:

| 正则 | 类型 | 层级 |
|---|---|---|
| `第X章 / 绪论` | CHAPTER | 跳过（已是章级） |
| `第X节` | SECTION_NUM | L3 |
| `一/二/三 + 空格` | CHINESE_NUM | L3 |
| `（一）/（二）` | PAREN_NUM | L4 |
| `（1）/（2）` | ARABIC_PAREN | L4 |
| `1. / 2.` | POINT_NUM | L4 |
| `［附］` | APPENDIX | L4 |

用虚拟根节点（L2 Section）作为 stack 锚点建树。

### 1.5 正文拼接

每个 TOC 节点的 title 精确匹配 heading_path → 取对应 chunk 的 `text` 字段拼接。

---

## Phase 2: LLM 摘要生成

**目标**: 自底向上为每个 TOC 节点生成中文摘要。

### 2.1 叶子节点

```
chunk texts → 拼接 → 分块(2000字/块,150字重叠) → LLM 摘要 →
多块则合并后再 LLM 聚合
```

Prompt:
```
你是内容提炼专家。
为以下小节正文生成 220 字左右的中文摘要，
必须忠于原文，不添加外部知识。
Input: {content}
```

### 2.2 非叶节点

```
子节点摘要列表 → LLM 聚合 → 上级摘要
```

### 2.3 并发策略

- 自底向上：深度 d → d-1 → ... → 1
- 同层并发（ThreadPoolExecutor, 8 workers）
- 支持断点续跑：已有 summary 且长度 > 10 → 跳过

### 2.4 API 调用

- 协议: Anthropic Messages API
- 端点: `https://ark.cn-beijing.volces.com/api/plan/v1/messages`
- 认证: `x-api-key` header
- 模型: `deepseek-v4-flash`
- 响应解析: `data["content"]` 中 `type=="text"` 的文本
- 后处理: 去除 `<|end|>` 和 `<think>...</think>`

---

## Phase 3: 实体与关系抽取

**输出**: `toc_with_entities_and_relations.json`

### 3.1 实体抽取

从每个小节摘要中提取领域实体:

Prompt 要求输出:
```json
{
  "entities": [
    {
      "name": "实体名",
      "alias": ["别名1"],
      "type": "实体类型",
      "raw_content": "摘要中描述该实体的原文"
    }
  ]
}
```

### 3.2 关系抽取

基于摘要 + 已提取实体列表，判断关系:

| 关系类型 | 含义 |
|---|---|
| prerequisite | 先修/前置 |
| part-of | 组成/包含 |
| applies-to | 应用 |
| example-of | 实例 |
| synonym | 同义 |
| contrasts-with | 对比 |
| related | 相关（兜底） |

---

## Phase 4: 显式知识图谱组装

**输出**: `toc_graph.json`

### 4.1 节点

- TOC 节点: `{name, type:"toc", level:"level1/2/3", description}`
- 实体节点: `{name, type:实体类型, level:"core/noncore", description}`

### 4.2 边

- `toc->toc`: 父子层级关系
- `toc->entity`: TOC 包含某实体
- `entity->entity`: 实体间语义关系

---

## Phase 5: 隐式知识图谱扩展

### 5.1 上下文卷积 (Conv)

增强实体描述，通过邻居实体和关系补全语义。

### 5.2 实体聚合 (Aggr)

将实体分配 `core` / `noncore` 角色:
- core: 核心实体（高频、高关联度）
- noncore: 辅助实体

### 5.3 节点嵌入 (Embedding)

Sentence-BERT 将实体描述转为稠密向量。

### 5.4 实体去重 (Dedup)

基于嵌入向量 + LLM 确认，合并语义相同但名称不同的实体。

### 5.5 边预测 (Pred)

综合语义相似性 + 结构关联性，预测新边。

### 5.6 最终输出

`final_kg.json`:
```json
{
  "nodes": [
    {"name": "绪论", "type": "toc", "level": "toc", "description": "绪论"},
    {"name": "辨证论治", "type": "中医概念", "level": "core", "description": "..."}
  ],
  "edges": [
    {"source": "绪论", "target": "辨证论治", "type": "toc->entity"},
    {"source": "辨证论治", "target": "整体观念", "type": "related"}
  ]
}
```

---

## 运行方式

```bash
cd TreeKG-main/src

# 配置 (summarize.yaml)
# INPUT_FORMAT: jsonl
# JSONL_BOOK_NAME: 中医学基础_clean
# JSONL_DATA_DIR: ..  # 相对于 TreeKG-main，示例数据位于仓库根目录

# API 凭据（PowerShell 示例；不要把密钥写进 config.yaml）
$env:TREEKG_API_KEY = "your-api-key"
# 可选覆盖：TREEKG_API_BASE、TREEKG_MODEL_NAME、TREEKG_API_TIMEOUT

# 一键运行
python ExplicitKG/main.py    # Phase 1-4
python HiddenKG/main.py      # Phase 5

# 网页可视化
python scripts/split_kg.py --input output/{book}/02_hidden_kg/final_kg.json --out data/{book}/
python server.py 8080
# 浏览器打开 http://localhost:8080
```
