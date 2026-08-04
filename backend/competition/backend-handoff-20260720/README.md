# 历史路径兼容入口

活动业务后端已迁移到 [`../../platform_backend`](../../platform_backend)。

此目录只为旧部署脚本和文档保留一个版本，不再存放 Python 业务源码。主应用读取旧的
`BACKEND_HANDOFF_ROOT=competition/backend-handoff-20260720` 时会自动切换到稳定目录。
请将新部署配置改为：

```dotenv
BACKEND_HANDOFF_ROOT=platform_backend
```
