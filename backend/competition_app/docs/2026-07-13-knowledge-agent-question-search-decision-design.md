# 知识库 Agent 题目检索决策设计

## 1. 目标

将已接入的只读题目混合检索能力纳入知识库管理 Agent 的实际工作流。知识库 Agent 不在每次运行时默认检索题库，而是通过规则和模型双判断决定是否需要题目候选。

该功能复用现有正式知识资产与题库的 Bridge、BM25 和 FAISS 混合检索，不修改题库、知识点或 Bridge 主数据。

## 2. 职责边界

- Planner 只提供任务类型和工作流步骤，不决定具体工具调用。
- 知识库 Agent 自主管理本职责范围内的教材证据和题目候选检索。
- 专家 Agent 自主决定当前对话是否消费已检索到的题目候选，以及将候选用于练习题、变式题、批改辅助或不使用。
- Tool Runtime 负责校验知识库 Agent 的工具权限、记录调用输入输出摘要、耗时和失败。
- Orchestrator 负责保留工具调用记录与最终 Agent 产物，不解释知识业务结论。
- 题目检索仍返回完整内部题目对象；面向学习者的输出必须经过 `to_learner_view()` 转换。

## 3. 双判断决策

决策由规则判断和模型判断组成。

### 3.1 规则判断

规则层输出 `rule_question_search_needed` 和 `rule_reasons`。

规则为 `true` 的条件：

- `task_type` 是 `paper_generation`、`grading_and_remediation` 或 `variant_question_generation`。
- 用户请求命中题库意图词：`出题`、`题目`、`练习题`、`相似题`、`批改`、`错题`、`试卷`。
- 已授权上游产物显式要求题库候选。

当前 `personalized_review_card` 不因任务类型自动触发题目检索。

### 3.2 模型判断

知识库 Agent 模型输出扩展为严格 JSON：

```json
{
  "quality_labels": ["教材证据已覆盖"],
  "uncertainty": [],
  "question_search_needed": false,
  "question_search_reason": "当前任务仅需教材证据生成复习卡。"
}
```

模型只能提出是否需要检索和简短原因。它不能输出题目 ID、知识点 ID、检索参数、工具名称、调用权限、答案、解析或题目内容。

### 3.3 合并策略

采用保守并集：

```text
final_question_search_needed = rule_question_search_needed OR model_question_search_needed
```

仅当规则和模型均为 `false` 时，跳过题目检索。模型不能覆盖规则结论，也不能阻止规则已要求的检索。

## 4. 工具调用和产物

知识库 Agent 总是先调用教材证据检索，构建正式 `EvidencePack`。

当最终决策为 `true` 时，知识库 Agent 在已解析 `kp_id` 范围内调用 `search_question_candidates(query, kp_ids, limit)`。题目检索调用参数由系统从当前请求和 EvidencePack 注入，模型不生成参数。

知识库 Agent 的系统产物扩展为：

```text
EvidencePack
+ QuestionSearchDecision
+ 可选 QuestionSearchResult 引用
```

完整题目候选不自动进入专家模型上下文或学习者输出；它们通过产物引用留给后续批改、组卷和变式题工作流按需消费。

当专家 Agent 被授权访问题目候选时，它先输出题目消费决策：`use_question_candidates`、`usage_reason` 和可选的候选引用。该决策只决定当前对话是否使用候选，不触发新的题库检索，也不改变知识库 Agent 的工具权限。

专家 Agent 可以读取完整内部题目对象以判断适用性；若最终资源面向学习者展示题目，系统必须将选定题目转换为 `LearnerQuestionView`，剥离标准答案和解析。答案与解析仅作为批改、审核或后续受控反馈的内部数据。

## 5. Trace

每次知识库 Agent 运行记录：

- 规则布尔值和规则原因。
- 模型布尔值和模型理由。
- 最终布尔值和合并策略 `conservative_union`。
- 是否调用题目检索工具。
- 工具名、已解析知识点数量、结果数量、耗时、通道摘要和失败类别。
- 专家 Agent 的题目消费布尔值、使用原因、候选引用和最终资源类型。

Trace 不保存完整标准答案、解析、题干或模型密钥。终端 `full` 追踪只显示经过现有脱敏器处理的输入、原始模型输出和系统产物摘要。

## 6. 错误处理

- 模型输出不符合扩展 schema 时，知识库 Agent 失败，不以未经验证的模型文本决定工具调用。
- 规则已触发或模型建议触发且题目混合检索不可用时，Agent 失败并保留工具错误 Trace；本阶段不降级为纯教材检索。
- 规则与模型均不触发时，不初始化或读取 FAISS 题目索引。
- 题目检索返回空结果不视为工具故障；结果数量为零仍进入 Trace。

## 7. 测试

- 纯复习卡请求：规则和 Stub 模型均为 `false`，不调用题目检索。
- 用户请求包含“出三道练习题”：规则触发，调用题目检索。
- 模型判断为 `true` 而规则为 `false`：因并集策略调用题目检索。
- 规则为 `true` 而模型为 `false`：仍调用题目检索。
- 已检索候选但专家判断当前对话不需要：不将题目写入资源正文或学习者输出。
- 专家判断需要候选：只允许通过题目引用选择候选；面向学习者的资源使用 `LearnerQuestionView`，不包含答案和解析。
- 模型输出题目 ID、工具名或其他禁止字段：协议校验失败。
- 题目工具失败：执行失败并记录工具调用 Trace。
- Trace 仅保留结果数量、通道摘要和安全元数据，不包含答案与解析。
