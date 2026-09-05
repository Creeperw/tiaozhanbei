# 部署与升级指南

## 部署前提

当前项目采用单个 FastAPI 进程同源托管 React 前端和业务 API，不要求 Docker。
使用 Python 3.10（本机已有 Conda `torch` 环境）、满足 Vite 7 要求的 Node.js
（20.19+ 或 22.12+）及双 MySQL 数据库。不得将本机运行成功等同于新服务器验收通过。

## 准备发布目录

1. 从本仓库新的 `main` 克隆到 `/srv/shizhen/app`。发布基线收录整理后的工作区源码，
   保留 `backend/`、`frontend/llm/`、`TreeKG-main/`、运行依赖的 `evaluation/`、
   `scripts/`、`deploy/` 和说明文档。不从旧分支拼装代码。
2. 在目标 Python 环境安装 `backend/competition_app/requirements.txt`，按启用能力安装
   `backend/platform_backend/requirements.txt` 与 `TreeKG-main/integration/requirements.txt`。
   解压知识包后还需核对并安装 `assets/knowledge/releases/2026-07-18/component/requirements.txt`，
   组件独立入口的 Flask、json-repair 等依赖不能仅靠复制源文件获得。
   依赖目前不是完整冻结集，部署前应在目标环境验证并记录实际版本，不自动升级本机依赖。
3. 在 `frontend/llm` 使用 `npm ci`，再执行 `npm run build`。构建应在发布暂存目录完成，
   不覆盖正在服务的 `dist/`；验证后再切换版本。
4. 按 [网盘数据包说明](data-package.md) 校验并安装五个外部资产包。
   [数据目录规范](data-layout.md) 解释布局；不要直接搬运本机绝对软链接。

## 配置与数据库

以 `deploy/production.env.example` 为基础创建项目外部的私有配置文件，
通过 `COMPETITION_ENV_FILE` 指定绝对路径。不要提交或打印真实配置。

生产入口要求显式设置 `COMPETITION_APP_MODE=live`、`BACKEND_HANDOFF_ENABLED=true`、
`AUTH_COOKIE_SECURE=true`，并为 `SECRET_KEY`、`BACKEND_HANDOFF_SECRET_KEY` 分别配置
至少 32 字符的随机秘密。安全 Cookie 要求客户端通过 HTTPS 访问。

按实际环境设置模型、Embedding、数据库和资产配置。迁移时必须同时保留
`EVOLUTION_ENABLED`、`EVOLUTION_RULES_ENABLED`、`USE_SQLITE`、`SQLITE_PATH`、
`COMPETITION_EXECUTION_ENGINE` 等配置，不只复制 `COMPETITION_*`。
不要将当前 SQLite 模式直接改成 MySQL 而不迁移用户数据。

数据库初始化、编号迁移、备份和恢复按 [数据库运维](database-operations.md) 执行。
生产启动入口不会自动安装依赖、构建前端、初始化数据库或清理数据。

## 生产启动

使用选定 Python 执行 `scripts/serve_production.py --check` 进行预检；移除 `--check`
后才会启动服务。预检只读取配置和资源，不启动模型、不创建 ApplicationContainer。
没有显式配置 Live、安全 Cookie 或安全密钥时会拒绝启动，而不是退回演示模式。

`deploy/shizhen.service.example` 是 Linux systemd 模板。按服务器修改运行用户、
项目目录、Python 路径和环境文件路径后安装。模板本身不会被自动部署。
环境文件采用简单 `KEY=VALUE`，使用绝对路径，不写 shell 命令或变量展开。

`backend/platform_backend/run.sh` 保留为开发启动脚本，默认 stub，不用于正式发布。
生产先保持单实例；检查点、后台调度和进化任务的多进程语义未验证前，不增加 workers。

在反向代理上终止 TLS，后端监听回环地址，通过 `API_HOST`、`API_PORT` 配置。
代理需要保留 Cookie，关闭 SSE 响应缓冲并为长任务设置足够的读取超时。
只信任受控代理转发头，不公开数据库、模型密钥或内部管理端口。

## 验收与升级回滚

- 执行部署资源检查、受影响后端非 Live 测试及前端测试、lint、构建。
- 确认 `/health` 返回 `live`，首页和静态资源可读。
- 通过 HTTPS 浏览器验证登录、用户隔离、规划、题库、针灸案例、图谱、教材 PDF 和 SSE。
- Live 验证仅在前端运行台点击 Execute；禁止 WSL 命令行 Live pytest。
- 升级前保存发布版本、环境配置、双库一致性备份和实际运行状态目录。
- 切换发布版本后再验收；失败则恢复旧发布和对应配置。数据库回滚必须遵循迁移策略，
  不能直接覆盖有新增用户记录的数据库。运行中的文件 tar 不是数据库一致性备份。

## 从零部署操作顺序

1. 准备 Python 3.10、Node.js 20.19+ 或 22.12+、MySQL 和可用 HTTPS 域名/证书。
   在服务器创建专用 `shizhen` 系统账号。已有 Python 3.12 环境必须另做兼容性检查，不能当作已验证环境。
