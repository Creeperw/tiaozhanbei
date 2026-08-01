"""电子教材页 AI 总结与问答服务。

与主链路 `container.chat_model`（强制 JSON 契约）不同，这里面向自由文本输出，
直接通过 OpenAI 兼容接口建立 httpx 流式通道（不携带 response_format），
文本来源为 pypdf 现场抽取的当前页 ± 上下文页。

会话复用 conversation_sessions / conversation_messages 表，
session_id 使用 "textbook-ai-" 前缀与 review-card 会话区分。
"""

from __future__ import annotations

import asyncio
import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable

import httpx
from pypdf import PdfReader

from competition_app.services.textbook_pdf import TextbookPdfService

MAX_TEXT_CHARS = 12_000

SESSION_PREFIX = "textbook-ai-"


@dataclass
class TextbookPdfAiSettings:
    base_url: str
    api_key: str
    model: str
    timeout_seconds: float = 120.0


class TextbookPdfAiService:
    def __init__(
        self,
        textbook_pdf_service: TextbookPdfService,
        settings: TextbookPdfAiSettings,
        conversation_repository: Any | None = None,
    ) -> None:
        self.textbook_pdf_service = textbook_pdf_service
        self.settings = settings
        self.conversation_repository = conversation_repository

    def page_text(
        self,
        book_id: str,
        page_number: int,
        owner_id: str | None = None,
        page_span: int = 0,
    ) -> str:
        """抽取当前页及上下文页文本（页码按 PDF 文件页，1 起）。"""
        path = self.textbook_pdf_service.file_path(book_id, owner_id)
        if path is None:
            raise FileNotFoundError("电子教材文件不存在")
        reader = PdfReader(path)
        total = len(reader.pages)
        if page_number < 1 or page_number > total:
            raise IndexError("页码超出教材范围")
        span = max(0, min(int(page_span or 0), 5))
        parts: list[str] = []
        for index in range(max(1, page_number - span), min(total, page_number + span) + 1):
            try:
                text = str(reader.pages[index - 1].extract_text() or "").strip()
            except Exception:
                text = ""
            if text:
                parts.append(f"（第 {index} 页）\n{text}")
        joined = "\n\n".join(parts)
        return joined[:MAX_TEXT_CHARS]

    async def stream(
        self,
        messages: list[dict[str, str]],
        on_delta: Callable[[str], None],
        temperature: float = 0.4,
    ) -> str:
        """流式调用 OpenAI 兼容 chat/completions，逐段回调增量文本。"""
        parts: list[str] = []
        payload: dict[str, Any] = {
            "model": self.settings.model,
            "messages": messages,
            "stream": True,
            "temperature": temperature,
        }
        async with httpx.AsyncClient(timeout=self.settings.timeout_seconds) as client:
            async with client.stream(
                "POST",
                f"{self.settings.base_url.rstrip('/')}/chat/completions",
                headers={"Authorization": f"Bearer {self.settings.api_key}"},
                json=payload,
            ) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if not line.startswith("data: "):
                        continue
                    data = line[6:]
                    if data == "[DONE]":
                        break
                    try:
                        event = json.loads(data)
                    except json.JSONDecodeError:
                        continue
                    choices = event.get("choices")
                    if not isinstance(choices, list) or not choices:
                        continue
                    delta_payload = choices[0].get("delta") or {}
                    content = delta_payload.get("content") or ""
                    if content:
                        parts.append(str(content))
                        on_delta(str(content))
        text = "".join(parts)
        if not text.strip():
            raise RuntimeError("模型流式输出为空")
        return text

    async def summarize(
        self,
        book_id: str,
        page_number: int,
        owner_id: str | None,
        page_span: int = 0,
        on_delta: Callable[[str], None] | None = None,
    ) -> str:
        page_text = await asyncio.to_thread(self.page_text, book_id, page_number, owner_id, page_span)
        if not page_text.strip():
            raise ValueError("本页暂无可识别的文字内容，可能是扫描版教材")
        messages = [
            {
                "role": "system",
                "content": (
                    "你是中医药教材电子版阅读助教。请基于给定页面的教材正文做总结，"
                    "保持学科严谨，不过度发挥；涉及诊疗内容时提示“不能替代专业诊断”。"
                ),
            },
            {
                "role": "user",
                "content": (
                    f"请总结以下教材第 {page_number} 页的内容（约 150-300 字，"
                    "分点列出要点，结尾给出一个思考问题）：\n\n" + page_text
                ),
            },
        ]
        return await self.stream(messages, on_delta or (lambda _: None))

    async def chat(
        self,
        book_id: str,
        page_number: int,
        question: str,
        history: list[dict[str, str]],
        owner_id: str | None,
        page_span: int = 0,
        on_delta: Callable[[str], None] | None = None,
    ) -> str:
        page_text = await asyncio.to_thread(self.page_text, book_id, page_number, owner_id, page_span)
        if not page_text.strip():
            raise ValueError("本页暂无可识别的文字内容，可能是扫描版教材")
        messages: list[dict[str, str]] = [
            {
                "role": "system",
                "content": (
                    "你是中医药教材电子版阅读助教，只依据给定页面的教材正文回答，"
                    "正文未覆盖的内容如实说明；涉及诊疗内容时提示“不能替代专业诊断”。"
                ),
            },
            {
                "role": "user",
                "content": f"以下是教材第 {page_number} 页的正文：\n\n{page_text}",
            },
        ]
        for item in history[-8:]:
            role = str(item.get("role") or "")
            content = str(item.get("content") or "").strip()
            if not content:
                continue
            messages.append({"role": "user" if role == "user" else "assistant", "content": content})
        messages.append({"role": "user", "content": question})
        return await self.stream(messages, on_delta or (lambda _: None), temperature=0.5)

    # ── 会话管理（复用 conversation_sessions 表，textbook-ai- 前缀）──

    def create_session(self, learner_id: str, title: str = "新对话") -> str:
        session_id = f"{SESSION_PREFIX}{uuid.uuid4().hex[:16]}"
        if self.conversation_repository is not None:
            self.conversation_repository.create_session(session_id, learner_id, title)
        return session_id

    def list_sessions(self, learner_id: str) -> list[dict[str, Any]]:
        if self.conversation_repository is None:
            return []
        return [
            session for session in self.conversation_repository.list_sessions(learner_id)
            if str(session.get("id") or "").startswith(SESSION_PREFIX)
        ]

    def get_messages(self, session_id: str, learner_id: str) -> list[dict[str, Any]]:
        if self.conversation_repository is None or not session_id.startswith(SESSION_PREFIX):
            return []
        return self.conversation_repository.get_messages(session_id, learner_id)

    def save_messages(
        self,
        session_id: str,
        learner_id: str,
        messages: list[dict[str, Any]],
    ) -> None:
        if self.conversation_repository is None or not session_id.startswith(SESSION_PREFIX):
            return
        self.conversation_repository.save_messages(session_id, learner_id, messages)

    def rename_session(self, session_id: str, learner_id: str, title: str) -> bool:
        if self.conversation_repository is None or not session_id.startswith(SESSION_PREFIX):
            return False
        return self.conversation_repository.rename_session(session_id, learner_id, title)

    def delete_session(self, session_id: str, learner_id: str) -> bool:
        if self.conversation_repository is None or not session_id.startswith(SESSION_PREFIX):
            return False
        return self.conversation_repository.delete_session(session_id, learner_id)
