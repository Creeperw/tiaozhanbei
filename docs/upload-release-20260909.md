# 上传功能正式发布记录（2026-09-09）

## 发布结果

上传处理收敛第一至第三阶段已部署至既有 Linux 生产服务。第四阶段兼容实现清理未实施。

- 发布目录：`/srv/tiaozhanbei-releases/20260905-live`。
- 服务：`tiaozhanbei.service`，重启后为 `active/running`。
- 健康检查：`mode=live`、`knowledge_source=formal`、`execution_engine=langgraph`、`frontend_backend=mounted`。
- 后端清单：`upload-release-20260909-files.txt`；前端发布 `frontend/llm/dist`。
- 19 个后端文件（含6个与生产一致的既有依赖）及207个前端产物逐一校验，226个文件哈希全部一致。
- 配置、数据库和业务资料目录未覆盖。个人知识库登记、切片、知识点三个文件的部署前后哈希一致；本项不代表全量业务数据校验。

## 验证

- 发布前后端上传专项离线测试：86项通过。
- 发布前前端全量测试：89个文件、681项通过；生产构建成功。
- 独立暂存快照验证：后端上传及依赖125项通过，前端上传专项40项通过，快照前端构建成功。此验证不依赖工作区未暂存的历史业务源码。
- 生产服务健康检查通过；教材历史接口未认证请求返回401。
- 生产浏览器可加载平台首页，当前浏览器未登录，统一上传面板的已登录生产验收尚未完成。
- 未运行命令行Live pytest，未上传或激活考纲，未确认个人题目，未删除资料或重建索引。
- 教材、考纲真实图像模型能力尚未验收。之前的模拟接口浏览器验证不替代Live上传验收。

## 备份与回滚

此前阶段文档记录的本地 `/tmp` 备份在发布时已不存在，本次重新建立持久备份：

- 本地：`/home/wangjl/tiaozhanbei-release-20260905/backups/upload-release-20260909/production-before.tgz`。
- 生产：`/srv/tiaozhanbei-releases/backups/upload-20260909/production-before.tgz`。
- 发布包：`/srv/tiaozhanbei-releases/upload-20260909-candidate.tgz`。

回滚前先确认没有活跃上传或工作流，停服后仅恢复备份中的后端文件和原前端产物，再启动并检查健康。若需清理新增模块，仅限 `contracts/upload.py`、`llm/upload_json_client.py`、`services/document_parsing.py`、`services/upload_tasks.py`；新任务元数据保留，旧实现不会读取。不得全树回退、删除runtime或覆盖用户现有资料。

## 提交范围

本次提交包含三阶段上传重构及所需、此前已部署但尚未提交的个人知识库和上传传输依赖。混合API文件仅暂存上传与知识库接口相关块；规划、审计、统计、学习连续性等历史改动继续保留在工作区，不纳入本次提交。未推送远端。

生产部署保留了此前已上线的历史改动，因此本次提交不是生产代码的完整快照；发布包、部署前备份及逐文件哈希校验用于追溯本次上线产物。不得用仅含本次提交的全树覆盖生产。

各阶段文档中的“未部署”描述是对应阶段完成时的历史状态；正式发布状态以本记录为准。
