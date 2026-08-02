#!/usr/bin/env python3
"""Stable command-line boundary for embedding TreeKG in an upload pipeline."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


SCHEMA_VERSION = "1.0.0"
INTEGRATION_DIR = Path(__file__).resolve().parent
TREEKG_ROOT = INTEGRATION_DIR.parent
SRC_DIR = TREEKG_ROOT / "src"


class ContractError(ValueError):
    """The caller supplied an invalid integration request."""

    code = "TREEKG_INVALID_REQUEST"


class PipelineError(RuntimeError):
    """A TreeKG processing stage failed."""

    code = "TREEKG_PIPELINE_FAILED"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def emit(job_id: str, stage: str, status: str, message: str, **extra: Any) -> None:
    event = {
        "type": "tree_kg_event",
        "schema_version": SCHEMA_VERSION,
        "job_id": job_id,
        "stage": stage,
        "status": status,
        "message": message,
        "timestamp": utc_now(),
        **extra,
    }
    print(json.dumps(event, ensure_ascii=False), flush=True)


def safe_token(value: str, field: str) -> str:
    token = re.sub(r"[^0-9A-Za-z_.-]+", "_", str(value or "").strip()).strip("._")
    if not token:
        raise ContractError(f"{field} 不能为空")
    return token[:128]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def load_request(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ContractError(f"无法读取任务 JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise ContractError("任务 JSON 必须是对象")
    if payload.get("schema_version") != SCHEMA_VERSION:
        raise ContractError(f"schema_version 必须为 {SCHEMA_VERSION}")
    for field in ("job_id", "user_id"):
        if not str(payload.get(field) or "").strip():
            raise ContractError(f"缺少必填字段: {field}")
    book = payload.get("book")
    if not isinstance(book, dict):
        raise ContractError("book 必须是对象")
    for field in ("book_id", "title"):
        if not str(book.get(field) or "").strip():
            raise ContractError(f"缺少必填字段: book.{field}")
    source = payload.get("source")
    if not isinstance(source, dict) or not str(source.get("chunks_path") or "").strip():
        raise ContractError("缺少必填字段: source.chunks_path")
    return payload


def resolve_source_path(request_path: Path, value: str) -> Path:
    path = Path(value)
    if not path.is_absolute():
        path = request_path.parent / path
    path = path.resolve()
    if not path.is_file():
        raise ContractError(f"切片文件不存在: {path}")
    return path


def iter_jsonl(path: Path) -> Iterable[tuple[int, dict[str, Any]]]:
    with path.open("r", encoding="utf-8-sig") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ContractError(f"切片 JSONL 第 {line_number} 行无效: {exc}") from exc
            if not isinstance(row, dict):
                raise ContractError(f"切片 JSONL 第 {line_number} 行必须是对象")
            yield line_number, row


def string_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str):
        return [item.strip() for item in re.split(r"\s*>\s*|\s*/\s*", value) if item.strip()]
    return []


def normalize_chunks(source: Path, target: Path, *, book_id: str, book_title: str) -> int:
    rows = list(iter_jsonl(source))
    if not rows:
        raise ContractError("切片文件为空")
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8", newline="\n") as handle:
        for index, (line_number, row) in enumerate(rows):
            original = row.get("original") if isinstance(row.get("original"), dict) else {}
            metadata = original.get("metadata") if isinstance(original.get("metadata"), dict) else {}
            row_metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
            metadata = {**metadata, **row_metadata}
            text = str(
                row.get("text")
                or row.get("content")
                or original.get("text")
                or original.get("content")
                or ""
            ).strip()
            if not text:
                raise ContractError(f"切片 JSONL 第 {line_number} 行缺少正文 text/content")
            catalog_path = (
                string_list(metadata.get("catalog_path"))
                or string_list(row.get("catalog_path"))
                or string_list(original.get("catalog_path"))
                or string_list(metadata.get("heading_path"))
                or string_list(row.get("heading_path"))
            )
            if catalog_path and catalog_path[0] == book_title:
                catalog_path = catalog_path[1:]
            heading = catalog_path[-1] if catalog_path else str(metadata.get("heading_path") or "正文").strip()
            kp_lv2 = str(
                row.get("kp_Lv2")
                or original.get("kp_Lv2")
                or metadata.get("kp_Lv2")
                or heading
            ).strip()
            chunk_id = str(
                row.get("chunk_id")
                or original.get("chunk_id")
                or f"{index + 1:05d}"
            )
            normalized = {
                "chunk_uid": f"{book_id}:{index + 1:05d}",
                "chunk_id": chunk_id,
                "book": book_title,
                "kp_Lv1": book_title,
                "kp_Lv2": kp_lv2,
                "chunk_index": index,
                "text": text,
                "char_count": len(text),
                "metadata": {
                    **metadata,
                    "book": book_title,
                    "heading_path": heading,
                    "catalog_path": catalog_path or [heading],
                    "chunk_index": index,
                    "total_chunks": len(rows),
                    "prev_chunk_id": f"{index:05d}" if index else None,
                    "next_chunk_id": f"{index + 2:05d}" if index + 1 < len(rows) else None,
                    "kp_Lv1": book_title,
                    "kp_Lv2": kp_lv2,
                },
            }
            handle.write(json.dumps(normalized, ensure_ascii=False) + "\n")
    return len(rows)


def run_stage(command: list[str], *, cwd: Path, env: dict[str, str], job_id: str, stage: str) -> None:
    process = subprocess.Popen(
        command,
        cwd=str(cwd),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
    )
    assert process.stdout is not None
    for line in process.stdout:
        message = line.rstrip()
        if message:
            emit(job_id, stage, "running", message, event_kind="log")
    return_code = process.wait()
    if return_code != 0:
        raise PipelineError(f"阶段 {stage} 失败，退出码 {return_code}")


def stable_id(prefix: str, *parts: str) -> str:
    raw = "\x00".join(parts).encode("utf-8")
    return prefix + hashlib.sha256(raw).hexdigest()[:24]


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def publish_artifacts(final_kg: Path, job_root: Path, *, graph_id: str, book_id: str) -> dict[str, Any]:
    payload = json.loads(final_kg.read_text(encoding="utf-8"))
    nodes = payload.get("nodes") if isinstance(payload.get("nodes"), list) else []
    edges = payload.get("edges") if isinstance(payload.get("edges"), list) else []
    artifact_root = job_root / "artifacts"
    artifact_root.mkdir(parents=True, exist_ok=True)
    final_target = artifact_root / "final_kg.json"
    shutil.copy2(final_kg, final_target)

    published_nodes: list[dict[str, Any]] = []
    name_to_id: dict[str, str] = {}
    for node in nodes:
        if not isinstance(node, dict):
            continue
        name = str(node.get("name") or "").strip()
        node_id = stable_id("KGN_", book_id, str(node.get("type") or ""), name)
        name_to_id.setdefault(name, node_id)
        published_nodes.append({"graph_id": graph_id, "book_id": book_id, "node_id": node_id, **node})

    published_edges: list[dict[str, Any]] = []
    for edge in edges:
        if not isinstance(edge, dict):
            continue
        source = str(edge.get("source") or "").strip()
        target = str(edge.get("target") or "").strip()
        relation = str(edge.get("type") or "related")
        source_id = name_to_id.get(source) or stable_id("KGN_", book_id, "unknown", source)
        target_id = name_to_id.get(target) or stable_id("KGN_", book_id, "unknown", target)
        edge_id = stable_id(
            "KGE_", book_id, source_id, target_id, relation, str(edge.get("description") or "")
        )
        published_edges.append({
            "graph_id": graph_id,
            "book_id": book_id,
            "edge_id": edge_id,
            "source_id": source_id,
            "target_id": target_id,
            **edge,
        })

    nodes_path = artifact_root / "nodes.jsonl"
    edges_path = artifact_root / "edges.jsonl"
    write_jsonl(nodes_path, published_nodes)
    write_jsonl(edges_path, published_edges)
    return {
        "counts": {"nodes": len(published_nodes), "edges": len(published_edges)},
        "paths": {
            "final_kg": str(final_target.relative_to(job_root)).replace("\\", "/"),
            "nodes": str(nodes_path.relative_to(job_root)).replace("\\", "/"),
            "edges": str(edges_path.relative_to(job_root)).replace("\\", "/"),
        },
        "sha256": {
            "final_kg": sha256_file(final_target),
            "nodes": sha256_file(nodes_path),
            "edges": sha256_file(edges_path),
        },
    }


def execute(request_path: Path, job_root: Path, *, validate_only: bool) -> dict[str, Any]:
    request = load_request(request_path)
    job_id = str(request["job_id"])
    user_id = str(request["user_id"])
    book = request["book"]
    book_id = str(book["book_id"])
    book_title = str(book["title"])
    safe_book_id = safe_token(book_id, "book.book_id")
    source = resolve_source_path(request_path, str(request["source"]["chunks_path"]))
    request_sha = sha256_file(request_path)
    source_sha = sha256_file(source)
    manifest_path = job_root / "manifest.json"
    if manifest_path.is_file():
        try:
            existing = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            existing = {}
        if existing.get("status") == "ready" and existing.get("request_sha256") == request_sha:
            emit(job_id, "done", "ready", "相同任务已经完成", manifest_path=str(manifest_path))
            return existing

    emit(job_id, "validate", "running", "正在校验接入请求")
    input_book_dir = job_root / "input" / safe_book_id
    normalized_chunks = input_book_dir / f"{safe_book_id}.jsonl"
    chunk_count = normalize_chunks(
        source,
        normalized_chunks,
        book_id=book_id,
        book_title=book_title,
    )
    base_manifest: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "job_id": job_id,
        "graph_id": None,
        "user_id": user_id,
        "book_id": book_id,
        "book_title": book_title,
        "owner_scope": "user",
        "status": "validated" if validate_only else "running",
        "request_sha256": request_sha,
        "source_sha256": source_sha,
        "created_at": utc_now(),
        "completed_at": utc_now() if validate_only else None,
        "input": {
            "chunks": str(normalized_chunks.relative_to(job_root)).replace("\\", "/"),
            "chunk_count": chunk_count,
        },
        "artifacts": {},
        "counts": {"chunks": chunk_count, "nodes": 0, "edges": 0},
        "error": None,
    }
    write_json(manifest_path, base_manifest)
    emit(job_id, "normalize", "completed", f"已规范化 {chunk_count} 个教材切片")
    if validate_only:
        emit(job_id, "done", "validated", "输入契约和切片格式验证通过")
        return base_manifest

    if not os.getenv("TREEKG_API_KEY", "").strip():
        raise ContractError("正式构建前必须设置 TREEKG_API_KEY")
    work_root = (job_root / "work").resolve()
    env = os.environ.copy()
    env.update({
        "PYTHONUNBUFFERED": "1",
        "PYTHONIOENCODING": "utf-8",
        "TREEKG_BOOK_NAME": safe_book_id,
        "TREEKG_INPUT_FORMAT": "jsonl",
        "TREEKG_JSONL_DATA_DIR": str((job_root / "input").resolve()),
        "TREEKG_OUTPUT_ROOT": str(work_root),
    })

    emit(job_id, "explicit", "running", "开始构建显式知识图谱")
    run_stage(
        [sys.executable, str(SRC_DIR / "ExplicitKG" / "main.py")],
        cwd=SRC_DIR,
        env=env,
        job_id=job_id,
        stage="explicit",
    )
    emit(job_id, "explicit", "completed", "显式知识图谱构建完成")

    emit(job_id, "hidden", "running", "开始扩展隐式关系")
    run_stage(
        [sys.executable, str(SRC_DIR / "HiddenKG" / "main.py")],
        cwd=SRC_DIR,
        env=env,
        job_id=job_id,
        stage="hidden",
    )
    final_kg = work_root / safe_book_id / "02_hidden_kg" / "final_kg.json"
    if not final_kg.is_file():
        raise PipelineError(f"流水线未生成 final_kg.json: {final_kg}")
    emit(job_id, "hidden", "completed", "隐式知识图谱构建完成")

    emit(job_id, "publish", "running", "正在发布按需加载数据")
    viewer_root = job_root / "viewer"
    run_stage(
        [
            sys.executable,
            str(SRC_DIR / "scripts" / "split_kg.py"),
            "--input",
            str(final_kg),
            "--out",
            str(viewer_root),
        ],
        cwd=SRC_DIR,
        env=env,
        job_id=job_id,
        stage="publish",
    )
    graph_id = stable_id("KG_", user_id, book_id, source_sha)
    published = publish_artifacts(final_kg, job_root, graph_id=graph_id, book_id=book_id)
    manifest = {
        **base_manifest,
        "graph_id": graph_id,
        "status": "ready",
        "completed_at": utc_now(),
        "artifacts": {
            **published["paths"],
            "viewer_root": "viewer",
            "viewer_meta": "viewer/meta.json",
            "sha256": published["sha256"],
        },
        "counts": {"chunks": chunk_count, **published["counts"]},
    }
    write_json(manifest_path, manifest)
    emit(job_id, "done", "ready", "知识图谱已经发布", graph_id=graph_id)
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description="TreeKG 流水线接入入口")
    parser.add_argument("--request", required=True, type=Path, help="任务请求 JSON")
    parser.add_argument("--output", required=True, type=Path, help="该任务的独立输出目录")
    parser.add_argument("--validate-only", action="store_true", help="仅验证和规范化输入，不调用模型")
    args = parser.parse_args()
    request_path = args.request.resolve()
    job_root = args.output.resolve()
    job_root.mkdir(parents=True, exist_ok=True)
    job_id = "unknown"
    try:
        try:
            preview = json.loads(request_path.read_text(encoding="utf-8-sig"))
            job_id = str(preview.get("job_id") or job_id) if isinstance(preview, dict) else job_id
        except Exception:
            pass
        execute(request_path, job_root, validate_only=args.validate_only)
        return 0
    except Exception as exc:
        code = getattr(exc, "code", "TREEKG_INTERNAL_ERROR")
        manifest_path = job_root / "manifest.json"
        try:
            existing = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            existing = {}
        failed = {
            **existing,
            "schema_version": SCHEMA_VERSION,
            "job_id": job_id,
            "status": "failed",
            "completed_at": utc_now(),
            "error": {"code": code, "message": str(exc)},
        }
        write_json(manifest_path, failed)
        emit(job_id, "failed", "failed", str(exc), error_code=code)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
