# 平台业务后端

本目录是时珍智训当前仍在运行的业务后端，提供认证映射、题库、训练工坊、教学资源、
学习行为和兼容业务接口。它由 `competition_app` 装载到同一个 FastAPI 进程，不是独立的
第二套产品，也不是数据交付目录。

- Python 导入名暂时保持 `APP.backend.*`，避免破坏现有动态导入和业务功能。
- 源代码只读；数据库、上传、日志和用户索引写入 `SHIZHEN_RUNTIME_ROOT`。
- 公共题库、知识点、视频和教材由 `SHIZHEN_ASSET_ROOT` 只读挂载。
- 新部署配置：`BACKEND_HANDOFF_ROOT=platform_backend`。
- 旧日期路径仍兼容一个版本，但不再存放业务源码。

启动、数据安装、测试和升级方法统一见：

- [`../../docs/deployment.md`](../../docs/deployment.md)
- [`../../docs/data-layout.md`](../../docs/data-layout.md)
- [`../../docs/database-operations.md`](../../docs/database-operations.md)
