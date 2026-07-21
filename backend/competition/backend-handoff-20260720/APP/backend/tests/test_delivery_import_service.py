import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from APP.backend.delivery_import_service import (
    DeliveryImportError,
    dry_run_delivery_import,
    iter_jsonl_records,
    parse_knowledge_point,
    parse_question,
    parse_question_link,
    parse_question_version,
    resolve_storage_path,
    validate_delivery_batch,
)


class DeliveryImportServiceTests(unittest.TestCase):
    def test_parses_minimal_delivery_dtos(self):
        knowledge_point = parse_knowledge_point({"kp_id": "KP_1", "name": "阴阳学说"})
        question = parse_question({"question_id": "Q_1", "question_type": "single_choice"})
        version = parse_question_version({"question_version_id": "QV_1", "question_id": "Q_1", "data_version": "v1"})
        link = parse_question_link({"question_id": "Q_1", "kp_id": "KP_1"})

        self.assertEqual(knowledge_point.kp_id, "KP_1")
        self.assertEqual(question.question_type, "single_choice")
        self.assertEqual(version.data_version, "v1")
        self.assertEqual(link.kp_id, "KP_1")

    def test_rejects_unsafe_storage_relative_paths(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "images"
            root.mkdir()
            for unsafe_path in ("/absolute.png", "../outside.png", "nested/../../outside.png"):
                with self.subTest(unsafe_path=unsafe_path):
                    with self.assertRaises(DeliveryImportError):
                        resolve_storage_path(root, unsafe_path)

    def test_rejects_jsonl_records_over_size_or_depth_limit(self):
        with TemporaryDirectory() as tmp:
            source = Path(tmp) / "records.jsonl"
            source.write_text(json.dumps({"value": "x" * 80}) + "\n", encoding="utf-8")
            with self.assertRaises(DeliveryImportError):
                list(iter_jsonl_records(source, max_record_bytes=32))

            source.write_text(json.dumps({"a": {"b": {"c": 1}}}) + "\n", encoding="utf-8")
            with self.assertRaises(DeliveryImportError):
                list(iter_jsonl_records(source, max_depth=2))

    def test_dry_run_never_calls_database_writer(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_batch(root)
            writes = []

            result = dry_run_delivery_import(root, database_writer=lambda batch: writes.append(batch))

            self.assertTrue(result.valid)
            self.assertEqual(writes, [])

    def test_repeated_version_and_sha256_is_idempotent(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_batch(root)
            first = dry_run_delivery_import(root, known_imports=set())
            second = dry_run_delivery_import(root, known_imports={(first.data_version, first.sha256)})

            self.assertFalse(first.idempotent)
            self.assertTrue(second.idempotent)
            self.assertEqual(first.sha256, second.sha256)

    def test_missing_formal_files_returns_explicit_result(self):
        with TemporaryDirectory() as tmp:
            result = validate_delivery_batch(Path(tmp))

        self.assertFalse(result.valid)
        self.assertEqual(result.status, "missing-files")
        self.assertEqual(result.error_code, "missing-files")
        self.assertEqual(result.reason, "missing-required-files")
        self.assertEqual(len(result.missing_files), 8)

    def test_rejects_symlinked_formal_file_outside_delivery_root(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "delivery"
            root.mkdir()
            self._write_batch(root)
            outside = Path(tmp) / "outside.jsonl"
            outside.write_text('{"kp_id": "KP_OUTSIDE", "name": "outside"}\n', encoding="utf-8")
            (root / "knowledge_points.jsonl").unlink()
            try:
                (root / "knowledge_points.jsonl").symlink_to(outside)
            except OSError as error:
                self.skipTest(f"symlink creation unavailable: {error.winerror}")

            result = validate_delivery_batch(root)
            dry_run = dry_run_delivery_import(root)

        self.assertFalse(result.valid)
        self.assertEqual(result.status, "invalid-path")
        self.assertEqual(result.error_code, "invalid-path")
        self.assertEqual(result.reason, "path-outside-root")
        self.assertFalse(dry_run.valid)
        self.assertEqual(dry_run.status, "invalid-path")
        self.assertEqual(dry_run.error_code, "invalid-path")
        self.assertEqual(dry_run.reason, "path-outside-root")

    def test_rejects_symlinked_delivery_root_outside_trusted_boundary(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "delivery"
            external_batch = Path(tmp) / "external-batch"
            external_batch.mkdir()
            self._write_batch(external_batch)
            try:
                root.symlink_to(external_batch, target_is_directory=True)
            except OSError as error:
                self.skipTest(f"symlink creation unavailable: {error.winerror}")

            result = validate_delivery_batch(root)
            dry_run = dry_run_delivery_import(root)

        self.assertFalse(result.valid)
        self.assertEqual(result.error_code, "invalid-path")
        self.assertEqual(result.reason, "path-outside-root")
        self.assertFalse(dry_run.valid)
        self.assertEqual(dry_run.error_code, "invalid-path")
        self.assertEqual(dry_run.reason, "path-outside-root")

    def test_rejects_oversized_unterminated_jsonl_record(self):
        with TemporaryDirectory() as tmp:
            source = Path(tmp) / "records.jsonl"
            source.write_bytes(b'{"value":"' + b"x" * 64)

            with self.assertRaises(DeliveryImportError):
                list(iter_jsonl_records(source, max_record_bytes=32))

    def test_deep_manifest_returns_controlled_invalid_status(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_batch(root)
            manifest = {"data_version": "v1"}
            for _ in range(100):
                manifest = {"nested": manifest}
            (root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

            result = validate_delivery_batch(root)
            dry_run = dry_run_delivery_import(root)

        self.assertFalse(result.valid)
        self.assertEqual(result.status, "invalid-structure")
        self.assertEqual(result.error_code, "invalid-structure")
        self.assertEqual(result.reason, "invalid-manifest")
        self.assertFalse(dry_run.valid)
        self.assertEqual(dry_run.status, "invalid-structure")
        self.assertEqual(dry_run.error_code, "invalid-structure")
        self.assertEqual(dry_run.reason, "invalid-manifest")

    def test_invalid_dto_or_minimal_record_mapping_invalidates_batch(self):
        dto_cases = (
            ("knowledge_points.jsonl", {"kp_id": "KP_1"}),
            ("questions.jsonl", {"question_id": "Q_1"}),
            ("question_versions.jsonl", {"question_version_id": "QV_1", "question_id": "Q_1"}),
            ("question_links.jsonl", {"question_id": "Q_1"}),
        )
        for filename, record in dto_cases:
            with self.subTest(filename=filename), TemporaryDirectory() as tmp:
                root = Path(tmp)
                self._write_batch(root)
                (root / filename).write_text(json.dumps(record) + "\n", encoding="utf-8")
                result = validate_delivery_batch(root)

                self.assertFalse(result.valid)
                self.assertEqual(result.status, "invalid-record")
                self.assertEqual(result.error_code, "invalid-record")
                self.assertEqual(result.reason, "invalid-record-schema")

    def test_invalid_json_record_returns_sanitized_reason(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_batch(root)
            (root / "media.jsonl").write_text("{not-json}\n", encoding="utf-8")

            result = validate_delivery_batch(root)
            dry_run = dry_run_delivery_import(root)

        self.assertFalse(result.valid)
        self.assertEqual(result.error_code, "invalid-json")
        self.assertEqual(result.reason, "invalid-json-record")
        self.assertFalse(dry_run.valid)
        self.assertEqual(dry_run.reason, "invalid-json-record")

    def test_other_required_records_need_object_mapping(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_batch(root)
            (root / "media.jsonl").write_text('"not-an-object"\n', encoding="utf-8")
            result = validate_delivery_batch(root)

        self.assertFalse(result.valid)
        self.assertEqual(result.status, "invalid-record")
        self.assertEqual(result.error_code, "invalid-record")
        self.assertEqual(result.reason, "invalid-record-schema")

    def _write_batch(self, root):
        files = {
            "knowledge_points.jsonl": [{"kp_id": "KP_1", "name": "阴阳学说"}],
            "questions.jsonl": [{"question_id": "Q_1", "question_type": "single_choice"}],
            "question_versions.jsonl": [{"question_version_id": "QV_1", "question_id": "Q_1", "data_version": "v1"}],
            "question_links.jsonl": [{"question_id": "Q_1", "kp_id": "KP_1"}],
            "knowledge_point_versions.jsonl": [],
            "knowledge_point_links.jsonl": [],
            "media.jsonl": [],
            "manifest.json": {"data_version": "v1"},
        }
        for name, value in files.items():
            text = json.dumps(value, ensure_ascii=False) if name == "manifest.json" else "".join(
                json.dumps(record, ensure_ascii=False) + "\n" for record in value
            )
            (root / name).write_text(text, encoding="utf-8")


if __name__ == "__main__":
    unittest.main()
