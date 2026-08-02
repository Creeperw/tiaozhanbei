# 挑战杯系统离线数据包

本目录与 GitHub `main` 分支配套。代码从 GitHub 拉取，大体积知识库、向量索引和教材 PDF 从本数据包安装。

## 包含内容

- `knowledge_delivery_2026-07-18.zip`：知识星球交付包，含题库、知识点、切片、图片、学习路径和视频运行数据。
- `vdb_store.zip`：题库与知识库向量索引。
- `textbook_pdfs.zip`：94 本教材 PDF；系统优先匹配“十四五”，无“十四五”时使用“十三五”。
- `install_data.ps1`：校验并安装以上三个压缩包。
- `SHA256SUMS.txt`：压缩包完整性校验值。

MySQL 只保存用户、计划、练习、学习记录、教材页收藏、阅读进度和批注等运行期数据。本包不包含原开发机上的账号和个人数据。新环境可从空库开始，系统迁移会自动建表。

## 安装

先拉取最新代码：

```powershell
git clone https://github.com/Creeperw/tiaozhanbei.git
cd tiaozhanbei
git switch main
git pull --ff-only origin main
```

直接在夸克网盘下载，文件夹有textbook_pdfs和knowledge_delivery...

## MySQL

安装 MySQL 8，确保本机 `3306` 端口可用。在 `backend\competition_app\.env.local` 中填写：

```dotenv
USE_SQLITE=false
MYSQL_HOST=localhost
MYSQL_PORT=3306
MYSQL_USER=root
MYSQL_PASSWORD=你的密码
MYSQL_DATABASE=competition_app
BACKEND_HANDOFF_MYSQL_DATABASE=competition_frontend
```

首次启动会创建数据库并执行迁移。不要共享真实用户数据库；需要演示数据时另做脱敏 seed。

## 启动

```powershell
cd D:\你的目录\tiaozhanbei\frontend\llm
npm install
npm run build

cd ..\..\backend
python -m competition_app.cli.app serve --host 0.0.0.0 --port 7860
```

打开 `http://127.0.0.1:7860/`。

## 手工校验

```powershell
Get-FileHash .\knowledge_delivery_2026-07-18.zip, .\vdb_store.zip, .\textbook_pdfs.zip -Algorithm SHA256
Get-Content .\SHA256SUMS.txt
```

文件名对应的哈希必须一致。
