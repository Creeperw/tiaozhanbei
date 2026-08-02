from pathlib import Path

import pytest
from PIL import Image

from competition_app.services.user_syllabus import UserSyllabusService


class FakeResolver:
    def resolve_topic(self, query, limit=3):
        if "\u56db\u6c14\u4e94\u5473" in query:
            return [{"kp_id": "KP001", "name": "\u56db\u6c14\u4e94\u5473", "score": 0.95}]
        return []


class FakeSyllabusService(UserSyllabusService):
    def _render_input(self, source, output_dir):
        output_dir.mkdir(parents=True, exist_ok=True)
        page = output_dir / "page_0001.jpg"
        Image.new("RGB", (20, 20), "white").save(page)
        return [(1, page)]

    async def _extract_batch(self, pages):
        return {
            "document_title": "\u4e2d\u836f\u671f\u672b\u8003\u8bd5\u8003\u7eb2",
            "subject": "\u4e2d\u836f",
            "exam_type": "\u671f\u672b\u8003\u8bd5",
            "sections": [{"title": "\u4e2d\u836f\u603b\u8bba", "requirements": [
                {"title": "\u638c\u63e1\u56db\u6c14\u4e94\u5473", "mastery_level": "\u638c\u63e1", "source_pages": [1], "confidence": 0.98},
                {"title": "\u81ea\u5b9a\u4e49\u672a\u5339\u914d\u8981\u6c42", "mastery_level": "\u4e86\u89e3", "source_pages": [1], "confidence": 0.9},
            ]}],
        }


@pytest.mark.asyncio
async def test_user_syllabus_is_personal_and_only_one_active(tmp_path: Path):
    service = FakeSyllabusService(tmp_path, chat_base_url="http://model", chat_model="kimi-k2.6", chat_api_key="key", knowledge_resolver=FakeResolver())
    first = await service.import_file("alice", "outline.txt", b"anything")
    second = await service.import_file("alice", "outline.txt", b"anything", title="second")
    other = await service.import_file("bob", "outline.txt", b"anything")

    alice_items = service.list("alice")
    assert len(alice_items) == 2
    assert sum(bool(item["is_active"]) for item in alice_items) == 1
    assert next(item for item in alice_items if item["syllabus_id"] == second["manifest"]["syllabus_id"])["is_active"]
    assert service.list("bob")[0]["owner_user_id"] == "bob"
    assert service.mappings("alice", second["manifest"]["syllabus_id"])[0]["match_status"] == "matched"
    assert service.mappings("alice", second["manifest"]["syllabus_id"])[1]["match_status"] == "unmatched"
    assert service.load_context("alice", "\u6211\u60f3\u7ec3\u4e2d\u836f\u671f\u672b\u8003\u8bd5\u9898")["user_syllabus"]["syllabus_id"] == second["manifest"]["syllabus_id"]
    assert service.load_context("alice", "\u666e\u901a\u95f2\u804a") == {}
    assert service.get("bob", other["manifest"]["syllabus_id"])["manifest"]["owner_user_id"] == "bob"
