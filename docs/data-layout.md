# 代码、资产与运行数据边界

## 源码和必要资源

- `backend/competition_app/`：主后端、提示词、内置数据、SQL 迁移与测试。
- `backend/platform_backend/`：同进程挂载的业务后端，不是可删除的旧副本。
- `backend/competition/`：章节映射及旧路径兼容；迁移资产前不能整目录删除。
- `frontend/llm/`：正式前端；`dist/` 是当前同源服务必需的构建产物。
- `TreeKG-main/`：知识图谱 viewer、数据与教材建图流水线，仍被正式接口调用。
- `evaluation/`：仍供运行台读取的数据集和维护工具；后端 `evaluation/` 模块也被 API 导入。
- `scripts/`：部署、资产维护和可重建缓存清理工具。
- `deploy/`：部署配置模板，不保存真实密钥。

针灸案例的权威文件为 `backend/competition_app/data/acupuncture_cases.v1.json`。
`docs/针灸交互训练-案例数据v1.json` 暂时仅为兼容未重启服务的软链接，不是第二份数据。

## 外部只读资产

使用 `SHIZHEN_ASSET_ROOT` 和版本 manifest 管理知识库、视频知识结果、题库、
向量索引、章节映射及教材 PDF。独立路径变量优先于 manifest，部署时必须同步核对。

本地 `assets/` 包含指向项目外部的绝对软链接；复制项目本身并不会复制这些数据。
新服务器通过 `scripts/migrate_external_assets.py --mode copy` 安装真实资产，
先预览，再按脚本帮助传入各项源目录并加 `--apply`；已有目标不会被静默覆盖。
模板为 `deploy/asset-manifest.example.json`。保留知识库组件的实际 Python 代码，不能只复制 JSON。

## 可写状态

`SHIZHEN_RUNTIME_ROOT` 提供统一运行根目录；历史配置仍可能将状态放在
`backend/competition_app/runtime/`。该目录含 Python 源码，绝不能整体排除或清空。
模拟病患与部分试卷接口仍有包内运行路径，因此当前版本不能直接将全部源码挂载只读。

保持现有环境变量及状态目录不变。迁移用户数据时先停写、备份双库及所有实际状态目录，
再迁移路径并验证会话、检查点、上传文件和学习记录；不要通过创建空目录掩盖缺失数据。

## 清理规则

`scripts/clean_local_artifacts.py` 默认只预览，加 `--apply` 仅清理可重建缓存。
生产包不包含 `node_modules/`、历史交付资料、开发缓存、真实 `.env` 和用户数据。
测试与说明保留在源码仓库；发布物可以不携带测试，但不可删除提示词、迁移和运行期数据集。