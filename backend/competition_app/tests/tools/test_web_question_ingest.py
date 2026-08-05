"""WebQuestionIngestService：网络搜索 → 清洗 → 去重 → 入库 的单元测试。"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from competition_app.runtime.event_stream import bind_event_sink, reset_event_sink
from competition_app.tools.knowledge_delivery import (
    DeliveryKnowledgeMapStore,
    KnowledgeDeliveryPaths,
)
from competition_app.tools.web_question_ingest import WebQuestionIngestService


class FakeSearcher:
    def __init__(self, hits: list[SimpleNamespace] | None = None) -> None:
        self.hits = hits or []
        self.queries: list[str] = []

    async def search_questions(self, query: str, limit: int = 5):
        self.queries.append((query, limit))
        return list(self.hits)


class FakeCleaner:
    def __init__(self, rows: list[dict] | None = None, error: Exception | None = None) -> None:
        self.rows = rows or []
        self.error = error
        self.calls: list[tuple] = []

    def extract(self, markdown, source_ref, source_type, owner_id):
        self.calls.append((markdown, source_ref, source_type, owner_id))
        if self.error is not None:
            raise self.error
        return [dict(row) for row in self.rows]


def _make_store(tmp_path: Path) -> DeliveryKnowledgeMapStore:
    paths = KnowledgeDeliveryPaths(
        component_root=tmp_path / "component",
        public_data=tmp_path / "public",
        video_results=tmp_path / "videos",
        runtime_root=tmp_path / "runtime",
        public_vector_store=tmp_path / "vdb",
    )
    store = DeliveryKnowledgeMapStore(paths)
    # 公共题库：一个 kp 只有 1 道题（低于默认 3 题门槛），另一个有 3 道。
    bank = [
        {
            "kp_ids": ["KP_PUBLIC_ONE"],
            "question_content": "公共题库唯一题：下列哪项属于四君子汤的组成？",
            "answer": "人参",
        },
        {
            "kp_ids": ["KP_PUBLIC_FULL"],
            "question_content": "公共题库第一题",
            "answer": "A",
        },
        {
            "kp_ids": ["KP_PUBLIC_FULL"],
            "question_content": "公共题库第二题",
            "answer": "B",
        },
        {
            "kp_ids": ["KP_PUBLIC_FULL"],
            "question_content": "公共题库第三题",
            "answer": "C",
        },
    ]
    bank_path = tmp_path / "public" / "01_question_bank"
    bank_path.mkdir(parents=True, exist_ok=True)
    (bank_path / "formatted_questions.json").write_text(
        __import__("json").dumps(bank, ensure_ascii=False), encoding="utf-8"
    )
    return store


def _hit(title: str, summary: str, url: str) -> SimpleNamespace:
    return SimpleNamespace(title=title, summary=summary, url=url)


@pytest.mark.asyncio
async def test_backfill_searches_cleans_and_persists_web_questions(
    tmp_path: Path,
) -> None:
    store = _make_store(tmp_path)
    searcher = FakeSearcher(
        [
            _hit(
                "四君子汤练习题",
                "1. 四君子汤的组成不包括：A 人参 B 白术 C 茯苓 D 甘草 E 附子",
                "https://example.com/tcm/quiz-a",
            ),
            _hit(
                "四君子汤方解",
                "2. 四君子汤的功效是：A 益气健脾（答案）",
                "https://example.com/tcm/quiz-b",
            ),
        ]
    )
    cleaner = FakeCleaner(
        rows=[
            {
                "question_type": "单选题",
                "stem": "四君子汤的组成不包括？",
                "options": ["人参", "白术", "茯苓", "附子"],
                "answer": "D",
                "analysis": "四君子汤由人参、白术、茯苓、甘草组成。",
            },
            {
                "question_type": "单选题",
                "stem": "四君子汤的功效是？",
                "options": ["益气健脾", "滋阴补肾"],
                "answer": "A",
                "analysis": "四君子汤益气健脾。",
            },
            # 无题干的脏数据应被跳过
            {"question_type": "单选题", "stem": "", "answer": "A"},
        ]
    )
    service = WebQuestionIngestService(
        searcher=searcher,
        cleaner=cleaner,
        store=store,
        runtime_dir=store.paths.question_runtime,
    )
    events: list[dict] = []
    token = bind_event_sink(events.append)
    try:
        result = await service.backfill_knowledge_point("四君子汤")
    finally:
        reset_event_sink(token)

    assert result.searched == 2
    assert result.extracted == 3
    assert result.ingested == 2
    assert result.skipped_blank == 1
    assert result.status == "ok"
    assert searcher.queries == [("四君子汤", 8)]
    # 持久化文件存在且只有 2 条
    lines = store.web_question_runtime.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
    # 内存索引立即可命中
    bundle = store.resolve_web_question_bundle("四君子汤", required_question_count=3)
    assert bundle is None  # 只有 2 道，不足 3 道
    bundle2 = store.resolve_web_question_bundle("四君子汤", required_question_count=2)
    assert bundle2 is not None
    assert bundle2["source"] == "knowledge_atlas"  # 与 workshop 注册器兼容
    assert len(bundle2["questions"]) == 2
    assert bundle2["questions"][0]["question_id"].startswith("WEBQ_")
    assert bundle2["questions"][0]["question_content"] == "四君子汤的组成不包括？"
    assert bundle2["questions"][0]["kp_ids"] == [bundle2["kp_id"]]
    assert bundle2["kp_id"].startswith("WEB_")
    status_event = next(
        item for item in events if item["event"] == "web_question_ingest_status"
    )
    assert status_event["ingested"] == 2


@pytest.mark.asyncio
async def test_backfill_deduplicates_against_public_bank_and_previous_runs(
    tmp_path: Path,
) -> None:
    store = _make_store(tmp_path)
    # 与公共题库题干完全相同 → 去重
    cleaner = FakeCleaner(
        rows=[
            {
                "question_type": "单选题",
                "stem": "公共题库唯一题：下列哪项属于四君子汤的组成？",
                "answer": "人参",
            },
            {
                "question_type": "单选题",
                "stem": "网络新题：四君子汤的君药是？",
                "answer": "人参",
            },
        ]
    )
    service = WebQuestionIngestService(
        searcher=FakeSearcher(
            [_hit("题目一", "题目一", "https://example.com/a")]
        ),
        cleaner=cleaner,
        store=store,
        runtime_dir=store.paths.question_runtime,
    )
    first = await service.backfill_knowledge_point("四君子汤")
    assert first.ingested == 1
    # 同一轮再来一次：全部重复
    cleaner.rows = [
        {
            "question_type": "单选题",
            "stem": "公共题库唯一题：下列哪项属于四君子汤的组成？",
            "answer": "人参",
        },
        {
            "question_type": "单选题",
            "stem": "网络新题：四君子汤的君药是？",
            "answer": "人参",
        },
    ]
    second = await service.backfill_knowledge_point("四君子汤")
    assert second.ingested == 0
    assert second.status == "skipped"
    assert second.skipped_duplicates == 2
    lines = store.web_question_runtime.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1


@pytest.mark.asyncio
async def test_backfill_empty_search_and_cleaner_failure_are_graceful(
    tmp_path: Path,
) -> None:
    store = _make_store(tmp_path)
    empty = WebQuestionIngestService(
        searcher=FakeSearcher([]),
        cleaner=FakeCleaner(),
        store=store,
        runtime_dir=store.paths.question_runtime,
    )
    result = await empty.backfill_knowledge_point("不存在的知识点")
    assert result.status == "empty"
    assert result.searched == 0

    failing = WebQuestionIngestService(
        searcher=FakeSearcher([_hit("标题", "内容", "https://example.com/x")]),
        cleaner=FakeCleaner(error=ValueError("no questions")),
        store=store,
        runtime_dir=store.paths.question_runtime,
    )
    result = await failing.backfill_knowledge_point("不存在的知识点")
    assert result.status == "failed"
    assert result.extracted == 0


@pytest.mark.asyncio
async def test_web_questions_survive_store_reload(tmp_path: Path) -> None:
    """持久化后重新加载 store 仍能命中（重启进程后网络题库不丢）。"""
    store = _make_store(tmp_path)
    cleaner = FakeCleaner(
        rows=[
            {
                "question_type": "单选题",
                "stem": f"网络题 {index}：关于四君子汤的说法？",
                "answer": "A",
            }
            for index in range(4)
        ]
    )
    service = WebQuestionIngestService(
        searcher=FakeSearcher(
            [_hit("练习", "练习内容", "https://example.com/y")]
        ),
        cleaner=cleaner,
        store=store,
        runtime_dir=store.paths.question_runtime,
    )
    result = await service.backfill_knowledge_point("四君子汤")
    assert result.ingested == 4

    # 新 store 实例（模拟重启）从 runtime 目录重新加载
    reloaded = _make_store(tmp_path)
    reloaded.ensure_web_questions()
    bundle = reloaded.resolve_web_question_bundle("四君子汤", required_question_count=3)
    assert bundle is not None
    assert len(bundle["questions"]) == 3
    # 再次入库会被去重拦截
    cleaner2 = FakeCleaner(rows=[dict(cleaner.rows[0])])
    service2 = WebQuestionIngestService(
        searcher=FakeSearcher(
            [_hit("练习", "练习内容", "https://example.com/y")]
        ),
        cleaner=cleaner2,
        store=reloaded,
        runtime_dir=reloaded.paths.question_runtime,
    )
    result2 = await service2.backfill_knowledge_point("四君子汤")
    assert result2.ingested == 0
    assert result2.skipped_duplicates == 1
