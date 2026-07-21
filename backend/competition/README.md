# 外部交付与运行资产

本目录只提交可审查的后端交接代码 `backend-handoff-20260720/`。

以下大体积资产不进入 Git，请从团队共享盘获取，并优先通过绝对路径环境变量挂载：

| 资产 | 环境变量 |
|---|---|
| FAISS 题库/教材索引 `vdb_store` | `QUESTION_VECTOR_STORE_ROOT`、`KNOWLEDGE_VECTOR_STORE_ROOT` |
| 视频知识库交付包 | `KNOWLEDGE_HANDOFF_ROOT` |
| 知识库可写 runtime | `KNOWLEDGE_RUNTIME_ROOT` |

启用交接业务接口时设置：

```bash
export BACKEND_HANDOFF_ENABLED=true
export BACKEND_HANDOFF_ROOT="$PWD/competition/backend-handoff-20260720"
```

本地只做主框架和前端接口联调时保持 `BACKEND_HANDOFF_ENABLED=false`。
