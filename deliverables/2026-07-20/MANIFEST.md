# 2026-07-20 团队交付清单

## 产物

| 文件 | 条目数 | 大小 | SHA-256 |
|---|---:|---:|---|
| `frontend-handoff-20260720.zip` | 151 | 13,493,957 bytes | `f1ab88b5ff4a4e773c87addf4318b3001a33f049fa1e8b098ae3b0108cac3b5b` |
| `backend-handoff-20260720.zip` | 200 | 578,232 bytes | `aef5e5c00e6032cd0a86ea54018714f10462a78840b62376ea10799417e4c913` |

哈希同时保存在 `SHA256SUMS.txt`。接收方应在解压前重新计算 SHA-256；不一致时不要使用该压缩包。

## 前端包包含

- `frontend/llm/src/`：React 源码与单元/组件测试。
- `frontend/llm/public/`：运行所需静态资源。
- `frontend/llm/e2e/`：Playwright 测试。
- `package.json`、`package-lock.json`、Vite、Tailwind、PostCSS、ESLint 和 Playwright 配置。
- 根目录 `README.md` 与 `.env.example`。

## 后端包包含

- `APP/backend/*.py`：后端模块的可移植物理副本。
- `APP/backend/routers/`：FastAPI 路由。
- `APP/backend/tests/`：后端测试。
- `APP/backend/scripts/`：导入、修复、重建和 seed 脚本源码。
- `APP/backend/official_exam_repository/`：正式题库仓储代码。
- `APP/backend/knowledge_atlas_contracts/`：知识图谱版本契约，不含图谱素材。
- `APP/__init__.py`、`APP/env_loader.py`、`APP/intent_reply_template.py`。
- 根意图模板、`requirements.txt`、启动脚本、`.env.example`、README 和技术文档。

## 明确排除

- `.env` 和所有真实凭据。
- SQLite/MySQL 数据、数据库备份和用户记录。
- 正式题库原始数据、公共知识源、图谱素材和演示种子数据。
- 上传文件、个人知识库、用户题目和文件元数据。
- FAISS/向量索引、图谱运行数据、知识运行状态和模型权重。
- `node_modules`、`dist`、虚拟环境、缓存、浏览器测试结果和历史备份目录。

## 自动检查结果

- 前端 ZIP 条目数：151。
- 后端 ZIP 条目数：200。
- 禁入路径匹配：0。
- 密钥模式扫描范围：582 个源码候选文件。
- 明文 LLM Key、Exa Key、邮件授权码和非空数据库默认密码匹配：0。
- 后端交付相关测试：100/100 通过。
- 前端单元/组件测试：34 个测试文件、182/182 通过。
- 前端生产构建：通过；存在大于 500 kB chunk 和 Browserslist 数据陈旧警告。
- 后端交付副本 OpenAPI 冒烟检查：122 个路径。
- 后端完整 `unittest discover`：3 分钟内未完成，未记录为通过或失败；接收方应在正式数据/依赖环境继续运行。

最终交接状态以根 README 第 12、18 节和 Git 提交说明为准。
