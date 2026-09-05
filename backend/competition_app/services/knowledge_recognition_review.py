from __future__ import annotations

import json
import re
import statistics
import unicodedata
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


REPORT_SCHEMA_VERSION = "1.0"
REPORT_BASIS = "deterministic_structural_rules_v1"
REPORT_FILE = "recognition_quality_report.json"
ISSUE_LIMIT = 100


def _safe_segment(value: str, label: str) -> str:
    segment = str(value or "").strip()
    if not segment or not re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", segment):
        raise ValueError(f"{label}格式无效")
    return segment


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError(f"{path.name} 必须是 JSON 对象")
    return value


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8-sig") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError(f"{path.name} 第 {line_number} 行不是对象")
            rows.append(value)
    return rows


def _book_key(value: Any) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).casefold()
    text = re.sub(r"(?:_clean|\.clean)$", "", text)
    return re.sub(r"[^0-9a-z\u3400-\u9fff]+", "", text)


def _text_of(row: dict[str, Any]) -> str:
    return str(row.get("retrieval_text") or row.get("text") or "").strip()


def _level(score: float) -> str:
    if score >= 0.95:
        return "excellent"
    if score >= 0.85:
        return "good"
    if score >= 0.70:
        return "review"
    return "poor"


def _ratio(numerator: int, denominator: int, *, empty: float = 0.0) -> float:
    return numerator / denominator if denominator else empty


def _created_at(path: Path, result: dict[str, Any]) -> str:
    for key in ("created_at", "completed_at", "updated_at"):
        value = result.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat()


def _summary(report: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "report_id", "created_at", "title", "status", "confidence_level",
        "structural_confidence", "book_count", "markdown_file_count",
        "source_chunk_count", "mapped_chunk_count", "needs_review_chunk_count",
        "issue_count", "selection_method",
    )
    return {key: report.get(key) for key in keys}


