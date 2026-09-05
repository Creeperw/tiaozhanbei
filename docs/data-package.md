# 网盘数据包与安装说明（20260905）

## 交付边界

Git 仓库包含源码、内置课程/考试数据、提示词、迁移、测试和部署说明；大资源单独交付。
**交付状态：分包方案和脚本已提供，完整数据包尚未生成和上传。下列文件名是约定名称，不表示文件已经可下载。**
网盘链接由项目负责人上传后提供，本文不包含尚不存在的下载链接。
资料仅供有权访问的项目成员使用；教材和视频相关资料须遵守原作者授权，不因本项目打包而获得公开传播许可。

数据包不含 `.env`、密钥、SQL 备份、用户数据库、个人知识库、会话、上传资料或笔记图片。
这是一份公共知识资源交付，**不是生产业务数据备份**；旧系统用户不会随包迁入。

## 五个独立分包

| 文件 | 解压后路径（相对于 `/srv/shizhen`） | 来源与用途 |
| --- | --- | --- |
| `shizhen-20260905-knowledge.tar.gz` | `assets/knowledge/releases/2026-07-18/{component,video}` | 本地知识组件及公共视频资料；组件包含实际运行的检索 Python 源码 |
| `shizhen-20260905-vectors.tar.gz` | `assets/vectors/public/2026-07-18/indexes` | 服务器公共 FAISS 索引与配套 `metadata.jsonl`；不使用空的本地索引目录 |
| `shizhen-20260905-graphs.tar.gz` | `assets/knowledge-atlas/chapters/releases/2026-07-22`、`app/TreeKG-main/src/data` | 章节映射及 TreeKG 阅读数据 |
| `shizhen-20260905-textbooks-13.tar.gz` | `assets/textbooks/public/十三五` | 本地十三五教材 |
| `shizhen-20260905-textbooks-14.tar.gz` | `assets/textbooks/public/十四五` | 本地十四五教材 |

同目录还应有 `PACKAGES.json`、`SHA256SUMS` 和每包的 `.files.jsonl`。
`PACKAGES.json` 记录最终字节数、文件数、解压体积及 SHA256；`.files.jsonl` 记录逐文件校验值。
上传全部这些文件，不上传名称含 `.partial` 的未完成文件，不上传旁边的 Git 历史备份。
资产目录保留原版本号是为了匹配应用默认路径；`20260905` 是打包日期，不代表索引重新生成。
向量与知识组件来自不同现存来源，文件完整性检查不代替检索召回及语义版本兼容性验收。

## 校验与解压

以下在存放完整下载包的目录执行。先校验，任一失败都应重新下载，不能忽略错误。

```bash
sha256sum -c SHA256SUMS
```

先把代码克隆至 `/srv/shizhen/app`，再解压。建议使用空的发布目录，避免覆盖已有图谱或运行数据。

```bash
mkdir -p /srv/shizhen
for archive in shizhen-20260905-*.tar.gz; do
  tar --extract --gzip --file="$archive" --directory=/srv/shizhen --no-same-owner
done
```

目标空间应依据 `PACKAGES.json` 的 `unpacked_bytes` 总和预留，再加压缩包、虚拟环境、数据库、备份与运行余量。
完整教材本地约 21 GiB、公共向量约 5.5 GiB，不能把“只够解压”当作运行容量规划；建议至少 80 GiB 可用空间。
分包都是独立 tar.gz，不需拼接。Linux 解压后请将资产与代码赋予服务账号读取权限，将运行目录赋予写权限。

## 配置

使用 `deploy/production.env.example`。若沿用默认 `/srv/shizhen`，统一资产根即可解析上述目录。
迁移旧环境时，旧的 `QUESTION_VECTOR_STORE_ROOT`、`KNOWLEDGE_RELEASE_ROOT` 等详细路径优先于统一根，
必须清理旧路径或改为本次安装路径，不能让旧配置悄悄覆盖新根。

FAISS 必须与建库时的 Embedding 模型、维度及文本处理匹配；模板使用 `Qwen/Qwen3-Embedding-4B`。
部署后应检查索引维度、记录数量及真实检索结果，不能仅根据目录存在宣布可用。
新增/更换模型不能直接复用旧索引，需要明确重建策略。

TreeKG 阅读数据目前按仓库相对路径读取，因此图谱包会写入 `app/TreeKG-main/src/data`。
自定义代码路径时，应把该目录安装到实际仓库的 `TreeKG-main/src/data`，不是资产根下。

## 重新打包

`scripts/package_public_assets.py` 接受 `--component`、`--video`、`--vectors`、`--chapters`、
`--textbooks`、`--treekg` 和 `--output` 七个绝对目录参数。
它解析指定根的软链接，但拒绝内部软链接、排除私有状态与缓存、记录逐文件校验值、读回压缩包并输出总清单。
失败留下 `.partial`，不会冒充完整交付；已完成包默认不覆盖，请为新一轮使用新的输出目录。
使用期间保证源数据不被写入。文件名过滤不等于隐私审计，新增资源需人工确认授权及内容边界。

完整启动流程见 [部署指南](deployment.md)。
