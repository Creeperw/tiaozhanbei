# 公共资产与运行数据目录规范

## 1. 原则

1. 公共资产只读，运行数据可写。
2. 代码、资产和用户数据分开部署、备份和升级。
3. 目录名保持稳定，版本写在 release 子目录和 manifest 中，不再写入活动代码目录名。
4. 现有细粒度环境变量继续有效，便于旧部署平滑迁移。
5. 数据迁移先校验、后切换，不自动删除源目录。

## 2. 标准目录

```text
SHIZHEN_ASSET_ROOT/
├── manifests/
│   └── asset-manifest.json
├── knowledge/
│   └── releases/<release_id>/
├── knowledge-atlas/
│   └── chapters/releases/<chapter_release_id>/
├── vectors/
│   └── public/<release_id>/
└── textbooks/
    └── public/

SHIZHEN_RUNTIME_ROOT/
├── competition_app/
├── platform_backend/
├── knowledge/
├── uploads/
├── indexes/users/
├── launcher/
└── logs/
```

生产环境可分别挂载到 `/srv/tiaozhanbei/assets` 和 `/srv/tiaozhanbei/runtime`。公共资产目录授予
服务账号读取权限，runtime 授予读写权限。

## 3. 配置优先级

```text
QUESTION_VECTOR_STORE_ROOT 等明确路径
→ SHIZHEN_ASSET_MANIFEST
→ SHIZHEN_ASSET_ROOT + SHIZHEN_ASSET_RELEASE
→ 旧目录兼容探测
```

统一配置示例：

```dotenv
SHIZHEN_ASSET_ROOT=/srv/tiaozhanbei/assets
SHIZHEN_RUNTIME_ROOT=/srv/tiaozhanbei/runtime
SHIZHEN_ASSET_MANIFEST=/srv/tiaozhanbei/assets/manifests/asset-manifest.json
SHIZHEN_ASSET_RELEASE=2026-07-18
SHIZHEN_ATLAS_CHAPTER_RELEASE=2026-07-22
BACKEND_HANDOFF_ROOT=platform_backend
```

manifest 示例位于 `deploy/asset-manifest.example.json`。manifest 内路径必须相对于
`SHIZHEN_ASSET_ROOT`，禁止 `..` 越界和绝对路径。

## 4. 旧数据迁移

先只查看计划：

```bash
python scripts/migrate_external_assets.py \
  --asset-root /srv/tiaozhanbei/assets \
  --release 2026-07-18 \
  --knowledge-source /old/知识星球视频知识库_前端交接包_2026-07-18 \
  --vector-source /old/vdb_store \
  --textbook-source /old/textbook_pdfs \
  --atlas-chapter-source /old/knowledge_atlas_chapters/2026-07-22
```

确认后选择安装方式：

```bash
# 同一台开发机快速兼容；依赖系统支持目录链接
python scripts/migrate_external_assets.py ... --mode link --apply

# 正式部署复制资产，不修改或删除源目录
python scripts/migrate_external_assets.py ... --mode copy --apply
```

脚本不提供自动删除源数据的模式。验证新目录、文件数量、manifest 和在线功能后，再由管理员
按备份策略人工处理旧目录。

## 5. 部署前检查

```bash
python scripts/validate_deployment_layout.py --require-live-assets
```

不带 `--require-live-assets` 时只要求活动业务后端代码存在，适合 Stub 开发环境。

## 6. 本地生成物清理

```bash
python scripts/clean_local_artifacts.py          # 仅预览
python scripts/clean_local_artifacts.py --apply  # 清理缓存
```

该脚本只处理 Python/pytest/ruff 缓存、前端测试结果和覆盖率目录，不删除数据库、PDF、向量索引、
用户上传、构建资产或运行日志。

## 7. 题目难度数据

题目难度字段属于受保护的兼容能力。迁移题库时必须保留 `difficulty`、`difficulty_source`、
`standard_difficulty` 等字段；缺少可信难度时继续保存 `null`，不得批量补默认等级。具体边界见
[`题目难度相关功能现状报告.md`](题目难度相关功能现状报告.md)。
