"""WebQuestionIngestService：网络搜索 → 清洗 → 去重 → 入库 的单元测试。"""

from __future__ import annotations

import asyncio
import json
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
    # 检索词带收尾词：只给知识点名时搜到的多是概念讲解页，抽不出可入卷的题。
    assert searcher.queries == [("四君子汤 参考答案", 8)]
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
async def test_backfill_duplicate_count_excludes_blank_rows(tmp_path: Path) -> None:
    """含脏数据（无题干）时，重复入库统计不应被累计 blank 数污染。"""
    store = _make_store(tmp_path)
    cleaner = FakeCleaner(
        rows=[
            {
                "question_type": "单选题",
                "stem": "网络新题：四君子汤的君药是？",
                "answer": "人参",
                "analysis": "君药为君，主证主药。",
            },
            {"question_type": "单选题", "answer": "A"},  # 脏数据：无题干
        ]
    )
    service = WebQuestionIngestService(
        searcher=FakeSearcher(
            [_hit("题目", "题目内容", "https://example.com/a")]
        ),
        cleaner=cleaner,
        store=store,
        runtime_dir=store.paths.question_runtime,
    )
    first = await service.backfill_knowledge_point("四君子汤")
    assert first.ingested == 1
    assert first.skipped_blank == 1
    assert first.skipped_duplicates == 0

    # 同内容再来一次：有效题全部重复，统计应为 1（旧公式会误算为 0）
    second = await service.backfill_knowledge_point("四君子汤")
    assert second.ingested == 0
    assert second.skipped_blank == 1  # 每次调用独立计数
    assert second.skipped_duplicates == 1  # 只统计有效清洗题


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
async def test_backfill_cleaner_timeout_degrades_without_blocking(
    tmp_path: Path,
) -> None:
    """cleaner.extract 卡住（同步 urllib LLM 重试）时必须按超时降级，
    不能阻塞 learning_plan_service 的 workflow 预算。"""
    store = _make_store(tmp_path)

    class SlowCleaner:
        def extract(self, markdown, source_ref, source_type, owner_id):
            import time

            time.sleep(30)  # 模拟 LLM 清洗无限挂起
            return []

    service = WebQuestionIngestService(
        searcher=FakeSearcher([_hit("标题", "内容", "https://example.com/x")]),
        cleaner=SlowCleaner(),
        store=store,
        runtime_dir=store.paths.question_runtime,
    )
    result = await service.backfill_knowledge_point(
        "四君子汤", timeout_seconds=0.2
    )
    assert result.status == "failed"
    assert result.detail == "question cleaning timed out"
    assert result.extracted == 0
    assert result.ingested == 0


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


@pytest.mark.asyncio
async def test_search_query_carries_the_question_types_the_unit_needs(
    tmp_path: Path,
) -> None:
    """单元需要简答题时，检索词要带上题型。

    线上实测：某单元需要 40 道简答题，而网络材料是一份选择题试卷，抽出来的
    题一道都用不上。题型是单元声明的结构化字段，写进检索词只是构造查询，
    不用它做任何分类判定。
    """
    store = _make_store(tmp_path)
    searcher = FakeSearcher([_hit("练习", "内容", "https://example.com/q")])
    service = WebQuestionIngestService(
        searcher=searcher,
        cleaner=FakeCleaner(rows=[{"stem": "题干", "answer": "A"}]),
        store=store,
        runtime_dir=store.paths.question_runtime,
    )

    await service.backfill_knowledge_point(
        "四君子汤", question_types=["简答题", "简答题", "单选题"]
    )

    assert searcher.queries == [("四君子汤 简答题 单选题 参考答案", 8)]


@pytest.mark.asyncio
async def test_web_questions_for_reads_only_and_never_triggers_ingestion(
    tmp_path: Path,
) -> None:
    """读取已入库网络题是纯读操作：不检索、不清洗、不阻塞。

    组卷只有分钟级预算，而清洗实测 152～379 秒；读路径一旦触发灌题，组卷
    就会被拖死。
    """
    store = _make_store(tmp_path)
    cleaner = FakeCleaner(
        rows=[
            {"stem": "网络题一", "answer": "A", "question_type": "单选题"},
            {"stem": "网络题二", "answer": "B", "question_type": "单选题"},
        ]
    )
    searcher = FakeSearcher([_hit("练习", "内容", "https://example.com/q")])
    service = WebQuestionIngestService(
        searcher=searcher,
        cleaner=cleaner,
        store=store,
        runtime_dir=store.paths.question_runtime,
    )

    # 还没灌过：读回空，且完全没有发生检索或清洗。
    assert service.web_questions_for("四君子汤") == []
    assert searcher.queries == []
    assert cleaner.calls == []

    await service.backfill_knowledge_point("四君子汤")

    rows = service.web_questions_for("四君子汤")
    assert [row["stem"] for row in rows] == ["网络题一", "网络题二"]
    # 读回之后依然没有新的检索/清洗调用。
    assert len(searcher.queries) == 1
    assert len(cleaner.calls) == 1


