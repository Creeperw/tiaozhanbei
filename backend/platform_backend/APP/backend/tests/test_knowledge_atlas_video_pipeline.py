from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


def _write_candidate(root: Path, *, bvid: str = "BV1TEST00001", segments: int = 1) -> Path:
    full_batch = root / "full_batch"
    results = root / "full_batch_results"
    full_batch.mkdir(parents=True)
    (results / bvid).mkdir(parents=True)
    (full_batch / "manifest.json").write_text(
        json.dumps({
            "model": "deepseek-v4-flash",
            "valid_primary_bvid_count": 1,
            "videos": [{"bvid": bvid, "status": "ready"}],
        }),
        encoding="utf-8",
    )
    (results / "catalog.json").write_text(
        json.dumps({
            "model": "deepseek-v4-flash",
            "video_count": 1,
            "segment_count": segments,
            "matched_segment_count": segments,
            "videos": [{"bvid": bvid}],
        }),
        encoding="utf-8",
    )
    (results / bvid / "classification_result.json").write_text(
        json.dumps({
            "bvid": bvid,
            "segment_count": segments,
            "matched_segment_count": segments,
            "pages": [{
                "page": 1,
                "segments": [{
                    "start_seconds": 12 + index,
                    "end_seconds": 24 + index,
                    "transcript": "折返讲解",
                    "kp_matches": [{"kp_id": "062438", "confidence": 0.9}],
                } for index in range(segments)],
            }],
        }),
        encoding="utf-8",
    )
    return root


