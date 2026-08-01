from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from competition_app.api.app import create_app
from competition_app.application.container import ApplicationContainer
from competition_app.config import Settings


@pytest.fixture
def stub_container(tmp_path: Path) -> ApplicationContainer:
    return ApplicationContainer.build(
        Settings(mode="stub"),
        snapshot_root=tmp_path,
        include_backend_handoff=False,
    )


def test_textbook_pdf_ai_returns_503_when_service_disabled(
    stub_container, tmp_path: Path
) -> None:
    stub_container.textbook_pdf_ai_service = None
    stub_container.textbook_pdf_service.by_id = lambda book_id, owner_id=None: (
        {"book_id": book_id, "available": True}
    )
    with TestClient(create_app(stub_container, auth_required=True)) as client:
        client.post(
            "/api/v1/auth/register",
            json={"username": "ai-user", "password": "correct-horse-2026"},
        )
        response = client.post(
            "/api/v1/textbooks/pdfs/TB_MISSING/pages/1/ai",
            json={"mode": "summary"},
        )
    assert response.status_code == 503


def test_textbook_pdf_ai_returns_404_for_unknown_book(stub_container, tmp_path) -> None:
    ai_service = SimpleNamespace(
        page_text=lambda *args, **kwargs: "",
        summarize=lambda *args, **kwargs: None,
        chat=lambda *args, **kwargs: None,
    )
    stub_container.textbook_pdf_ai_service = ai_service
    with TestClient(create_app(stub_container, auth_required=True)) as client:
        client.post(
            "/api/v1/auth/register",
            json={"username": "ai-user-2", "password": "correct-horse-2026"},
        )
        response = client.post(
            "/api/v1/textbooks/pdfs/TB_MISSING/pages/1/ai",
            json={"mode": "summary"},
        )
    assert response.status_code == 404
    assert response.json()["detail"] == "教材不存在"


def test_textbook_pdf_ai_streams_summary_deltas_and_done(
    stub_container, tmp_path: Path
) -> None:
    async def fake_summarize(book_id, page_number, owner_id, page_span=0, on_delta=None):
        for piece in ("阴", "阳", "理论"):
            on_delta(piece)

    stub_container.textbook_pdf_ai_service = SimpleNamespace(
        summarize=fake_summarize,
        chat=None,
    )
    # 让 by_id 返回可用教材，否则 404
    stub_container.textbook_pdf_service.by_id = lambda book_id, owner_id=None: (
        {"book_id": book_id, "available": True}
    )

    with TestClient(create_app(stub_container, auth_required=True)) as client:
        client.post(
            "/api/v1/auth/register",
            json={"username": "ai-user-3", "password": "correct-horse-2026"},
        )
        response = client.post(
            "/api/v1/textbooks/pdfs/TB_1/pages/3/ai",
            json={"mode": "summary", "page_span": 1},
        )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    frames = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]
    events = [frame["event"] for frame in frames]
    assert events == ["delta", "delta", "delta", "done"]
    assert "".join(frame["text"] for frame in frames if frame["event"] == "delta") == "阴阳理论"


def test_textbook_pdf_ai_streams_error_frame_on_empty_page(
    stub_container, tmp_path: Path
) -> None:
    async def fake_summarize(book_id, page_number, owner_id, page_span=0, on_delta=None):
        raise ValueError("本页暂无可识别的文字内容，可能是扫描版教材")

    stub_container.textbook_pdf_ai_service = SimpleNamespace(
        summarize=fake_summarize,
        chat=None,
    )
    stub_container.textbook_pdf_service.by_id = lambda book_id, owner_id=None: (
        {"book_id": book_id, "available": True}
    )

    with TestClient(create_app(stub_container, auth_required=True)) as client:
        client.post(
            "/api/v1/auth/register",
            json={"username": "ai-user-4", "password": "correct-horse-2026"},
        )
        response = client.post(
            "/api/v1/textbooks/pdfs/TB_1/pages/3/ai",
            json={"mode": "summary"},
        )

    frames = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]
    assert frames[0]["event"] == "error"
    assert "扫描版" in frames[0]["message"]
    assert frames[-1]["event"] == "done"


