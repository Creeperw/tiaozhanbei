"""根路径命名空间归属：SPA 页面优先于业务路由。

约定：根路径归 SPA 页面所有，业务 API 一律走 `/api` 前缀。

回归背景：业务应用里存在与页面同名的路由（例如
`/personalization/profile`，来自平台应用 `personalization_routes.py`）。
若 SPA catch-all 先按业务路由委托，则该页面 URL 会返回 JSON —— 从站内点击
进入时前端不重新请求 HTML 所以看似正常，但刷新或打开收藏链接时整个界面
渲染成裸 JSON。
"""

from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from competition_app.api.app import SPA_PAGE_PREFIXES, create_app
from competition_app.application.container import ApplicationContainer
from competition_app.config import Settings

INDEX_HTML = "<!doctype html><html><body><div id=\"root\"></div></body></html>"


class _FakeBusinessRuntime:
    """模拟业务应用运行时：在页面路径与 legacy 路径下各注册一条 JSON 路由。"""

    def __init__(self) -> None:
        self.app = FastAPI()
        for prefix in SPA_PAGE_PREFIXES:
            self.app.add_api_route(
                f"{prefix}/profile",
                self._profile_payload,
                methods=["GET"],
            )
        # 与页面前缀无关的 legacy 直连路由，必须继续走业务应用。
        self.app.add_api_route(
            "/training/practice/grade",
            self._legacy_payload,
            methods=["GET"],
        )

    async def _profile_payload(self) -> dict:
        return {"display_name": "", "constitution": "低（low）"}

    async def _legacy_payload(self) -> dict:
        return {"legacy": True}

    async def startup(self) -> None:
        return None

    async def shutdown(self) -> None:
        return None


def _build_client(tmp_path: Path) -> TestClient:
    frontend_root = tmp_path / "frontend"
    frontend_root.mkdir()
    (frontend_root / "index.html").write_text(INDEX_HTML, encoding="utf-8")

    container = ApplicationContainer.build(
        Settings(mode="stub"), snapshot_root=tmp_path / "snapshot"
    )
    container.frontend_dist_root = frontend_root
    container.backend_handoff_runtime = _FakeBusinessRuntime()
    return TestClient(create_app(container, auth_required=False))


def test_page_prefix_returns_index_html_instead_of_conflicting_business_route(
    tmp_path: Path,
) -> None:
    client = _build_client(tmp_path)

    for prefix in SPA_PAGE_PREFIXES:
        response = client.get(f"{prefix}/profile")

        assert response.status_code == 200, prefix
        assert response.headers["content-type"].startswith("text/html"), prefix
        assert "id=\"root\"" in response.text, prefix


def test_non_page_business_route_still_delegates(tmp_path: Path) -> None:
    client = _build_client(tmp_path)

    response = client.get("/training/practice/grade")

    assert response.status_code == 200
    assert response.json() == {"legacy": True}


def test_api_prefix_is_not_swallowed_by_page_rules(tmp_path: Path) -> None:
    client = _build_client(tmp_path)

    response = client.get("/api/personalization/profile")

    assert response.status_code == 200
    assert response.json()["constitution"] == "低（low）"
