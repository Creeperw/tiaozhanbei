from pathlib import Path

import pytest

from competition_app.asset_layout import (
    AssetLayout,
    AssetLayoutError,
    load_asset_manifest,
    resolve_platform_backend_root,
)


def resolve(tmp_path: Path, values: dict[str, str]) -> AssetLayout:
    repository = tmp_path / "repo"
    backend = repository / "backend"
    runtime = backend / "competition_app" / "runtime"
    backend.mkdir(parents=True)
    return AssetLayout.resolve(
        values,
        repository_root=repository,
        backend_root=backend,
        package_runtime_root=runtime,
    )


def test_unified_roots_keep_assets_read_only_and_runtime_separate(tmp_path: Path) -> None:
    layout = resolve(
        tmp_path,
        {
            "SHIZHEN_ASSET_ROOT": "shared/assets",
            "SHIZHEN_RUNTIME_ROOT": "shared/runtime",
            "SHIZHEN_ASSET_RELEASE": "2026-08-03",
        },
    )

    repository = tmp_path / "repo"
    assert layout.knowledge_delivery_root == (
        repository / "shared/assets/knowledge/releases/2026-08-03"
    ).resolve()
    assert layout.question_vector_store_root == (
        repository / "shared/assets/vectors/public/2026-08-03"
    ).resolve()
    assert layout.knowledge_atlas_chapter_root == (
        repository
        / "shared/assets/knowledge-atlas/chapters/releases/2026-07-22"
    ).resolve()
    assert layout.runtime_root == (
        repository / "shared/runtime/competition_app"
    ).resolve()
    assert layout.knowledge_runtime_root == (
        repository / "shared/runtime/knowledge"
    ).resolve()


def test_manifest_paths_are_relative_to_asset_root(tmp_path: Path) -> None:
    repository = tmp_path / "repo"
    manifest = repository / "asset-manifest.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(
        """{
          "schema_version": "1.0",
          "release_id": "release-7",
          "assets": {
            "knowledge_delivery": "knowledge/release-7",
            "question_vector_store": "vectors/release-7",
            "knowledge_atlas_chapters": "knowledge-atlas/chapters/release-7",
            "textbook_pdfs": "textbooks/current"
          }
        }""",
        encoding="utf-8",
    )

    layout = resolve(
        tmp_path,
        {
            "SHIZHEN_ASSET_ROOT": "assets",
            "SHIZHEN_ASSET_MANIFEST": "asset-manifest.json",
        },
    )

    assert layout.release_id == "release-7"
    assert layout.knowledge_delivery_root == (
        repository / "assets/knowledge/release-7"
    ).resolve()
    assert layout.textbook_pdf_root == (
        repository / "assets/textbooks/current"
    ).resolve()
    assert layout.knowledge_atlas_chapter_root == (
        repository / "assets/knowledge-atlas/chapters/release-7"
    ).resolve()


def test_specific_legacy_environment_variable_overrides_manifest(tmp_path: Path) -> None:
    layout = resolve(
        tmp_path,
        {
            "SHIZHEN_ASSET_ROOT": "assets",
            "KNOWLEDGE_HANDOFF_ROOT": "/mnt/legacy/knowledge-delivery",
            "KNOWLEDGE_RUNTIME_ROOT": "/mnt/runtime/knowledge",
        },
    )

    assert layout.knowledge_delivery_root == Path(
        "/mnt/legacy/knowledge-delivery"
    )
    assert layout.knowledge_runtime_root == Path("/mnt/runtime/knowledge")


def test_manifest_rejects_parent_traversal(tmp_path: Path) -> None:
    repository = tmp_path / "repo"
    manifest = repository / "asset-manifest.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(
        '{"schema_version":"1.0","assets":{"knowledge_delivery":"../escape"}}',
        encoding="utf-8",
    )

    with pytest.raises(AssetLayoutError, match="escapes"):
        resolve(
            tmp_path,
            {
                "SHIZHEN_ASSET_ROOT": "assets",
                "SHIZHEN_ASSET_MANIFEST": "asset-manifest.json",
            },
        )


def test_manifest_allows_admin_installed_symlink_below_asset_root(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repo"
    source = tmp_path / "external-knowledge"
    source.mkdir()
    asset_root = repository / "assets"
    link = asset_root / "knowledge" / "release-7"
    link.parent.mkdir(parents=True)
    link.symlink_to(source, target_is_directory=True)
    manifest = asset_root / "manifest.json"
    manifest.write_text(
        '{"schema_version":"1.0","release_id":"release-7",'
        '"assets":{"knowledge_delivery":"knowledge/release-7"}}',
        encoding="utf-8",
    )

    layout = resolve(
        tmp_path,
        {
            "SHIZHEN_ASSET_ROOT": "assets",
            "SHIZHEN_ASSET_MANIFEST": "assets/manifest.json",
        },
    )

    assert layout.knowledge_delivery_root == source.resolve()


def test_manifest_requires_supported_schema(tmp_path: Path) -> None:
    path = tmp_path / "manifest.json"
    path.write_text('{"schema_version":"2.0","assets":{}}', encoding="utf-8")

    with pytest.raises(AssetLayoutError, match="schema_version"):
        load_asset_manifest(path)


def test_old_backend_setting_redirects_to_stable_directory_after_move(
    tmp_path: Path,
) -> None:
    backend = tmp_path / "backend"
    canonical = backend / "platform_backend"
    canonical.mkdir(parents=True)

    resolved = resolve_platform_backend_root(
        {"BACKEND_HANDOFF_ROOT": "competition/backend-handoff-20260720"},
        backend_root=backend,
    )

    assert resolved == canonical.resolve()
