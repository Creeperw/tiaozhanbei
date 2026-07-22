# 本地完整体验迁移 Task Breakdown

> **For Claude:** REQUIRED SUB-SKILL: Use executing-plans skill to execute this task breakdown task-by-task.

**Goal:** 将旧版本的本机密钥与知识资产安全接入当前项目，并让主账户、会话和学习数据在本地 SQLite 中跨后端重启持久化。

**Approach:** 不整体覆盖新项目，也不合并两套不兼容数据库。先以 TDD 补齐主后端已声明但未接通的 SQLite 持久化，再按变量名迁移本机密钥、按绝对路径挂载大型知识资产，最后通过两次后端重启验证账户与主要功能。

**Skills:** @test-driven-development、@systematic-debugging、@requesting-code-review、@run

**Tech Details:** FastAPI、SQLAlchemy、SQLite、现有 SQL repositories、`.env.local`、知识交接包、Vite

---

### Task 1：锁定 SQLite 持久化缺口

**Files:**
- Modify: `backend/competition_app/tests/api/test_auth.py`
- Test: `backend/competition_app/tests/api/test_auth.py`

1. 新增测试：以临时 SQLite 路径构建容器并注册账户。
2. 销毁第一个客户端，使用相同设置重建容器。
3. 断言原账户可再次登录，且会话身份可读取。
4. 运行单测，确认测试因当前容器回退到内存仓库而失败。

### Task 2：补齐主后端 SQLite 初始化

**Files:**
- Modify: `backend/competition_app/db/bootstrap.py`
- Modify: `backend/competition_app/db/migrations.py`
- Modify: `backend/competition_app/application/container.py`
- Test: `backend/competition_app/tests/api/test_auth.py`

1. 让 `DatabaseBootstrap` 按 `DATABASE_URL`、`USE_SQLITE`、MySQL 的顺序选择数据库。
2. SQLite 路径仅创建父目录和本地 engine，不执行 MySQL 建库语句。
3. 为现有 migrations 提供最小 SQLite 方言转换，处理内联索引、`TIMESTAMP(6)`、`CURRENT_TIMESTAMP(6)` 与 `ON UPDATE`。
4. 复用现有 `SqlAuthRepository`、计划、会话和运行状态仓库，不复制业务逻辑。
5. 跑新增测试，确认从 RED 变为 GREEN。
6. 跑认证、配置、计划与数据库仓库相关回归测试。

### Task 3：安全迁移本机配置

**Files:**
- Backup: `backend/competition_app/.env.local`
- Modify: `backend/competition_app/.env.local`

1. 从旧 `.env` 只读取受支持变量；日志只记录变量名和是否非空。
2. 将模型、Embedding、检索、会话签名等兼容变量映射到目标配置。
3. 保留新项目特有变量，不整体覆盖目标文件。
4. 设置 `USE_SQLITE=true` 和目标主数据库路径。
5. 确认 `.env.local`、数据库和运行资产均被 Git 忽略。

### Task 4：接入知识与题库资产

**Files:**
- Modify: `backend/competition_app/.env.local`
- Reuse by path: old knowledge handoff/vector/question assets

1. 验证 `final_knowledge_points.json`、`formatted_questions.json`、`source_chunks.jsonl` 及图片目录结构。
2. 验证 FAISS 索引与题库目录完整性。
3. 优先使用绝对路径挂载，不复制多 GB 资产。
4. 不把旧兼容数据库当作主数据库；兼容业务域继续使用自己的 SQLite。

### Task 5：完整模式与重启验收

**Files:**
- No source changes expected

1. 启动后端并检查 `/health`、`/api/v1/platform/status`。
2. 注册或登录测试账户，访问首页、学习工坊、知识星球和训练入口。
3. 停止并重新启动后端。
4. 使用同一账户再次登录，断言账户、会话和基础学习数据仍存在。
5. 检查 Git 状态，确认密钥、数据库和大型资产没有进入待提交列表。
6. 未经用户明确要求，不执行 Git commit 或 push。
