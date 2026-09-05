# 外部资产兼容入口

本目录不再存放活动业务后端，也不应保存数据库、用户上传、日志或大体积公共资产。

当前保留内容：

- `knowledge_atlas_chapters/`：可审查、可版本化的章节映射；
- `backend-handoff-20260720/`：旧启动路径兼容壳，一个版本后可删除；
- 本地被 Git 忽略的旧软链接：仅供迁移期开发机使用。

活动业务代码位于 [`../platform_backend`](../platform_backend)。正式资产使用
`SHIZHEN_ASSET_ROOT`，可写数据使用 `SHIZHEN_RUNTIME_ROOT`。完整目录规范和旧数据迁移命令见
[`../../docs/data-layout.md`](../../docs/data-layout.md)。
