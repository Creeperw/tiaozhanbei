from __future__ import annotations
from io import BytesIO
from pathlib import Path
import pytest
from pypdf import PdfWriter
from competition_app.services.textbook_import import (
    TOC_EXTRACTION_FAILED,
    TextbookImportService,
    TextbookTocNotFound,
)
def sample_pdf() -> bytes:
    stream = BytesIO()
    writer = PdfWriter()
    for _ in range(8):
        writer.add_blank_page(width=595, height=842)
    writer.write(stream)
    return stream.getvalue()
class FakeTextbookImportService(TextbookImportService):
    async def _locate_toc(self, pdf_path, document):
        return {"has_toc": True, "toc_pdf_pages": [2], "page_number_anchors": []}
    async def _extract_toc(self, pdf_path, toc_pages):
        return {
            "book_title": "测试教材",
            "summary": "测试教材介绍。",
            "chapters": [{
                "title": "第一章 导论",
                "printed_page": 1,
                "sections": [{"title": "第一节 基础", "printed_page": 2}],
            }],
        }
    def _run_mineru(self, pdf_path: Path, output_dir: Path):
        target = output_dir / "normalized_books" / "book" / "book_clean.md"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("# 第一章 导论\n## 第一节 基础", encoding="utf-8")
        return target, {4: "第一章 导论", 5: "第一节 基础"}
    def _save_cover(self, pdf_path, book_dir, custom_cover, media_type):
        target = book_dir / "cover.jpg"
        target.write_bytes(b"cover")
        return target.name
@pytest.mark.asyncio
async def test_textbook_import_persists_category_toc_and_mineru_output(tmp_path: Path):
    service = FakeTextbookImportService(
        runtime_root=tmp_path,
        chat_base_url="https://example.test/v1",
        chat_model="vision-model",
        chat_api_key="configured",
        mineru_token="configured",
        mineru_pipeline_root=tmp_path,
    )
    item = await service.import_pdf(
        owner_id="U1",
        filename="测试教材.pdf",
        content=sample_pdf(),
        new_category="法学",
    )
    assert item["category"] == "法学"
    assert item["toc_parser"] == "multimodal_llm"
    assert item["content_parser"] == "mineru"
    assert item["toc"]["chapters"][0]["pdf_page"] == 4
    assert item["toc"]["chapters"][0]["sections"][0]["pdf_page"] == 5
    assert (service.runtime_root / "U1" / item["book_id"] / "manifest.json").is_file()
    assert service.categories() == ["中医药", "法学"]
@pytest.mark.asyncio
async def test_textbook_import_falls_back_to_flat_toc_when_missing(tmp_path: Path):
    service = FakeTextbookImportService(
        runtime_root=tmp_path,
        chat_base_url="https://example.test/v1",
        chat_model="vision-model",
        chat_api_key="configured",
        mineru_token="configured",
        mineru_pipeline_root=tmp_path,
    )

    async def missing_toc(pdf_path, document):
        return {"has_toc": False, "toc_pdf_pages": []}

    service._locate_toc = missing_toc
    item = await service.import_pdf(
        owner_id="U1",
        filename="无目录教材.pdf",
        content=sample_pdf(),
    )
    assert item["toc_status"] in {"headings", "flat"}
    assert item["toc"]["chapters"]