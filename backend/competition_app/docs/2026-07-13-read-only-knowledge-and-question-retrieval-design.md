# 只读知识与题库检索组件设计

## 1. 目标

在 `competition_app` 中整合正式交付包中的知识点、教材切片、题库和 Bridge 关系，形成一个只读、可追溯的知识检索组件。该组件为知识库管理 Agent、后续组卷、批改和学习路径工作流提供统一数据访问入口。

本阶段不包含用户文档上传、个人知识库索引、题目新增、题目去重、题目质量写回或题库版本治理。

## 2. 权威数据来源

只读取 `competition/题库与知识点库（原始）` 的正式交付包：

- `01_question_bank/final_questions.json`：题库主表，包含题干、答案、解析、题型、标签和来源。
- `03_pipeline_chunks/source_chunks.jsonl`：教材标准切片。
- `04_knowledge_points/final_knowledge_points.json`：正式知识点。
- `05_bridge/kp_chunk_links.jsonl`：知识点到教材切片的 Bridge。
- `05_bridge/question_kp_all_matches.jsonl`：题目到知识点的最终合并 Bridge。

不读取旧版 `legacy_30w_strict_questions.json`，不将 `99_optional_cache` 作为线上依赖。

## 3. 组件边界

新增 `KnowledgeRepository` 作为统一只读数据访问层。现有 `KnowledgeAssetRepository` 保持兼容，并迁移为知识点与教材证据的内部适配层或被 `KnowledgeRepository` 替代。

`KnowledgeRepository` 负责：

- 主题或 `kp_id` 对齐正式知识点。
- 通过 `kp_chunk_links` 获取可追溯教材证据。
- 通过 `question_kp_all_matches` 按主题或 `kp_id` 找到题目候选。
- 返回题目到知识点的 Bridge 层、关系、置信度、排序和证据切片引用。
- 按题目 ID 获取完整题目详情。

该组件不负责：

- 调用 LLM 生成题目、知识点或答案。
- 修改正式题库、知识点或 Bridge。
- 决定个人掌握度、错因、复习优先级或发布状态。
- 输出面向学习者的最终资源正文。

## 4. 完整数据与学习者视图

仓储层始终加载并返回完整题目数据，包括标准答案与解析。答案不在数据层删除，也不由检索权限决定是否加载。

题目对象分为两个明确视图：

- `QuestionDetail`：内部完整对象，包含题干、题型、标签、答案、解析、来源、关联知识点和 Bridge 元数据。供知识库 Agent、专家、批改、审核和后端服务使用。
- `LearnerQuestionView`：在面向学习者的最终展示边界调用 `to_learner_view()` 生成，剥离标准答案与解析。

因此“防泄题”由一个集中、可测试的展示转换函数负责，而非让每个检索调用方自行裁剪字段。

## 5. 数据契约

### 5.1 `QuestionBridge`

```python
class QuestionBridge(ContractModel):
    kp_id: str
    bridge_layer: Literal["strict", "llm", "similarity"]
    relation: str
    confidence: float
    rank: int
    evidence_chunk_uid: str
    match_method: str
```

`strict` 是最高可信层，`llm` 次之，`similarity` 只能作为候选补充。`relation`、`confidence`、`rank` 和 `evidence_chunk_uid` 始终保留，供审核和训练快照追溯。

### 5.2 `QuestionDetail`

```python
class QuestionDetail(ContractModel):
    question_id: str
    question_type: str
    stem: str
    reference_answer: str
    analysis: str | None
    tags: list[str]
    source_metadata: dict[str, object]
    bridges: list[QuestionBridge]
```

### 5.3 `LearnerQuestionView`

```python
class LearnerQuestionView(ContractModel):
    question_id: str
    question_type: str
    stem: str
    tags: list[str]
    kp_ids: list[str]
```

### 5.4 `QuestionSearchResult`

```python
class QuestionSearchResult(ContractModel):
    query: str
    resolved_kp_ids: list[str]
    embedding_model: str
    vector_index_path: str
    items: list[QuestionDetail]
```

