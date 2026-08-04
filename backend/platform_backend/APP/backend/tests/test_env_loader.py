import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from APP.env_loader import load_project_env


class ProjectEnvLoaderTests(unittest.TestCase):
    def test_loads_missing_values_without_overwriting_existing_environment(self):
        with tempfile.TemporaryDirectory() as tmp:
            env_path = Path(tmp) / ".env"
            env_path.write_text("NEW_VALUE=loaded\nEXISTING_VALUE=from-file\n", encoding="utf-8")
            with patch.dict(os.environ, {"EXISTING_VALUE": "already-set"}, clear=True):
                load_project_env(env_path)

                self.assertEqual(os.environ["NEW_VALUE"], "loaded")
                self.assertEqual(os.environ["EXISTING_VALUE"], "already-set")


if __name__ == "__main__":
    unittest.main()
