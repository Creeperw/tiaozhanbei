"""网络搜索题目补充：搜索 → LLM 清洗 → 去重 → 入库。

当日任务物化时，知识库（图谱 + workshop 题库）可能缺少某个知识点，
或某知识点题量不足（默认需要 3 道）。本服务用 Exa 在网络上搜索该知识点的
练习题，用现有 question_pipeline 的 LLM 抽取器清洗成结构化题目（不补造原文
没有的题），与公共题库及已入库的网络题做题干归一化去重后，持久化到
``runtime/questions/web_ingested/questions.jsonl``，并同步更新
``DeliveryKnowledgeMapStore`` 的内存索引，使后续物化能立即命中。
"""

from __future__ import annotations

import asyncio
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from competition_app.runtime.event_stream import emit_runtime_event


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

    async def backfill_knowledge_point(
        self,
        knowledge_point_name: str,
        *,
        learning_chapter: str = "",
        limit: int = 8,
        timeout_seconds: float = 15.0,
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
                self.searcher.search_questions(name, limit=min(limit, self.max_questions_per_kp)),
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
        for row in rows:
            if not isinstance(row, dict):
                continue
            stem = str(row.get("stem") or row.get("题干") or "").strip()
            if not stem:
                result.skipped_blank += 1
                continue
            normalized.append(
                {
                    "stem": stem,
                    "question_type": str(
                        row.get("question_type") or row.get("题型") or "未分类"
                    ),
                    "options": row.get("options") or [],
                    "answer": row.get("answer") or row.get("答案") or "",
                    "analysis": row.get("analysis") or row.get("解析") or "",
                    "source_ref": str(row.get("source_ref") or f"web://{name}"),
                    "source_urls": [str(hit.url) for hit in hits if hit.url],
                    "origin": "web_search",
                }
            )
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
        # normalized 已剔除无题干脏行，重复数 = 有效清洗题 - 实际入库数。
        result.skipped_duplicates = max(0, len(normalized) - ingested)
        result.status = "ok" if ingested else "skipped"
        result.detail = (
            "ingested web questions" if ingested else "all questions already in bank"
        )
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
        )
        return result

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
