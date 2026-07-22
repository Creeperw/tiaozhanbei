# 协作与贡献说明

感谢所有参与时珍智训前端、后端、知识资源和部署工作的贡献者。本仓库以 `main` 作为可部署基线，功能开发通过独立分支完成，并保留原作者提交历史后合并。

## 当前协作贡献

| 贡献者 | 已合并工作 |
|---|---|
| Creeperw | 多智能体后端、前后端整合、章节层级、数据持久化、接口与部署文档 |
| sunjingyan | 登录体验、顺序学习路径、知识空间和学习工坊前端交互 |
| 11075 | 团队交接与 PowerShell 启动端口校准 |

GitHub Contributors 页面依据 `main` 可达提交的作者邮箱统计。提交者应使用已绑定到个人 GitHub 账号的邮箱；修改历史提交作者会破坏审计链路，不应为了统计而重写已经共享的提交。

## 已保留的协作分支历史

2026-07-23 的整合基线已将下列历史作为合并父提交接入 `main`：

- `feature/light-login-page`
- `feat/sequential-learning-path`
- `fxz/merge-sequential-learning-path-20260722`
- `feat/chapter-hierarchy-api-settings-20260722`
- `codex/team-handoff-2026-07-20`

这些功能在合并前已经进入当前工作区并完成在线验收，因此合并提交以当前可运行文件树为准，同时保留原分支提交和作者信息。后续不要再次复制或压缩这些历史。

## 开发流程

1. 从最新 `main` 创建单一目标的功能分支。
2. 后端接口变化同步修改 OpenAPI/Pydantic 契约和 `docs/frontend-api-reference.md`。
3. 数据库变化新增编号迁移，不修改已经执行的迁移文件。
4. 不提交 `.env`、密钥、数据库、向量索引、缓存、用户数据或运行快照。
5. 合并前完成与改动对应的单元测试、前端构建和在线流程验收。
6. 使用普通 merge 或 Pull Request 保留作者历史，避免 squash 掉需要计入贡献列表的多人提交。

## 验收命令

```bash
cd backend
conda run -n torch python -m pytest -q competition_app/tests \
  --ignore=competition_app/tests/integration/test_learning_plan_live_flow.py

cd ../frontend/llm
npm run test:unit
npm run lint
npm run build
```

不要从 WSL 命令行运行 Live pytest。Live 流程应在已经启动的前端运行面板点击 Execute，并以浏览器真实接口结果为准。

部署、数据库和接口细节分别见：

- [部署与升级指南](docs/deployment.md)
- [数据库运维指南](docs/database-operations.md)
- [前端接口参考](docs/frontend-api-reference.md)
- [学情监测与资源匹配口径](docs/learning-monitoring-methodology.md)
