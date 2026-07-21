from dataclasses import dataclass

import pytest

from competition_app.services.workshop import WorkshopKnowledgeService


class FakeMap:
    def __init__(self, *, videos=None, questions=None):
        self.videos = videos or []
        self.questions = questions or []

    def detail(self, kp_id, question_limit=10):
        return {
            "kp": {"kp_id": kp_id, "kp_lv3": "四君子汤"},
            "chunks": [{"chunk_uid": "CH_1", "retrieval_text": "四君子汤益气健脾。"}],
            "videos": self.videos,
            "questions": self.questions[:question_limit],
        }


@dataclass
class Hit:
    source_id: str
    title: str
    summary: str
    url: str
    score: float
    resource_type: str


class FakeRetrieval:
    def __init__(self):
        self.video_calls = 0
        self.question_calls = 0

    async def search_video_resources(self, query, limit=5):
        self.video_calls += 1
        return [Hit("WEB_V_1", "视频", "讲解", "https://video.example/1", 0.8, "video")]

    async def search_question_resources(self, query, limit=5):
        self.question_calls += 1
        return [Hit("WEB_Q_1", "题目", "练习题", "https://question.example/1", 0.7, "question")]


class FakeBackend:
    def __init__(self, mapping):
        self.map = mapping


@pytest.mark.asyncio
async def test_resource_bundle_uses_web_only_for_missing_video_and_questions():
    retrieval = FakeRetrieval()
    bundle = await WorkshopKnowledgeService(
        FakeBackend(FakeMap()), retrieval
    ).resolve("KP_1")

    assert bundle.knowledge_point["title"] == "四君子汤"
    assert bundle.textbook_slices[0]["chunk_uid"] == "CH_1"
    assert bundle.videos[0]["origin"] == "web_search"
    assert bundle.questions[0]["origin"] == "web_search"
    assert bundle.coverage.fallback_used == ["video", "question"]
    assert retrieval.video_calls == 1
    assert retrieval.question_calls == 1


@pytest.mark.asyncio
async def test_resource_bundle_keeps_local_resources_without_web_search():
    retrieval = FakeRetrieval()
    mapping = FakeMap(
        videos=[{"bvid": "BV1", "page": 1, "start_seconds": 12, "video_title": "本地视频"}],
        questions=[{"question_id": "Q1", "stem": "功效是什么？", "answer": "益气健脾"}],
    )
    bundle = await WorkshopKnowledgeService(FakeBackend(mapping), retrieval).resolve("KP_1")

    assert bundle.videos[0]["origin"] == "knowledge_repository"
    assert bundle.questions[0]["origin"] == "knowledge_repository"
    assert bundle.coverage.fallback_used == []
    assert retrieval.video_calls == 0
    assert retrieval.question_calls == 0
