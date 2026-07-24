import tempfile
import unittest
from pathlib import Path

from APP.backend.question_index_v2_service import (
    QuestionIndexContractError,
    _validate_question_collection,
)


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

            with self.assertRaisesRegex(QuestionIndexContractError, "embedding model"):
                _validate_question_collection(
                    index_dir,
                    expected_model="Qwen/Qwen3-Embedding-4B",
                    expected_dimensions=2560,
                    require_normalized=True,
                )


if __name__ == "__main__":
    unittest.main()
