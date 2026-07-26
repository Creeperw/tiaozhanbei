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

## 数据文件

最终教材结构数据位于：

```text
backend/competition/knowledge_atlas_chapters/2026-07-22/final/
```

主要文件：

- `chapter_nodes.jsonl`：教材、章节和小节层级结构；
- `chunk_chapter_links.jsonl`：文本块与章节的映射关系；
- `publish-report.json`：结构化数据发布报告。

教材封面位于：

```text
frontend/llm/public/textbook-covers/
```

封面按照教材名称保存为 JPG 文件，例如：

```text
frontend/llm/public/textbook-covers/中医学基础.jpg
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

## 注意事项

- 知识图谱接口需要登录态认证。
- 当前后端没有持久化教材阅读断点，因此“继续学习”依据的是当前学习计划和正式学习任务，不代表精确的上次视频播放位置。
- 章节结构处理脚本、审计报告和中间文件不属于项目运行所需内容，不应提交到 Git。
