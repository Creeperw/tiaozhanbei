# 时珍智训

时珍智训是我和团队一起开发的中医药学习与备考平台。我们把教材知识、学习规划、练习和复习放在同一个应用里，希望它不只是一个回答问题的聊天窗口，也能帮助学习者安排接下来学什么、练什么。

我主要负责多智能体后端和前后端整合，前端交互、知识资源整理等工作由团队共同完成。参与成员和具体分工见 [贡献说明](CONTRIBUTING.md)。

## 可以用它做什么

- **安排学习计划**：结合专业背景、学习基础、目标考试和每天可用时间，制定长期规划、短期计划及今日任务，在学习路径中查看阶段、教材和知识点。
- **查资料、问问题**：通过智能助教获取知识讲解和学习建议，查看教材引用；也可以浏览教材章节、知识图谱、知识卡片和相关视频。
- **练习与复习**：使用客观题、案例简答、计时试卷和 AI 病患模拟进行训练，把错题、收藏和笔记留在自己的账号下。
- **了解自己的学习情况**：查看作答记录、薄弱知识点和到期复习内容，按需要调整画像与学习安排。

应用提供经典路线和个性化路线两种入口。经典路线可以直接浏览，个性化路线需要先选择目标、补齐学情并生成学习计划；两者不是同一份数据。

这些能力依赖对应的模型服务、数据库和知识资源。只克隆代码或启动开发模式，并不能体验全部功能。项目仍在持续完善，我不会把首页能打开、健康检查通过当作所有学习流程都已验证。

平台内容用于学习参考，不替代教材、教师指导或临床诊疗意见。选择考试路线也不代表符合该考试的报名条件。

## 技术实现

前端使用 React 和 Vite，后端使用 FastAPI，通过 LangGraph 组织多智能体工作流。生产构建后的页面和 API 由同一个后端进程提供，默认端口为 `7860`，不要求使用 Docker。

智能体分为任务规划、记忆管理、学情诊断、知识库管理、专家和审核裁判六类角色，按任务需要参与，而不是每次都依次运行一遍。知识回答优先使用可检索的资料，规划与练习结果经过审核后再保存；复习调度、数据存储等工作由后端服务处理。

登录使用 Cookie 会话。主应用接口位于 `/api/v1`，业务模块还保留一部分 `/api` 接口，两者都由当前应用提供。用户身份由服务端确定，学习记录按账号隔离。

```text
backend/
  competition_app/    FastAPI 主应用、多智能体工作流和学习状态
  platform_backend/   题库、训练、教材等业务接口
  competition/        章节映射及兼容入口
frontend/llm/          React 前端
TreeKG-main/          教材建图与知识图谱相关工具
assets/               公共知识资源入口
runtime/              本地运行数据
evaluation/           评测数据与工具
scripts/              启动、资源检查和维护脚本
deploy/               部署配置模板
docs/                 开发与运维文档
```

更详细的模块关系见 [系统架构](docs/architecture.md)。

## 本地开发

建议准备 Python 3.10，以及满足 Vite 7 要求的 Node.js 20.19+ 或 22.12+。Python 环境可以使用 Conda 或虚拟环境，不需要沿用我的本地环境名称。

下面从仓库根目录开始，使用独立虚拟环境安装主应用依赖：

```bash
git clone https://github.com/Creeperw/tiaozhanbei.git
cd tiaozhanbei
python3.10 -m venv .venv
source .venv/bin/activate
python -m pip install -r backend/competition_app/requirements.txt
cp backend/competition_app/.env.example backend/competition_app/.env.local

cd frontend/llm
npm ci
npm run build
cd ../../backend
COMPETITION_APP_MODE=stub BACKEND_HANDOFF_ENABLED=true python -m competition_app.cli.app serve
```

启动后访问：

- 应用：<http://127.0.0.1:7860/>
- 健康检查：<http://127.0.0.1:7860/health>
- 接口文档：<http://127.0.0.1:7860/docs>

这里使用的 `stub` 模式主要用于界面和接口联调，不会验证真实模型效果。业务模块及知识处理组件的额外依赖，请按 [部署指南](docs/deployment.md) 安装。

开发前端时，可以在后端运行的情况下进入 `frontend/llm` 执行 `npm run dev`。Vite 会把 `/api` 和 `/health` 请求代理到本地后端。

## 完整运行与部署

完整运行需要显式设置 `COMPETITION_APP_MODE=live`，并配置聊天模型、Embedding 服务、数据库及知识资源。配置项见 [`backend/competition_app/.env.example`](backend/competition_app/.env.example)，具体步骤见 [部署与升级指南](docs/deployment.md)。

我把源码和大体积资源分开管理。教材、知识库和向量索引不直接放进 Git，需要按 [数据包说明](docs/data-package.md) 获取、校验并设置路径。公共资源包不包含账号数据库、个人学习记录、用户上传内容或密钥，这些数据需要单独备份和迁移。

数据库通常使用同一个 MySQL 实例下的两个库：

- `competition_app`：认证、学习规划、复习和工作流状态。
- `competition_frontend`：题库、训练、教材等业务数据。

初始化、迁移和备份步骤见 [数据库运维指南](docs/database-operations.md)。已有用户数据时，不要把重新初始化数据库当作升级方式。

生产部署使用 `scripts/serve_production.py`，先执行预检，再启动服务。公网部署还需要 HTTPS、安全 Cookie 和独立的认证密钥。不要把模型密钥、数据库密码或真实环境文件提交到仓库。

## 测试与调试

前端测试与构建在 `frontend/llm` 下执行：

```bash
npm run test:unit
npm run lint
npm run build
```

后端测试按改动范围选择，入口说明见 [后端 README](backend/competition_app/README.md)。涉及登录、规划、流式对话和中断恢复的改动，还需要在真实运行环境里检查对应流程，不能只看单元测试结果。

在 WSL 环境下，不要从命令行运行 Live pytest；请使用已启动前端的运行面板，确认模式为 `live` 后点击 Execute。测试数量和通过情况以当次输出为准，不在这里维护一个容易过期的数字。

## 文档与参与方式

| 想了解的内容 | 文档 |
| --- | --- |
| 安装、部署与升级 | [部署指南](docs/deployment.md) |
| 数据资源如何放置 | [数据目录规范](docs/data-layout.md)、[数据包说明](docs/data-package.md) |
| 前端如何调用接口 | [前端接口参考](docs/frontend-api-reference.md) |
| 学情指标如何计算 | [学情监测与资源匹配](docs/learning-monitoring-methodology.md) |
| 如何参与开发 | [贡献说明](CONTRIBUTING.md) |
| 其他说明 | [文档索引](docs/index.md) |

遇到问题可以提交 Issue，尽量附上操作步骤、预期结果和实际表现。涉及账号、学习记录或密钥的内容请先脱敏。欢迎提出建议，也欢迎通过 Pull Request 一起改进。
