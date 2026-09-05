import json
import unittest

from APP.backend.data_import_service import convert_chunks_to_markdown, normalize_chunk_id, normalize_kp_id, normalize_question_id


class TcmDemoDataImportTests(unittest.TestCase):
    def test_resource_ids_have_namespaces(self):
        self.assertEqual(normalize_kp_id("000001"), "KP_TCM_000001")
        self.assertEqual(normalize_chunk_id("00001"), "CHUNK_ACU_MOXA_00001")
        self.assertEqual(normalize_question_id("中医儿科学习题集__q000002"), "Q_TCM_PED_000002")

    def test_convert_chunks_to_markdown_preserves_type_and_source(self):
        from tempfile import TemporaryDirectory
        from pathlib import Path

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "chunks.jsonl"
            source.write_text(json.dumps({
                "chunk_id": "00001",
                "text": "刺法灸法学内容",
                "metadata": {"book": "刺法灸法学_clean", "kp_Lv1": "刺法灸法学", "kp_Lv2": "概念"},
            }, ensure_ascii=False) + "\n", encoding="utf-8")
            target = root / "刺法灸法学_clean.md"

            count = convert_chunks_to_markdown(source, target)

            self.assertEqual(count, 1)
            content = target.read_text(encoding="utf-8")
            self.assertIn("source_id: KB_ACU_MOXA:00001", content)
            self.assertIn("resource_type: knowledge_chunk", content)
            self.assertIn("刺法灸法学内容", content)


if __name__ == "__main__":
    unittest.main()
