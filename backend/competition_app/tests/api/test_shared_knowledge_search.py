from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from competition_app.api.app import create_app
from competition_app.application.container import ApplicationContainer
from competition_app.config import Settings
from competition_app.contracts.knowledge import EvidenceItem, EvidencePack


@pytest.fixture
def search_client(tmp_path):
    container = ApplicationContainer.build(
        Settings(mode="stub"), snapshot_root=tmp_path, include_backend_handoff=False
    )
    tool = AsyncMock(return_value=EvidencePack(
        evidence_pack_id="EP_TEST", query="阴阳",
        evidence_items=[EvidenceItem(
            evidence_id="E_1", source_id="chunk-1", content_summary="教材原文",
            authority_level="textbook", confidence=0.9, bridge_layer="strict",
            resource_type="textbook", source_label="中医学基础",
        )],
    ))
    container.question_retrieval_tool = SimpleNamespace(get_kp_with_content=tool)
    with TestClient(create_app(container, auth_required=True)) as client:
        yield client, container, tool


def login(client):
    response = client.post("/api/v1/auth/register", json={
        "username": "search-reader", "display_name": "检索用户",
        "password": "test-search-password-2026",
    })
    assert response.status_code == 201
    return response.json()["user"]["user_id"]


def test_content_search_reuses_agent_tool_without_web(search_client):
    client, _, tool = search_client
    login(client)
    response = client.post("/api/v1/knowledge/content/search", json={"query": " 阴阳 ", "limit": 5})
    assert response.status_code == 200
    tool.assert_awaited_once_with("阴阳", limit=5, local_only=True)
    assert response.json()["scope"] == "public"
    assert response.json()["evidence_items"][0]["content_summary"] == "教材原文"


def test_content_search_requires_login(search_client):
    client, _, tool = search_client
    assert client.post("/api/v1/knowledge/content/search", json={"query": "阴阳"}).status_code == 401
    tool.assert_not_awaited()


@pytest.mark.parametrize("payload", [{"query": "  "}, {"query": "阴阳", "limit": 0}, {"query": "阴阳", "limit": 51}])
def test_content_search_rejects_invalid_input(search_client, payload):
    client, _, tool = search_client
    login(client)
    assert client.post("/api/v1/knowledge/content/search", json=payload).status_code == 422
    tool.assert_not_awaited()


def test_content_search_distinguishes_no_match_from_failure(search_client):
    client, _, tool = search_client
    login(client)
    tool.side_effect = LookupError("no matching knowledge point")
    response = client.post("/api/v1/knowledge/content/search", json={"query": "阴阳"})
    assert response.status_code == 200
    assert response.json()["evidence_items"] == []
    tool.side_effect = RuntimeError("private upstream configuration")
    response = client.post("/api/v1/knowledge/content/search", json={"query": "阴阳"})
    assert response.status_code == 503
    assert "private" not in response.text


def test_question_search_keeps_authenticated_owner_and_degradation(search_client):
    client, container, _ = search_client
    owner = login(client)
    result = SimpleNamespace(model_dump=lambda **kwargs: {
        "items": [], "vector_degraded": True, "embedding_model": "bm25-only",
    })
    search = AsyncMock(return_value=result)
    container.knowledge_backend = SimpleNamespace(search_questions=search)
    response = client.post("/api/v1/knowledge/questions/search", json={
        "query": "阴阳", "limit": 5, "scope": "all",
    })
    assert response.status_code == 200
    assert response.json()["vector_degraded"] is True
    search.assert_awaited_once_with("阴阳", [], 5, owner_id=owner, scope="all")