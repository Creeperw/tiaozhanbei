from datetime import datetime, timedelta, timezone
import json
from types import SimpleNamespace

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
import httpx
import pytest

from competition_app.evaluation.planning_mode_probe import PlanningModeProbe, register_planning_mode_probe
from competition_app.llm.openai_compatible import OpenAICompatibleChatModel


def client_model(handler):
    return OpenAICompatibleChatModel("https://offline.invalid", "secret", "deepseek-v4-flash", transport=httpx.MockTransport(handler))


@pytest.mark.asyncio
async def test_six_requests_change_only_response_format_and_store_no_prose(tmp_path):
    bodies, headers = [], []

    def handler(request):
        body = json.loads(request.content)
        bodies.append(body)
        headers.append({k: v for k, v in request.headers.items() if k != "content-length"})
        if len(bodies) == 3:
            return httpx.Response(400, json={"error": "secret provider body"})
        raw = json.dumps({"plan_document": "## 【最终目标】\nprivate-prose", "selected_path_candidate_id": None})
        event = {"choices": [{"delta": {"content": raw, "reasoning_content": "private-reasoning"}, "finish_reason": "stop"}], "usage": {"completion_tokens": 40}}
        return httpx.Response(200, text="data: " + json.dumps(event) + "\n\ndata: [DONE]\n\n")

    probe = PlanningModeProbe(client_model(handler), tmp_path, "live")
    probe.start()
    with pytest.raises(HTTPException) as error:
        probe.start()
    assert error.value.status_code == 409
    await probe.task
    assert probe.state["status"] == "completed"
    assert len(bodies) == 6
    assert [b["response_format"]["type"] for b in bodies] == ["json_schema", "json_object"] * 3
    common = [{k: v for k, v in b.items() if k != "response_format"} for b in bodies]
    assert all(b == common[0] for b in common)
    assert all(h == headers[0] for h in headers)
    assert probe.state["results"][2]["metadata"]["http_status"] == 400
    stored = probe.receipt.read_text()
    assert not any(s in stored for s in ("private-prose", "private-reasoning", "secret", "合成学习者", "SYNTHETIC_NO_ACCOUNT"))
    assert probe.receipt.stat().st_mode & 0o777 == 0o600
    second = PlanningModeProbe(probe.model, tmp_path, "live")
    with pytest.raises(HTTPException):
        second.start()


@pytest.mark.parametrize("user,expired,mode,expected", [(None, False, "live", 401), ("other", False, "live", 403), ("owner", True, "live", 410), ("owner", False, "stub", 409)])
def test_authorization_and_expiry(tmp_path, user, expired, mode, expected):
    root = tmp_path / "evaluation/planning-mode-probe"
    root.mkdir(parents=True)
    (root / "config.json").write_text(json.dumps({"owner_id": "owner", "expires_at": (datetime.now(timezone.utc) + timedelta(hours=-1 if expired else 1)).isoformat()}))
    app = FastAPI()
    register_planning_mode_probe(app, model=client_model(lambda _: httpx.Response(500)), runtime_root=tmp_path, mode=mode, current_user=lambda _: SimpleNamespace(user_id=user) if user else None)
    with TestClient(app) as client:
        for path in ("/internal-eval/planning-mode-probe", "/api/v1/internal-eval/planning-mode-probe"):
            assert client.get(path).status_code == expected
        assert client.post("/api/v1/internal-eval/planning-mode-probe").status_code == expected


def test_opt_in_and_cross_origin_protection(tmp_path):
    app = FastAPI()
    model = client_model(lambda _: httpx.Response(500))
    register_planning_mode_probe(app, model=model, runtime_root=tmp_path, mode="live", current_user=lambda _: SimpleNamespace(user_id="owner"))
    assert not any("planning-mode-probe" in r.path for r in app.routes)
    root = tmp_path / "evaluation/planning-mode-probe"
    root.mkdir(parents=True)
    (root / "config.json").write_text(json.dumps({"owner_id": "owner", "expires_at": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()}))
    register_planning_mode_probe(app, model=model, runtime_root=tmp_path, mode="live", current_user=lambda _: SimpleNamespace(user_id="owner"))
    with TestClient(app) as client:
        assert client.get("/internal-eval/planning-mode-probe").status_code == 200
        assert client.post("/api/v1/internal-eval/planning-mode-probe", headers={"Origin": "https://evil.invalid", "X-Planning-Mode-Probe": "execute"}).status_code == 403
    assert not (root / "started.lock").exists()