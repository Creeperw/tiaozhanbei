#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Build an additive book/chapter/section index for source chunks.

The canonical ``source_chunks.jsonl`` is intentionally left untouched.  This
module writes a normalized node table and a one-to-one chunk mapping table.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Sequence


ROOT = Path(__file__).resolve().parent
DEFAULT_DATA = ROOT / "data" / "backend_delivery"
CN_DIGITS = "零〇一二三四五六七八九十百千万两"
MD_PREFIX_RE = re.compile(r"^\s{0,3}(#{1,6})\s*(.*?)\s*$")
PAGE_TRAIL_RE = re.compile(r"(?:\s*[.·…⋯]{2,}\s*\d{1,4}|\s+\d{1,4})\s*$")
CHAPTER_RE = re.compile(rf"^第([{CN_DIGITS}0-9]+)(章|篇|部分|单元)\s*(.*)$")
SECTION_RE = re.compile(rf"^第([{CN_DIGITS}0-9]+)节\s*(.*)$")
EXPERIMENT_RE = re.compile(rf"^实验([{CN_DIGITS}0-9]+)\s*(.*)$")
CLASSIC_RE = re.compile(rf"^(.{{2,60}}?)第([{CN_DIGITS}0-9]+)$")
TRAILING_OCR_TOKEN_RE = re.compile(r"\s+[a-z]{3,8}$")


def iter_jsonl(path: Path) -> Iterable[dict[str, Any]]:
    with path.open("r", encoding="utf-8-sig") as handle:
        for line in handle:
            if line.strip():
                yield json.loads(line)


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
            count += 1
    return count


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def compact(value: Any) -> str:
    value = unicodedata.normalize("NFKC", str(value or ""))
    return re.sub(r"\s+", " ", value).strip()


def clean_heading(raw: str) -> tuple[str, int]:
    text = compact(raw)
    level = 0
    match = MD_PREFIX_RE.match(text)
    if match:
        level = len(match.group(1))
        text = compact(match.group(2))
    text = re.sub(r"<[^>]+>", "", text)
    return compact(text).strip("-—–·.。"), level


def title_key(value: str) -> str:
    value = PAGE_TRAIL_RE.sub("", compact(value)).lower()
    return re.sub(r"[\s,，、.．。:：;；()（）\[\]【】<>《》'\"]", "", value)


def stable_id(prefix: str, value: str) -> str:
    return f"{prefix}_{hashlib.sha1(value.encode('utf-8')).hexdigest()[:16]}"


def normalized_book_key(value: str) -> str:
    value = compact(value)
    value = re.sub(r"_clean$", "", value, flags=re.I)
    value = re.sub(r"[-_－—]?修$", "", value)
    value = re.sub(r"__upload_[0-9a-f]{8,}$", "", value, flags=re.I)
    return title_key(value)