2. 克隆代码并安装三份 requirements 中实际需要的能力。4 GiB 内存服务器优先远程 Embedding，
   不启用本地语音/向量大模型。依赖可能带有较大机器学习运行库，安装前检查磁盘和内存。
3. 构建前端并安装数据包。创建 `/srv/shizhen/runtime` 与 `/etc/shizhen`。
4. 私下填写 `/etc/shizhen/app.env` 的数据库、模型、Embedding、管理员、独立随机认证密钥；
   设置权限 `600` 并确保 systemd 可读取。模型 URL 与密钥必须属于同一服务商。
   搜索、PDF 解析、视觉和邮件需要相应配置，留空不代表这些能力已经可用。
5. 按数据库文档预建双库、授权；在设置 `COMPETITION_ENV_FILE=/etc/shizhen/app.env` 的环境中，
   从 `backend/` 执行主库 `init-db`。升级已有系统前先备份，不能把新库初始化当作旧数据迁移。
6. 用目标 Python 执行 `scripts/serve_production.py --check`。通过只表示配置与目录存在，
   不代表模型连通、索引完整、数据库迁移或用户功能验收已经通过。
7. 编辑 `deploy/shizhen.service.example` 的实际 Python 路径，安装成 `/etc/systemd/system/shizhen.service`，
   重新加载 systemd，启用并启动服务。新账号须能读代码/资产并写运行目录。
8. 当前少数模块仍写 `backend/competition_app/runtime`、`data/workshop_note_images` 与 TreeKG 建图目录。
   部署时需按写入范围授权，并将这些路径纳入状态备份；`runtime` 本身含 Python 源码，
   不能把整个目录排除或替换成空目录。升级应保留其中的非源码状态。
9. 配置 HTTPS 反向代理，关闭 SSE 缓冲，长请求读取超时至少覆盖模型超时，
   然后执行登录及各功能的浏览器验收。HTTP 公网端口不能作为安全 Cookie 的正式登录入口。

## 本次发布的验收边界

本地已完成 33 项定向离线测试和一次独立前端生产构建，未覆盖正在服务的前端。
前端 lint 尚有 196 个错误、25 个警告；三个定向 Vitest 文件因 worker 启动超时未实际执行。
新服务器已完成编号迁移至 023，并成功切换新版 Live，公网首页和登录表单可加载，
健康检查返回正式知识源和 LangGraph。登录及历史记录浏览已通过下述验收，模型驱动的端到端 Live 验收仍未完成。
当前聊天服务商接口从服务器连接超时，智能对话与规划不能据此宣布可用；模型配置未擅自更换。
当前部署是负责人授权的临时 HTTP 测试，未满足上文 HTTPS 正式生产要求。
源代码发布、数据打包或 `/health` 成功均不等于正式上线验收。

本次修正了 MySQL 无参数迁移执行方式，避免 SQL 注释中的百分号被驱动解释为参数占位符。
没有修改既有 SQL 文件或校验和。若迁移中途失败，MySQL DDL 可能已提交，不能直接反复重试：
应先核对实际表结构，再受控恢复剩余步骤和迁移记录，不能删除字段或跳过整个迁移。

### 已知部署注意事项

- 2026-09-06 按负责人要求移除额外的“历史学习记录（含导入数据）”页面面板。
   数据库记录及受用户身份约束的只读历史接口保留，不删除学习数据。
   历史导入数据尚未接入原有报告、正式复习历史、考试路径及助教会话的完整展示链路；
   先前的历史面板验收不代表这些正式页面已修复，不应宣称原有页面可展示全部导入记录。
- 此次修复通过 4 项 SQLite 后端测试、10 项前端测试和隔离生产构建。
   四个测试账号均通过真实服务器登录表单及历史页面验收，全部分页读取与导入数量核对通过，
   原始学习任务编号均保留，跨账号会话访问返回 404。正常登录产生的新活动不算数据差异。
   原有全量 lint 问题未在本次处理，不将定向回归等同于全系统验收。
- 知识点名称缺失按数据问题记录，暂不处理；页面可能仍显示知识点编号或待补充名称。
   本次仅发布服务器修复，未覆盖原本地工作区及运行中的前端。

- 新建 TreeKG 图谱需要 `TreeKG-main/src/HiddenKG/model/bert-base-chinese` 的完整模型/tokenizer。
   当前现有文件和五个数据包不包含该模型；需从可信模型来源取得后安装，并遵守模型许可。
   缺失时已有图谱仍可读取，但完整新建图谱流水线会在本地嵌入阶段失败，不能宣称该能力已验收。
- 业务后端 `APP/backend/config.py` 存在固定管理员密码回退；生产必须显式提供强随机
   `ADMIN_DEFAULT_PASSWORD`。只指定 `COMPETITION_ENV_FILE` 不保证该值导出给业务后端，
   因此使用带 `EnvironmentFile` 的 systemd 模板，并私下核验账号初始化状态。
   已存在管理员的密码不会因为更新启动环境自动修改；应通过受控账号管理流程处理。
- `seed_phase3_dashboard.py` 与当前演示 seed 的用户字段不一致，不用于生产初始化；
   不为运行演示脚本恢复固定可登录密码。
