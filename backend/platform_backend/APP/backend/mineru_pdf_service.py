from __future__ import annotations

import os
from pathlib import Path
from uuid import uuid4

from competition_app.services.document_parsing import parse_mineru, validate_mineru


# The integrated host projects its settings only while importing this package.
# Keep those validated values available for request-time parser instances.
_IMPORTED_PIPELINE_ROOT = os.environ.get("KNOWLEDGE_UPLOAD_PIPELINE_ROOT", "")
_IMPORTED_MINERU_TOKEN = os.environ.get("MINERU_TOKEN") or os.environ.get("MINERU_API_KEY") or ""
_IMPORTED_RUNTIME_ROOT = os.environ.get("BACKEND_RUNTIME_ROOT", "")


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
            or _IMPORTED_PIPELINE_ROOT
        ).expanduser()
        self.token = str(
            token
            or os.environ.get("MINERU_TOKEN")
            or os.environ.get("MINERU_API_KEY")
            or _IMPORTED_MINERU_TOKEN
            or ""
        ).strip()
        self.runtime_root = Path(
            runtime_root
            or os.environ.get("BACKEND_RUNTIME_ROOT")
            or _IMPORTED_RUNTIME_ROOT
            or self.pipeline_root / "runtime"
        ).expanduser()

    def validate(self) -> None:
        validate_mineru(self.pipeline_root, self.token)

    def parse(self, file_path: Path) -> str:
        self.validate()
        source = Path(file_path).resolve()
        if not source.is_file() or source.suffix.lower() != ".pdf":
            raise ValueError("MinerU 只处理有效 PDF 文件")
        output_dir = self.runtime_root / "mineru_pdf_runs" / uuid4().hex
        return parse_mineru(
            [source], pipeline_root=self.pipeline_root, token=self.token,
            output_dir=output_dir,
        ).markdown