def chinese_number(value: str) -> int | None:
    value = value.replace("〇", "零").replace("两", "二")
    if value.isdigit():
        return int(value)
    digits = {"零": 0, "一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
    units = {"十": 10, "百": 100, "千": 1000, "万": 10000}
    if not value or any(ch not in digits and ch not in units for ch in value):
        return None
    total = section = number = 0
    for ch in value:
        if ch in digits:
            number = digits[ch]
            continue
        unit = units[ch]
        if unit == 10000:
            section = (section + number) * unit
            total += section
            section = number = 0
        else:
            section += (number or 1) * unit
            number = 0
    return total + section + number


@dataclass(frozen=True)
class Heading:
    line_no: int
    kind: str
    title: str
    number: int | None
    is_toc: bool
    method: str


@dataclass
class Assignment:
    chapter_name: str
    method: str
    confidence: float
    needs_review: bool
    unresolved_identifier: str = ""


def detect_heading(raw: str, line_no: int) -> Heading | None:
    title, md_level = clean_heading(raw)
    if not title or len(title) > 100:
        return None
    is_toc = bool(PAGE_TRAIL_RE.search(title))
    title = PAGE_TRAIL_RE.sub("", title).strip()
    # MinerU occasionally leaves a short lowercase OCR fragment after an
    # otherwise complete Chinese heading (for example ``evao`` or ``vixiu``).
    # Uppercase subject abbreviations such as DNA, RNA, PBL and SCE remain.
    title = TRAILING_OCR_TOKEN_RE.sub("", title).strip()
    match = CHAPTER_RE.match(title)
    if match:
        return Heading(line_no, "chapter", title, chinese_number(match.group(1)), is_toc, "standard_chapter")
    match = SECTION_RE.match(title)
    if match:
        return Heading(line_no, "section", title, chinese_number(match.group(1)), is_toc, "standard_section")
    match = EXPERIMENT_RE.match(title)
    if match:
        return Heading(line_no, "chapter", title, chinese_number(match.group(1)), is_toc, "experiment_heading")
    if title in {"绪论", "绪言"}:
        return Heading(line_no, "chapter", title, 0, is_toc, "introduction_heading")
    match = CLASSIC_RE.match(title)
    if match and (md_level > 0 or len(title) <= 42):
        return Heading(line_no, "chapter", title, chinese_number(match.group(2)), is_toc, "classic_heading")
    return None


def markdown_index(roots: Sequence[Path]) -> dict[str, list[Path]]:
    result: dict[str, list[Path]] = defaultdict(list)
    for root in roots:
        if not root.exists():
            continue
        candidates = [root] if root.is_file() else root.rglob("*.md")
        for path in candidates:
            if path.is_file() and path.suffix.lower() == ".md":
                result[normalized_book_key(path.stem)].append(path)
    return result


def choose_markdown(book: str, index: dict[str, list[Path]]) -> Path | None:
    candidates = index.get(normalized_book_key(book), [])
    if not candidates:
        return None
    exact = [path for path in candidates if title_key(path.stem) == title_key(book)]
    pool = exact or candidates
    return sorted(pool, key=lambda path: ("最终整理" not in str(path), len(str(path)), str(path)))[0]


def source_headings(path: Path | None) -> list[Heading]:
    if path is None:
        return []
    result = []
    for line_no, raw in enumerate(path.read_text(encoding="utf-8-sig", errors="replace").splitlines(), 1):
        heading = detect_heading(raw, line_no)
        if heading:
            result.append(heading)
    return result


def metadata_chapter(row: dict[str, Any]) -> str:
    metadata = row.get("metadata") or {}
    values: list[str] = []
    catalog = metadata.get("catalog_path") or []
    if isinstance(catalog, str):
        values.append(catalog)
    else:
        values.extend(map(str, catalog))
    heading_path = metadata.get("heading_path") or []
    if isinstance(heading_path, str):
        values.extend(part.strip() for part in heading_path.split(">"))
    else:
        values.extend(map(str, heading_path))
    for value in values:
        heading = detect_heading(value, 0)
        if heading and heading.kind == "chapter":
            return heading.title
    return ""


def catalog_section_map(headings: list[Heading], first_stage_line: int) -> dict[str, list[str]]:
    """Recover chapter->section relations from a front-matter table of contents."""
    current = ""
    result: dict[str, list[str]] = defaultdict(list)
    for heading in headings:
        if first_stage_line > 0 and heading.line_no > first_stage_line:
            break
        if heading.kind == "chapter":
            current = heading.title
        elif heading.kind == "section" and current:
            key = title_key(heading.title)
            if current not in result[key]:
                result[key].append(current)
    return result


def unique_stages(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    stages: dict[tuple[str, str, int], dict[str, Any]] = {}
    for row in rows:
        metadata = row.get("metadata") or {}
        line_no = int(metadata.get("stage1_source_line_no") or 0)
        stage = str(row.get("stage1_range_index") if row.get("stage1_range_index") is not None else "")
        key = (stage, str(row.get("kp_Lv2") or ""), line_no)
        stages.setdefault(key, row)
    return sorted(stages.values(), key=lambda row: (
        int((row.get("metadata") or {}).get("stage1_source_line_no") or 10**12),
        int(row.get("chunk_index") or 0),
    ))


def assign_stages(book: str, stages: list[dict[str, Any]], headings: list[Heading]) -> dict[tuple[str, str, int], Assignment]:
    source_lines = [int((row.get("metadata") or {}).get("stage1_source_line_no") or 0) for row in stages]
    first_stage_line = min((line for line in source_lines if line > 0), default=0)
    catalog = catalog_section_map(headings, first_stage_line)
    assignments: dict[tuple[str, str, int], Assignment] = {}
    previous_line = 0
    current_body_chapter = ""
    previous_section_number: int | None = None
    unresolved_order = 0
    catalog_uses: Counter[tuple[str, str]] = Counter()

    for row in stages:
        metadata = row.get("metadata") or {}
        line_no = int(metadata.get("stage1_source_line_no") or 0)
        stage = str(row.get("stage1_range_index") if row.get("stage1_range_index") is not None else "")
        section_name = compact(row.get("kp_Lv2") or metadata.get("kp_Lv2") or "未分节内容")
        key = (stage, str(row.get("kp_Lv2") or ""), line_no)

        embedded = metadata_chapter(row)
        if embedded:
            assignment = Assignment(embedded, "metadata_path", 0.99, False)
            current_body_chapter = embedded
        else:
            between = [
                heading for heading in headings
                if heading.kind == "chapter" and not heading.is_toc
                and ((previous_line < heading.line_no <= line_no) if previous_line else (0 < line_no - heading.line_no <= 250))
            ]
            if between:
                current_body_chapter = between[-1].title
                assignment = Assignment(current_body_chapter, "source_body_heading", 0.98, False)
            else:
                options = catalog.get(title_key(section_name), [])
                if options:
                    selected = min(options, key=lambda value: catalog_uses[(title_key(section_name), value)])
                    catalog_uses[(title_key(section_name), selected)] += 1
                    assignment = Assignment(selected, "source_catalog_section", 0.94, False)
                    current_body_chapter = selected
                else:
                    own_heading = detect_heading(section_name, line_no)
                    if own_heading and own_heading.kind == "chapter":
                        assignment = Assignment(own_heading.title, "section_as_chapter", 0.96, False)
                        current_body_chapter = own_heading.title
                    elif current_body_chapter:
                        assignment = Assignment(current_body_chapter, "carry_forward", 0.91, False)
                    else:
                        section_heading = detect_heading(section_name, line_no)
                        section_number = section_heading.number if section_heading and section_heading.kind == "section" else None
                        starts_group = unresolved_order == 0 or section_number == 1 or (
                            section_number is not None and previous_section_number is not None and section_number <= previous_section_number
                        )
                        if starts_group:
                            unresolved_order += 1
                        unresolved_order = max(1, unresolved_order)
                        unresolved = f"UNRESOLVED_{stable_id('BOOK', book)[5:13]}_{unresolved_order:04d}"
                        assignment = Assignment("未识别章节", "section_sequence_fallback", 0.35, True, unresolved)
            section_heading = detect_heading(section_name, line_no)
            if section_heading and section_heading.kind == "section":
                previous_section_number = section_heading.number
        assignments[key] = assignment
        if line_no:
            previous_line = line_no
    return assignments


def build_chapter_hierarchy(
    chunks_path: Path,
    markdown_roots: Sequence[Path],
    output_dir: Path,
) -> dict[str, Any]:
    rows = list(iter_jsonl(chunks_path))
    by_book: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_book[str(row.get("book") or "未命名书籍")].append(row)
    source_index = markdown_index(markdown_roots)
    links: list[dict[str, Any]] = []
    nodes: list[dict[str, Any]] = []
    source_files: dict[str, str | None] = {}
    unresolved_books: set[str] = set()
    method_counts: Counter[str] = Counter()

    for book_order, (book, book_rows) in enumerate(by_book.items(), 1):
        book_rows.sort(key=lambda row: (int(row.get("chunk_index") or 0), str(row.get("chunk_uid") or "")))
        markdown = choose_markdown(book, source_index)
        source_files[book] = markdown.name if markdown else None
        stages = unique_stages(book_rows)
        assignments = assign_stages(book, stages, source_headings(markdown))
        book_id = stable_id("BOOK", book)
        nodes.append({
            "schema_version": "1.0.0", "node_id": book_id, "parent_id": None,
            "node_type": "book", "book": book, "title": re.sub(r"_clean$", "", book),
            "order": book_order, "chunk_count": len(book_rows), "source_markdown": source_files[book],
        })

        chapter_state: dict[tuple[str, str], dict[str, Any]] = {}
        section_state: dict[tuple[str, str, int], dict[str, Any]] = {}
        chapter_order = 0
        section_counts: Counter[str] = Counter()
        stage_to_location: dict[tuple[str, str, int], tuple[dict[str, Any], dict[str, Any], Assignment]] = {}
        for stage_row in stages:
            meta = stage_row.get("metadata") or {}
            stage_key = (
                str(stage_row.get("stage1_range_index") if stage_row.get("stage1_range_index") is not None else ""),
                str(stage_row.get("kp_Lv2") or ""),
                int(meta.get("stage1_source_line_no") or 0),
            )
            assignment = assignments[stage_key]
            chapter_key = (assignment.chapter_name, assignment.unresolved_identifier)
            if chapter_key not in chapter_state:
                chapter_order += 1
                chapter_id = stable_id("CH", f"{book}|{chapter_order}")
                chapter_state[chapter_key] = {
                    "schema_version": "1.0.0", "node_id": chapter_id, "parent_id": book_id,
                    "node_type": "chapter", "book": book, "title": assignment.chapter_name,
                    "order": chapter_order, "chapter_order": chapter_order,
                    "unresolved_identifier": assignment.unresolved_identifier or None,
                    "detection_method": assignment.method, "confidence": assignment.confidence,
                    "review_status": "needs_review" if assignment.needs_review else "resolved",
                    "chunk_uids": [],
                }
            chapter = chapter_state[chapter_key]
            section_counts[chapter["node_id"]] += 1
            section_order = section_counts[chapter["node_id"]]
            section_name = compact(stage_row.get("kp_Lv2") or "未分节内容")
            section_id = stable_id("SEC", f"{chapter['node_id']}|{section_order}|{section_name}")
            section = {
                "schema_version": "1.0.0", "node_id": section_id, "parent_id": chapter["node_id"],
                "node_type": "section", "book": book, "title": section_name,
                "order": section_order, "chapter_order": chapter["chapter_order"],
                "section_order": section_order, "stage1_range_index": stage_key[0],
                "source_line_no": stage_key[2], "chunk_uids": [],
            }
            section_state[stage_key] = section
            stage_to_location[stage_key] = (chapter, section, assignment)

        for row in book_rows:
            meta = row.get("metadata") or {}
            stage_key = (
                str(row.get("stage1_range_index") if row.get("stage1_range_index") is not None else ""),
                str(row.get("kp_Lv2") or ""), int(meta.get("stage1_source_line_no") or 0),
            )
            chapter, section, assignment = stage_to_location[stage_key]
            uid = str(row.get("chunk_uid") or "")
            chapter["chunk_uids"].append(uid)
            section["chunk_uids"].append(uid)
            method_counts[assignment.method] += 1
            if assignment.needs_review:
                unresolved_books.add(book)
            links.append({
                "schema_version": "1.0.0", "chunk_uid": uid, "book": book,
                "chapter_id": chapter["node_id"], "chapter_name": chapter["title"],
                "chapter_order": chapter["chapter_order"],
                "section_id": section["node_id"], "section_name": section["title"],
                "section_order": section["section_order"],
                "detection_method": assignment.method, "confidence": assignment.confidence,
                "review_status": "needs_review" if assignment.needs_review else "resolved",
                "unresolved_identifier": assignment.unresolved_identifier or None,
            })
        for chapter in chapter_state.values():
            values = chapter.pop("chunk_uids")
            chapter.update({"chunk_count": len(values), "first_chunk_uid": values[0], "last_chunk_uid": values[-1]})
            nodes.append(chapter)
        for section in section_state.values():
            values = section.pop("chunk_uids")
            section.update({"chunk_count": len(values), "first_chunk_uid": values[0], "last_chunk_uid": values[-1]})
            nodes.append(section)

    source_uids = [str(row.get("chunk_uid") or "") for row in rows]
    link_uids = [row["chunk_uid"] for row in links]
    errors = []
    if len(source_uids) != len(set(source_uids)):
        errors.append("source_chunks contains duplicate chunk_uid")
    if len(link_uids) != len(set(link_uids)):
        errors.append("chunk_chapter_links contains duplicate chunk_uid")
    if set(source_uids) != set(link_uids):
        errors.append("chunk_chapter_links does not cover source_chunks exactly")
    node_ids = {row["node_id"] for row in nodes}
    if any(row["chapter_id"] not in node_ids or row["section_id"] not in node_ids for row in links):
        errors.append("chunk_chapter_links contains broken node references")

    write_jsonl(output_dir / "chapter_nodes.jsonl", nodes)
    write_jsonl(output_dir / "chunk_chapter_links.jsonl", links)
    report = {
        "schema_version": "1.0.0", "ok": not errors, "errors": errors,
        "source_chunks": len(rows), "mapped_chunks": len(links), "books": len(by_book),
        "books_with_source_markdown": sum(bool(value) for value in source_files.values()),
        "chapter_nodes": sum(row["node_type"] == "chapter" for row in nodes),
        "section_nodes": sum(row["node_type"] == "section" for row in nodes),
        "resolved_chunks": sum(row["review_status"] == "resolved" for row in links),
        "needs_review_chunks": sum(row["review_status"] == "needs_review" for row in links),
        "needs_review_books": sorted(unresolved_books),
        "detection_methods": dict(sorted(method_counts.items())),
        "source_markdown": source_files,
    }
    write_json(output_dir / "chapter_hierarchy_report.json", report)
    if errors:
        raise RuntimeError("; ".join(errors))
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="为切片生成书籍—章节—小节独立映射")
    parser.add_argument("--chunks", type=Path, default=DEFAULT_DATA / "03_pipeline_chunks" / "source_chunks.jsonl")
    parser.add_argument("--markdown-root", type=Path, action="append", default=[])
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_DATA / "03_pipeline_chunks")
    args = parser.parse_args()
    report = build_chapter_hierarchy(args.chunks.resolve(), [path.resolve() for path in args.markdown_root], args.output_dir.resolve())
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