@pytest.mark.asyncio
async def test_web_questions_for_degrades_when_read_fails(tmp_path: Path) -> None:
    """读取失败按“没有网络题”降级，不得向上抛异常拖垮组卷。"""
    store = _make_store(tmp_path)

    class BrokenStore:
        def web_questions_for(self, name: str):
            raise RuntimeError("index broken")

    service = WebQuestionIngestService(
        searcher=FakeSearcher([]),
        cleaner=FakeCleaner(),
        store=BrokenStore(),
        runtime_dir=store.paths.question_runtime,
    )
    events: list[dict] = []
    token = bind_event_sink(events.append)
    try:
        rows = service.web_questions_for("四君子汤")
    finally:
        reset_event_sink(token)

    assert rows == []
    status_event = next(
        item for item in events if item["event"] == "web_question_ingest_status"
    )
    assert status_event["status"] == "failed"


@pytest.mark.asyncio
async def test_register_web_questions_does_not_load_the_full_question_index(
    tmp_path: Path,
) -> None:
    """入库去重只建题干签名，不把整个公共题库建成索引常驻。

    服务进程实测已占 1.63 GB，而机器可用内存约 1.3 GB。ensure_questions()
    实测解析峰值 769 MB、常驻 +380 MB；只建签名时峰值 60 MB、常驻 +35 MB，
    签名集合完全一致。
    """
    store = _make_store(tmp_path)
    service = WebQuestionIngestService(
        searcher=FakeSearcher([_hit("练习", "内容", "https://example.com/q")]),
        cleaner=FakeCleaner(rows=[{"stem": "网络新题", "answer": "A"}]),
        store=store,
        runtime_dir=store.paths.question_runtime,
    )

    await service.backfill_knowledge_point("四君子汤")

    assert store.questions_by_kp == {}
    assert store._questions_ready is False
    assert store._public_stem_signatures


@pytest.mark.asyncio
async def test_web_questions_are_deduplicated_against_the_public_bank(
    tmp_path: Path,
) -> None:
    """公共题库已有的题干不会被当成网络新题入库（签名集合来源改变后仍生效）。"""
    store = _make_store(tmp_path)
    service = WebQuestionIngestService(
        searcher=FakeSearcher([_hit("练习", "内容", "https://example.com/q")]),
        cleaner=FakeCleaner(
            rows=[
                {"stem": "公共题库唯一题：下列哪项属于四君子汤的组成？", "answer": "人参"},
                {"stem": "网络新题", "answer": "A"},
            ]
        ),
        store=store,
        runtime_dir=store.paths.question_runtime,
    )

    await service.backfill_knowledge_point("四君子汤")
    rows = service.web_questions_for("四君子汤")

    assert [row["stem"] for row in rows] == ["网络新题"]


@pytest.mark.asyncio
async def test_schedule_backfill_returns_immediately_and_dedupes(
    tmp_path: Path,
) -> None:
    """投递后台灌题必须立即返回，同一知识点只投一次。

    组卷只有分钟级预算，而清洗实测要 152～379 秒：在组卷内等待必然提前掐断
    清洗、产出恒为 0（线上该功能上线后一道题都没进过卷）。改为后台灌题后，
    本次组卷读已入库的题，新题从下一次组卷起可用。
    """
    store = _make_store(tmp_path)
    release = asyncio.Event()

    class BlockingSearcher:
        def __init__(self) -> None:
            self.queries: list[tuple[str, int]] = []

        async def search_questions(self, query: str, limit: int = 5):
            self.queries.append((query, limit))
            await release.wait()
            return [_hit("练习", "内容", "https://example.com/q")]

    searcher = BlockingSearcher()
    service = WebQuestionIngestService(
        searcher=searcher,
        cleaner=FakeCleaner(rows=[{"stem": "网络题一", "answer": "A"}]),
        store=store,
        runtime_dir=store.paths.question_runtime,
    )

    # 投递即返回：此刻检索还挂着，调用方已经拿到结果，没有等待清洗。
    assert service.schedule_backfill("四君子汤") is True
    assert service.web_questions_for("四君子汤") == []
    # 同一知识点重复投递被拒：否则每次组卷都会重新触发一轮检索 + 清洗。
    assert service.schedule_backfill("四君子汤") is False
    assert len(service._background_tasks) == 1

    task = next(iter(service._background_tasks))
    release.set()
    await task

    rows = service.web_questions_for("四君子汤")
    assert [row["stem"] for row in rows] == ["网络题一"]
    # 后台任务用的是同一套检索词，题型由单元声明带入。
    assert searcher.queries == [("四君子汤 参考答案", 8)]


