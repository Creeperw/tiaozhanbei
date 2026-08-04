# 题目难度模块实施完成报告（Phase 1-6）

> 报告日期：2026-08-04
> 分支：`creeper/后端v1`（已推送 `origin/creeper/后端v1`）
> 提交：`4d42853`、`6f79a76`、`b5af3a1`（44 files，+2130/-99）
> 关联文档：[`题目难度相关功能现状报告.md`](题目难度相关功能现状报告.md)（2026-08-01 未启用状态的历史记录）

## 一、需求与实现对照

用户提出的 6 项核心需求全部落地：

| # | 需求 | 实现位置 |
|---|---|---|
| 1 | 仅使用真实难度标注（不推断不默认） | `contracts/difficulty.py`（`parse_difficulty` 永不推断）；后端三层过滤；前端文案"仅使用真实难度标注" |
| 2 | 题目检索支持指定难度（单级+范围，严格匹配） | `training_routes.py`、`knowledge_delivery.py`、`question_retrieval.py`、`competition_app/api/app.py` practice 接口 |
| 3 | 组卷分级降级（指定难度→未标注→网络→生成） | `knowledge_base.py` `_retrieve_questions_by_blueprint` + `paper_assembly.py` `_build_difficulty_source_summary` |
| 4 | 明确告知用户（各来源数量、不伪装补充题） | `PaperDifficultySourceSummary` + 前端"题目来源与难度说明"区 |
| 5 | 个性化 difficulty_fit 条件启用 | `multiscale_learning_service.py`（难度适配分项）+ `learning_governance_service.py`（权重 .20） |
| 6 | 前端条件启用 | `AtlasPracticePanel`（`difficulty_available` 条件显示）、`PaperGenerationPanel`、`SmartPaperPanel` |

## 二、提交详情

### 1. `4d42853` — Phase 1-6 实施完成（36 files，+1697/-77）

**Phase 1 统一难度契约**
- 新增 `competition_app/contracts/difficulty.py`：`VALID_DIFFICULTY_LEVELS=1-5`、`parse_difficulty`、`parse_difficulty_source`、`DifficultyCoverageSummary`
- `contracts/paper.py`：`BlueprintUnit`/`ExamPaperDraft` 加难度字段、`PaperDifficultySourceSummary`
- `database.py`：`UserQuestionItem`/`QuestionBankItem` 等表加 `difficulty`/`difficulty_source` 列（含旧库 ALTER 支持）

**Phase 2 训练接口 + 检索**
- `training_routes.py`：难度参数（单级/范围）、`_difficulty_matches` 严格过滤、无候选返回 `no_question_matches_difficulty`
- `knowledge_delivery.py`/`question_retrieval.py`：检索透传难度、`_question_difficulty_matches` 过滤

**Phase 3 组卷分级降级**
- `paper_blueprint_compiler.py`/`paper_blueprint.py`：prompt 逐字提取难度（模糊词不换算）、`_explicit_difficulty` 支持阿拉伯/汉字数字
- `knowledge_base.py`：分级检索（exact 优先→未标注补足→扩展），统计 exact/unlabeled/web_reference/unmet
- `paper_assembly.py`：生成来源统计 + notice
- `audit.py`：确定性审核（生成题不得带难度、硬难度单元其他难度正式题阻断、降级披露缺失阻断；已披露降级的难度抱怨不阻断）

**Phase 4 difficulty_fit 个性化**
- `multiscale_learning_service.py`：`MIN_DIFFICULTY_ATTEMPTS=3`、难度作答证据聚合（LearningQuestion + QuestionBankItem 两表）、`difficulty_fit = 1 - |归一化难度 - 学习水平|`
- `learning_governance_service.py`：难度权重 .20、匹配报告加难度分项

**Phase 5 前端**
- `pageDataLoaders.js`：难度参数透传
- `AtlasPracticePanel.jsx`：难度筛选控件（`difficulty_available` 条件启用、无标注时按钮 disabled）
- `PaperGenerationPanel.jsx`：组卷难度选择 + 难度来源提示区