def test_textbook_pdf_ai_chat_requires_question(
    stub_container, tmp_path: Path
) -> None:
    stub_container.textbook_pdf_ai_service = SimpleNamespace(
        summarize=None,
        chat=None,
    )
    stub_container.textbook_pdf_service.by_id = lambda book_id, owner_id=None: (
        {"book_id": book_id, "available": True}
    )

    with TestClient(create_app(stub_container, auth_required=True)) as client:
        client.post(
            "/api/v1/auth/register",
            json={"username": "ai-user-5", "password": "correct-horse-2026"},
        )
        response = client.post(
            "/api/v1/textbooks/pdfs/TB_1/pages/3/ai",
            json={"mode": "chat", "question": "  "},
        )

    frames = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]
    assert frames[0]["event"] == "error"
    assert frames[0]["message"] == "请输入问题"


def test_textbook_pdf_ai_chat_passes_history_and_streams(
    stub_container, tmp_path: Path
) -> None:
    captured = {}

    async def fake_chat(
        book_id, page_number, question, history, owner_id, page_span=0, on_delta=None
    ):
        captured["question"] = question
        captured["history"] = history
        on_delta("答")

    stub_container.textbook_pdf_ai_service = SimpleNamespace(
        summarize=None,
        chat=fake_chat,
    )
    stub_container.textbook_pdf_service.by_id = lambda book_id, owner_id=None: (
        {"book_id": book_id, "available": True}
    )

    with TestClient(create_app(stub_container, auth_required=True)) as client:
        client.post(
            "/api/v1/auth/register",
            json={"username": "ai-user-6", "password": "correct-horse-2026"},
        )
        response = client.post(
            "/api/v1/textbooks/pdfs/TB_1/pages/3/ai",
            json={
                "mode": "chat",
                "question": "什么是阴阳？",
                "history": [{"role": "user", "content": "上个问题"}],
                "page_span": 2,
            },
        )

    assert response.status_code == 200
    assert captured["question"] == "什么是阴阳？"
    assert captured["history"] == [{"role": "user", "content": "上个问题"}]
    frames = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]
    assert frames[0]["event"] == "delta"
    assert frames[0]["text"] == "答"


def test_textbook_ai_session_crud_roundtrip(stub_container, tmp_path: Path) -> None:
    from competition_app.repositories.runtime import InMemoryConversationRepository

    stub_container.textbook_pdf_ai_service = SimpleNamespace(
        summarize=None,
        chat=None,
        conversation_repository=InMemoryConversationRepository(),
    )
    svc = stub_container.textbook_pdf_ai_service
    svc.create_session = lambda learner_id, title="新对话": (
        svc.conversation_repository.create_session("textbook-ai-test1", learner_id, title)
        or "textbook-ai-test1"
    )
    svc.list_sessions = lambda learner_id: svc.conversation_repository.list_sessions(learner_id)
    svc.get_messages = lambda session_id, learner_id: svc.conversation_repository.get_messages(session_id, learner_id)
    svc.save_messages = lambda session_id, learner_id, messages: svc.conversation_repository.save_messages(session_id, learner_id, messages)
    svc.rename_session = lambda session_id, learner_id, title: svc.conversation_repository.rename_session(session_id, learner_id, title)
    svc.delete_session = lambda session_id, learner_id: svc.conversation_repository.delete_session(session_id, learner_id)

    with TestClient(create_app(stub_container, auth_required=True)) as client:
        client.post(
            "/api/v1/auth/register",
            json={"username": "ai-session", "password": "correct-horse-2026"},
        )
        created = client.post("/api/v1/textbooks/ai/sessions", json={"title": "教材问答"})
        assert created.status_code == 201
        session_id = created.json()["session_id"]
        assert session_id.startswith("textbook-ai-")

        listed = client.get("/api/v1/textbooks/ai/sessions")
        assert listed.status_code == 200
        assert listed.json()["sessions"][0]["id"] == session_id

        messages = client.get(f"/api/v1/textbooks/ai/sessions/{session_id}/messages")
        assert messages.status_code == 200
        assert messages.json()["messages"] == []

        renamed = client.patch(
            f"/api/v1/textbooks/ai/sessions/{session_id}", json={"title": "改标题"}
        )
        assert renamed.status_code == 200

        deleted = client.delete(f"/api/v1/textbooks/ai/sessions/{session_id}")
        assert deleted.status_code == 200
        assert client.get(f"/api/v1/textbooks/ai/sessions/{session_id}/messages").status_code == 404
