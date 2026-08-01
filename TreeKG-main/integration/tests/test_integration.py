from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


INTEGRATION_ROOT = Path(__file__).resolve().parents[1]
RUNNER = INTEGRATION_ROOT / "build_graph.py"
EXAMPLES = INTEGRATION_ROOT / "examples"


class BuildGraphIntegrationTest(unittest.TestCase):
    def test_validate_only_normalizes_pipeline_chunks(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / "job"
            completed = subprocess.run(
                [
                    sys.executable,
                    str(RUNNER),
                    "--request",
                    str(EXAMPLES / "request.json"),
                    "--output",
                    str(output),
                    "--validate-only",
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
            manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["status"], "validated")
            self.assertEqual(manifest["counts"]["chunks"], 4)
            normalized = output / manifest["input"]["chunks"]
            rows = [json.loads(line) for line in normalized.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(rows[0]["chunk_uid"], "UTB_demo_tcm:00001")
            self.assertEqual(rows[0]["metadata"]["catalog_path"], ["绪论", "第一节 整体观念"])
            self.assertEqual(rows[1]["kp_Lv2"], "第一节 阴阳学说")

    def test_invalid_request_writes_structured_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            request = root / "request.json"
            request.write_text(
                json.dumps({"schema_version": "1.0.0", "job_id": "bad"}),
                encoding="utf-8",
            )
            output = root / "job"
            completed = subprocess.run(
                [sys.executable, str(RUNNER), "--request", str(request), "--output", str(output), "--validate-only"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
            )
            self.assertEqual(completed.returncode, 1)
            manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["status"], "failed")
            self.assertEqual(manifest["error"]["code"], "TREEKG_INVALID_REQUEST")


if __name__ == "__main__":
    unittest.main()