@pytest.mark.asyncio
async def test_schedule_backfill_caps_concurrent_ingestions(tmp_path: Path) -> None:
    """同时在跑的灌题数有上限。

    清洗是同步 urllib 调用，经 asyncio.to_thread 落到事件循环的默认线程池
    （上限 min(32, CPU+4)，线上 2 核 = 6）。该线程池同时服务 SQLAlchemy
    checkpointer 等每次图步都要用的同步调用，被数百秒的清洗占满会让
    checkpoint 读写一起排队。超限时不投递、也不登记，留给下一轮组卷。
    """
    store = _make_store(tmp_path)
    release = asyncio.Event()

    class BlockingSearcher:
        async def search_questions(self, query: str, limit: int = 5):
            await release.wait()
            return [_hit("练习", "内容", "https://example.com/q")]

    service = WebQuestionIngestService(
        searcher=BlockingSearcher(),
        cleaner=FakeCleaner(rows=[{"stem": "网络题一", "answer": "A"}]),
        store=store,
        runtime_dir=store.paths.question_runtime,
    )

    assert service.schedule_backfill("知识点一") is True
    assert service.schedule_backfill("知识点二") is True
    assert service.schedule_backfill("知识点三") is False
    assert "知识点三" not in service._scheduled

    release.set()
    for task in list(service._background_tasks):
        await task
    # 清理回调经 call_soon 排队，让事件循环跑一轮才能看到任务出队。
    await asyncio.sleep(0)
    assert service._background_tasks == set()


def test_schedule_backfill_without_running_loop_is_a_no_op(tmp_path: Path) -> None:
    """同步调用方没有事件循环时直接放弃投递，不抛异常。"""
    store = _make_store(tmp_path)
    service = WebQuestionIngestService(
        searcher=FakeSearcher([]),
        cleaner=FakeCleaner(),
        store=store,
        runtime_dir=store.paths.question_runtime,
    )

    assert service.schedule_backfill("四君子汤") is False
    assert service._background_tasks == set()


@pytest.mark.asyncio
async def test_background_backfill_failure_does_not_raise(tmp_path: Path) -> None:
    """后台灌题失败只记事件，不能让任务带着未处理异常结束。"""
    store = _make_store(tmp_path)

    class ExplodingSearcher:
        async def search_questions(self, query: str, limit: int = 5):
            raise RuntimeError("exa down")

    service = WebQuestionIngestService(
        searcher=ExplodingSearcher(),
        cleaner=FakeCleaner(rows=[]),
        store=store,
        runtime_dir=store.paths.question_runtime,
    )
    events: list[dict] = []
    token = bind_event_sink(events.append)
    try:
        assert service.schedule_backfill("四君子汤") is True
        task = next(iter(service._background_tasks))
        await task  # 不抛异常
    finally:
        reset_event_sink(token)

    assert task.exception() is None
    status_event = next(
        item for item in events if item["event"] == "web_question_ingest_status"
    )
    assert status_event["status"] == "failed"


def test_web_question_id_is_shared_by_write_read_and_bundle(tmp_path: Path) -> None:
    """写入、读取、bundle 三处必须得到同一个题目标识。

    线上实测：入库只落盘题干与答案，没有 question_id，而组卷侧要求
    question_id 非空、否则丢弃，25 道已入库的网络题因此一道都没进过卷。
    """
    store = _make_store(tmp_path)
    stem = "四君子汤的组成不包括？"

    ingested = store.register_web_questions(
        "四君子汤", [{"stem": stem, "answer": "D", "question_type": "单选题"}]
    )
    assert ingested == 1

    rows = store.web_questions_for("四君子汤")
    assert len(rows) == 1
    question_id = rows[0]["question_id"]
    assert question_id.startswith("WEBQ_")
    # 名称归一化后仍得到同一个 ID（检索侧与读取侧的名称写法可能不同）。
    assert question_id == store.web_question_id("四君子汤", stem)
    assert question_id == store.web_question_id("  四君子汤  ", stem)

    bundle = store.resolve_web_question_bundle("四君子汤", required_question_count=1)
    assert bundle is not None
    assert bundle["questions"][0]["question_id"] == question_id


