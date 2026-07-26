import json
from pathlib import Path

import pytest

from competition_app.services.knowledge_recognition_review import KnowledgeRecognitionReportReader


def write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")


def test_recognition_review_is_deterministic_and_owner_scoped(tmp_path: Path) -> None:
    reader = KnowledgeRecognitionReportReader(tmp_path)
    run_dir = tmp_path / "knowledge_runs" / "alice" / "run-a"
    write_json(run_dir / "result.json", {"title": "用户方剂学"})
    (run_dir / "normalized_books").mkdir(parents=True, exist_ok=True)
    (run_dir / "normalized_books" / "用户方剂学.md").write_text(
        "# 第一章 总论\n\n## 第一节 方剂基础\n\n四君子汤。", encoding="utf-8"
    )
    delivery = tmp_path / "knowledge_customers" / "alice" / "TCM_backend_delivery"
    pipeline = delivery / "03_pipeline_chunks"
    write_jsonl(pipeline / "source_chunks.jsonl", [
        {"chunk_uid": "用户方剂学_clean:00001", "book": "用户方剂学_clean", "text": "四君子汤。", "metadata": {}},
        {"chunk_uid": "用户方剂学_clean:00002", "book": "用户方剂学_clean", "text": "", "metadata": {}},
    ])
    write_jsonl(pipeline / "chapter_nodes.jsonl", [
        {"node_id": "BOOK_1", "node_type": "book", "book": "用户方剂学_clean", "title": "用户方剂学", "source_markdown": "用户方剂学.md"},
        {"node_id": "CH_1", "parent_id": "BOOK_1", "node_type": "chapter", "book": "用户方剂学_clean", "title": "第一章 总论"},
        {"node_id": "SEC_1", "parent_id": "CH_1", "node_type": "section", "book": "用户方剂学_clean", "title": "第一节 方剂基础"},
    ])
    write_jsonl(pipeline / "chunk_chapter_links.jsonl", [
        {"chunk_uid": "用户方剂学_clean:00001", "book": "用户方剂学_clean", "chapter_id": "CH_1", "section_id": "SEC_1", "review_status": "resolved", "detection_method": "source_body_heading", "confidence": 0.98},
        {"chunk_uid": "用户方剂学_clean:00002", "book": "用户方剂学_clean", "chapter_id": "CH_1", "section_id": "SEC_1", "review_status": "needs_review", "detection_method": "section_sequence_fallback", "confidence": 0.35},
    ])

    report = reader.create_snapshot("alice", run_dir, delivery)
    page = reader.list_reports("alice")

    assert report["confidence_basis"] == "deterministic_structural_rules_v1"
    assert report["read_only"] is True
    assert report["source_chunk_count"] == 2
    assert report["mapped_chunk_count"] == 2
    assert report["needs_review_chunk_count"] == 1
    assert {issue["code"] for issue in report["issues"]} >= {"EMPTY_CHUNK_TEXT", "LOW_CONFIDENCE_MAPPING"}
    assert page["total"] == 1
    assert page["items"][0]["report_id"] == "run-a"
    assert reader.list_reports("bob")["items"] == []
    with pytest.raises(KeyError):
        reader.get_report("bob", "run-a")
    with pytest.raises(ValueError):
        reader.get_report("alice", "../run-a")