from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from competition_app.legacy_asset_compat import (
    LEGACY_KNOWLEDGE_DELIVERY_NAME,
    LEGACY_PLATFORM_BACKEND_NAME,
    knowledge_component_root,
    knowledge_video_root,
)


ASSET_MANIFEST_SCHEMA_VERSION = "1.0"
DEFAULT_ATLAS_CHAPTER_RELEASE = "2026-07-22"


class AssetLayoutError(ValueError):
    """Raised when an external asset manifest is malformed or unsafe."""


def _configured_path(
    values: Mapping[str, str],
    name: str,
    *,
    base: Path,
) -> Path | None:
    raw = str(values.get(name, "") or "").strip()
    if not raw:
        return None
    path = Path(raw).expanduser()
    return path.resolve() if path.is_absolute() else (base / path).resolve()


def _manifest_relative_path(root: Path, value: Any, *, field: str) -> Path | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    path = Path(raw)
    if path.is_absolute():
        raise AssetLayoutError(f"asset manifest field {field} must be relative")
    # Validate the manifest path lexically before following filesystem links.
    # Development migrations intentionally install read-only assets as symlinks
    # below SHIZHEN_ASSET_ROOT; resolving first would incorrectly classify every
    # such link as a manifest traversal.
    resolved_root = Path(os.path.abspath(root))
    resolved = Path(os.path.abspath(root / path))
    try:
        resolved.relative_to(resolved_root)
    except ValueError as exc:
        raise AssetLayoutError(
            f"asset manifest field {field} escapes SHIZHEN_ASSET_ROOT"
        ) from exc
    return resolved


def load_asset_manifest(path: Path | None) -> dict[str, Any]:
    if path is None or not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AssetLayoutError(f"cannot read asset manifest: {path}") from exc
    if not isinstance(payload, dict):
        raise AssetLayoutError("asset manifest root must be an object")
    schema_version = str(payload.get("schema_version") or "").strip()
    if schema_version != ASSET_MANIFEST_SCHEMA_VERSION:
        raise AssetLayoutError(
            "asset manifest schema_version must be " + ASSET_MANIFEST_SCHEMA_VERSION
        )
    assets = payload.get("assets")
    if not isinstance(assets, dict):
        raise AssetLayoutError("asset manifest assets must be an object")
    return payload


def _prefer_existing(canonical: Path, legacy: Path) -> Path:
    if canonical.exists() or not legacy.exists():
        return canonical.resolve()
    return legacy.resolve()


