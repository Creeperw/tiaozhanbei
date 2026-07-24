from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from uuid import uuid4


class MinerUPdfParser:
    """Run the delivered MinerU precision pipeline and return normalized Markdown."""

    def __init__(
        self,
        pipeline_root: str | Path | None = None,
        token: str | None = None,
        runtime_root: str | Path | None = None,
    ) -> None:
        self.pipeline_root = Path(
            pipeline_root
            or os.environ.get("KNOWLEDGE_UPLOAD_PIPELINE_ROOT", "")
        ).expanduser()
        self.token = str(
            token
            or os.environ.get("MINERU_TOKEN")
            or os.environ.get("MINERU_API_KEY")
            or ""
        ).strip()
        self.runtime_root = Path(
            runtime_root
            or os.environ.get("BACKEND_RUNTIME_ROOT")
            or self.pipeline_root / "runtime"
        ).expanduser()

    def validate(self) -> None:
        if not self.token:
            raise RuntimeError("MinerU 服务端密钥未配置")
        required = (
            self.pipeline_root / "parse_question_pdf.py",
            self.pipeline_root / "pipeline_config.json",
        )
        missing = [str(path) for path in required if not path.is_file()]
        if missing:
            raise RuntimeError("MinerU PDF 管线不完整：" + "；".join(missing))

    def parse(self, file_path: Path) -> str:
        self.validate()
        source = Path(file_path).resolve()
        if not source.is_file() or source.suffix.lower() != ".pdf":
            raise ValueError("MinerU 只处理有效 PDF 文件")
        output_dir = self.runtime_root / "mineru_pdf_runs" / uuid4().hex
        output_dir.mkdir(parents=True, exist_ok=True)
        env = os.environ.copy()
        env["MINERU_TOKEN"] = self.token
        completed = subprocess.run(
            [
                sys.executable,
                str(self.pipeline_root / "parse_question_pdf.py"),
                "--config",
                str(self.pipeline_root / "pipeline_config.json"),
                "--output-dir",
                str(output_dir),
                "--pdf",
                str(source),
            ],
            cwd=self.pipeline_root,
            env=env,
            capture_output=True,
            text=True,
            timeout=24 * 60 * 60,
            check=False,
        )
        if completed.returncode != 0:
            detail = (
                completed.stderr
                or completed.stdout
                or "MinerU PDF 解析失败"
            ).strip()
            raise RuntimeError(detail[-4000:])
        markdown_files = sorted(output_dir.rglob("*_clean.md"))
        if not markdown_files:
            markdown_files = sorted(output_dir.rglob("*.md"))
        if not markdown_files:
            raise RuntimeError("MinerU 未生成 Markdown")
        return "\n\n".join(
            path.read_text(encoding="utf-8-sig")
            for path in markdown_files
        )