class KnowledgeAtlasVideoPipelineTests(unittest.TestCase):
    def test_validated_release_switches_pointer_without_overwriting_current_tree(self):
        from APP.backend.knowledge_atlas_video_pipeline import (
            active_video_release_root,
            publish_video_candidate,
        )

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runtime = root / "runtime"
            candidate = _write_candidate(root / "candidate")
            runtime.mkdir()
            (runtime / "sentinel.txt").write_text("keep-current", encoding="utf-8")

            report = publish_video_candidate(candidate, runtime, version="release-001")

            self.assertEqual(report["video_count"], 1)
            self.assertEqual(report["segment_count"], 1)
            self.assertEqual(active_video_release_root(runtime).name, "release-001")
            self.assertEqual((runtime / "sentinel.txt").read_text(encoding="utf-8"), "keep-current")
            pointer = json.loads((runtime / ".video-active.json").read_text(encoding="utf-8"))
            self.assertEqual(pointer["version"], "release-001")
            self.assertTrue((runtime / "versions" / "release-001" / "full_batch_results" / "catalog.json").is_file())

    def test_invalid_candidate_does_not_change_previous_active_release(self):
        from APP.backend.knowledge_atlas_video_pipeline import (
            VideoPipelineContractError,
            active_video_release_root,
            publish_video_candidate,
        )

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runtime = root / "runtime"
            first = _write_candidate(root / "first")
            publish_video_candidate(first, runtime, version="release-001")
            pointer_before = (runtime / ".video-active.json").read_bytes()
            invalid = _write_candidate(root / "invalid", bvid="BV1TEST00002")
            (invalid / "full_batch_results" / "catalog.json").write_text("{broken", encoding="utf-8")

            with self.assertRaises(VideoPipelineContractError):
                publish_video_candidate(invalid, runtime, version="release-002")

            self.assertEqual((runtime / ".video-active.json").read_bytes(), pointer_before)
            self.assertEqual(active_video_release_root(runtime).name, "release-001")
            self.assertFalse((runtime / "versions" / "release-002").exists())

    def test_bad_classification_schema_or_segment_count_never_switches_pointer(self):
        from APP.backend.knowledge_atlas_video_pipeline import (
            VideoPipelineContractError,
            publish_video_candidate,
        )

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runtime = root / "runtime"
            publish_video_candidate(_write_candidate(root / "first"), runtime, version="release-001")
            pointer_before = (runtime / ".video-active.json").read_bytes()

            bad_schema = _write_candidate(root / "bad-schema", bvid="BV1TEST00002")
            result_path = bad_schema / "full_batch_results" / "BV1TEST00002" / "classification_result.json"
            result = json.loads(result_path.read_text(encoding="utf-8"))
            result["pages"][0]["segments"][0].pop("start_seconds")
            result_path.write_text(json.dumps(result), encoding="utf-8")
            with self.assertRaisesRegex(VideoPipelineContractError, "start_seconds"):
                publish_video_candidate(bad_schema, runtime, version="release-002")
            self.assertEqual((runtime / ".video-active.json").read_bytes(), pointer_before)

            wrong_count = _write_candidate(root / "wrong-count", bvid="BV1TEST00003")
            catalog_path = wrong_count / "full_batch_results" / "catalog.json"
            catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
            catalog["segment_count"] = 99
            catalog_path.write_text(json.dumps(catalog), encoding="utf-8")
            with self.assertRaisesRegex(VideoPipelineContractError, "segment count"):
                publish_video_candidate(wrong_count, runtime, version="release-003")
            self.assertEqual((runtime / ".video-active.json").read_bytes(), pointer_before)

    def test_malformed_declared_count_is_reported_as_contract_error(self):
        from APP.backend.knowledge_atlas_video_pipeline import (
            VideoPipelineContractError,
            validate_video_candidate,
        )

        with tempfile.TemporaryDirectory() as directory:
            candidate = _write_candidate(Path(directory) / "candidate")
            catalog_path = candidate / "full_batch_results" / "catalog.json"
            catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
            catalog["segment_count"] = "not-a-number"
            catalog_path.write_text(json.dumps(catalog), encoding="utf-8")

            with self.assertRaisesRegex(VideoPipelineContractError, "segment_count"):
                validate_video_candidate(candidate)

    def test_traversal_bvid_never_switches_active_pointer(self):
        from APP.backend.knowledge_atlas_video_pipeline import (
            VideoPipelineContractError,
            publish_video_candidate,
        )

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runtime = root / "runtime"
            publish_video_candidate(_write_candidate(root / "first"), runtime, version="release-001")
            pointer_before = (runtime / ".video-active.json").read_bytes()
            candidate = _write_candidate(root / "candidate", bvid="BV1TEST00002")
            manifest_path = candidate / "full_batch" / "manifest.json"
            catalog_path = candidate / "full_batch_results" / "catalog.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
            manifest["videos"][0]["bvid"] = "../full_batch"
            catalog["videos"][0]["bvid"] = "../full_batch"
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            catalog_path.write_text(json.dumps(catalog), encoding="utf-8")
            result = json.loads(
                (
                    candidate
                    / "full_batch_results"
                    / "BV1TEST00002"
                    / "classification_result.json"
                ).read_text(encoding="utf-8")
            )
            result["bvid"] = "../full_batch"
            (candidate / "full_batch" / "classification_result.json").write_text(
                json.dumps(result), encoding="utf-8"
            )

            with self.assertRaisesRegex(VideoPipelineContractError, "BVID"):
                publish_video_candidate(candidate, runtime, version="release-002")

            self.assertEqual((runtime / ".video-active.json").read_bytes(), pointer_before)
            self.assertFalse((runtime / "versions" / "release-002").exists())

    def test_candidate_cannot_contain_its_release_target(self):
        from APP.backend.knowledge_atlas_video_pipeline import (
            VideoPipelineContractError,
            publish_video_candidate,
        )

        with tempfile.TemporaryDirectory() as directory:
            runtime = _write_candidate(Path(directory) / "runtime")

            with self.assertRaisesRegex(VideoPipelineContractError, "must not contain"):
                publish_video_candidate(runtime, runtime, version="release-001")

            self.assertFalse((runtime / "versions").exists())

    def test_tampered_existing_release_is_not_reused_or_reactivated(self):
        from APP.backend.knowledge_atlas_video_pipeline import (
            VideoPipelineContractError,
            publish_video_candidate,
        )

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runtime = root / "runtime"
            candidate = _write_candidate(root / "candidate")
            publish_video_candidate(candidate, runtime, version="release-001")
            pointer_before = (runtime / ".video-active.json").read_bytes()
            result_path = (
                runtime
                / "versions"
                / "release-001"
                / "full_batch_results"
                / "BV1TEST00001"
                / "classification_result.json"
            )
            result = json.loads(result_path.read_text(encoding="utf-8"))
            result["pages"][0]["segments"][0]["transcript"] = "tampered"
            result_path.write_text(json.dumps(result), encoding="utf-8")

            with self.assertRaisesRegex(VideoPipelineContractError, "differs"):
                publish_video_candidate(candidate, runtime, version="release-001")

            self.assertEqual((runtime / ".video-active.json").read_bytes(), pointer_before)

    def test_tampered_release_manifest_version_is_not_reactivated(self):
        from APP.backend.knowledge_atlas_video_pipeline import (
            VideoPipelineContractError,
            publish_video_candidate,
        )

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runtime = root / "runtime"
            candidate = _write_candidate(root / "candidate")
            publish_video_candidate(candidate, runtime, version="release-001")
            pointer_before = (runtime / ".video-active.json").read_bytes()
            manifest_path = runtime / "versions" / "release-001" / ".video-release.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["version"] = "release-evil"
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

            with self.assertRaisesRegex(VideoPipelineContractError, "differs"):
                publish_video_candidate(candidate, runtime, version="release-001")

            self.assertEqual((runtime / ".video-active.json").read_bytes(), pointer_before)

    def test_active_pointer_with_tampered_release_falls_back_to_current_root(self):
        from APP.backend.knowledge_atlas_video_pipeline import (
            active_video_release_root,
            publish_video_candidate,
        )

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runtime = root / "runtime"
            publish_video_candidate(_write_candidate(root / "candidate"), runtime, version="release-001")
            release_manifest = runtime / "versions" / "release-001" / ".video-release.json"
            release_manifest.write_text("{}", encoding="utf-8")

            self.assertEqual(active_video_release_root(runtime), runtime.resolve())

    def test_admin_pipeline_credentials_are_environment_only_and_validated(self):
        from APP.backend.knowledge_atlas_video_pipeline import pipeline_credentials_from_environment

        with patch.dict("os.environ", {}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "DEEPSEEK_BASE_URL"):
                pipeline_credentials_from_environment()

        values = {
            "DEEPSEEK_BASE_URL": "https://example.invalid/v1",
            "DEEPSEEK_API_KEY": "secret-key",
            "BILIBILI_SESSION_JSON": json.dumps({"cookies": {"SESSDATA": "secret-cookie"}}),
        }
        with patch.dict("os.environ", values, clear=True):
            credentials = pipeline_credentials_from_environment()
        self.assertEqual(credentials["bilibili_session"]["cookies"]["SESSDATA"], "secret-cookie")

    def test_staged_subprocess_receives_explicit_knowledge_data_root(self):
        from APP.backend.scripts import update_knowledge_atlas_video_links as cli

        with tempfile.TemporaryDirectory() as directory, patch.object(
            cli.subprocess, "run"
        ) as run:
            cli._run_staged_pipeline(
                Path(directory),
                knowledge_data_root=Path(directory) / "backend_delivery",
                harvest_workers=1,
                page_workers=1,
                api_workers=1,
            )

        environment = run.call_args.kwargs["env"]
        self.assertEqual(
            environment["KNOWLEDGE_PUBLIC_DATA"],
            str((Path(directory) / "backend_delivery").resolve()),
        )

    def test_run_rejects_traversal_version_before_creating_staging(self):
        from APP.backend.scripts import update_knowledge_atlas_video_links as cli

        values = {
            "DEEPSEEK_BASE_URL": "https://example.invalid/v1",
            "DEEPSEEK_API_KEY": "secret-key",
            "BILIBILI_SESSION_JSON": json.dumps({"cookies": {"SESSDATA": "secret-cookie"}}),
        }
        with tempfile.TemporaryDirectory() as directory, patch.dict(
            "os.environ", values, clear=False
        ), patch.object(
            cli.sys,
            "argv",
            [
                "update_knowledge_atlas_video_links.py",
                "--run",
                "--video-root",
                str(Path(directory) / "runtime"),
                "--version",
                r"..\..\outside",
            ],
        ), patch.object(cli, "_prepare_staging_worktree") as prepare:
            exit_code = cli.main()

        self.assertEqual(exit_code, 2)
        prepare.assert_not_called()

    def test_atlas_store_reads_results_from_active_version_pointer(self):
        from APP.backend.knowledge_atlas_service import KnowledgeAtlasStore
        from APP.backend.knowledge_atlas_video_pipeline import publish_video_candidate

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            data_root = root / "backend_delivery"
            data_root.mkdir()
            runtime = root / "video"
            publish_video_candidate(_write_candidate(root / "candidate"), runtime, version="release-001")
            store = KnowledgeAtlasStore(data_root, video_root=runtime)

            self.assertEqual(store.video_result_root.name, "full_batch_results")
            self.assertEqual(store.video_result_root.parent.name, "release-001")


if __name__ == "__main__":
    unittest.main()
