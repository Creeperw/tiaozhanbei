# 时珍智训

时珍智训是面向中医药学习与资格考试备考的智能学习平台。本项目以教材知识、考试目标和学习行为为基础，整合知识检索、个性化规划、专项训练、复习调度与智能答疑，为学习者提供可追踪、可解释的连续学习支持。

项目参与成员及主要分工见 [贡献说明](CONTRIBUTING.md)。

## 项目定位与能力范围

- **学习规划**：结合专业背景、学习基础、目标考试和可用时间，生成长期规划、短期计划及今日任务，并展示阶段、教材和知识点之间的关联。
- **知识学习与答疑**：通过智能助教获取知识讲解和学习建议，查看教材证据、检索来源、知识图谱、知识卡片及相关视频。
- **专项训练与复习**：支持客观题、案例简答、计时试卷和 AI 病患模拟等训练形式，并将错题、收藏、笔记和复习任务归集到用户账号。
- **学情分析**：基于作答记录、掌握度、薄弱知识点和复习到期状态，辅助调整学习画像与学习安排。
- **多智能体协作**：由任务规划、记忆管理、学情诊断、知识库管理、专家和审核裁判等角色按任务需要协作处理请求；阶段产出经过安全投影与审核后再进入用户界面或持久化流程。

应用提供经典路线和个性化路线两种入口。经典路线支持直接浏览与学习；个性化路线依据已选目标、学情信息及已生成的学习计划展示。两类路线使用不同的数据来源和状态计算逻辑。

完整功能依赖相应的模型服务、数据库和知识资源，仅克隆源码或启动开发模式不足以运行全部流程。项目持续迭代中，各项功能的可用性应以目标环境中的实际验证结果为准；首页可访问或健康检查通过不代表全部学习流程均已验证。

平台内容用于学习参考，不替代教材、教师指导或临床诊疗意见。选择考试路线也不代表符合该考试的报名条件。

## 技术架构

前端采用 React 和 Vite，后端采用 FastAPI，通过 LangGraph 风格的运行时组织多智能体工作流。生产构建后的页面与 API 由同一个后端进程提供，默认端口为 `7860`；本地开发和生产部署均不强制依赖 Docker。

智能体分为任务规划、记忆管理、学情诊断、知识库管理、专家和审核裁判六类角色，根据任务需求参与执行，不采用固定的全角色顺序调用。知识回答优先使用可检索资料，规划与练习结果经过审核后再保存；复习调度、身份校验和数据持久化由后端服务负责。流式输出采用安全事件投影，模型原始响应、系统提示词和内部标识不直接暴露给用户。

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

## 本地开发与启动

建议使用 Python 3.10，以及满足 Vite 7 要求的 Node.js 20.19+ 或 22.12+。Python 环境可通过 Conda 或虚拟环境管理，环境名称与安装路径按实际情况设置。

以下命令从仓库根目录开始，使用独立虚拟环境安装主应用依赖：

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

`stub` 模式仅用于界面和接口联调，不代表真实模型效果，也不替代线上验收。业务模块及知识处理组件的额外依赖，请按 [部署指南](docs/deployment.md) 安装。

开发前端时，可以在后端运行的情况下进入 `frontend/llm` 执行 `npm run dev`。Vite 会把 `/api` 和 `/health` 请求代理到本地后端。

## 完整运行与生产部署

完整运行需要显式设置 `COMPETITION_APP_MODE=live`，并配置聊天模型、Embedding 服务、数据库及知识资源。配置项见 [`backend/competition_app/.env.example`](backend/competition_app/.env.example)，具体步骤见 [部署与升级指南](docs/deployment.md)。

本项目对源码与大体积资源实行独立管理。教材、知识库和向量索引不纳入 Git，需要按 [数据包说明](docs/data-package.md) 获取、校验并设置路径。公共资源包不包含账号数据库、个人学习记录、用户上传内容或密钥，这些数据需要单独备份和迁移。

数据库通常使用同一个 MySQL 实例下的两个库：

- `competition_app`：认证、学习规划、复习和工作流状态。
- `competition_frontend`：题库、训练、教材等业务数据。

初始化、迁移和备份步骤见 [数据库运维指南](docs/database-operations.md)。包含已有用户数据的环境应通过迁移升级，不应重新初始化数据库。

生产部署使用 `scripts/serve_production.py`，启动前应先完成环境预检。公网部署必须配置 HTTPS、安全 Cookie、独立认证密钥和受控的模型服务凭据。模型密钥、数据库密码、Cookie、真实环境文件及用户数据不得提交到仓库。

## 测试、验收与故障排查

前端单元测试与构建在 `frontend/llm` 下执行：

```bash
npm run test:unit
npm run lint
npm run build
```

后端测试按改动范围选择，入口说明见 [后端 README](backend/competition_app/README.md)。涉及登录、规划、流式对话、中断恢复、审核发布或数据隔离的改动，还需在真实运行环境中验证对应流程；单元测试结果不能替代端到端验收。

在 WSL 环境下，禁止从命令行运行 Live pytest；应使用已启动前端的运行面板，确认模式为 `live` 后点击 Execute。测试数量与通过情况以当次执行结果为准。

## 文档与参与方式

| 内容 | 文档 |
| --- | --- |
| 安装、部署与升级 | [部署指南](docs/deployment.md) |
| 数据资源如何放置 | [数据目录规范](docs/data-layout.md)、[数据包说明](docs/data-package.md) |
| 前端如何调用接口 | [前端接口参考](docs/frontend-api-reference.md) |
| 学情指标如何计算 | [学情监测与资源匹配](docs/learning-monitoring-methodology.md) |
| 如何参与开发 | [贡献说明](CONTRIBUTING.md) |
| 其他说明 | [文档索引](docs/index.md) |

问题反馈与功能建议通过 Issue 提交，并附操作步骤、预期结果、实际表现、运行模式和必要的脱敏日志。涉及账号、学习记录、模型凭据或安全漏洞的信息不得公开披露，应先通过非公开渠道联系维护者。代码及文档改进通过 Pull Request 提交。
