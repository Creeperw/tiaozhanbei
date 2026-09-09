import json

import pytest

from competition_app.contracts.upload import upload_progress
from competition_app.services.user_syllabus import UserSyllabusService


@pytest.mark.parametrize("kind,status,expected", [
    ("textbook", "running", "running"), ("textbook", "done", "succeeded"),
    ("textbook", "failed", "failed"), ("syllabus", "processing", "running"),
    ("syllabus", "success", "succeeded"), ("syllabus", "failed", "failed"),
    ("personal_questions", "preview_ready", "needs_review"),
    ("personal_questions", "needs_human_review", "needs_review"),
    ("personal_questions", "processing", "running"),
    ("personal_questions", "failed", "failed"),
    ("admin_questions", "queued", "queued"), ("admin_questions", "running", "running"),
    ("admin_questions", "completed", "succeeded"), ("admin_questions", "failed", "failed"),
    ("personal_questions", "unrecognized", "unknown"),
])
def test_fixed_state_projection(kind, status, expected):
    result = upload_progress(kind, "task", status)
    assert result["state"] == expected and result["source_status"] == status
    assert "percent" not in result


def test_syllabus_progress_is_read_only_and_owner_scoped(tmp_path):
    service = UserSyllabusService(tmp_path, chat_base_url="https://example.test/v1",
                                  chat_model="test", chat_api_key="fake")
    root = service.root / "alice" / "USY_test1234"
    root.mkdir(parents=True)
    path = root / "manifest.json"
    path.write_text(json.dumps({"syllabus_id": "USY_test1234", "processing_status": "success",
                                "is_active": True}))
    before = path.read_bytes()
    assert service.list("alice")[0]["progress"]["state"] == "succeeded"
    assert service.get("alice", "USY_test1234")["manifest"]["progress"]["state"] == "succeeded"
    assert service.list("bob") == []
    assert path.read_bytes() == before