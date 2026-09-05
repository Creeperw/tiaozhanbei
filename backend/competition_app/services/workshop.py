from __future__ import annotations

import asyncio
import hashlib
from typing import Any

from competition_app.contracts.workshop import KnowledgeResourceBundle, ResourceCoverage


def _plain(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if hasattr(value, "__dict__"):
        return {
            key: item
            for key, item in vars(value).items()
            if not str(key).startswith("_")
        }
    return value


def _title(kp: dict[str, Any]) -> str:
    return str(
        kp.get("kp_lv3")
        or kp.get("lv3")
        or kp.get("name")
        or kp.get("kp_id")
        or kp.get("id")
        or "知识点"
    )


class WorkshopKnowledgeService:
    """Build one complete, source-labelled card payload for a formal KP."""

    def __init__(self, knowledge_backend, retrieval_tool) -> None:
        self.knowledge_backend = knowledge_backend
        self.retrieval_tool = retrieval_tool

    async def resolve(self, kp_id: str, *, question_limit: int = 10) -> KnowledgeResourceBundle:
        if self.knowledge_backend is None:
            raise RuntimeError("正式知识仓库未启用")
        detail = await asyncio.to_thread(
            self.knowledge_backend.map.detail,
            kp_id,
            question_limit=question_limit,
        )
        kp = dict(detail.get("kp") or {})
        title = _title(kp)
        chunks = [dict(item) for item in detail.get("chunks") or []]
        videos = [self._local_video(item) for item in detail.get("videos") or []]
        questions = [self._local_question(item) for item in detail.get("questions") or []]
        fallback_used: list[str] = []

        if not videos:
            external_videos = await self.retrieval_tool.search_video_resources(title, limit=3)
            videos = [self._external_resource(item) for item in external_videos]
            if videos:
                fallback_used.append("video")
        if not questions:
            external_questions = await self.retrieval_tool.search_question_resources(title, limit=5)
            questions = [self._external_resource(item) for item in external_questions]
            if questions:
                fallback_used.append("question")

        explanation_text = "\n\n".join(
            str(chunk.get("retrieval_text") or chunk.get("text") or "").strip()
            for chunk in chunks[:3]
            if str(chunk.get("retrieval_text") or chunk.get("text") or "").strip()
        )
        explanation = {
            "title": title,
            "content": explanation_text or str(kp.get("description") or title),
            "source": "textbook_slices" if explanation_text else "knowledge_point",
        }
        provenance = [
            {
                "kind": "knowledge_point",
                "source_id": str(kp.get("kp_id") or kp.get("id") or kp_id),
                "origin": "knowledge_repository",
            }
        ]
        provenance.extend(
            {
                "kind": "textbook_slice",
                "source_id": str(item.get("chunk_uid") or item.get("uid") or ""),
                "origin": "knowledge_repository",
            }
            for item in chunks
        )
        provenance.extend(
            {
                "kind": "video",
                "source_id": str(item.get("source_id") or item.get("bvid") or ""),
                "origin": str(item.get("origin") or "knowledge_repository"),
            }
            for item in videos
        )
        provenance.extend(
            {
                "kind": "question",
                "source_id": str(item.get("question_id") or item.get("source_id") or ""),
                "origin": str(item.get("origin") or "knowledge_repository"),
            }
            for item in questions
        )
        digest = hashlib.sha1(
            f"{kp_id}|{len(chunks)}|{len(videos)}|{len(questions)}".encode("utf-8")
        ).hexdigest()[:16]
        return KnowledgeResourceBundle(
            bundle_id=f"KRB_{digest}",
            knowledge_point={
                **kp,
                "kp_id": str(kp.get("kp_id") or kp.get("id") or kp_id),
                "title": title,
            },
            explanation=explanation,
            textbook_slices=chunks,
            videos=videos,
            questions=questions,
            coverage=ResourceCoverage(
                knowledge_point=True,
                explanation=bool(explanation["content"]),
                textbook_slices=bool(chunks),
                videos=bool(videos),
                questions=bool(questions),
                fallback_used=fallback_used,
            ),
            provenance=provenance,
        )

    @staticmethod
    def _local_video(item: dict[str, Any]) -> dict[str, Any]:
        value = dict(item)
        bvid = str(value.get("bvid") or "")
        page = int(value.get("page") or 1)
        start = int(float(value.get("start_seconds") or 0))
        return {
            **value,
            "origin": "knowledge_repository",
            "source_id": f"{bvid}:p{page}:{start}",
            "url": f"https://www.bilibili.com/video/{bvid}?p={page}&t={start}" if bvid else "",
        }

    @staticmethod
    def _local_question(item: dict[str, Any]) -> dict[str, Any]:
        value = dict(item)
        return {
            "question_id": str(value.get("question_id") or value.get("题目id") or value.get("id") or ""),
            "question_type": str(value.get("question_type") or value.get("题型") or ""),
            "stem": str(value.get("question_content") or value.get("题目内容") or value.get("stem") or value.get("content") or ""),
            "options": value.get("options") or [],
            "reference_answer": value.get("answer", value.get("题目答案", "")),
            "analysis": str(value.get("explanation") or value.get("题目解析") or value.get("analysis") or ""),
            "origin": "knowledge_repository",
        }

    @staticmethod
    def _external_resource(item: Any) -> dict[str, Any]:
        value = _plain(item)
        if not isinstance(value, dict):
            value = {"summary": str(value)}
        return {
            "source_id": str(value.get("source_id") or ""),
            "title": str(value.get("title") or "网络补充资源"),
            "summary": str(value.get("summary") or ""),
            "url": str(value.get("url") or ""),
            "score": float(value.get("score") or 0.0),
            "resource_type": str(value.get("resource_type") or ""),
            "origin": "web_search",
        }
