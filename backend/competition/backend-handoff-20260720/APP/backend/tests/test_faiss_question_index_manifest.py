import sys
import tempfile
import unittest
from pathlib import Path

COMPONENT_ROOT = Path(__file__).resolve().parents[2] / "division of labor" / "7-15 知识库管理组件"
if str(COMPONENT_ROOT) not in sys.path:
    sys.path.insert(0, str(COMPONENT_ROOT))

from question_pipeline.faiss_question_index import FaissQuestionIndex


class FaissQuestionIndexManifestTests(unittest.TestCase):
    def test_requires_matching_embedding_manifest_before_loading_index(self):
        with tempfile.TemporaryDirectory() as tmp:
            index_dir = Path(tmp)
            (index_dir / "index.faiss").write_bytes(b"placeholder")
            (index_dir / "metadata.jsonl").write_text("{}\n", encoding="utf-8")
            (index_dir / "index_manifest.json").write_text(
                '{"embedding_model":"other-model","dimensions":2560,"normalized":true}',
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "embedding model"):
                FaissQuestionIndex(index_dir, expected_embedding_model="Qwen/Qwen3-Embedding-4B")


if __name__ == "__main__":
    unittest.main()
