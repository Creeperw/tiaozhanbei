from __future__ import annotations

from dataclasses import dataclass
from typing import Any

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


class ExaVideoRetriever:
    """Search external teaching videos, references, and question resources through Exa."""

    def __init__(self, api_key: str, *, client: Any | None = None) -> None:
        self.client = client or AsyncExa(api_key)

    async def search_videos(self, query: str, limit: int = 5) -> list[ExaResourceHit]:
        return await self.search_resources(query, resource_type="video", limit=limit)

    async def search_references(self, query: str, limit: int = 5) -> list[ExaResourceHit]:
        return await self.search_resources(query, resource_type="reference", limit=limit)

    async def search_questions(self, query: str, limit: int = 5) -> list[ExaResourceHit]:
        return await self.search_resources(query, resource_type="question", limit=limit)

    async def search_resources(
        self, query: str, *, resource_type: str, limit: int = 5
    ) -> list[ExaResourceHit]:
        if not query.strip() or limit <= 0:
            return []
        suffixes = {
            "video": "中医药 教学 视频 讲解",
            "reference": "中医药 教学 参考资料 原文 论文",
            "question": "中医药 练习题 考试题 题目 解析",
        }
        if resource_type not in suffixes:
            raise ValueError(f"unsupported Exa resource type: {resource_type}")
        search_query = f"{query} {suffixes[resource_type]}"
        options: dict[str, Any] = {
            "type": "auto",
            "num_results": min(limit, 10),
            "contents": {"highlights": {"max_characters": 500}},
        }
        if resource_type == "video":
            options["include_domains"] = [
                "youtube.com", "www.youtube.com", "bilibili.com", "www.bilibili.com"
            ]
        try:
            response = await self.client.search(search_query, **options)
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