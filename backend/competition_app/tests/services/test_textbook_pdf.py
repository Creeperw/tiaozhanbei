from __future__ import annotations

import json

from competition_app.services.textbook_pdf import (
    InMemoryTextbookPdfAnnotationRepository,
    TextbookPdfService,
)


def test_textbook_pdf_prefers_newer_edition_and_persists_page_data(tmp_path):
    pdf_root = tmp_path / "pdfs"
    pdf_root.mkdir()
    (pdf_root / "new.pdf").write_bytes(b"%PDF-1.7\n")
    (pdf_root / "old.pdf").write_bytes(b"%PDF-1.7\n")
    catalog = tmp_path / "catalog.json"
    catalog.write_text(json.dumps({"books": [
        {
            "book_id": "old",
            "title": "中医基础理论",
            "aliases": ["中医基础理论学"],
            "edition": "十三五",
            "relative_path": "old.pdf",
        },
        {
            "book_id": "new",
            "title": "中医基础理论",
            "aliases": ["中医基础理论学"],
            "edition": "十四五",
            "relative_path": "new.pdf",
        },
    ]}, ensure_ascii=False), encoding="utf-8")
    repository = InMemoryTextbookPdfAnnotationRepository()
    service = TextbookPdfService(pdf_root, catalog, repository)

    resolved = service.resolve("《中医基础理论学》")
    assert resolved is not None
    assert resolved["book_id"] == "new"
    assert resolved["edition"] == "十四五"
    assert resolved["available"] is True
    assert service.file_path("new") == pdf_root / "new.pdf"

    annotations = [{"id": "a1", "type": "pen", "points": [{"x": 0.1, "y": 0.2}]}]
    assert repository.save_page("u1", "new", 10, annotations)["annotations"] == annotations
    assert repository.get_page("u1", "new", 10)["annotations"] == annotations
    assert repository.get_page("u2", "new", 10)["annotations"] == []
    assert repository.save_reading_state("u1", "new", 10, 1.2) == {
        "book_id": "new", "page_number": 10, "zoom": 1.2,
    }


def test_textbook_pdf_rejects_missing_or_escaped_files(tmp_path):
    pdf_root = tmp_path / "pdfs"
    pdf_root.mkdir()
    catalog = tmp_path / "catalog.json"
    catalog.write_text(json.dumps({"books": [{
        "book_id": "missing",
        "title": "不存在教材",
        "aliases": [],
        "edition": "十四五",
        "relative_path": "../outside.pdf",
    }]}), encoding="utf-8")
    service = TextbookPdfService(
        pdf_root, catalog, InMemoryTextbookPdfAnnotationRepository()
    )

    assert service.resolve("不存在教材")["available"] is False
    assert service.file_path("missing") is None
    assert service.resolve("未收录教材") is None