每个候选还需保留各检索通道的命中信息和融合得分，以支持训练快照、审核和问题定位。

## 6. 检索与排序

检索输入为自然语言主题、可选显式 `kp_id` 列表和结果数量。

1. 显式 `kp_id` 直接作为已解析知识点。
2. 主题通过正式知识点名和别名进行确定性匹配。
3. 由命中知识点反查题目 Bridge，作为可追溯的结构化召回通道。
4. 对 Bridge 候选集合执行中文双字词 BM25 关键词检索。
5. 使用附件 `7-12用户传题目+传知识点+三个数据包/retrieval/hybrid_question_retrieval.py` 的既有 FAISS 题目向量索引读取方式和 `Qwen/Qwen3-Embedding-4B` 查询向量进行语义召回。
6. 合并同一题目的 Bridge、BM25 和向量命中，保留全部通道和分数。
7. 按 Bridge 可信层、融合得分、primary/candidate 关系、Bridge 置信度、Bridge rank 和题目 ID 稳定排序。

未匹配题目不伪造知识点关系；未来可由题库治理工作流处理。

本阶段将既有题目 FAISS 索引视为混合检索的必要输入。索引缺失、不可读取或向量维度与当前 embedding 模型不一致时，检索明确失败并记录原因；不在本阶段实现 `Bridge + BM25` 自动降级策略。

## 7. 与 Agent 和工具运行时的集成

`KnowledgeRetrievalTool` 扩展为两个只读操作：

- `build_evidence_pack(query)`：为当前知识库 Agent 构建教材 `EvidencePack`。
- `search_question_candidates(query, kp_ids, limit)`：通过 Bridge、BM25 和 FAISS 向量混合召回，返回完整 `QuestionSearchResult`，供业务 Agent 后续使用。

知识库 Agent 在当前复习卡工作流中继续只生成证据质量标签和不确定性。题目检索结果不自动塞入复习卡模型上下文，避免无关题目和标准答案污染当前资源生成链路。

后续批改、组卷和学习路径模板可按 Agent 权限调用 `search_question_candidates()`；最终学习者界面只能消费 `LearnerQuestionView`。

## 8. 错误处理与性能

- 文件缺失或 JSON/JSONL 格式损坏时，仓储初始化抛出明确错误，不返回不完整结果。
- Bridge 引用不存在的题目、知识点或切片时跳过该行并记录可观测计数；不允许无效引用进入返回对象。
- 采用惰性加载和内存索引：题目按 ID，Bridge 分别按题目和知识点，切片按 `chunk_uid`；FAISS 索引按需加载并验证向量维度。
- 复用附件已有 `hybrid_question_retrieval.py` 的中文 BM25 分词、FAISS 读取、向量维度验证和通道融合思路；不引入其个人题库运行时写入功能。
- 首版不向 MySQL 复制 9 万题或 7 万知识点，交付包保持主数据来源。

## 9. 测试

- 从小型 fixture 验证主题/KP 解析、教材证据和题目 Bridge 加载。
- 验证 `strict` 优先于 `llm` 和 `similarity`。
- 验证 Bridge、BM25 和向量三通道命中被合并，并保留来源通道和分数。
- 验证 FAISS 索引缺失、损坏或向量维度不一致时返回明确失败，不静默回退。
- 验证完整题目对象保留答案/解析，`to_learner_view()` 始终移除二者。
- 验证损坏 Bridge 和缺失外键不会产生错误候选。
- 复习卡既有 EvidencePack 检索回归保持通过。

## 10. 实现状态

已实现：题目检索契约、完整题目与学习者视图转换、正式只读仓储、Bridge 外键校验、Bridge/BM25/FAISS 三通道召回、向量维度校验，以及 Live 容器中的显式题目检索入口。

当前复习卡仍只使用教材 `EvidencePack`；题目候选将在批改、组卷和学习路径工作流中按 Agent 权限显式调用，不自动进入复习卡模型上下文。