@dataclass(frozen=True)
class AssetLayout:
    """Resolved read-only asset and writable runtime roots.

    Specific legacy environment variables remain authoritative. A manifest can
    then select versioned assets relative to ``asset_root``. If neither is
    supplied, a canonical path is preferred while existing legacy deliveries
    remain readable during migration.
    """

    asset_root: Path
    runtime_root: Path
    manifest_path: Path | None
    release_id: str
    knowledge_delivery_root: Path
    question_vector_store_root: Path
    knowledge_vector_store_root: Path
    textbook_pdf_root: Path
    knowledge_atlas_chapter_root: Path
    knowledge_runtime_root: Path

    @property
    def knowledge_release_root(self) -> Path:
        return self.knowledge_delivery_root

    @property
    def knowledge_component_root(self) -> Path:
        return Path(
            os.path.abspath(knowledge_component_root(self.knowledge_release_root))
        )

    @property
    def knowledge_data_root(self) -> Path:
        return self.knowledge_component_root / "data" / "backend_delivery"

    @property
    def knowledge_video_root(self) -> Path:
        return Path(os.path.abspath(knowledge_video_root(self.knowledge_release_root)))

    @classmethod
    def resolve(
        cls,
        values: Mapping[str, str],
        *,
        repository_root: Path,
        backend_root: Path,
        package_runtime_root: Path,
    ) -> "AssetLayout":
        asset_root = _configured_path(
            values, "SHIZHEN_ASSET_ROOT", base=repository_root
        ) or (repository_root / "assets").resolve()
        unified_assets_configured = bool(
            str(values.get("SHIZHEN_ASSET_ROOT", "") or "").strip()
            or str(values.get("SHIZHEN_ASSET_MANIFEST", "") or "").strip()
        )
        shared_runtime_root = _configured_path(
            values, "SHIZHEN_RUNTIME_ROOT", base=repository_root
        )
        runtime_root = _configured_path(
            values, "RUNTIME_ROOT", base=repository_root
        ) or (
            (shared_runtime_root / "competition_app").resolve()
            if shared_runtime_root is not None
            else package_runtime_root.resolve()
        )
        manifest_path = _configured_path(
            values, "SHIZHEN_ASSET_MANIFEST", base=repository_root
        )
        if manifest_path is None:
            candidate = asset_root / "manifests" / "asset-manifest.json"
            manifest_path = candidate.resolve() if candidate.is_file() else None
        manifest = load_asset_manifest(manifest_path)
        unified_assets_configured = unified_assets_configured or bool(manifest)
        assets = manifest.get("assets", {}) if manifest else {}
        release_id = str(
            values.get("SHIZHEN_ASSET_RELEASE")
            or manifest.get("release_id")
            or "2026-07-18"
        ).strip()

        legacy_competition_root = backend_root / "competition"
        legacy_knowledge = (
            legacy_competition_root / LEGACY_KNOWLEDGE_DELIVERY_NAME
        )
        canonical_knowledge = asset_root / "knowledge" / "releases" / release_id
        manifest_knowledge = _manifest_relative_path(
            asset_root, assets.get("knowledge_delivery"), field="knowledge_delivery"
        )
        default_knowledge = (
            canonical_knowledge
            if unified_assets_configured or not legacy_knowledge.exists()
            else legacy_knowledge
        )
        knowledge_delivery_root = (
            _configured_path(values, "KNOWLEDGE_RELEASE_ROOT", base=backend_root)
            or _configured_path(values, "KNOWLEDGE_HANDOFF_ROOT", base=backend_root)
            or manifest_knowledge
            or default_knowledge
        )

        legacy_vector = legacy_competition_root / "vdb_store"
        canonical_vector = asset_root / "vectors" / "public" / release_id
        manifest_vector = _manifest_relative_path(
            asset_root,
            assets.get("question_vector_store"),
            field="question_vector_store",
        )
        default_vector = (
            canonical_vector
            if unified_assets_configured or not legacy_vector.exists()
            else legacy_vector
        )
        question_vector_store_root = _configured_path(
            values, "QUESTION_VECTOR_STORE_ROOT", base=backend_root
        ) or manifest_vector or default_vector
        knowledge_vector_store_root = _configured_path(
            values, "KNOWLEDGE_VECTOR_STORE_ROOT", base=backend_root
        ) or _manifest_relative_path(
            asset_root,
            assets.get("knowledge_vector_store"),
            field="knowledge_vector_store",
        ) or question_vector_store_root

        legacy_textbooks = legacy_competition_root / "textbook_pdfs"
        canonical_textbooks = asset_root / "textbooks" / "public"
        default_textbooks = (
            canonical_textbooks
            if unified_assets_configured or not legacy_textbooks.exists()
            else legacy_textbooks
        )
        textbook_pdf_root = _configured_path(
            values, "TEXTBOOK_PDF_ROOT", base=backend_root
        ) or _manifest_relative_path(
            asset_root, assets.get("textbook_pdfs"), field="textbook_pdfs"
        ) or default_textbooks

        atlas_chapter_release = str(
            values.get("SHIZHEN_ATLAS_CHAPTER_RELEASE")
            or DEFAULT_ATLAS_CHAPTER_RELEASE
        ).strip()
        legacy_atlas_chapters = (
            legacy_competition_root
            / "knowledge_atlas_chapters"
            / atlas_chapter_release
        )
        canonical_atlas_chapters = (
            asset_root
            / "knowledge-atlas"
            / "chapters"
            / "releases"
            / atlas_chapter_release
        )
        manifest_atlas_chapters = _manifest_relative_path(
            asset_root,
            assets.get("knowledge_atlas_chapters"),
            field="knowledge_atlas_chapters",
        )
        default_atlas_chapters = (
            canonical_atlas_chapters
            if unified_assets_configured or not legacy_atlas_chapters.exists()
            else legacy_atlas_chapters
        )
        knowledge_atlas_chapter_root = _configured_path(
            values, "KNOWLEDGE_ATLAS_CHAPTER_ROOT", base=backend_root
        ) or manifest_atlas_chapters or default_atlas_chapters

        knowledge_runtime_root = _configured_path(
            values, "KNOWLEDGE_RUNTIME_ROOT", base=backend_root
        ) or (
            (shared_runtime_root / "knowledge").resolve()
            if shared_runtime_root is not None
            else (runtime_root / "knowledge").resolve()
        )
        return cls(
            asset_root=asset_root,
            runtime_root=runtime_root,
            manifest_path=manifest_path,
            release_id=release_id,
            knowledge_delivery_root=knowledge_delivery_root.resolve(),
            question_vector_store_root=question_vector_store_root.resolve(),
            knowledge_vector_store_root=knowledge_vector_store_root.resolve(),
            textbook_pdf_root=textbook_pdf_root.resolve(),
            knowledge_atlas_chapter_root=knowledge_atlas_chapter_root.resolve(),
            knowledge_runtime_root=knowledge_runtime_root.resolve(),
        )


def resolve_platform_backend_root(
    values: Mapping[str, str], *, backend_root: Path
) -> Path:
    """Resolve active business backend while accepting the old dated setting."""

    canonical = (backend_root / "platform_backend").resolve()
    legacy = (backend_root / "competition" / LEGACY_PLATFORM_BACKEND_NAME).resolve()
    configured = _configured_path(
        values, "PLATFORM_BACKEND_ROOT", base=backend_root
    ) or _configured_path(values, "BACKEND_HANDOFF_ROOT", base=backend_root)
    if configured is None:
        return _prefer_existing(canonical, legacy)
    legacy_entrypoint = configured / "APP" / "backend" / "main.py"
    if configured == legacy and not legacy_entrypoint.is_file() and canonical.exists():
        return canonical
    return configured
