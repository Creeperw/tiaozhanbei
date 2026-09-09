"""Shared document execution only; ownership and publication stay with callers."""
from __future__ import annotations

import logging
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

_LOGGER = logging.getLogger(__name__)


class DocumentParseError(RuntimeError):
    """Safe public message, never subprocess output or provider credentials."""


@dataclass(frozen=True)
class ParsedDocument:
    markdown: str
    markdown_files: tuple[Path, ...]
    output_dir: Path
    run_id: str
    parser: str = "mineru"
    warnings: tuple[str, ...] = ()


def validate_mineru(pipeline_root: Path, token: str) -> None:
    if not token:
        raise DocumentParseError("MinerU 服务端密钥未配置")
    if not all((pipeline_root / name).is_file() for name in (
        "parse_question_pdf.py", "pipeline_config.json",
    )):
        raise DocumentParseError("MinerU 处理管线不完整")


def parse_mineru(
    sources: list[Path], *, pipeline_root: Path, token: str, output_dir: Path,
    attempts: int = 1, prefer_clean: bool = True,
    exclude_markdown: tuple[Path, ...] = (),
) -> ParsedDocument:
    """Execute one PDF or ordered PDF parts; callers retain page-offset handling.

    Existing output directories are intentionally retained for MinerU resume.
    Caller-created aggregate Markdown must be excluded on a resumed attempt.
    """
    pipeline_root, output_dir = Path(pipeline_root).resolve(), Path(output_dir).resolve()
    validate_mineru(pipeline_root, token)
    sources = [Path(source).resolve() for source in sources]
    if not sources or any(not p.is_file() or p.suffix.lower() != ".pdf" for p in sources):
        raise DocumentParseError("MinerU 只处理有效 PDF 文件")
    output_dir.mkdir(parents=True, exist_ok=True)
    run_id = uuid4().hex
    command = [sys.executable, str(pipeline_root / "parse_question_pdf.py"),
               "--config", str(pipeline_root / "pipeline_config.json"),
               "--output-dir", str(output_dir)]
    for source in sources:
        command.extend(["--pdf", str(source)])
    env = os.environ.copy()
    env["MINERU_TOKEN"] = token
    attempts = max(1, min(attempts, 3))
    for attempt in range(attempts):
        try:
            completed = subprocess.run(
                command, cwd=pipeline_root, env=env, capture_output=True,
                text=True, encoding="utf-8", errors="replace",
                timeout=24 * 60 * 60, check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            _LOGGER.warning("document parse failed run=%s kind=%s", run_id, type(exc).__name__)
            raise DocumentParseError(f"MinerU 执行失败或超时（记录：{run_id}）") from None
        if completed.returncode == 0:
            break
        # Fixed parser diagnostic, not classification of user-authored text.
        download_failed = "download failed after retries" in str(
            completed.stderr or completed.stdout or ""
        ).lower()
        if download_failed and attempt + 1 < attempts:
            time.sleep(4 * (attempt + 1))
            continue
        _LOGGER.warning("document parse failed run=%s exit=%s", run_id, completed.returncode)
        stage = "MinerU 结果下载失败" if download_failed else "MinerU 解析失败"
        raise DocumentParseError(f"{stage}（记录：{run_id}）")
    excluded = {p.resolve() for p in exclude_markdown}
    files = tuple(p for p in sorted(output_dir.rglob("*.md"))
                  if p.resolve() not in excluded)
    if prefer_clean:
        files = tuple(p for p in files if p.name.endswith("_clean.md")) or files
    if not files:
        raise DocumentParseError(f"MinerU 未生成 Markdown（记录：{run_id}）")
    try:
        if any(not p.resolve().is_relative_to(output_dir) for p in files):
            raise ValueError("outside output directory")
        markdown = "\n\n".join(p.read_text(encoding="utf-8-sig") for p in files)
    except (OSError, UnicodeError, ValueError):
        raise DocumentParseError(f"MinerU 解析产物无法读取（记录：{run_id}）") from None
    return ParsedDocument(markdown, files, output_dir, run_id)