class KnowledgeRecognitionReportReader:
    """Builds owner-scoped, read-only structural recognition quality reports."""

    def __init__(self, runtime_root: Path) -> None:
        self.runtime_root = Path(runtime_root).resolve()

    def _owner_root(self, owner_id: str) -> Path:
        owner = _safe_segment(owner_id, "owner_id")
        root = (self.runtime_root / "knowledge_runs" / owner).resolve()
        expected = (self.runtime_root / "knowledge_runs").resolve()
        if expected not in root.parents:
            raise ValueError("owner_id 格式无效")
        return root

    def _run_dir(self, owner_id: str, report_id: str) -> Path:
        root = self._owner_root(owner_id)
        report = _safe_segment(report_id, "report_id")
        path = (root / report).resolve()
        if root not in path.parents:
            raise ValueError("report_id 格式无效")
        if not path.is_dir() or not (path / "result.json").is_file():
            raise KeyError("识别审查报告不存在")
        return path

    def list_reports(self, owner_id: str, *, offset: int = 0, limit: int = 20) -> dict[str, Any]:
        root = self._owner_root(owner_id)
        runs = sorted(
            (path.parent for path in root.glob("*/result.json")),
            key=lambda path: ((path / "result.json").stat().st_mtime_ns, path.name),
            reverse=True,
        ) if root.is_dir() else []
        total = len(runs)
        start = max(0, int(offset))
        size = min(100, max(1, int(limit)))
        items = [_summary(self._load_or_build(owner_id, path)) for path in runs[start:start + size]]
        return {
            "items": items,
            "total": total,
            "offset": start,
            "limit": size,
            "has_more": start + len(items) < total,
        }

    def get_report(self, owner_id: str, report_id: str) -> dict[str, Any]:
        return self._load_or_build(owner_id, self._run_dir(owner_id, report_id))

    def create_snapshot(self, owner_id: str, run_dir: Path, delivery: Path) -> dict[str, Any]:
        owner_root = self._owner_root(owner_id)
        resolved_run = Path(run_dir).resolve()
        if owner_root not in resolved_run.parents:
            raise ValueError("运行目录不属于当前用户")
        report = self._build(resolved_run, Path(delivery).resolve())
        (resolved_run / REPORT_FILE).write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return report

    def _load_or_build(self, owner_id: str, run_dir: Path) -> dict[str, Any]:
        snapshot = run_dir / REPORT_FILE
        if snapshot.is_file():
            try:
                value = _read_json(snapshot)
                if value.get("schema_version") == REPORT_SCHEMA_VERSION:
                    return value
            except (OSError, ValueError, json.JSONDecodeError):
                pass
        result = _read_json(run_dir / "result.json")
        delivery_value = result.get("customer_delivery") or result.get("delivery")
        if delivery_value:
            delivery = Path(str(delivery_value)).resolve()
        else:
            owner = _safe_segment(owner_id, "owner_id")
            delivery = self.runtime_root / "knowledge_customers" / owner / "TCM_backend_delivery"
        return self._build(run_dir, delivery)

    def _build(self, run_dir: Path, delivery: Path) -> dict[str, Any]:
        result_path = run_dir / "result.json"
        result: dict[str, Any] = {}
        issues: list[dict[str, Any]] = []
        artifact_status: list[dict[str, Any]] = []

        def issue(code: str, severity: str, message: str, artifact: str, **context: Any) -> None:
            issues.append({
                "code": code,
                "severity": severity,
                "message": message,
                "artifact": artifact,
                **{key: value for key, value in context.items() if value not in (None, "")},
            })

        try:
            result = _read_json(result_path)
            artifact_status.append({"artifact": "result", "present": True, "readable": True, "record_count": 1})
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            artifact_status.append({"artifact": "result", "present": result_path.is_file(), "readable": False, "record_count": 0})
            issue("JSON_INVALID", "error", str(exc), "result")

        markdown_files = sorted((run_dir / "normalized_books").rglob("*.md")) if (run_dir / "normalized_books").is_dir() else []
        markdown_keys: set[str] = set()
        markdown_stats = {
            "file_count": len(markdown_files), "matched_file_count": 0,
            "total_characters": 0, "heading_count": 0,
            "replacement_character_count": 0, "nul_character_count": 0,
            "very_long_line_count": 0,
        }
        for path in markdown_files:
            try:
                text = path.read_text(encoding="utf-8-sig")
            except (OSError, UnicodeError) as exc:
                issue("NORMALIZED_MARKDOWN_UNREADABLE", "error", str(exc), "normalized_markdown")
                continue
            markdown_keys.add(_book_key(path.stem))
            markdown_stats["total_characters"] += len(text)
            markdown_stats["heading_count"] += sum(1 for line in text.splitlines() if re.match(r"^#{1,6}\s+\S", line))
            markdown_stats["replacement_character_count"] += text.count("�")
            markdown_stats["nul_character_count"] += text.count("\x00")
            markdown_stats["very_long_line_count"] += sum(len(line) > 10_000 for line in text.splitlines())
            if not text.strip():
                issue("NORMALIZED_MARKDOWN_EMPTY", "error", f"{path.name} 为空", "normalized_markdown")
        artifact_status.append({
            "artifact": "normalized_markdown", "present": bool(markdown_files),
            "readable": bool(markdown_files), "record_count": len(markdown_files),
        })
        if not markdown_files:
            issue("ARTIFACT_MISSING", "error", "缺少标准化 Markdown", "normalized_markdown")
        if markdown_stats["replacement_character_count"]:
            issue("NORMALIZED_MARKDOWN_REPLACEMENT_CHARACTERS", "warning", "标准化文本含 Unicode 替换字符", "normalized_markdown")

        pipeline = delivery / "03_pipeline_chunks"
        asset_paths = {
            "source_chunks": pipeline / "source_chunks.jsonl",
            "chapter_nodes": pipeline / "chapter_nodes.jsonl",
            "chunk_chapter_links": pipeline / "chunk_chapter_links.jsonl",
        }
        assets: dict[str, list[dict[str, Any]]] = {}
        for name, path in asset_paths.items():
            try:
                rows = _read_jsonl(path)
                assets[name] = rows
                artifact_status.append({"artifact": name, "present": True, "readable": True, "record_count": len(rows)})
            except (OSError, ValueError, json.JSONDecodeError) as exc:
                assets[name] = []
                artifact_status.append({"artifact": name, "present": path.is_file(), "readable": False, "record_count": 0})
                issue("ARTIFACT_MISSING" if not path.is_file() else "JSONL_LINE_INVALID", "error", str(exc), name)

        nodes = assets["chapter_nodes"]
        book_nodes = [row for row in nodes if row.get("node_type") == "book"]
        selected_books: set[str] = set()
        selection_method = "unresolved"
        for row in book_nodes:
            source_name = Path(str(row.get("source_markdown") or "")).stem
            if source_name and _book_key(source_name) in markdown_keys:
                selected_books.add(str(row.get("book") or ""))
        if selected_books:
            selection_method = "source_markdown"
        else:
            book_by_key = {_book_key(row.get("book")): str(row.get("book") or "") for row in book_nodes}
            selected_books = {book_by_key[key] for key in markdown_keys if key in book_by_key}
            if selected_books:
                selection_method = "normalized_book_name"
        markdown_stats["matched_file_count"] = len(selected_books)
        if not selected_books:
            issue("RUN_BOOK_SELECTION_UNRESOLVED", "error", "无法将本次 Markdown 与教材节点唯一关联", "chapter_nodes")

        chunks = [row for row in assets["source_chunks"] if str(row.get("book") or "") in selected_books]
        links = [row for row in assets["chunk_chapter_links"] if str(row.get("book") or "") in selected_books]
        selected_nodes = [row for row in nodes if str(row.get("book") or "") in selected_books]
        chunk_uids = [str(row.get("chunk_uid") or "") for row in chunks]
        link_uids = [str(row.get("chunk_uid") or "") for row in links]
        chunk_counts = Counter(uid for uid in chunk_uids if uid)
        link_counts = Counter(uid for uid in link_uids if uid)
        unique_source = set(chunk_counts)
        uniquely_mapped = {uid for uid in unique_source if link_counts[uid] == 1}
        unmapped = sorted(uid for uid in unique_source if link_counts[uid] == 0)
        duplicate_links = sorted(uid for uid, count in link_counts.items() if count > 1)
        orphan_links = sorted(uid for uid in link_counts if uid not in unique_source)
        for uid in unmapped[:ISSUE_LIMIT]:
            issue("UNMAPPED_CHUNK", "error", "切片没有章节映射", "chunk_chapter_links", chunk_uid=uid)
        for uid in duplicate_links[:ISSUE_LIMIT]:
            issue("DUPLICATE_CHAPTER_LINK", "error", "切片存在重复章节映射", "chunk_chapter_links", chunk_uid=uid)

        chapter_by_id = {str(row.get("node_id") or ""): row for row in selected_nodes if row.get("node_type") == "chapter"}
        section_by_id = {str(row.get("node_id") or ""): row for row in selected_nodes if row.get("node_type") == "section"}
        valid_references = 0
        for link in links:
            chapter_id = str(link.get("chapter_id") or "")
            section_id = str(link.get("section_id") or "")
            chapter = chapter_by_id.get(chapter_id)
            section = section_by_id.get(section_id)
            valid = bool(chapter and section and str(section.get("parent_id") or "") == chapter_id)
            if valid:
                valid_references += 1
            elif len(issues) < ISSUE_LIMIT:
                issue("SECTION_PARENT_MISMATCH", "error", "章节或小节引用无效", "chunk_chapter_links", chunk_uid=link.get("chunk_uid"))

        usable_chunks = sum(bool(_text_of(row)) and bool(str(row.get("book") or "").strip()) for row in chunks)
        empty_chunks = [str(row.get("chunk_uid") or "") for row in chunks if not _text_of(row)]
        for uid in empty_chunks[:ISSUE_LIMIT]:
            issue("EMPTY_CHUNK_TEXT", "warning", "切片正文为空", "source_chunks", chunk_uid=uid)
        resolved_links = sum(str(row.get("review_status") or "resolved") == "resolved" for row in links)
        needs_review = len(links) - resolved_links
        method_counts = Counter(str(row.get("detection_method") or "unknown") for row in links)
        confidences = []
        for row in links:
            try:
                confidences.append(max(0.0, min(1.0, float(row.get("confidence")))))
            except (TypeError, ValueError):
                pass
        low_confidence = sum(value < 0.70 for value in confidences)
        if low_confidence:
            issue("LOW_CONFIDENCE_MAPPING", "warning", f"{low_confidence} 条映射低于 70%", "chunk_chapter_links")

        artifact_ratio = _ratio(sum(item["present"] and item["readable"] for item in artifact_status), len(artifact_status))
        mapping_ratio = _ratio(len(uniquely_mapped), len(unique_source))
        resolved_ratio = _ratio(resolved_links, len(links))
        integrity_ratio = _ratio(valid_references, len(links))
        usable_ratio = _ratio(usable_chunks, len(chunks))
        markdown_ratio = _ratio(markdown_stats["matched_file_count"], markdown_stats["file_count"])
        score = round(
            0.20 * artifact_ratio + 0.25 * mapping_ratio + 0.15 * resolved_ratio
            + 0.15 * integrity_ratio + 0.15 * usable_ratio + 0.10 * markdown_ratio,
            4,
        )
        status = "complete" if artifact_ratio == 1 and selected_books else "partial"
        if not result:
            status = "invalid"
        metrics = [
            {"key": "artifact_completeness", "label": "资产完整性", "ratio": round(artifact_ratio, 4), "numerator": sum(item["present"] and item["readable"] for item in artifact_status), "denominator": len(artifact_status), "passed": artifact_ratio == 1},
            {"key": "mapping_coverage", "label": "切片映射覆盖率", "ratio": round(mapping_ratio, 4), "numerator": len(uniquely_mapped), "denominator": len(unique_source), "passed": mapping_ratio == 1},
            {"key": "resolved_ratio", "label": "已解决映射比例", "ratio": round(resolved_ratio, 4), "numerator": resolved_links, "denominator": len(links), "passed": resolved_ratio >= 0.95},
            {"key": "reference_integrity", "label": "章节引用完整性", "ratio": round(integrity_ratio, 4), "numerator": valid_references, "denominator": len(links), "passed": integrity_ratio == 1},
            {"key": "usable_text_ratio", "label": "切片文本可用率", "ratio": round(usable_ratio, 4), "numerator": usable_chunks, "denominator": len(chunks), "passed": usable_ratio >= 0.99},
            {"key": "markdown_book_linkage", "label": "Markdown 教材关联率", "ratio": round(markdown_ratio, 4), "numerator": markdown_stats["matched_file_count"], "denominator": markdown_stats["file_count"], "passed": markdown_ratio == 1},
        ]
        issue_count_total = len(issues)
        title = str(result.get("title") or "").strip()
        if not title and markdown_files:
            title = markdown_files[0].stem
        return {
            "schema_version": REPORT_SCHEMA_VERSION,
            "confidence_basis": REPORT_BASIS,
            "read_only": True,
            "report_id": run_dir.name,
            "created_at": _created_at(result_path, result),
            "title": title or "用户教材",
            "status": status,
            "confidence_level": _level(score),
            "structural_confidence": score,
            "book_count": len(selected_books),
            "markdown_file_count": len(markdown_files),
            "source_chunk_count": len(chunks),
            "mapped_chunk_count": len(uniquely_mapped),
            "needs_review_chunk_count": needs_review,
            "issue_count": issue_count_total,
            "selection_method": selection_method,
            "books": sorted(selected_books),
            "metrics": metrics,
            "detection_methods": dict(sorted(method_counts.items())),
            "confidence_distribution": {
                "min": min(confidences) if confidences else None,
                "mean": round(statistics.fmean(confidences), 4) if confidences else None,
                "max": max(confidences) if confidences else None,
                "below_0_7": low_confidence,
                "below_0_9": sum(value < 0.90 for value in confidences),
            },
            "artifact_status": artifact_status,
            "normalized_markdown": markdown_stats,
            "mapping": {
                "unmapped_chunk_count": len(unmapped),
                "duplicate_link_chunk_count": len(duplicate_links),
                "orphan_link_count": len(orphan_links),
            },
            "issues": issues[:ISSUE_LIMIT],
            "issue_count_total": issue_count_total,
            "issues_truncated": issue_count_total > ISSUE_LIMIT,
        }
