from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import numpy as np

from APP.backend.faiss_io import read_faiss_index, write_faiss_index
from APP.backend.question_index_v2_service import (
    QuestionIndexContractError,
    active_question_index_name,
    build_question_index_v2,
    switch_active_question_index,
)


class QuestionIndexV2ServiceTests(unittest.TestCase):
    def setUp(self):
        try:
            import faiss
        except ImportError as exc:  # pragma: no cover - requirements.txt declares faiss-cpu
            self.skipTest(str(exc))
        self.faiss = faiss
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.index_root = self.root / "indexes"
        self.source_dir = self.index_root / "题库"
        self.target_dir = self.index_root / "题库-v2"
        self.pointer = self.index_root / ".question-index-active.json"
        self.source_dir.mkdir(parents=True)

        vectors = np.asarray(
            [
                [1.0, 0.0, 0.0, 0.0],
                [0.0, 1.0, 0.0, 0.0],
                [0.0, 0.0, 1.0, 0.0],
                [0.0, 0.0, 0.0, 1.0],
            ],
            dtype="float32",
        )
        index = faiss.IndexFlatIP(4)
        index.add(vectors)
        write_faiss_index(index, self.source_dir / "index.faiss")
        with (self.source_dir / "metadata.jsonl").open("w", encoding="utf-8") as handle:
            for question_id in ("q1", "legacy-extra", "q2", "q3"):
                handle.write(
                    json.dumps(
                        {
                            "type": "json_field",
                            "content": f"content {question_id}",
                            "original": {"题目id": question_id, "题目内容": f"stem {question_id}"},
                        },
                        ensure_ascii=False,
                    )
                    + "\n"
                )
        (self.source_dir / "index_manifest.json").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "embedding_model": "Qwen/Qwen3-Embedding-4B",
                    "dimensions": 4,
                    "normalized": True,
                    "metadata_format": "jsonl",
                }
            ),
            encoding="utf-8",
        )
        self.atlas_questions = self.root / "formatted_questions.json"
        self.atlas_questions.write_text(
            json.dumps(
                [
                    {
                        "question_id": "q1",
                        "question_content": "stem q1",
                        "options": [{"option_id": "A", "content": "one"}],
                        "answer": ["A"],
                        "explanation": "why q1",
                        "kp_ids": ["kp-1"],
                    },
                    {
                        "question_id": "q2",
                        "question_content": "stem q2",
                        "options": [],
                        "answer": "two",
                        "explanation": "",
                        "kp_ids": [],
                    },
                    {
                        "question_id": "q3",
                        "question_content": "stem q3",
                        "options": [],
                        "answer": "three",
                        "explanation": "why q3",
                        "kp_ids": ["kp-3"],
                    },
                ],
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

    def tearDown(self):
        self.temp.cleanup()

    def test_unicode_safe_faiss_io_round_trip(self):
        path = self.root / "中文目录" / "索引.faiss"
        path.parent.mkdir()
        index = self.faiss.IndexFlatIP(2)
        index.add(np.asarray([[1.0, 0.0]], dtype="float32"))

        write_faiss_index(index, path)
        loaded = read_faiss_index(path)

        self.assertEqual(loaded.d, 2)
        self.assertEqual(loaded.ntotal, 1)

    def test_vector_database_does_not_silently_hide_a_corrupt_index(self):
        from APP.backend.rag_text import VectorDatabase

        target = self.root / "中文损坏索引"
        target.mkdir()
        (target / "index.faiss").write_bytes(b"not-a-faiss-index")
        (target / "metadata.jsonl").write_text("{}\n", encoding="utf-8")

        with self.assertRaisesRegex(RuntimeError, "Failed to load vector database"):
            VectorDatabase(str(target / "index.faiss"), str(target / "metadata.jsonl"))

    def test_rebuild_filters_old_vectors_by_atlas_truth_and_atomically_activates_v2(self):
        report = build_question_index_v2(
            source_dir=self.source_dir,
            atlas_questions_path=self.atlas_questions,
            target_dir=self.target_dir,
            active_pointer_path=self.pointer,
            expected_model="Qwen/Qwen3-Embedding-4B",
            expected_dimensions=4,
            asset_version="2026-07-18",
            activate=True,
            batch_size=2,
        )

        self.assertEqual(report["vector_count"], 3)
        self.assertEqual(report["excluded_source_count"], 1)
        self.assertEqual(report["linked_count"], 2)
        self.assertEqual(report["pending_link_count"], 1)
        self.assertEqual(read_faiss_index(self.source_dir / "index.faiss").ntotal, 4)
        rebuilt = read_faiss_index(self.target_dir / "index.faiss")
        self.assertEqual(rebuilt.ntotal, 3)
        self.assertEqual(active_question_index_name(self.index_root), "题库-v2")
        metadata = [
            json.loads(line)
            for line in (self.target_dir / "metadata.jsonl").read_text(encoding="utf-8").splitlines()
        ]
        self.assertEqual([row["original"]["题目id"] for row in metadata], ["q1", "q2", "q3"])
        self.assertEqual(metadata[0]["atlas"]["kp_ids"], ["kp-1"])
        self.assertEqual(metadata[1]["atlas"]["status"], "pending_link")
        manifest = json.loads((self.target_dir / "index_manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["dimensions"], 4)
        self.assertTrue(manifest["normalized"])
        self.assertEqual(manifest["vector_strategy"], "reuse-compatible-v1-vectors")

    def test_incompatible_source_manifest_refuses_build_and_does_not_switch(self):
        manifest_path = self.source_dir / "index_manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["embedding_model"] = "wrong-model"
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

        with self.assertRaisesRegex(QuestionIndexContractError, "embedding model"):
            build_question_index_v2(
                source_dir=self.source_dir,
                atlas_questions_path=self.atlas_questions,
                target_dir=self.target_dir,
                active_pointer_path=self.pointer,
                expected_model="Qwen/Qwen3-Embedding-4B",
                expected_dimensions=4,
                asset_version="2026-07-18",
                activate=True,
            )

        self.assertFalse(self.pointer.exists())
        self.assertFalse(self.target_dir.exists())

    def test_pointer_can_roll_back_without_deleting_either_index(self):
        switch_active_question_index(
            index_root=self.index_root,
            collection="题库",
            pointer_path=self.pointer,
        )

        self.assertEqual(active_question_index_name(self.index_root), "题库")
        self.assertTrue(self.source_dir.is_dir())

    def test_active_pointer_with_changed_manifest_safely_falls_back_to_v1(self):
        build_question_index_v2(
            source_dir=self.source_dir,
            atlas_questions_path=self.atlas_questions,
            target_dir=self.target_dir,
            active_pointer_path=self.pointer,
            expected_model="Qwen/Qwen3-Embedding-4B",
            expected_dimensions=4,
            asset_version="2026-07-18",
            activate=True,
        )
        manifest_path = self.target_dir / "index_manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["vector_count"] = 999
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

        self.assertEqual(active_question_index_name(self.index_root), "题库")

    def test_active_pointer_with_malformed_count_safely_falls_back_to_v1(self):
        build_question_index_v2(
            source_dir=self.source_dir,
            atlas_questions_path=self.atlas_questions,
            target_dir=self.target_dir,
            active_pointer_path=self.pointer,
            expected_model="Qwen/Qwen3-Embedding-4B",
            expected_dimensions=4,
            asset_version="2026-07-18",
            activate=True,
        )
        pointer = json.loads(self.pointer.read_text(encoding="utf-8"))
        pointer["vector_count"] = "not-a-number"
        self.pointer.write_text(json.dumps(pointer), encoding="utf-8")

        self.assertEqual(active_question_index_name(self.index_root), "题库")

    def test_switch_refuses_manifest_count_that_disagrees_with_index_and_metadata(self):
        manifest_path = self.source_dir / "index_manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["count"] = 999
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

        with self.assertRaisesRegex(QuestionIndexContractError, "count"):
            switch_active_question_index(
                index_root=self.index_root,
                collection="题库",
                pointer_path=self.pointer,
                expected_model="Qwen/Qwen3-Embedding-4B",
                expected_dimensions=4,
                require_normalized=True,
            )

        self.assertFalse(self.pointer.exists())


if __name__ == "__main__":
    unittest.main()
