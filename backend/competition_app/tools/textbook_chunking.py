# -*- coding: utf-8 -*-
"""用户教材切片与关联匹配。

把上传教材按目录章节的页码区间切成与本地 pipeline_chunks 相同格式的
chunk（heading_path 来自目录），批量 embedding 建索引，再匹配现有题库
与知识点库，产出关联报告。只做关联，不修改公共知识库。
"""
from __future__ import annotations

import json
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import faiss
import numpy as np

from competition_app.embeddings.base import EmbeddingModel

MIN_CHUNK = 200
TARGET_CHUNK = 600
MAX_CHUNK = 800
OVERLAP = 100
EMBED_BATCH = 64
QUESTION_TOP_K = 3
QUESTION_MIN_SCORE = 0.45


def _split_paragraphs(text: str) -> list[str]:
    return [para.strip() for para in re.split(r"\n\s*\n", text) if para.strip()]


def _hard_split(text: str, size: int, overlap: int) -> list[str]:
    pieces: list[str] = []
    start = 0
    while start < len(text):
        end = min(len(text), start + size)
        pieces.append(text[start:end].strip())
        if end >= len(text):
            break
        start = max(start + size - overlap, start + 1)
    return pieces


def _chunk_text(text: str, *, size: int = TARGET_CHUNK, overlap: int = OVERLAP) -> list[str]:
    paragraphs = _split_paragraphs(text)
    chunks: list[str] = []
    current = ""
    for para in paragraphs:
        if len(para) > MAX_CHUNK:
            if current:
                chunks.append(current)
                current = ""
            chunks.extend(_hard_split(para, MAX_CHUNK, overlap))
            continue
        if current and len(current) + len(para) + 1 > size:
            chunks.append(current)
            tail = current[-overlap:].strip() if overlap else ""
            current = tail + ("\n" + para if tail else para)
        else:
            current = ("\n".join(part for part in (current, para) if part)).strip()
    if current:
        chunks.append(current)
    return chunks


def leaf_sections(chapters: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """展开章节为带 pdf_page 的叶子（有小节用小节，否则用章）。"""
    leaves: list[dict[str, Any]] = []
    for chapter_index, chapter in enumerate(chapters, 1):
        if not isinstance(chapter, dict):
            continue
        chapter_title = str(chapter.get("title") or f"第{chapter_index}章").strip()
        sections = [sec for sec in (chapter.get("sections") or []) if isinstance(sec, dict)]
        if sections:
            for section_index, section in enumerate(sections, 1):
                page = section.get("pdf_page")
                if page:
                    leaves.append({
                        "path": [chapter_title, str(section.get("title") or f"第{section_index}节").strip()],
                        "pdf_page": int(page),
                    })
        else:
            page = chapter.get("pdf_page")
            if page:
                leaves.append({"path": [chapter_title], "pdf_page": int(page)})
    return sorted(leaves, key=lambda leaf: leaf["pdf_page"])


def _page_ranges(leaves: list[dict[str, Any]], page_count: int) -> list[tuple[int, int]]:
    ranges: list[tuple[int, int]] = []
    for index, leaf in enumerate(leaves):
        start = leaf["pdf_page"]
        end = (leaves[index + 1]["pdf_page"] - 1) if index + 1 < len(leaves) else page_count
        ranges.append((max(1, start), min(page_count, max(start, end))))
    return ranges


def chunk_book_by_toc(
    page_text: dict[int, str],
    chapters: list[dict[str, Any]],
    *,
    book_title: str,
    page_count: int,
    marker_prefix: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """返回 (叶子段, pipeline_chunks 格式 chunk 列表)。"""
    leaves = leaf_sections(chapters)
    ranges = _page_ranges(leaves, page_count)
    sections: list[dict[str, Any]] = []
    chunks: list[dict[str, Any]] = []
    for leaf, (start, end) in zip(leaves, ranges):
        text = " ".join(page_text.get(page, "") for page in range(start, end + 1))
        text = re.sub(r"\s+", " ", text).strip()
        if not text:
            continue
        sections.append({"path": leaf["path"], "pdf_pages": list(range(start, end + 1)), "text": text})
        for piece in _chunk_text(text):
            chunks.append({"path": leaf["path"], "content": piece, "pdf_pages": list(range(start, end + 1))})
    output: list[dict[str, Any]] = []
    for index, chunk in enumerate(chunks):
        path = chunk["path"]
        heading_path = [book_title, *path]
        section_title = path[-1] if path else ""
        prev_id = f"{index - 1:05d}" if index > 0 else None
        next_id = f"{index + 1:05d}" if index + 1 < len(chunks) else None
        content = chunk["content"]
        original = {
            "chunk_id": f"{index + 1:05d}",
            "text": content,
            "char_count": len(content),
            "heading_path": heading_path,
            "metadata": {
                "book": book_title,
                "heading_path": " > ".join(heading_path),
                "chunk_index": index,
                "total_chunks": len(chunks),
                "prev_chunk_id": prev_id,
                "next_chunk_id": next_id,
                "images": [],
                "kp_Lv1": book_title,
                "kp_Lv2": section_title,
                "catalog_path": path,
                "marker_id": f"{marker_prefix}__{index + 1:05d}",
                "pdf_pages": chunk["pdf_pages"],
            },
        }
        output.append({
            "type": "json_field",
            "source": f"{marker_prefix}_chunks.jsonl",
            "record_id": index,
            "field": "text",
            "chunk_id": index,
            "content": content,
            "original": original,
        })
    return sections, output


async def build_chunk_index(
    chunks: list[dict[str, Any]],
    embedding_model: EmbeddingModel,
    out_dir: Path,
) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    texts = [row["content"] for row in chunks]
    vectors: list[list[float]] = []
    for offset in range(0, len(texts), EMBED_BATCH):
        vectors.extend(await embedding_model.embed(texts[offset:offset + EMBED_BATCH]))
    matrix = np.asarray(vectors, dtype="float32").reshape(len(vectors), -1)
    normalized = matrix.copy()
    faiss.normalize_L2(normalized)
    index = faiss.IndexFlatIP(matrix.shape[1])
    index.add(normalized)
    faiss.write_index(index, str(out_dir / "index.faiss"))
    (out_dir / "metadata.jsonl").write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in chunks),
        encoding="utf-8",
    )
    return out_dir / "index.faiss"


