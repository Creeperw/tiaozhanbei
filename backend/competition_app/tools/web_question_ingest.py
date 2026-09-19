"""网络搜索题目补充：搜索 → LLM 清洗 → 去重 → 入库。

当日任务物化时，知识库（图谱 + workshop 题库）可能缺少某个知识点，
或某知识点题量不足（默认需要 3 道）。本服务用 Exa 在网络上搜索该知识点的
练习题，用现有 question_pipeline 的 LLM 抽取器清洗成结构化题目（不补造原文
没有的题），与公共题库及已入库的网络题做题干归一化去重后，持久化到
``runtime/questions/web_ingested/questions.jsonl``，并同步更新
``DeliveryKnowledgeMapStore`` 的内存索引，使后续物化能立即命中。

组卷路径不能同步等待本服务：实测单次清洗耗时 152～379 秒（要一次性输出全部
题目与解析），而组卷只有分钟级预算。因此组卷侧只读已经入库的网络题，灌题由
``schedule_backfill`` 投到后台完成，下一次组卷自然命中。
"""

from __future__ import annotations

import asyncio
import threading
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, Sequence

from competition_app.contracts.question_types import (
    KNOWN_QUESTION_TYPES,
    normalize_question_type,
)
from competition_app.runtime.event_stream import emit_runtime_event

# 后台灌题的并发上限。清洗是同步 urllib 调用，经 ``asyncio.to_thread`` 落到事件
# 循环的默认线程池（上限 ``min(32, CPU+4)``，线上 2 核 = 6）。该线程池同时服务
# SQLAlchemy checkpointer 等每次图步都要用的同步调用，若被若干个数百秒的清洗
# 任务占满，checkpoint 读写会一起排队。这里限制同时在跑的灌题数。
_BACKGROUND_CONCURRENCY = 2
# 后台灌题的默认预算。实测单次清洗 152～379 秒（要一次性输出全部题目与解析），
# 在组卷内等待必然被提前掐断，所以只在后台使用长预算。
_BACKGROUND_TIMEOUT_SECONDS = 600.0


class QuestionCleaner(Protocol):
    """LLM 题目抽取器的最小接口（question_pipeline.markdown_ingest）。"""

    def extract(
        self,
        markdown: str,
        source_ref: str,
        source_type: str,
        owner_id: str | None,
    ) -> list[dict[str, Any]]: ...


@dataclass
class WebBackfillResult:
    knowledge_point_name: str
    searched: int = 0
    extracted: int = 0
    ingested: int = 0
    skipped_duplicates: int = 0
    skipped_blank: int = 0
    # 清洗出来但原文没有答案、按规则不入库的题。它们此前会被写进题库，然后
    # 在候选准入处被「缺少标准答案」剔除——检索、清洗、入库都做完了才发现
    # 用不上，且过程里没有任何一处如实说明。这里在入口就挡住并计数。
    skipped_no_answer: int = 0
    # 词表不认识的题型写法（含空题型兜底成的「未分类」），形如「论述题×3」。
    # 归一化刻意不吞掉未知写法：词表缺什么必须能被看见。
    unmapped_question_types: list[str] = field(default_factory=list)
    duration_seconds: float = 0.0
    status: str = "ok"  # ok | skipped | empty | failed | error
    detail: str = ""


@dataclass
class _SearchHit:
    title: str = ""
    summary: str = ""
    url: str = ""


@dataclass
class _SearchBackend(Protocol):
    async def search_questions(
        self, query: str, limit: int = 5
    ) -> list[Any]: ...


class _NoOpSearcher:
    """Live 环境未配置 Exa 时的占位，保证调用方无需判空。"""

    async def search_questions(self, query: str, limit: int = 5) -> list[Any]:
        return []


