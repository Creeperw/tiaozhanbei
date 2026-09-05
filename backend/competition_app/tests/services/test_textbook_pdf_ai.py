from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from competition_app.services.textbook_pdf import (
    InMemoryTextbookPdfAnnotationRepository,
    TextbookPdfService,
)
from competition_app.services.textbook_pdf_ai import (
    MAX_TEXT_CHARS,
    TextbookPdfAiService,
    TextbookPdfAiSettings,
)


class FakePdfService:
    """替代 TextbookPdfService：file_path 指向内存路径，不校验文件内容。"""

    def __init__(self, pdf_path):
        self.pdf_path = pdf_path

    def file_path(self, book_id, owner_id=None):
        return self.pdf_path


def _mock_async_client(monkeypatch, captured, chunks):
    class MockStreamResponse:
        def __init__(self, lines):
            self.lines = lines

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        def raise_for_status(self):
            return None

        async def aiter_lines(self):
            for chunk in self.lines:
                yield chunk

    class MockAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        def stream(self, method, url, headers, json):
            captured["method"] = method
            captured["url"] = url
            captured["headers"] = headers
            captured["body"] = json
            return MockStreamResponse(chunks)

    monkeypatch.setattr(
        "competition_app.services.textbook_pdf_ai.httpx.AsyncClient",
        MockAsyncClient,
    )


@pytest.fixture
def ai_service(tmp_path):
    pdf_path = tmp_path / "sample.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n")  # pypdf 无法解析时返回空文本，足够覆盖空文本分支
    settings = TextbookPdfAiSettings(
        base_url="https://llm.example.com/v1",
        api_key="test-key",
        model="test-model",
    )
    return TextbookPdfAiService(FakePdfService(pdf_path), settings)


def test_page_text_missing_file_raises(tmp_path):
    service = TextbookPdfAiService(
        FakePdfService(tmp_path / "missing.pdf"),
        TextbookPdfAiSettings("https://x", "k", "m"),
    )
    with pytest.raises(FileNotFoundError):
        service.page_text("b1", 1, None, page_span=0)


def test_page_text_out_of_range_raises(tmp_path, monkeypatch):
    pdf_path = tmp_path / "out.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n")

    class FakeReader:
        pages = [SimpleNamespace(extract_text=lambda: "")]

    monkeypatch.setattr(
        "competition_app.services.textbook_pdf_ai.PdfReader",
        lambda _path: FakeReader(),
    )
    service = TextbookPdfAiService(
        FakePdfService(pdf_path),
        TextbookPdfAiSettings("https://x", "k", "m"),
    )
    with pytest.raises(IndexError):
        service.page_text("b1", 99, None, page_span=0)


def test_page_text_truncates_very_long_pages(tmp_path, monkeypatch):
    pdf_path = tmp_path / "long.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n")

    class FakeReader:
        pages = [SimpleNamespace(extract_text=lambda: "字" * 40_000)]

    monkeypatch.setattr(
        "competition_app.services.textbook_pdf_ai.PdfReader",
        lambda _path: FakeReader(),
    )
    service = TextbookPdfAiService(
        FakePdfService(pdf_path),
        TextbookPdfAiSettings("https://x", "k", "m"),
    )
    assert len(service.page_text("b1", 1, None, page_span=0)) <= MAX_TEXT_CHARS


def test_stream_calls_openai_compatible_endpoint_and_yields_deltas(ai_service, monkeypatch):
    chunks = [
        'data: {"choices":[{"delta":{"content":"你"}}]}\n\n',
        'data: {"choices":[{"delta":{"content":"好"}}]}\n\n',
        "data: [DONE]\n\n",
    ]
    captured = {}
    _mock_async_client(monkeypatch, captured, chunks)

    async def run():
        deltas = []
        result = await ai_service.stream(
            [{"role": "user", "content": "hi"}],
            on_delta=deltas.append,
        )
        return result, deltas

    result, deltas = asyncio.run(run())

    assert captured["url"] == "https://llm.example.com/v1/chat/completions"
    assert captured["headers"]["Authorization"] == "Bearer test-key"
    assert captured["body"]["model"] == "test-model"
    assert captured["body"]["stream"] is True
    assert "response_format" not in captured["body"]
    assert result == "你好"
    assert deltas == ["你", "好"]


def test_summarize_rejects_scan_only_pages(ai_service, monkeypatch):
    class FakeReader:
        pages = [SimpleNamespace(extract_text=lambda: "")]

    monkeypatch.setattr(
        "competition_app.services.textbook_pdf_ai.PdfReader",
        lambda _path: FakeReader(),
    )
    with pytest.raises(ValueError, match="扫描版"):
        asyncio.run(ai_service.summarize("b1", 1, None))


def test_chat_builds_messages_with_history(ai_service, monkeypatch):
    class FakeReader:
        pages = [SimpleNamespace(extract_text=lambda: "第一页正文内容")]

    monkeypatch.setattr(
        "competition_app.services.textbook_pdf_ai.PdfReader",
        lambda _path: FakeReader(),
    )
    captured = {}
    _mock_async_client(
        monkeypatch,
        captured,
        [
            'data: {"choices":[{"delta":{"content":"回答"}}]}\n\n',
            "data: [DONE]\n\n",
        ],
    )

    result = asyncio.run(ai_service.chat(
        "b1", 1, "这是什么？",
        [{"role": "user", "content": "之前的问题"}, {"role": "assistant", "content": "之前的回答"}],
        None,
        page_span=0,
    ))

    assert result == "回答"
    roles = [item["role"] for item in captured["body"]["messages"]]
    assert roles[:2] == ["system", "user"]
    assert roles[-1] == "user"
    assert captured["body"]["messages"][-1]["content"] == "这是什么？"
    assert len(captured["body"]["messages"]) == 5
