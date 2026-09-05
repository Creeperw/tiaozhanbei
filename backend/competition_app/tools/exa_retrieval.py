from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, TypeVar

from exa_py import AsyncExa

from competition_app.runtime.event_stream import emit_runtime_event


@dataclass(frozen=True)
class ExaResourceHit:
    source_id: str
    title: str
    summary: str
    url: str
    score: float
    resource_type: str


ExaVideoHit = ExaResourceHit
_ExaResult = TypeVar("_ExaResult")


class ExaVideoRetriever:
    """Search external teaching videos, references, and question resources through Exa."""

    def __init__(
        self,
        api_key: str,
        *,
        client: Any | None = None,
        timeout_seconds: float = 60.0,
        max_concurrency: int = 8,
    ) -> None:
        self.client = client or AsyncExa(api_key)
        self.timeout_seconds = max(1.0, float(timeout_seconds))
        self._request_semaphore = asyncio.Semaphore(max(1, int(max_concurrency)))

    async def _call_with_limits(
        self,
        operation: Callable[[], Awaitable[_ExaResult]],
    ) -> _ExaResult:
        """Bound Exa fan-out and prevent the SDK's 600s timeout from consuming a step."""
        async with self._request_semaphore:
            return await asyncio.wait_for(
                operation(),
                timeout=self.timeout_seconds,
            )

    async def search_videos(self, query: str, limit: int = 5) -> list[ExaResourceHit]:
        return await self.search_resources(query, resource_type="video", limit=limit)

    async def search_references(self, query: str, limit: int = 5) -> list[ExaResourceHit]:
        return await self.search_resources(query, resource_type="reference", limit=limit)

    async def search_questions(self, query: str, limit: int = 5) -> list[ExaResourceHit]:
        return await self.search_resources(query, resource_type="question", limit=limit)

    async def search_web(self, query: str, limit: int = 5) -> list[ExaResourceHit]:
        """Search current external facts that are not textbook knowledge points.

        This deliberately uses the same approved Exa adapter as video/reference
        retrieval, but does not append a Chinese-medicine suffix.  It is used
        for time-sensitive questions such as exam dates and weather.
        """
        return await self.search_resources(query, resource_type="web", limit=limit)

    async def search_web_knowledge(self, query: str, limit: int = 5) -> list[ExaResourceHit]:
        """Search general web content for a knowledge concept (definitions, explanations).

        Unlike ``search_web`` (current facts), this keeps a light medicine
        context suffix so concept-definition lookups stay on-topic while still
        covering textbook gaps (e.g. 概念定义 医学).
        """
        return await self.search_resources(
            query, resource_type="web", limit=limit, suffix="医学 概念 定义 讲解"
        )

    async def livecrawl_urls(
        self, urls: list[str], *, max_chars: int = 3000
    ) -> list[ExaResourceHit]:
        """Force-fetch fresh content for specific URLs, bypassing the Exa cache.

        Exa livecrawl（``max_age_hours=0``）对目标 URL 实时抓取页面并渲染
        JS 内容，适用于：检索命中的题库页面答案/解析需点击或 JS 加载、缓存
        版本内容不完整时，由知识库管理智能体对已知 URL 重新抓取补充证据。
        注意：答案在点击后由 AJAX 拉取的页面（如长北医考 zuotishi.com）
        连 livecrawl 也拿不到——这类限制无法绕过。

        实测说明：text 选项必须传 True（传 ``{"maxCharacters": N}`` 对象时
        livecrawl 返回空结果），文本长度在工具层自行截断。
        """
        urls = [str(u).strip() for u in urls if str(u).strip()]
        if not urls:
            return []
        hits: list[ExaResourceHit] = []
        try:
            response = await self._call_with_limits(
                lambda: self.client.get_contents(
                    urls[:5],
                    text=True,
                    max_age_hours=0,
                )
            )
        except Exception as exc:
            emit_runtime_event(
                "web_search_status",
                provider="exa",
                resource_type="livecrawl",
                status="failed",
                result_count=0,
                error_type=type(exc).__name__,
            )
            return []
        results = (
            getattr(response, "results", [])
            if not isinstance(response, dict)
            else response.get("results", [])
        )
        for index, item in enumerate(results, start=1):
            value = item if isinstance(item, dict) else vars(item)
            url = value.get("url")
            if not url:
                continue
            title = str(value.get("title") or "未命名资源")
            text = str(value.get("text") or value.get("summary") or "")
            hits.append(
                ExaResourceHit(
                    source_id=f"EXA_LIVE_{index}",
                    title=title,
                    summary=text[:max_chars] or title,
                    url=str(url),
                    score=max(0.0, min(1.0, float(value.get("score") or 0.5))),
                    resource_type="web",
                )
            )
        emit_runtime_event(
            "web_search_status",
            provider="exa",
            resource_type="livecrawl",
            status="success" if hits else "empty",
            result_count=len(hits),
        )
        return hits

    async def search_resources(
        self, query: str, *, resource_type: str, limit: int = 5, suffix: str | None = None
    ) -> list[ExaResourceHit]:
        if not query.strip() or limit <= 0:
            return []
        suffixes = {
            "video": "中医药 教学 视频 讲解",
            "reference": "中医药 教学 参考资料 原文 论文",
            "question": "中医药 练习题 考试题 题目 解析 答案",
            "web": "",
        }
        if resource_type not in suffixes:
            raise ValueError(f"unsupported Exa resource type: {resource_type}")
        search_query = f"{query} {suffix if suffix is not None else suffixes[resource_type]}"
        # question 类证据需要抓全题干 + 选项 + 标准答案（常位于页面后半段）：
        # 用整页全文 text 而非 highlights 片段（500 字符），否则静态题库页的答案
        # 会被截断，专家无法采信考试口径。上限 3000 字符足够容纳题干+选项+答案
        # （实测答案页 <700 字符），又不至于把论文全文/导航噪音塞进上下文导致超时。
        # 其他资源类型内容较短，保持 highlights 以控制 token 成本。
        if resource_type == "question":
            contents: dict[str, Any] = {"text": {"maxCharacters": 3000}}
        else:
            contents = {"highlights": {"max_characters": 500}}
        options: dict[str, Any] = {
            "type": "auto",
            "num_results": min(limit, 10),
            "contents": contents,
        }
        if resource_type == "video":
            options["include_domains"] = [
                "youtube.com", "www.youtube.com", "bilibili.com", "www.bilibili.com"
            ]
        try:
            response = await self._call_with_limits(
                lambda: self.client.search(search_query, **options)
            )
            hits = self._parse_results(response, resource_type)
        except Exception as exc:
            emit_runtime_event(
                "web_search_status",
                provider="exa",
                resource_type=resource_type,
                status="failed",
                result_count=0,
                error_type=type(exc).__name__,
            )
            return []
        emit_runtime_event(
            "web_search_status",
            provider="exa",
            resource_type=resource_type,
            status="success" if hits else "empty",
            result_count=len(hits),
        )
        return hits

    @staticmethod
    def _parse_results(body: Any, resource_type: str = "video") -> list[ExaResourceHit]:
        results = (
            body.get("results", [])
            if isinstance(body, dict)
            else getattr(body, "results", [])
        )
        hits: list[ExaResourceHit] = []
        for index, item in enumerate(results, start=1):
            value = item if isinstance(item, dict) else vars(item)
            if not value.get("url"):
                continue
            highlights = value.get("highlights") or []
            summary = " ".join(str(value).strip() for value in highlights if str(value).strip())
            title = str(value.get("title") or "未命名资源")
            hits.append(
                ExaResourceHit(
                    source_id=f"EXA_{resource_type.upper()}_{index}",
                    title=title,
                    summary=(
                        summary
                        or str(value.get("summary") or value.get("text") or title)
                    ),
                    url=str(value["url"]),
                    score=max(0.0, min(1.0, float(value.get("score") or 0.5))),
                    resource_type=resource_type,
                )
            )
        return hits
