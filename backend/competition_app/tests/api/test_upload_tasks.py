import asyncio

import pytest
from fastapi.testclient import TestClient

from competition_app.api.app import create_app
from competition_app.application.container import ApplicationContainer
from competition_app.config import Settings
from competition_app.services.textbook_import import TextbookImportError
from competition_app.services.upload_tasks import UploadTaskStore


@pytest.fixture
def container(tmp_path):
    return ApplicationContainer.build(Settings(mode="stub", runtime_root=tmp_path / "runtime"),
        snapshot_root=tmp_path / "snapshots", include_backend_handoff=False)


def register(client, name):
    response = client.post("/api/v1/auth/register", json={
        "username": name, "password": "test-password-2026",
    })
    assert response.status_code < 300


def submit(client):
    return client.post("/api/v1/textbooks/import", files={"file": ("test.pdf", b"%PDF-test", "application/pdf")})


def test_persistent_api_auth_and_duplicate_requests(container):
    calls = []
    async def import_pdf(**kwargs):
        calls.append(kwargs["owner_id"])
        kwargs["progress"]("extract", "正在解析")
        await asyncio.sleep(0)
        return {"book_id": "UTB_TEST"}
    container.textbook_import_service.import_pdf = import_pdf
    container.textbook_pdf_service.by_id = lambda *a: {"book_id": "UTB_TEST"}
    with TestClient(create_app(container)) as client:
        assert client.get("/api/v1/textbooks/import/TBI_" + "a" * 32).status_code == 401
        assert client.get("/api/v1/textbooks/imports").status_code == 401
        register(client, "upload-alice")
        response = submit(client)
        assert response.status_code == 202
        task_id = response.json()["task_id"]
        assert submit(client).json()["task_id"] == task_id
    assert len(calls) == 1
    # Same storage, fresh app: completed status is not process memory.
    with TestClient(create_app(container)) as client:
        client.post("/api/v1/auth/login", json={"username": "upload-alice", "password": "test-password-2026"})
        payload = client.get("/api/v1/textbooks/import/" + task_id).json()
        assert payload["status"] == "done" and payload["book"]["book_id"] == "UTB_TEST"
        assert "owner_id" not in payload and "fingerprint" not in payload
        assert len(client.get("/api/v1/textbooks/imports").json()["items"]) == 1
        register(client, "upload-bob")
        assert client.get("/api/v1/textbooks/import/" + task_id + "?owner_id=" + calls[0]).status_code == 404
        assert client.get("/api/v1/textbooks/imports").json()["items"] == []


def test_failure_sanitized_and_reupload_after_known_failure(container):
    calls = []
    async def import_pdf(**kwargs):
        calls.append(1)
        raise TextbookImportError("secret-token private-document")
    container.textbook_import_service.import_pdf = import_pdf
    with TestClient(create_app(container)) as client:
        register(client, "upload-fail")
        first = submit(client).json()["task_id"]
        # Drain the already-scheduled coroutine without wall-clock waits.
        client.portal.call(asyncio.sleep, 0)
        failed = client.get("/api/v1/textbooks/import/" + first).json()
        assert failed["status"] == "failed" and failed["retry_allowed"]
        assert "secret" not in str(failed) and "private" not in str(failed)
        second = submit(client).json()["task_id"]
        assert second != first
    assert len(calls) == 2


def test_existing_interrupted_task_remains_queryable(container):
    with TestClient(create_app(container)) as client:
        register(client, "upload-interrupted")
        # Obtain owner from the existing auth API rather than accepting it from a request.
        auth = client.get("/api/v1/auth/me").json()
        owner = auth.get("user", auth)["user_id"]
        store = UploadTaskStore(container.runtime_root)
        state, lease = store.start(owner, "f")
        lease.close()
        payload = client.get("/api/v1/textbooks/import/" + state["task_id"]).json()
        assert payload["status"] == "failed"
        assert payload["error"]["code"] == "UPLOAD_INTERRUPTED"