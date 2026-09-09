# 个人题库连接重试修复（2026-09-09）

## 原因与范围

生产题目上传曾返回502；13:11:50 UTC后端记录ConnectError，同刻代理记录连接opencode.ai:443超时。直接故障是连接建立失败，更深层代理节点超时原因未确认。

仅修改 `backend/platform_backend/APP/backend/question_workspace_service.py` 的 `_llm_extract_questions`：

- 当前分段的模型调用最多尝试3次，连接错误和连接超时后分别退避1秒、2秒。
- 仅捕获httpx.ConnectError、httpx.ConnectTimeout；耗尽后继续走原任务失败持久化与脱敏响应。
- 复用同一LLMClient、会话、端点、模型、凭据和请求正文。
- 不重跑完成的分段、文件解析、上传或发布，不改变答案补全和其它共享客户端调用。
- HTTP错误、读取/写入/连接池超时及无效JSON不因本补丁自动重试。
- 连接异常下等待时间可能增加；原每次调用超时设置不变。未修改代理配置。

## 验证

- 新增10项离线专项测试；修复前4项失败、6项通过，修复后与题库、管理员审核、客户端回归合计62项通过。
- HTTP替身验证同一端点、正文、凭据与OpenCode会话；验证尝试次数上限及仅重试当前分段。
- 生产mode=live，通过已登录前端连续上传三份单题Markdown：

| 任务 | HTTP | 终态 | 题目数 |
| --- | --- | --- | --- |
| UQJ_c81fdbb6422a44d7 | 201 | preview_ready | 1 |
| UQJ_ee53e027d5834975 | 201 | preview_ready | 1 |
| UQJ_3f1f6b179e2442f8 | 201 | preview_ready | 1 |

刷新后3条新记录均保留，最后一条可恢复预览，个人已激活题目仍为0；共8条历史。没有确认题目、修改考纲、删除资料或重建索引。

这三次真实调用未出现连接异常，服务端无重试日志；不能声称在生产重现了“自动重试后恢复”。该分支由离线故障注入验证。三次成功不代表代理长期稳定，也不替代教材PDF、MinerU、个人知识完整入库或考纲图像模型验收。

## 发布与回滚

仅发布上述服务文件，未更新前端或配置。生产重启前活跃工作流、个人题库处理任务、管理员队列均为0。

- 备份：`/srv/tiaozhanbei-releases/backups/upload-retry-20260909/question_workspace_service.py`。
- 新文件SHA256：`aa04bab7fc69d955f690fc798d9333e34aed0c6b213ef9f967f2b03fbc2ccf43`，本地与生产一致。
- 重启后service active，health保持live/formal/langgraph/mounted。
- 回滚须先确认空闲，停服后只恢复该备份文件，再启动和检查健康；不得覆盖runtime或其它源码。