**Phase 6 收尾**
- git diff --check 清理 385 处（CRLF+行尾空白）→ CLEAN
- platform_backend 968 passed（1 flaky 重跑通过）、competition_app 184 passed

### 2. `6f79a76` — 集成缺口修复（7 files，+427/-21）

在线验证暴露的真实缺口：前端主路径打 7860 competition_app，其 practice 接口与智能组卷面板原无难度支持。

- `competition_app/api/app.py`：`/api/v1/workshop/practice/next` 加难度 Query 参数（ge=1 le=5 + 422 互斥/范围校验），响应加 `difficulty_available`/`available_difficulties` 覆盖报告；resume 分支在难度请求时跳过（修复"难度请求返回无标注题"缺陷）
- `integrations/backend_handoff.py`：`issue_formal_practice` 提取真实难度写入 bank/core/version 表；`_real_difficulty_label` 用 importlib 加载 parse_difficulty，永不推断
- `tools/stub_question_retrieval.py` + `agents/knowledge_base.py`：修复 30 个 Phase 3 遗留回归（`StubQuestionRetriever.search() got an unexpected keyword argument 'difficulty'`）——stub 签名加难度参数 + registry.invoke 条件透传
- `SmartPaperPanel.jsx`：新增"4. 难度要求"表单（此前主组卷面板无难度控件）+ 预览行 + difficulty 透传
- 测试：`test_practice_api.py` 8 passed（`LabeledDifficultyQuestionStore` + 3 个新测试）、前端 SmartPaperPanel 3 tests

### 3. `b5af3a1` — 测试竞态修复（1 file，+6/-1）

- `QualificationRoutePage.test.jsx`：`does not reuse another qualification target personalized plan` 是异步时序竞态（路径区同步翻转 empty，hero 进度行在后续 useEffect 才清除），断言改 `await waitFor` 等待旧标题清除，组件零改动

## 三、验证结果

| 验证项 | 结果 |
|---|---|
| git diff --check | CLEAN（385 处清理） |
| platform_backend 全套 | 968 passed（1 flaky 重跑通过） |
| competition_app 全套 | 1084 passed |
| 前端全套 | 85 files / 561 tests 全绿 |
| practice API 在线验证 | 覆盖报告 / 严格匹配 / 422 校验 / resume 缺陷修复 |
| 浏览器 UI | 无标注隐藏控件 / 组卷难度 section / 预览行联动 |

### API 在线验证要点（题库 93111 题难度全 None）
- 无难度参数：正常出题，`difficulty_available: false`
- `difficulty=3`：返回 `unavailable_reason: no_question_matches_difficulty`（严格匹配，未标注题永不命中）
- `difficulty_min=2&difficulty_max=4`：匹配到 `difficulty: 2.0` 的真实标注题
- `difficulty=2&difficulty_min=1`（互斥）与 `difficulty_min=4&difficulty_max=2`（倒置）：均 422

## 四、关键设计决策

1. **永不推断**：raw 为 None 或解析失败均返回 `(None, None)`，未标注题永不满足任何难度过滤
2. **权威匹配优先**：个性化路径 `currentPlanMatchesTarget` 用 `textbook_route_id` 权威匹配，单侧缺失才退化到名字宽松兜底
3. **降级披露**：组卷降级时如实标注各来源数量，不把补充题伪装成指定难度
4. **两表合并证据**：difficulty_fit 作答证据需同时查 `LearningQuestion` 与 `QuestionBankItem`（attempt 的 question_id 可能对应任一表）

## 五、遗留问题（非本次引入）

1. **competition_app 5 个环境性失败**：缺数据文件（`test_simulated_patient_routes` 4 个版本化病患数据 + `test_default_route_repository` 缺 `tcm-credential-default-routes.json`，全仓无此文件，非 git 跟踪）
2. **题库难度标注现状**：93111 题难度全 None——线上按设计隐藏控件，等题库打标注后自动生效