class WebQuestionIngestService:
    """搜索并清洗网络题目，去重后写入网络补充题库。"""

    def __init__(
        self,
        *,
        searcher: _SearchBackend | None,
        cleaner: QuestionCleaner | None,
        store: Any,
        runtime_dir: Path,
        max_questions_per_kp: int = 8,
    ) -> None:
        self.searcher = searcher or _NoOpSearcher()
        self.cleaner = cleaner
        self.store = store
        self.runtime_dir = Path(runtime_dir)
        self.max_questions_per_kp = max_questions_per_kp
        self._lock = threading.RLock()
        # 已投递过后台灌题的知识点，避免每次组卷都重复触发检索 + 清洗。
        self._scheduled: set[str] = set()
        # 在跑的后台任务强引用：不持有引用的话任务可能被 GC 回收。
        self._background_tasks: set[asyncio.Task[Any]] = set()

    async def backfill_knowledge_point(
        self,
        knowledge_point_name: str,
        *,
        learning_chapter: str = "",
        limit: int = 8,
        timeout_seconds: float = 15.0,
        question_types: Sequence[str] = (),
    ) -> WebBackfillResult:
        """对一个知识点执行一次完整的“搜索→清洗→去重→入库”。"""
        name = str(knowledge_point_name or "").strip()
        if not name:
            return WebBackfillResult(
                knowledge_point_name=name or "",
                status="error",
                detail="empty knowledge point name",
            )
        started = asyncio.get_event_loop().time()
        result = WebBackfillResult(knowledge_point_name=name)
        try:
            hits = await asyncio.wait_for(
                self.searcher.search_questions(
                    self._search_query(name, question_types),
                    limit=min(limit, self.max_questions_per_kp),
                ),
                timeout=timeout_seconds,
            )
        except asyncio.TimeoutError:
            result.status = "error"
            result.detail = "web search timed out"
            result.duration_seconds = asyncio.get_event_loop().time() - started
            emit_runtime_event(
                "web_question_ingest_status",
                knowledge_point_name=name,
                status="failed",
                detail=result.detail,
            )
            return result
        except Exception as exc:
            result.status = "error"
            result.detail = f"web search failed: {type(exc).__name__}"
            result.duration_seconds = asyncio.get_event_loop().time() - started
            emit_runtime_event(
                "web_question_ingest_status",
                knowledge_point_name=name,
                status="failed",
                detail=result.detail,
            )
            return result
        result.searched = len(hits)
        if not hits:
            result.status = "empty"
            result.detail = "no web question resources found"
            result.duration_seconds = asyncio.get_event_loop().time() - started
            emit_runtime_event(
                "web_question_ingest_status",
                knowledge_point_name=name,
                status="empty",
                result_count=0,
            )
            return result

        if self.cleaner is None:
            result.status = "skipped"
            result.detail = "question cleaner is not configured"
            result.duration_seconds = asyncio.get_event_loop().time() - started
            emit_runtime_event(
                "web_question_ingest_status",
                knowledge_point_name=name,
                status="skipped",
                detail=result.detail,
            )
            return result

        markdown = self._build_markdown(name, hits)
        # cleaner.extract 内部走同步 urllib LLM 调用（question_pipeline 的
        # request_json 默认 4 次 × 180s 重试），asyncio.to_thread 无法被外层
        # wait_for 取消线程，必须用独立超时护栏包裹，超时按降级处理，绝不
        # 阻塞 learning_plan_service 的 workflow 预算。
        cleaner_timeout_seconds = max(15.0, float(timeout_seconds))
        try:
            rows = await asyncio.wait_for(
                asyncio.to_thread(
                    self.cleaner.extract,
                    markdown,
                    f"web://{name}",
                    "web_search",
                    None,
                ),
                timeout=cleaner_timeout_seconds,
            )
        except asyncio.TimeoutError:
            result.status = "failed"
            result.detail = "question cleaning timed out"
            result.duration_seconds = asyncio.get_event_loop().time() - started
            emit_runtime_event(
                "web_question_ingest_status",
                knowledge_point_name=name,
                status="failed",
                detail=result.detail,
            )
            return result
        except Exception as exc:
            result.status = "failed"
            result.detail = f"question cleaning failed: {type(exc).__name__}"
            result.duration_seconds = asyncio.get_event_loop().time() - started
            emit_runtime_event(
                "web_question_ingest_status",
                knowledge_point_name=name,
                status="failed",
                detail=result.detail,
            )
            return result
        result.extracted = len(rows)
        if not rows:
            result.status = "failed"
            result.detail = "cleaner extracted no questions"
            result.duration_seconds = asyncio.get_event_loop().time() - started
            emit_runtime_event(
                "web_question_ingest_status",
                knowledge_point_name=name,
                status="failed",
                detail=result.detail,
            )
            return result

        normalized: list[dict[str, Any]] = []
        unmapped_types: Counter[str] = Counter()
        for row in rows:
            if not isinstance(row, dict):
                continue
            stem = str(row.get("stem") or row.get("题干") or "").strip()
            if not stem:
                result.skipped_blank += 1
                continue
            answer = str(row.get("answer") or row.get("答案") or "").strip()
            if not answer:
                # 没答案的题不入库。清洗器的契约是「原文没答案时 answer 必须
                # 为空，不得猜答案」，所以空答案是合规输出，不是脏数据；但
                # 没有标准答案的题无法判分，候选准入会把它整条剔除。此前这
                # 类行照常入库占题量，实测一批 59 道里 54 道是空答案，等于
                # 白跑一次检索和清洗。
                result.skipped_no_answer += 1
                continue
            # 题型统一成中文规范名。网络题抽取器的提示词只声明字段名、不声明
            # 允许值，模型会把平台英文枚举（single_choice 等）原样写回来，而
            # 候选准入的题型过滤只认中文名，这些题此前全被判成题型不一致丢弃。
            raw_type = str(row.get("question_type") or row.get("题型") or "").strip()
            question_type = normalize_question_type(raw_type) or "未分类"
            if question_type not in KNOWN_QUESTION_TYPES:
                # 词表不认识就原样入库并计数，不猜测、不兜底成某个已知题型：
                # 猜错会让题以错误的题型进卷面，兜底则会让词表缺口永远沉默。
                unmapped_types[question_type] += 1
            normalized.append(
                {
                    "stem": stem,
                    "question_type": question_type,
                    "options": row.get("options") or [],
                    "answer": answer,
                    "analysis": row.get("analysis") or row.get("解析") or "",
                    "source_ref": str(row.get("source_ref") or f"web://{name}"),
                    "source_urls": [str(hit.url) for hit in hits if hit.url],
                    "origin": "web_search",
                }
            )
        result.unmapped_question_types = [
            f"{value}×{count}" for value, count in unmapped_types.most_common()
        ]
        try:
            ingested = await asyncio.to_thread(
                self.store.register_web_questions, name, normalized
            )
        except Exception as exc:
            result.status = "error"
            result.detail = f"question persistence failed: {type(exc).__name__}"
            result.duration_seconds = asyncio.get_event_loop().time() - started
            emit_runtime_event(
                "web_question_ingest_status",
                knowledge_point_name=name,
                status="failed",
                detail=result.detail,
            )
            return result
        result.ingested = ingested
        # normalized 已剔除无题干、无答案的行，重复数 = 有效清洗题 - 实际入库数。
        result.skipped_duplicates = max(0, len(normalized) - ingested)
        result.status = "ok" if ingested else "skipped"
        if ingested:
            result.detail = "ingested web questions"
        elif not normalized and result.skipped_no_answer:
            # 区分「全都重复」与「全都缺答案」：前者是题库已有，后者是这一轮
            # 检索清洗白跑，两者的处置完全不同。
            result.detail = "all cleaned questions have no answer"
        else:
            result.detail = "all questions already in bank"
        result.duration_seconds = asyncio.get_event_loop().time() - started
        emit_runtime_event(
            "web_question_ingest_status",
            knowledge_point_name=name,
            status=result.status,
            searched=result.searched,
            extracted=result.extracted,
            ingested=result.ingested,
            skipped_duplicates=result.skipped_duplicates,
            skipped_blank=result.skipped_blank,
            skipped_no_answer=result.skipped_no_answer,
            unmapped_question_types=result.unmapped_question_types,
        )
        return result

    # 检索词收尾词。组卷要的是可入卷的题，而「练习题」会把检索拉向执业医师
    # 选择题库；换「参考答案」后更偏向问答/大题材料。实测 5 个知识点（太阳中风
    # 证、太阳伤寒证、桂枝汤证、太阳蓄水证、太阳病提纲），「参考答案」没有
    # 一个比「练习题」差，其中 4 个多出可用的问答材料。这只是弱改善：网络上
    # 简答题始终是少数，题型过滤仍会丢掉多数命中。
    SEARCH_QUERY_SUFFIX = "参考答案"

    @classmethod
    def _search_query(
        cls, knowledge_point_name: str, question_types: Sequence[str] = ()
    ) -> str:
        """网络题检索词。

        只给知识点名时搜到的多是概念讲解页；组卷需要的是可入卷的练习题，
        因此把本单元实际需要的题型写进检索词（线上实测：某单元需要 40 道
        简答题，而网络材料是一份选择题试卷，抽出来的题一道都用不上）。
        """
        types = [str(value).strip() for value in question_types if str(value).strip()]
        parts = [knowledge_point_name, *dict.fromkeys(types)]
        return " ".join(parts) + " " + cls.SEARCH_QUERY_SUFFIX

    def web_questions_for(self, knowledge_point_name: str) -> list[dict[str, Any]]:
        """已入库的网络题（只读，不检索、不清洗、不阻塞）。"""
        name = str(knowledge_point_name or "").strip()
        if not name:
            return []
        try:
            return list(self.store.web_questions_for(name))
        except Exception as exc:  # noqa: BLE001 - 读取失败按无网络题处理
            emit_runtime_event(
                "web_question_ingest_status",
                knowledge_point_name=name,
                status="failed",
                detail=f"web question read failed {type(exc).__name__}",
            )
            return []

    def schedule_backfill(
        self,
        knowledge_point_name: str,
        *,
        question_types: Sequence[str] = (),
        learning_chapter: str = "",
        limit: int = 8,
        timeout_seconds: float = _BACKGROUND_TIMEOUT_SECONDS,
    ) -> bool:
        """投递一次后台灌题并立即返回；投递成功返回 True。

        组卷不能等“网络检索 + LLM 清洗”：实测单次清洗耗时 152～379 秒，而
        组卷本身只有分钟级预算，原先在组卷内等 45 秒必然提前掐断清洗、产出
        恒为 0。改为组卷只读已入库的网络题，灌题在后台完成，下一次组卷自然
        命中。

        同一个知识点在一个进程生命周期内只投递一次：重复投递会让每次组卷都
        触发一轮检索 + 清洗，而结果大概率被去重拦下。并发已满时不投递，也
        不登记该知识点，留给下一轮组卷再投。
        """
        name = str(knowledge_point_name or "").strip()
        if not name:
            return False
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            # 同步调用方没有事件循环，无法投递后台任务。
            return False
        with self._lock:
            if name in self._scheduled:
                return False
            if len(self._background_tasks) >= _BACKGROUND_CONCURRENCY:
                return False
            self._scheduled.add(name)
            task = loop.create_task(
                self._run_backfill(
                    name,
                    question_types=question_types,
                    learning_chapter=learning_chapter,
                    limit=limit,
                    timeout_seconds=timeout_seconds,
                )
            )
            self._background_tasks.add(task)
            task.add_done_callback(self._background_tasks.discard)
        return True

    async def _run_backfill(
        self,
        knowledge_point_name: str,
        *,
        question_types: Sequence[str],
        learning_chapter: str,
        limit: int,
        timeout_seconds: float,
    ) -> None:
        """后台灌题执行体：异常只记事件，绝不外抛。"""
        try:
            await self.backfill_knowledge_point(
                knowledge_point_name,
                learning_chapter=learning_chapter,
                limit=limit,
                timeout_seconds=timeout_seconds,
                question_types=question_types,
            )
        except Exception as exc:  # noqa: BLE001 - 后台任务不得抛出
            emit_runtime_event(
                "web_question_ingest_status",
                knowledge_point_name=knowledge_point_name,
                status="failed",
                detail=f"background backfill raised {type(exc).__name__}",
            )

    @staticmethod
    def _build_markdown(knowledge_point_name: str, hits: list[_SearchHit]) -> str:
        """把搜索结果拼成抽取器可读的 markdown 片段。

        每个结果独立成段，保留来源标题与 URL，让 LLM 只从真实网页内容中
        抽取题目；没有题目内容的结果会被抽取器自然跳过。
        """
        lines = [
            f"以下是关于「{knowledge_point_name}」的网络检索材料，从中抽取出现的练习题：",
            "",
        ]
        for index, hit in enumerate(hits, start=1):
            lines.append(f"## 检索结果 {index}")
            title = str(hit.title or "").strip()
            if title:
                lines.append(f"标题：{title}")
            summary = str(hit.summary or "").strip()
            if summary:
                lines.append(f"内容：{summary}")
            if hit.url:
                lines.append(f"来源：{hit.url}")
            lines.append("")
        return "\n".join(lines)
