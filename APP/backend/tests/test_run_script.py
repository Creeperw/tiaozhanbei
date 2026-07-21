import unittest
from pathlib import Path


class RunScriptTests(unittest.TestCase):
    def test_backend_port_matches_frontend_proxy_target(self):
        script = (Path(__file__).resolve().parents[3] / "run.ps1").read_text(encoding="utf-8")

        self.assertIn('"--port","7860"', script)


if __name__ == "__main__":
    unittest.main()