def _load_question_index(vector_store_root: Path) -> tuple[Any, list[dict[str, Any]]]:
    root = vector_store_root / "indexes" / "题库"
    index_path = root / "index.faiss"
    metadata_path = root / "metadata.jsonl"
    if not index_path.is_file() or not metadata_path.is_file():
        return None, []
    try:
        index = faiss.deserialize_index(np.frombuffer(index_path.read_bytes(), dtype="uint8"))
        metadata = [
            json.loads(line)
            for line in metadata_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        return index, metadata
    except Exception:
        return None, []


async def match_chunks(
    chunks: list[dict[str, Any]],
    sections: list[dict[str, Any]],
    *,
    embedding_model: EmbeddingModel,
    vector_store_root: Path,
    kp_resolver: Any | None,
    out_path: Path,
) -> dict[str, int]:
    """每块：关联题目（向量）+ 知识点（按章节 resolve_topic）。写入 jsonl。"""
    index, metadata = _load_question_index(vector_store_root)
    question_rows: list[list[dict[str, Any]]] = [[] for _ in chunks]
    if index is not None:
        texts = [row["content"] for row in chunks]
        vectors: list[list[float]] = []
        for offset in range(0, len(texts), EMBED_BATCH):
            vectors.extend(await embedding_model.embed(texts[offset:offset + EMBED_BATCH]))
        matrix = np.asarray(vectors, dtype="float32").reshape(len(vectors), -1)
        normalized = matrix.copy()
        faiss.normalize_L2(normalized)
        if normalized.shape[1] == index.d:
            scores, positions = index.search(normalized, QUESTION_TOP_K)
            for row in range(len(chunks)):
                for score, position in zip(scores[row], positions[row]):
                    if position < 0 or position >= len(metadata):
                        continue
                    if float(score) < QUESTION_MIN_SCORE:
                        continue
                    item = metadata[int(position)]
                    original = item.get("original") if isinstance(item.get("original"), dict) else {}
                    question_rows[row].append({
                        "question_id": str(original.get("题目id") or ""),
                        "stem": str(original.get("题目内容") or item.get("content") or "")[:100],
                        "type": str(original.get("题型") or ""),
                        "source": str(original.get("题目大来源") or ""),
                        "score": round(float(score), 4),
                    })
    for row in range(len(chunks)):
        question_rows[row].sort(key=lambda hit: hit["score"], reverse=True)
        question_rows[row] = question_rows[row][:3]

    resolver = getattr(kp_resolver, "resolve_topic", None)
    section_kp: list[list[dict[str, Any]]] = [[] for _ in sections]
    if resolver is not None:
        warmup = getattr(kp_resolver, "_kp_search_entries", None)
        if callable(warmup):
            try:
                warmup()
            except Exception:
                pass

        def resolve(section: dict[str, Any]) -> list[dict[str, Any]]:
            query = " ".join(section["path"])
            try:
                raw = resolver(query, limit=3) or []
            except Exception:
                return []
            return [{
                "kp_id": str(candidate.get("kp_id") or ""),
                "kp_name": str(candidate.get("name") or ""),
                "score": round(min(1.0, float(candidate.get("score") or 0)), 4),
            } for candidate in raw if candidate.get("kp_id")]

        with ThreadPoolExecutor(max_workers=4) as pool:
            section_kp = list(pool.map(resolve, sections))

    # map section kp -> chunk rows
    section_chunk_start = 0
    matched_kp = 0
    matched_question = 0
    rows: list[dict[str, Any]] = []
    for chunk_index, row in enumerate(chunks):
        original = row.get("original") if isinstance(row.get("original"), dict) else {}
        metadata = original.get("metadata") if isinstance(original.get("metadata"), dict) else {}
        path = list(metadata.get("catalog_path") or [])
        section_index = next((i for i, section in enumerate(sections) if section["path"] == path), None)
        kp_hits = section_kp[section_index] if section_index is not None else []
        questions = question_rows[chunk_index]
        if kp_hits:
            matched_kp += 1
        if questions:
            matched_question += 1
        rows.append({
            "chunk_id": chunk_index,
            "content_head": str(row.get("content") or "")[:80],
            "heading_path": path,
            "kp_matches": kp_hits,
            "question_matches": questions,
            "matched_kp": bool(kp_hits),
            "matched_question": bool(questions),
        })
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )
    return {"chunk_count": len(chunks), "matched_kp": matched_kp, "matched_question": matched_question}