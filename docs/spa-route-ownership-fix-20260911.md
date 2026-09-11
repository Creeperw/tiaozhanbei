# SPA 页面路径与业务路由命名空间归属修复（2026-09-11）

## 问题

「个人数据 → 学习画像」页面在浏览器刷新后无法加载：地址栏保留
`/personalization/profile`，但服务器返回 JSON，浏览器把画像数据当作文本渲染，
React 不启动，整个界面消失（`#root` 不存在）。从站内点击进入看似正常，因为前端
只做 `pushState` 不重新请求 HTML。

未登录访问同一链接返回 `401 {"code":"unauthorized","detail":"Could not validate
credentials"}`。

只有「学习画像」一个页面受影响；`/personalization/review`、`/personalization/memory`
等其余 23 个页面路径均正常。

## 根因

前端页面 URL 与业务应用接口 URL 命名撞车，SPA 兜底逻辑把页面路径让给了业务路由。

1. 前端把学习画像的页面地址序列化为 `/personalization/profile`
   （`frontend/llm/src/appShell.js` 菜单意图 `view: 'user-profile'`；
   `frontend/llm/src/urlRouting.js` `'user-profile': 'profile'` 与
   `intentToPath` 拼接；`App.jsx` 在导航时 `window.history.pushState`）。
2. 业务应用在同一路径注册了真实接口
   （`backend/platform_backend/APP/backend/routers/personalization_routes.py`，
   `APIRouter(prefix="/personalization")` + `@router.get("/profile")`）。
3. `backend/competition_app/api/app.py` 的 SPA catch-all
   `spa_frontend_fallback`（`@app.get("/{full_path:path}")`）在返回
   `index.html` 之前，先遍历业务应用路由，命中 `Match.FULL` 就整体委托。
   `/personalization/profile` 命中业务路由 → 返回 JSON；
   `/personalization/review` 无对应业务路由 → 返回 `index.html`。
4. 同一份「页面路径」知识被维护了两遍且方向相反：认证中间件的
   `spa_page_paths`（正向白名单，含 `personalization`）与 catch-all 的
   `_SPA_NON_PAGE_PREFIXES`（反向黑名单，不含 `personalization`），二者互不
   知情，是该缺陷的直接温床。

已登录时业务应用鉴权依赖从中间件注入的 `request.state.current_user` 解析到
宿主用户并通过，因此返回 200 JSON；未登录时抛 401。

## 修复

按「根路径归 SPA 页面所有，业务 API 一律走 `/api` 前缀」的既有约定收敛：

- 在 `backend/competition_app/api/app.py` 提取模块级 `SPA_PAGE_PREFIXES` 作为
  页面路径的唯一事实来源，认证中间件与 catch-all 共用，删除中间件内的局部副本。
- `spa_frontend_fallback` 中把页面前缀判定提到业务路由委托之前：命中页面前缀
  直接返回 `index.html`，不再委托业务应用。

`/api` 前缀的挂载点注册在 catch-all 之前，业务接口契约与调用方完全不受影响；
非页面的 legacy 直连路由（如 `/training/practice/grade`）仍按原逻辑委托。

## 验证

- 新增回归测试 `backend/competition_app/tests/api/test_spa_route_ownership.py`：
  用带冲突路由的伪业务运行时验证页面前缀返回 `index.html`、非页面 legacy 路由
  仍委托、`/api` 前缀未被页面规则吞掉。3 项通过。
- 负向对照：把 `SPA_PAGE_PREFIXES` 置空（等价于修复前行为）后，
  `/personalization/profile` 返回 `200 application/json`，确认测试确实覆盖该缺陷。
- 定向回归：`competition_app/tests` 共 2328 通过、4 跳过；12 项失败全部是同一
  预存环境原因（本机缺少 `assets/knowledge/releases/2026-07-18/component/`
  知识库交接包），与本次改动无关。
- 发布验证：24 个页面路径全部返回 `200 text/html`；`/api/personalization/*`、
  `/api/knowledge/atlas/*`、`/api/v1/dashboard/home` 等 8 个接口返回 200；
  浏览器实测 `/personalization/profile` 直接打开与刷新均正常加载 SPA，
  学习画像标签页正确激活，控制台与网络无 4xx/5xx。
- 部署后 `/health` 返回 `mode=live`、`status=ok`。

## 部署记录

- 目标：`/srv/tiaozhanbei-releases/20260905-live/backend/competition_app/api/app.py`
- 备份：`/srv/tiaozhanbei-releases/backups/app.py.before-spa-route-fix-20260911-010136`
- 部署后文件 SHA256：`d588bee58c08665d39136d8c7f684f9e1bc669b19a200730df4f22942a5e83d9`
- 服务：`systemctl restart tiaozhanbei.service`，启动完成无新增错误。
  日志中 `active question index manifest is unavailable` 为既有告警，重启前
  历史日志已出现 9 次，与本次改动无关。

## 未处理事项

前端 slug 仍为 `profile`（`urlRouting.js`）。修复后该 URL 已能正确返回页面，
但「页面路径长得像资源路径」的语义问题仍在，后续若调整需同步
`urlRouting.test.js` 的断言。本次未改动，以免引入无收益的 URL 迁移。
