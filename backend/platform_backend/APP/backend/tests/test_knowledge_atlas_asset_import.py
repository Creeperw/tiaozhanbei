from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from APP.backend.scripts.import_knowledge_atlas_assets import (
    AssetConflictError,
    ContractVerificationError,
    SourceAssetMissingError,
    _promote_staging,
    build_component_inventory,
    import_knowledge_atlas_assets,
)


class KnowledgeAtlasAssetImportTests(unittest.TestCase):
    def _fixture(self, root: Path) -> tuple[Path, Path, Path, Path]:
        package_root = root / "handoff"
        backend_delivery = package_root / "知识库管理组件" / "data" / "backend_delivery"
        full_batch = package_root / "bilibili_video_page" / "runtime" / "full_batch"
        full_batch_results = package_root / "bilibili_video_page" / "runtime" / "full_batch_results"
        source_excel = package_root / "bilibili_video_page" / "DATA" / "预处理"

        backend_delivery.mkdir(parents=True)
        full_batch.mkdir(parents=True)
        full_batch_results.mkdir(parents=True)
        source_excel.mkdir(parents=True)
        (backend_delivery / "questions.json").write_text('[{"question_id":"q1"}]', encoding="utf-8")
        (full_batch / "catalog.json").write_text('{"pages":1}', encoding="utf-8")
        (full_batch_results / "classification_result.json").write_text(
            '{"segments":[{"kp_id":"kp-1"}]}', encoding="utf-8"
        )
        (source_excel / "source.xlsx").write_bytes(b"xlsx-placeholder")

        component_specs = [
            {
                "name": "backend_delivery",
                "source": "知识库管理组件/data/backend_delivery",
                "root": "data",
                "target": "backend_delivery",
                "sample_files": ["questions.json"],
            },
            {
                "name": "video_full_batch",
                "source": "bilibili_video_page/runtime/full_batch",
                "root": "video",
                "target": "full_batch",
                "sample_files": ["catalog.json"],
            },
            {
                "name": "video_full_batch_results",
                "source": "bilibili_video_page/runtime/full_batch_results",
                "root": "video",
                "target": "full_batch_results",
                "sample_files": ["classification_result.json"],
            },
            {
                "name": "video_source_excel",
                "source": "bilibili_video_page/DATA/预处理",
                "root": "video",
                "target": "source_excel",
                "sample_files": ["source.xlsx"],
            },
        ]
        components = []
        for spec in component_specs:
            inventory = build_component_inventory(package_root / spec["source"], spec["sample_files"])
            components.append({**spec, **inventory})
        contract_path = root / "contract.json"
        contract_path.write_text(
            json.dumps(
                {
                    "schema_version": "1.0.0",
                    "asset_version": "2026-07-18",
                    "components": components,
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        return package_root, root / "atlas-data", root / "atlas-video", contract_path

    def test_import_is_idempotent_when_contract_and_target_match(self):
        with tempfile.TemporaryDirectory() as tmp:
            package_root, data_root, video_root, contract_path = self._fixture(Path(tmp))

            first = import_knowledge_atlas_assets(
                package_root=package_root,
                data_root=data_root,
                video_root=video_root,
                contract_path=contract_path,
            )
            second = import_knowledge_atlas_assets(
                package_root=package_root,
                data_root=data_root,
                video_root=video_root,
                contract_path=contract_path,
            )

            self.assertEqual(first["copied"], 4)
            self.assertEqual(first["skipped"], 0)
            self.assertEqual(second["copied"], 0)
            self.assertEqual(second["skipped"], 4)
            self.assertEqual(
                (data_root / "questions.json").read_text(encoding="utf-8"),
                '[{"question_id":"q1"}]',
            )

    def test_existing_mismatched_target_is_rejected_without_overwrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            package_root, data_root, video_root, contract_path = self._fixture(Path(tmp))
            target = data_root
            target.mkdir(parents=True)
            (target / "questions.json").write_text("local-change", encoding="utf-8")

            with self.assertRaisesRegex(AssetConflictError, "backend_delivery"):
                import_knowledge_atlas_assets(
                    package_root=package_root,
                    data_root=data_root,
                    video_root=video_root,
                    contract_path=contract_path,
                )

            self.assertEqual((target / "questions.json").read_text(encoding="utf-8"), "local-change")

    def test_source_mismatch_is_rejected_before_any_copy(self):
        with tempfile.TemporaryDirectory() as tmp:
            package_root, data_root, video_root, contract_path = self._fixture(Path(tmp))
            (package_root / "知识库管理组件" / "data" / "backend_delivery" / "questions.json").write_text(
                "tampered", encoding="utf-8"
            )

            with self.assertRaisesRegex(ContractVerificationError, "backend_delivery"):
                import_knowledge_atlas_assets(
                    package_root=package_root,
                    data_root=data_root,
                    video_root=video_root,
                    contract_path=contract_path,
                )

            self.assertFalse(data_root.exists())
            self.assertFalse(video_root.exists())

    def test_missing_source_reports_local_atlas_degradation(self):
        with tempfile.TemporaryDirectory() as tmp:
            package_root, data_root, video_root, contract_path = self._fixture(Path(tmp))
            missing_root = package_root.parent / "missing-handoff"

            with self.assertRaisesRegex(SourceAssetMissingError, "missing-handoff"):
                import_knowledge_atlas_assets(
                    package_root=missing_root,
                    data_root=data_root,
                    video_root=video_root,
                    contract_path=contract_path,
                )

            self.assertFalse(data_root.exists())
            self.assertFalse(video_root.exists())

    def test_windows_directory_promotion_retries_a_transient_permission_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            staging = root / ".atlas.importing"
            target = root / "atlas"
            staging.mkdir()
            (staging / "ready.txt").write_text("ready", encoding="utf-8")
            original_rename = Path.rename
            calls = 0

            def flaky_rename(path: Path, destination: Path):
                nonlocal calls
                calls += 1
                if calls == 1:
                    raise PermissionError("transient Windows scanner lock")
                return original_rename(path, destination)

            with patch.object(Path, "rename", new=flaky_rename), patch("time.sleep") as sleep:
                _promote_staging(staging, target, retry_delays=(0.01, 0.02))

            self.assertEqual(calls, 2)
            sleep.assert_called_once_with(0.01)
            self.assertEqual((target / "ready.txt").read_text(encoding="utf-8"), "ready")

    def test_verified_deterministic_staging_tree_is_resumed_after_interruption(self):
        with tempfile.TemporaryDirectory() as tmp:
            package_root, data_root, video_root, contract_path = self._fixture(Path(tmp))
            contract = json.loads(contract_path.read_text(encoding="utf-8"))
            component = contract["components"][0]
            staging = data_root.parent / (
                f".{data_root.name}.importing-{component['tree_sha256'][:12]}"
            )
            staging.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(package_root / component["source"], staging)

            report = import_knowledge_atlas_assets(
                package_root=package_root,
                data_root=data_root,
                video_root=video_root,
                contract_path=contract_path,
            )

            self.assertEqual(report["copied"], 4)
            self.assertFalse(staging.exists())
            self.assertTrue((data_root / "questions.json").is_file())

    def test_locked_windows_staging_uses_verified_copy_and_ready_receipt(self):
        with tempfile.TemporaryDirectory() as tmp:
            package_root, data_root, video_root, contract_path = self._fixture(Path(tmp))

            with patch(
                "APP.backend.scripts.import_knowledge_atlas_assets._promote_staging",
                side_effect=PermissionError("workspace watcher"),
            ):
                report = import_knowledge_atlas_assets(
                    package_root=package_root,
                    data_root=data_root,
                    video_root=video_root,
                    contract_path=contract_path,
                )

            self.assertEqual(report["copied"], 4)
            ready = json.loads(
                (data_root.parent / f".{data_root.name}.ready.json").read_text(encoding="utf-8")
            )
            self.assertEqual(ready["tree_sha256"], report["components"][0]["tree_sha256"])
            self.assertTrue((data_root / "questions.json").is_file())
            self.assertFalse(any(data_root.parent.glob(f".{data_root.name}.importing-*")))

    def test_component_selection_imports_only_requested_payload(self):
        with tempfile.TemporaryDirectory() as tmp:
            package_root, data_root, video_root, contract_path = self._fixture(Path(tmp))

            report = import_knowledge_atlas_assets(
                package_root=package_root,
                data_root=data_root,
                video_root=video_root,
                contract_path=contract_path,
                component_names={"video_source_excel"},
            )

            self.assertEqual(report["verified"], 1)
            self.assertEqual(report["copied"], 1)
            self.assertFalse(data_root.exists())
            self.assertTrue((video_root / "source_excel" / "source.xlsx").is_file())


if __name__ == "__main__":
    unittest.main()