def test_legacy_rows_without_question_id_get_one_on_read(tmp_path: Path) -> None:
    """历史落盘行（没有 question_id）读取时按同一口径补算。

    入库侧补 ID 只能救新写入的题；已经落盘的行必须靠读取侧补算才能重新可用。
    """
    store = _make_store(tmp_path)
    runtime = store.web_question_runtime
    runtime.parent.mkdir(parents=True, exist_ok=True)
    runtime.write_text(
        json.dumps(
            {
                "kp_name": "四君子汤",
                "question": {
                    "stem": "历史网络题",
                    "answer": "A",
                    "question_type": "单选题",
                },
                "ingested_at": "2026-09-18T00:00:00Z",
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    rows = store.web_questions_for("四君子汤")

    assert len(rows) == 1
    assert rows[0]["question_id"] == store.web_question_id("四君子汤", "历史网络题")


@pytest.mark.asyncio
async def test_backfill_normalizes_question_types_and_skips_answerless_rows(
    tmp_path: Path,
) -> None:
    """题型统一成中文规范名；没有标准答案的题不入库并如实计数。

    线上失效现场（2026-09-18，太阳中风证）：网络题抽取器把平台英文枚举
    ``single_choice`` 原样写回，而候选准入的题型过滤只认中文名，已入库的 8
    道网络题一道都进不了候选池；同一批里另有 4 道原文没有答案（清洗器的契约
    要求此时留空、不得猜答案），入库后在准入判定处被整条剔除，等于白跑一次
    检索和清洗。
    """

    store = _make_store(tmp_path)
    cleaner = FakeCleaner(
        rows=[
            {
                "question_type": "single_choice",
                "stem": "英文题型题：四君子汤的君药是？",
                "options": ["人参", "白术"],
                "answer": "A",
                "analysis": "君药为人参。",
            },
            {
                "question_type": "Single Choice",
                "stem": "带空格英文题型题：四君子汤的功效是？",
                "options": ["益气健脾", "滋阴补肾"],
                "answer": "A",
                "analysis": "益气健脾。",
            },
            {
                "question_type": "short_answer",
                "stem": "英文简答题：简述四君子汤的配伍意义。",
                "answer": "四药皆甘温，益气健脾。",
                "analysis": "甘温平补。",
            },
            # 原文没答案：清洗器按契约留空，不得猜答案；没有标准答案的题既
            # 不能判分也给不出解析，不能入库。
            {
                "question_type": "single_choice",
                "stem": "无答案题：四君子汤的出处是？",
                "options": ["《太平惠民和剂局方》", "《伤寒论》"],
                "answer": "",
                "analysis": "",
            },
            # 词表不认识的写法原样入库并计数，不猜测、不兜底成已知题型。
            {
                "question_type": "论述题",
                "stem": "论述题：试述四君子汤的临床应用。",
                "answer": "用于脾胃气虚证。",
                "analysis": "随证加减。",
            },
        ]
    )
    service = WebQuestionIngestService(
        searcher=FakeSearcher([_hit("题目", "题目内容", "https://example.com/a")]),
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

    assert result.extracted == 5
    assert result.skipped_no_answer == 1
    assert result.ingested == 4
    assert result.unmapped_question_types == ["论述题×1"]
    rows = [
        json.loads(line)["question"]
        for line in store.web_question_runtime.read_text(
            encoding="utf-8"
        ).splitlines()
    ]
    assert [row["question_type"] for row in rows] == [
        "单项选择题",
        "单项选择题",
        "简答题",
        "论述题",
    ]
    assert all(row["answer"].strip() for row in rows)
    status_event = next(
        item for item in events if item["event"] == "web_question_ingest_status"
    )
    assert status_event["skipped_no_answer"] == 1
    assert status_event["unmapped_question_types"] == ["论述题×1"]


@pytest.mark.asyncio
async def test_backfill_keeps_answer_bearing_questions_without_analysis(
    tmp_path: Path,
) -> None:
    """有答案、没解析的题必须入库。

    「没解析的可以的」是明确的产品口径：解析只在组卷阶段的
    ``requires_explanation`` 约束下才必需，而那条约束只作用于现场生成的题。
    入库侧若顺手把没解析的题也挡掉，等于把口径改成「必须有解析」，会再丢
    一批本来可用的题。
    """

    store = _make_store(tmp_path)
    cleaner = FakeCleaner(
        rows=[
            {
                "question_type": "简答题",
                "stem": "没有解析但有答案：简述四君子汤的组成。",
                "answer": "人参、白术、茯苓、甘草。",
            }
        ]
    )
    service = WebQuestionIngestService(
        searcher=FakeSearcher([_hit("题目", "题目内容", "https://example.com/a")]),
        cleaner=cleaner,
        store=store,
        runtime_dir=store.paths.question_runtime,
    )

    result = await service.backfill_knowledge_point("四君子汤")

    assert result.ingested == 1
    assert result.skipped_no_answer == 0
    rows = [
        json.loads(line)["question"]
        for line in store.web_question_runtime.read_text(
            encoding="utf-8"
        ).splitlines()
    ]
    assert rows[0]["analysis"] == ""
    assert rows[0]["answer"] == "人参、白术、茯苓、甘草。"


def test_register_web_questions_skips_answerless_rows(tmp_path: Path) -> None:
    """注册兜底：存量行与绕过入库侧的调用也不能把无答案题写进去。"""

    store = _make_store(tmp_path)

    written = store.register_web_questions(
        "四君子汤",
        [
            {"stem": "有答案的题", "answer": "A", "question_type": "单项选择题"},
            {"stem": "答案是空串的题", "answer": "", "question_type": "单项选择题"},
            {"stem": "答案是空列表的题", "answer": [], "question_type": "单项选择题"},
            {"stem": "答案只有空白的题", "answer": "   ", "question_type": "单项选择题"},
        ],
    )

    assert written == 1
    rows = store.web_questions_for("四君子汤")
    assert [row["stem"] for row in rows] == ["有答案的题"]


def test_resolve_web_question_bundle_never_delivers_answerless_questions(
    tmp_path: Path,
) -> None:
    """取题时按有答案筛：这是会把无答案题直接交给学习者的那条路径。

    ``resolve_web_question_bundle`` 在公共题库缺该知识点时被调用，返回的
    bundle 会注册成学习者要做的练习。它此前按「已入库题数」判断够不够，再
    从含空答案的列表里切片——线上 8 道题里 4 道没答案，切片取到的题一半没有
    标准答案，学习者做完既不能判分也看不到解析。
    """

    store = _make_store(tmp_path)
    runtime = store.web_question_runtime
    runtime.parent.mkdir(parents=True, exist_ok=True)
    # 直接落盘模拟存量行：入库侧现在已挡住空答案，但线上 jsonl 里还留着旧
    # 版本写进去的 60 道空答案题。
    rows = [
        {"stem": "有答案一", "answer": "A", "question_type": "单项选择题"},
        {"stem": "无答案一", "answer": "", "question_type": "单项选择题"},
        {"stem": "有答案二", "answer": "B", "question_type": "单项选择题"},
        {"stem": "无答案二", "answer": "", "question_type": "单项选择题"},
        {"stem": "有答案三", "answer": "C", "question_type": "单项选择题"},
    ]
    with runtime.open("w", encoding="utf-8") as handle:
        for question in rows:
            handle.write(
                json.dumps(
                    {
                        "kp_name": "桂枝加附子汤证",
                        "question": question,
                        "ingested_at": "2026-09-18T18:18:00+00:00",
                        "origin": "web_search",
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )

    assert len(store.web_questions_for("桂枝加附子汤证")) == 5  # 存量行全部读回

    bundle = store.resolve_web_question_bundle(
        "桂枝加附子汤证", required_question_count=3
    )
    assert bundle is not None
    assert [row["answer"] for row in bundle["questions"]] == ["A", "B", "C"]
    assert all(row["answer"].strip() for row in bundle["questions"])
    # 只有 3 道有答案：不能因为「已入库 5 道」就判定 4 题也够。
    assert (
        store.resolve_web_question_bundle(
            "桂枝加附子汤证", required_question_count=4
        )
        is None
    )
