# 数据库运维指南

本文描述时珍智训 MySQL 数据库的初始化、迁移、检查、备份、恢复和用户数据隔离。所有示例均不在命令行中直接展开密码，`mysql`/`mysqldump` 的 `-p` 会交互式询问。

## 1. 数据库边界

| 数据库 | 配置项 | 主要数据 | 结构管理方式 |
|---|---|---|---|
| 主库 | `MYSQL_DATABASE`，默认 `competition_app` | 用户认证、规划、会话、运行状态、学习监控、掌握度、复习队列 | 编号 SQL 迁移与 SHA-256 校验和 |
| 兼容业务库 | `BACKEND_HANDOFF_MYSQL_DATABASE`，默认 `competition_frontend` | 题库、知识卡、题目训练、案例、错题、试卷和兼容页面数据 | SQLAlchemy 元数据初始化与增量结构修复 |

两个数据库可以位于同一 MySQL 实例，但不能配置成同名。业务数据通过服务端认证用户 ID 或稳定的外部身份映射隔离，前端提交的 `learner_id`、`user_id` 不能改变数据归属。

## 2. 建库和授权

开发机可使用已有 MySQL 管理账号执行 `init-db`。生产环境建议 DBA 先建库：

```sql
CREATE DATABASE competition_app
  CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE DATABASE competition_frontend
  CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

CREATE USER 'competition_app'@'127.0.0.1' IDENTIFIED BY 'replace-with-strong-password';
GRANT SELECT, INSERT, UPDATE, DELETE, CREATE, ALTER, INDEX, DROP, REFERENCES
  ON competition_app.* TO 'competition_app'@'127.0.0.1';
GRANT SELECT, INSERT, UPDATE, DELETE, CREATE, ALTER, INDEX, DROP, REFERENCES
  ON competition_frontend.* TO 'competition_app'@'127.0.0.1';
```

首次初始化需要 DDL 权限。若让应用自动创建数据库，账号还需实例级 `CREATE` 权限；更推荐由 DBA 预建数据库。初始化完成后，可以按组织要求移除运行账号的 `DROP`、`ALTER`、`CREATE` 权限，但下次升级迁移前必须临时恢复，且兼容业务库启动期的增量修复也可能需要 DDL 权限。

不要把密码写进 SQL 文件或 Git。将其写入权限受控的 `.env.local`、systemd `EnvironmentFile` 或团队密钥管理服务。

## 3. 主库迁移

从 `backend/` 执行：

```bash
conda activate torch
python -m competition_app.cli.app init-db
```

执行器会：

1. 创建 `MYSQL_DATABASE`；
2. 创建 `schema_migrations(version, checksum)`；
3. 按文件名顺序执行 `competition_app/migrations/*.sql`；
4. 保存每个文件的 SHA-256 校验和；
5. 已执行且校验和一致的迁移会跳过，不一致则立即失败。

查看迁移历史：

```sql
SELECT version, checksum
FROM competition_app.schema_migrations
ORDER BY version;
```

新增字段或表时，新建下一个编号的 SQL 文件，例如 `007_feature_name.sql`。已经在任何环境执行过的迁移文件不得修改、重命名或删除；修正错误也应添加新迁移。

当前迁移执行器按分号拆分 MySQL 语句。迁移文件应保持简单 DDL/DML，不要加入包含内部分号的存储过程、触发器定义或依赖自定义 delimiter 的脚本。

## 4. 兼容业务库初始化

当 `BACKEND_HANDOFF_ENABLED=true` 且配置了 `MYSQL_PASSWORD` 时，应用装载兼容业务模块并连接
`BACKEND_HANDOFF_MYSQL_DATABASE`。该模块会创建缺失数据库、用 SQLAlchemy 元数据创建缺失表，并运行现有增量结构修复。

首次初始化建议：

