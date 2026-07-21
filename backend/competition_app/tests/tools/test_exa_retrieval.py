import pytest
from types import SimpleNamespace

from competition_app.tools.exa_retrieval import ExaVideoRetriever
from competition_app.runtime.event_stream import bind_event_sink, reset_event_sink


@pytest.mark.asyncio
async def test_exa_video_retriever_uses_async_sdk_and_reports_success() -> None:
    class FakeAsyncExa:
        def __init__(self) -> None:
            self.calls = []

        async def search(self, query, **kwargs):
            self.calls.append((query, kwargs))
            return SimpleNamespace(results=[SimpleNamespace(
                title="四君子汤教学",
                url="https://www.bilibili.com/video/BVdemo",
                score=0.83,
                highlights=["讲解组成、功效与配伍意义"],
                text=None,
                summary=None,
            )])

    client = FakeAsyncExa()
    retriever = ExaVideoRetriever("exa-test-key", client=client)
    events = []
    token = bind_event_sink(events.append)
    try:
        hits = await retriever.search_videos("四君子汤", limit=3)
    finally:
        reset_event_sink(token)

    assert len(hits) == 1
    assert hits[0].url.endswith("BVdemo")
    assert hits[0].score == 0.83
    assert "视频" in client.calls[0][0]
    assert client.calls[0][1]["include_domains"] == [
        "youtube.com", "www.youtube.com", "bilibili.com", "www.bilibili.com"
    ]
    assert next(item for item in events if item["event"] == "web_search_status") == {
        "event": "web_search_status",
        "provider": "exa",
        "resource_type": "video",
        "status": "success",
        "result_count": 1,
    }


@pytest.mark.asyncio
async def test_exa_video_retriever_degrades_to_empty_on_api_failure() -> None:
    class FailingAsyncExa:
        async def search(self, query, **kwargs):
            raise RuntimeError("authentication failed")

    retriever = ExaVideoRetriever("exa-test-key", client=FailingAsyncExa())
    events = []
    token = bind_event_sink(events.append)
    try:
        result = await retriever.search_videos("四君子汤")
    finally:
        reset_event_sink(token)

    assert result == []
    failure = next(item for item in events if item["event"] == "web_search_status")
    assert failure == {
        "event": "web_search_status",
        "provider": "exa",
        "resource_type": "video",
        "status": "failed",
        "result_count": 0,
        "error_type": "RuntimeError",
    }


@pytest.mark.asyncio
async def test_exa_retriever_supports_reference_and_question_resources() -> None:
    class FakeAsyncExa:
        async def search(self, query, **kwargs):
            return SimpleNamespace(results=[SimpleNamespace(
                title="参考资料", url="https://example.test/a", score=0.5,
                highlights=["摘要"], text=None, summary=None,
            )])

    retriever = ExaVideoRetriever("exa-test-key", client=FakeAsyncExa())

    references = await retriever.search_references("四君子汤")
    questions = await retriever.search_questions("四君子汤")

    assert references[0].resource_type == "reference"
    assert questions[0].resource_type == "question"