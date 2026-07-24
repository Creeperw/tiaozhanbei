import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from APP.backend.mineru_pdf_service import MinerUPdfParser


class MinerUPdfParserTests(unittest.TestCase):
    def test_uses_server_token_and_returns_normalized_markdown(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pipeline = root / "pipeline"
            pipeline.mkdir()
            (pipeline / "parse_question_pdf.py").write_text("# test", encoding="utf-8")
            (pipeline / "pipeline_config.json").write_text("{}", encoding="utf-8")
            source = root / "教材.pdf"
            source.write_bytes(b"%PDF-test")

            def run(command, **kwargs):
                output_dir = Path(command[command.index("--output-dir") + 1])
                markdown = output_dir / "books" / "教材_clean.md"
                markdown.parent.mkdir(parents=True)
                markdown.write_text("# 教材\n\n阴阳学说", encoding="utf-8")
                self.assertEqual(kwargs["env"]["MINERU_TOKEN"], "server-token")
                return subprocess.CompletedProcess(command, 0, "", "")

            parser = MinerUPdfParser(
                pipeline_root=pipeline,
                token="server-token",
                runtime_root=root / "runtime",
            )
            with patch(
                "APP.backend.mineru_pdf_service.subprocess.run",
                side_effect=run,
            ):
                markdown = parser.parse(source)

            self.assertIn("阴阳学说", markdown)

    def test_rejects_missing_server_token(self):
        with patch.dict("os.environ", {}, clear=True):
            parser = MinerUPdfParser(
                pipeline_root="/missing",
                token="",
                runtime_root="/tmp/mineru-test",
            )
            with self.assertRaisesRegex(RuntimeError, "密钥"):
                parser.validate()


if __name__ == "__main__":
    unittest.main()
