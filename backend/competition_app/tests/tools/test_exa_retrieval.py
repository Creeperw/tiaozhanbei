import pytest
import asyncio
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
    event = next(item for item in events if item["event"] == "web_search_status")
    assert event == {
        "event": "web_search_status",
        "provider": "exa",
        "resource_type": "video",
        "status": "success",
        "result_count": 1,
        "ts": event["ts"],
    }
    assert isinstance(event["ts"], int)


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
        "ts": failure["ts"],
    }
    assert isinstance(failure["ts"], int)


@pytest.mark.asyncio
async def test_exa_retriever_times_out_and_degrades_to_empty() -> None:
    class HangingAsyncExa:
        async def search(self, query, **kwargs):
            await asyncio.Event().wait()

    retriever = ExaVideoRetriever(
        "exa-test-key",
        client=HangingAsyncExa(),
        timeout_seconds=0.01,
    )
    events = []
    token = bind_event_sink(events.append)
    try:
        result = await retriever.search_web("四君子汤")
    finally:
        reset_event_sink(token)

    assert result == []
    failure = next(item for item in events if item["event"] == "web_search_status")
    assert failure["status"] == "failed"
    assert failure["error_type"] == "TimeoutError"


@pytest.mark.asyncio
async def test_exa_retriever_creates_sdk_coroutine_only_after_acquiring_limit() -> None:
    class BlockingAsyncExa:
        def __init__(self) -> None:
            self.created_queries: list[str] = []
            self.first_started = asyncio.Event()
            self.release_first = asyncio.Event()

        def search(self, query, **kwargs):
            self.created_queries.append(query)

            async def run():
                if len(self.created_queries) == 1:
                    self.first_started.set()
                    await self.release_first.wait()
                return SimpleNamespace(results=[])

            return run()

    client = BlockingAsyncExa()
    retriever = ExaVideoRetriever(
        "exa-test-key",
        client=client,
        max_concurrency=1,
    )
    first = asyncio.create_task(retriever.search_web("第一条查询"))
    await client.first_started.wait()
    second = asyncio.create_task(retriever.search_web("第二条查询"))
    await asyncio.sleep(0)

    assert len(client.created_queries) == 1

    client.release_first.set()
    await asyncio.gather(first, second)
    assert len(client.created_queries) == 2


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


@pytest.mark.asyncio
async def test_exa_question_route_uses_full_page_text_for_answers() -> None:
    """question 路必须用整页全文（text）而非 highlights 片段，否则题库页答案被截断。"""
    class FakeAsyncExa:
        def __init__(self) -> None:
            self.calls: list[tuple[str, dict]] = []

        async def search(self, query, **kwargs):
            self.calls.append((query, kwargs))
            return SimpleNamespace(results=[SimpleNamespace(
                title="题库原题",
                url="https://example.test/q/1",
                score=0.5,
                highlights=[],
                text="关于meta分析，下列说法正确的是 A. ... B. ... ## 正确答案 D",
                summary=None,
            )])

    client = FakeAsyncExa()
    retriever = ExaVideoRetriever("exa-test-key", client=client)
    questions = await retriever.search_questions("关于meta分析，下列说法正确的是")

    assert questions[0].resource_type == "question"
    assert questions[0].summary == "关于meta分析，下列说法正确的是 A. ... B. ... ## 正确答案 D"
    # question 路必须请求整页全文 text 而非 highlights 片段
    contents = client.calls[0][1]["contents"]
    assert "text" in contents and contents["text"]["maxCharacters"] == 3000
    assert "highlights" not in contents


@pytest.mark.asyncio
async def test_exa_video_route_keeps_highlights_snippet() -> None:
    """非 question 路保持 highlights 片段模式，控制 token 成本。"""
    class FakeAsyncExa:
        def __init__(self) -> None:
            self.calls: list[tuple[str, dict]] = []

        async def search(self, query, **kwargs):
            self.calls.append((query, kwargs))
            return SimpleNamespace(results=[SimpleNamespace(
                title="视频", url="https://example.test/v", score=0.5,
                highlights=["讲解组成、功效与配伍意义"], text=None, summary=None,
            )])

    client = FakeAsyncExa()
    retriever = ExaVideoRetriever("exa-test-key", client=client)
    await retriever.search_videos("四君子汤")

    contents = client.calls[0][1]["contents"]
    assert "highlights" in contents and contents["highlights"]["max_characters"] == 500
    assert "text" not in contents


@pytest.mark.asyncio
async def test_exa_livecrawl_forces_fresh_fetch_and_truncates_text() -> None:
    """livecrawl 必须绕过缓存（max_age_hours=0）强制实时抓取，并截断整页文本。"""
    class FakeAsyncExa:
        def __init__(self) -> None:
            self.calls: list[tuple[list, dict]] = []

        async def get_contents(self, urls, **kwargs):
            self.calls.append((urls, kwargs))
            return SimpleNamespace(results=[SimpleNamespace(
                title="题库原题（实时版）",
                url="https://example.test/q/1",
                text="关于meta分析，下列说法正确的是" + "答案" * 2000,
                summary=None,
            )])

    client = FakeAsyncExa()
    retriever = ExaVideoRetriever("exa-test-key", client=client)
    hits = await retriever.livecrawl_urls(
        ["https://example.test/q/1"], max_chars=500
    )

    assert len(hits) == 1
    assert hits[0].resource_type == "web"
    assert len(hits[0].summary) == 500  # 工具层截断
    urls, options = client.calls[0]
    assert urls == ["https://example.test/q/1"]
    assert options["text"] is True  # text 必须传 True（dict 形式 livecrawl 返回空）
    assert options["max_age_hours"] == 0  # 绕过缓存强制实时


@pytest.mark.asyncio
async def test_exa_livecrawl_degrades_to_empty_on_api_failure() -> None:
    class FailingAsyncExa:
        async def get_contents(self, urls, **kwargs):
            raise RuntimeError("livecrawl failed")

    retriever = ExaVideoRetriever("exa-test-key", client=FailingAsyncExa())
    hits = await retriever.livecrawl_urls(["https://example.test/q/1"])

    assert hits == []


@pytest.mark.asyncio
async def test_exa_livecrawl_ignores_blank_urls() -> None:
    # 空 URL 列表应直接返回空，不发起任何调用（无 client 也不会炸）
    retriever = ExaVideoRetriever("exa-test-key", client=None)
    hits = await retriever.livecrawl_urls(["", "   "])
    assert hits == []

@pytest.mark.asyncio
async def test_exa_retriever_supports_current_web_fact_search() -> None:
    class FakeAsyncExa:
        async def search(self, query, **kwargs):
            return {
                "results": [
                    {
                        "title": "官方考试时间",
                        "url": "https://example.test/exam",
                        "highlights": ["考试时间以官方公告为准"],
                        "score": 0.91,
                    }
                ]
            }

    retriever = ExaVideoRetriever("exa-test-key", client=FakeAsyncExa())
    hits = await retriever.search_web("距离下次执业医师资格考试还有多久")

    assert hits[0].resource_type == "web"
    assert hits[0].url.endswith("/exam")