1. 先执行主库 `init-db`；
2. 备份已有的兼容业务库（若不是空库）；
3. 启动应用一次并等待 `/health` 正常；
4. 登录后检查 `GET /api/v1/platform/status`；
5. 抽查题库、学习工坊、知识卡和试卷页面。

不要直接手工修改兼容库表结构来绕过启动错误。先保存错误日志和数据库结构，再通过代码中的增量修复补丁处理。

## 5. 备份

备份应覆盖两个数据库，并记录对应的 Git commit、配置版本和资产版本。低写入压力场景可用：

```bash
backup_dir=/srv/backups/tiaozhanbei/$(date +%F_%H%M%S)
mkdir -p "$backup_dir"

mysqldump --single-transaction --routines --triggers --events \
  -h "$MYSQL_HOST" -P "$MYSQL_PORT" -u "$MYSQL_USER" -p \
  competition_app > "$backup_dir/competition_app.sql"

mysqldump --single-transaction --routines --triggers --events \
  -h "$MYSQL_HOST" -P "$MYSQL_PORT" -u "$MYSQL_USER" -p \
  competition_frontend > "$backup_dir/competition_frontend.sql"
```

若数据库名不是默认值，用实际 `MYSQL_DATABASE` 和 `BACKEND_HANDOFF_MYSQL_DATABASE` 替换。完成后至少检查文件大小、`mysqldump` 退出码，并定期在隔离实例做恢复演练。备份文件包含账号、学习记录等敏感数据，应加密、限制访问并设置保留期限。

大型生产实例应采用托管 MySQL 快照或物理备份，并确保两个库来自同一一致性时间点。

## 6. 恢复与回滚

恢复会覆盖业务状态，应先停止应用写入并在目标实例验证数据库名称。推荐先恢复到临时实例验收：

```bash
mysql -h "$MYSQL_HOST" -P "$MYSQL_PORT" -u "$MYSQL_USER" -p \
  competition_app < /srv/backups/example/competition_app.sql

mysql -h "$MYSQL_HOST" -P "$MYSQL_PORT" -u "$MYSQL_USER" -p \
  competition_frontend < /srv/backups/example/competition_frontend.sql
```

恢复后使用备份对应的代码版本启动，检查登录、用户隔离、规划、会话、复习队列、题库和试卷，再开放流量。若要升级到更新版本，随后重新执行 `init-db` 并启动兼容模块完成增量结构更新。

系统没有自动 down migration。不要手工删除 `schema_migrations` 记录假装回滚，也不要只回滚代码而保留不兼容的数据库结构。

## 7. 日常检查

```sql
SELECT VERSION();
SHOW VARIABLES LIKE 'character_set_server';
SHOW VARIABLES LIKE 'collation_server';

SELECT COUNT(*) AS applied_migrations
FROM competition_app.schema_migrations;

SELECT table_schema, COUNT(*) AS table_count
FROM information_schema.tables
WHERE table_schema IN ('competition_app', 'competition_frontend')
GROUP BY table_schema;
```

还应监控连接数、慢查询、磁盘使用量、备份结果和数据库错误率。应用连接启用了 `pool_pre_ping` 与定期连接回收，但这不能代替 MySQL 服务端监控。

## 8. 数据安全与排障

- 排障查询必须带当前用户边界，禁止导出全表后在前端过滤；
- 日志中不要记录密码、Cookie、完整访问令牌、病例原文或大段用户画像；
- 删除账号或清理数据前先确认两个数据库中的关联身份映射与业务记录；
- 不要将生产数据库复制到个人开发机；需要复现时使用脱敏快照；
- 遇到 `migration checksum changed`，还原被修改的旧迁移并新增修复迁移；
- 遇到连接拒绝，依次检查 MySQL 监听地址、端口、防火墙、账号 host 范围和密码；
- 遇到字符异常，确认两个库、连接 URL 和导入文件均使用 `utf8mb4`。

整体部署步骤见 [部署与升级指南](deployment.md)，接口的数据归属规则见
[前端接口参考](frontend-api-reference.md)。
