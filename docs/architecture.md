# 当前系统架构

## 1. 运行边界

正式系统由一个 `competition_app` FastAPI 进程统一提供 React 页面和 HTTP/SSE 接口：

```text
Browser
  └─ http://host:7860/
       ├─ React production frontend
       ├─ /api/v1/*        主应用、智能助教与稳定接口
       ├─ /api/*           平台业务后端兼容接口
       └─ /health

competition_app
  ├─ LangGraph 多智能体工作流
  ├─ 会话、计划、审核、复习和运行状态
  ├─ 外部资产统一解析
  └─ 装载 platform_backend/APP/backend

platform_backend
  ├─ 登录身份映射与业务数据库
  ├─ 题库、试卷、训练、教材和学习行为
  └─ 兼容业务路由
```

`backend/platform_backend` 是活动业务代码，不是交接数据。内部导入名暂时保留
`APP.backend.*`，以保护现有动态导入和测试。

## 2. 多智能体边界

六个产品智能体位于 `backend/competition_app/agents/`。本轮目录治理不改变其职责、提示词、
自然语言优先输出、Compiler、审核局部返修或上下文结构。

`platform_backend` 中仍有部分历史智能体命名模块被训练工坊和兼容路由调用。在解除真实依赖、
完成 OpenAPI 对比和在线回归前，不按文件名判定为废弃代码。

## 3. 数据边界

- 公共知识、题库、视频、教材和公共向量索引：只读资产。
- 数据库、用户上传、个人索引、日志、缓存和任务状态：可写运行数据。
- 代码目录在生产环境可以只读挂载。
- 两类数据的标准目录见 [`data-layout.md`](data-layout.md)。

## 4. 产品页面

正式产品页面只有 React 应用根入口 `/`。旧 `/chat/` 和 `/demo/` 静态界面已经删除；智能助教
由正式 React 页面中的全屏工作区和悬浮小窗提供。

## 5. 稳定接口与兼容接口

- 新功能优先加入 `/api/v1/*`。
- `/api/*` 由平台业务后端提供，现阶段仍是正式前端若干模块的运行依赖，不能整体删除。
- 接口是否可删除以调用方、OpenAPI、运行日志、测试和在线验证为依据，不以“legacy”命名为依据。
