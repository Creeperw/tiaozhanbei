import json
import tempfile
import unittest
from pathlib import Path

from APP.backend.official_exam_repository import OfficialExamRepository


REQUIRED_ROWS = {
    "exam_tracks": [
        {"track_id": "track-a", "title": "中医执业医师", "year": 2025, "schema_version": "2.0.0"},
        {"track_id": "track-b", "title": "中医执业助理医师", "year": 2025, "schema_version": "2.0.0"},
    ],
    "exam_stage_nodes": [],
    "exam_stage_edges": [],
    "exam_stage_stats": [],
    "syllabus_nodes": [
        {"node_id": "node-root", "title_normalized": "医学综合", "is_requirement": False},
        {"node_id": "node-subject", "title_normalized": "中医基础理论", "is_requirement": False},
        {"node_id": "node-requirement", "title_normalized": "阴阳学说", "is_requirement": True},
    ],
    "track_node_memberships": [
        {"membership_id": "root-a", "track_id": "track-a", "node_id": "node-root", "parent_membership_id": None, "sort_index": 0, "is_requirement": False},
        {"membership_id": "subject-a", "track_id": "track-a", "node_id": "node-subject", "parent_membership_id": "root-a", "sort_index": 0, "is_requirement": False},
        {"membership_id": "requirement-a", "track_id": "track-a", "node_id": "node-requirement", "parent_membership_id": "subject-a", "sort_index": 0, "is_requirement": True},
        {"membership_id": "root-b", "track_id": "track-b", "node_id": "node-root", "parent_membership_id": None, "sort_index": 0, "is_requirement": False},
        {"membership_id": "requirement-b", "track_id": "track-b", "node_id": "node-requirement", "parent_membership_id": "root-b", "sort_index": 0, "is_requirement": True},
    ],
    "requirement_mapping_status": [
        {"node_id": "node-requirement", "mapping_status": "accepted", "accepted_count": 1, "candidate_count": 1},
    ],
    "exam_catalog_nodes": [
        {"catalog_node_id": "root-a", "membership_id": "root-a", "track_id": "track-a", "canonical_node_id": "node-root", "parent_id": None, "node_type": "exam_section", "title": "医学综合", "node_order": 0},
        {"catalog_node_id": "requirement-a", "membership_id": "requirement-a", "track_id": "track-a", "canonical_node_id": "node-requirement", "parent_id": "root-a", "node_type": "requirement", "title": "阴阳学说", "node_order": 1},
    ],
    "node_kp_matches": [
        {"node_id": "node-requirement", "kp_id": "kp-accepted", "decision": "accepted", "rank": 1, "kp_lv1": "中医学", "kp_lv2": "基础理论", "kp_lv3": "阴阳学说"},
        {"node_id": "node-requirement", "kp_id": "kp-candidate", "decision": "candidate", "rank": 2, "kp_lv1": "中医学", "kp_lv2": "基础理论", "kp_lv3": "候选知识"},
    ],
    "kp_exam_matches": [
        {"kp_id": "kp-accepted", "node_id": "node-requirement", "track_ids": ["track-a", "track-b"], "decision": "accepted"},
    ],
}


def write_exam_fixture(directory: Path) -> None:
    for name in OfficialExamRepository.REQUIRED:
        with (directory / f"{name}.jsonl").open("w", encoding="utf-8") as handle:
            for row in REQUIRED_ROWS[name]:
                handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    (directory / "manifest.jsonl").write_text(
        json.dumps({"record_type": "build", "schema_version": "2.0.0"}) + "\n",
        encoding="utf-8",
    )


class OfficialExamRepositoryTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.temp_dir.name)
        write_exam_fixture(self.data_dir)
        self.repository = OfficialExamRepository(self.data_dir)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_keeps_details_lazy_until_node_navigation(self):
        self.assertFalse(self.repository.details_loaded)
        self.assertEqual([row["track_id"] for row in self.repository.list_tracks()], ["track-a", "track-b"])
        self.assertFalse(self.repository.details_loaded)

        roots = self.repository.get_membership_children("track-a")

        self.assertTrue(self.repository.details_loaded)
        self.assertEqual([row["membership_id"] for row in roots], ["root-a"])

    def test_returns_direct_children_and_breadcrumb_with_track_ownership(self):
        children = self.repository.get_membership_children("track-a", "root-a")
        self.assertEqual([row["membership_id"] for row in children], ["subject-a"])
        detail = self.repository.get_membership("track-a", "requirement-a")
        self.assertEqual([item["membership_id"] for item in detail["breadcrumb"]], ["root-a", "subject-a", "requirement-a"])
        with self.assertRaises(KeyError):
            self.repository.get_membership("track-b", "requirement-a")

    def test_public_reads_only_return_accepted_knowledge_points(self):
        requirement = self.repository.get_requirement_matches("node-requirement", include_candidates=False)
        self.assertEqual([row["kp_id"] for row in requirement["matches"]], ["kp-accepted"])
        subtree = self.repository.get_catalog_subtree_knowledge_points("root-a", accepted_only=True)
        self.assertEqual([row["kp_id"] for row in subtree["knowledge_points"]], ["kp-accepted"])
        self.assertEqual(subtree["mapping_count"], 1)

    def test_missing_artifact_fails_fast(self):
        (self.data_dir / "exam_tracks.jsonl").unlink()
        with self.assertRaises(FileNotFoundError):
            OfficialExamRepository(self.data_dir)


if __name__ == "__main__":
    unittest.main()
