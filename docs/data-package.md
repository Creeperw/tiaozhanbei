# 网盘数据包与安装说明（20260905）

## 交付边界

Git 仓库包含源码、内置课程/考试数据、提示词、迁移、测试和部署说明；大资源单独交付。
**交付状态：四个归档已打包并上传至网盘 `/挑战杯/数据`，其中三个以分卷形式存放。**
2026-09-06 进度：约 5.5 GiB 服务器向量资源已下载，334 个索引及元数据文件通过源端 SHA256 比对；
8,359 个非二进制文件通过已知私密凭据扫描。压缩包现已生成并上传；`.partial` 文件不是可交付成品。
已知凭据扫描不是全面内容审计，也不代表资料获得公开传播授权。
网盘链接由项目负责人提供，本文不包含下载链接。
资料仅供有权访问的项目成员使用；教材和视频相关资料须遵守原作者授权，不因本项目打包而获得公开传播许可。

数据包不含 `.env`、密钥、SQL 备份、用户数据库、个人知识库、会话、上传资料或笔记图片。
这是一份公共知识资源交付，**不是生产业务数据备份**；旧系统用户不会随包迁入。

## 四个归档与分卷

网盘 `/挑战杯/数据` 下共四个归档。knowledge、vectors、textbooks 三个归档已切分为等长分卷，graphs 保持单文件。

| 归档 | 网盘位置 | 分卷数 | 压缩体积 | 解压后路径（相对于 `/srv/shizhen`） | 来源与用途 |
| --- | --- | --- | --- | --- | --- |
| knowledge | `知识分卷/` | 6 | 966 MiB | `assets/knowledge/releases/2026-07-18/{component,video}` | 本地知识组件及公共视频资料；组件包含实际运行的检索 Python 源码 |
| vectors | `向量分卷/` | 16 | 2.90 GiB | `assets/vectors/public/2026-07-18/indexes` | 服务器公共 FAISS 索引与配套 `metadata.jsonl`；不使用空的本地索引目录 |
| textbooks | `教材分卷/` | 48 | 8.77 GiB | `assets/textbooks/public/{十三五,十四五}` | 本地十三五与十四五教材，合并在同一归档内 |
| graphs | 根目录 | 1（不分卷） | 3.46 MiB | `assets/knowledge-atlas/chapters/releases/2026-07-22`、`app/TreeKG-main/src/data` | 章节映射及 TreeKG 阅读数据 |

分卷命名为 `<归档名>.tar.gz.partNNN`，三位序号自 `001` 起连续编号，除末卷外每卷 199,229,440 字节。
按序号拼接后即为完整 `tar.gz`，归档没有加密或额外封装。

每个归档随附元数据，与分卷同目录存放：

| 文件 | 作用 |
| --- | --- |
| `<归档名>.package.json` | 该归档的字节数、文件数、SHA256 与解压体积 |
| `<归档名>.SHA256SUMS` | 合并后完整归档与逐文件清单的校验值 |
| `<归档名>.files.jsonl` | 归档内逐文件校验值 |
| `<归档名>-PARTS.json` | 分卷清单 |
| `<归档名>-PARTS.SHA256SUMS` | 分卷本身的校验值 |

后两项仅分卷归档具备，`graphs` 没有。分卷元数据使用大写归档名，且 textbooks 的写法是单数：
`KNOWLEDGE-PARTS.json`、`VECTOR-PARTS.json`、`TEXTBOOK-PARTS.json`（不是 `TEXTBOOKS-PARTS.json`）。

网盘根目录另有 `交付说明.md`，`教材分卷/` 另有 `教材交付说明.md`。两份说明由交付方编写，
与本文件有出入时以交付说明为准。

上传全部这些文件，不上传名称含 `.partial` 的未完成文件，不上传旁边的 Git 历史备份。
资产目录保留原版本号是为了匹配应用默认路径；`20260905` 是打包日期，不代表索引重新生成。
向量与知识组件来自不同现存来源，文件完整性检查不代替检索召回及语义版本兼容性验收。

## 校验、合并与解压

以下在存放完整下载包的目录执行。先校验分卷，再合并，最后校验完整归档；任一失败都应重新下载，不能忽略错误。

```bash
sha256sum -c KNOWLEDGE-PARTS.SHA256SUMS
sha256sum -c VECTOR-PARTS.SHA256SUMS
sha256sum -c TEXTBOOK-PARTS.SHA256SUMS
```

合并分卷。序号为三位补零，按字典序拼接即等于按序拼接：

```bash
for name in knowledge vectors textbooks; do
  cat "${name}".tar.gz.part* > "${name}".tar.gz
done
```

校验合并结果与逐文件清单。`graphs.tar.gz` 未分卷，一并直接校验：

```bash
for name in knowledge vectors textbooks graphs; do
  sha256sum -c "${name}".SHA256SUMS
done
```

先把代码克隆至 `/srv/shizhen/app`，再解压。建议使用空的发布目录，避免覆盖已有图谱或运行数据。

```bash
mkdir -p /srv/shizhen
for name in knowledge vectors textbooks graphs; do
  tar --extract --gzip --file="${name}.tar.gz" --directory=/srv/shizhen --no-same-owner
done
```

目标空间应依据各包 `*.package.json` 记录的解压体积（打包脚本对应字段为 `unpacked_bytes`）核算，
再加压缩包、合并临时文件、虚拟环境、数据库、备份与运行余量。
按当前运行部署实测换算：教材约 11 GiB、公共向量约 5.5 GiB、知识组件与视频约 2.0 GiB、图谱约 71 MiB，
解压后合计约 19 GiB；合并分卷期间另需约 12.6 GiB 临时空间，合并完成后可删除分卷回收。
不能把“只够解压”当作运行容量规划；建议至少预留 50 GiB 可用空间；
若教材包内容大于当前部署，应按 `TEXTBOOKS.package.json` 重算。
分卷必须按序号拼接后才能解压，单独解压某一卷会得到截断的归档。
Linux 解压后请将资产与代码赋予服务账号读取权限，将运行目录赋予写权限。

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

脚本按六个源参数生成五个归档，输出 `<包名>.tar.gz`、`<包名>.files.jsonl`，以及顶层 `PACKAGES.json` 与 `SHA256SUMS`；
它不切分卷，也不生成 `*-PARTS.json` 与 `*-PARTS.SHA256SUMS`。
网盘交付的命名与脚本输出不同：归档名缩短为 `knowledge`、`vectors`、`textbooks`、`graphs`；
十三五与十四五教材合并为一个 `textbooks` 归档；顶层 `PACKAGES.json`、`SHA256SUMS` 改为每包一份
`*.package.json` 与 `*.SHA256SUMS`，并另加分卷元数据。
**分卷切分与这组元数据由仓库之外的步骤产生，本仓库不包含对应脚本**；
重做交付时需自行补齐切分与校验环节，或沿用网盘现有产物。

完整启动流程见 [部署指南](deployment.md)。
