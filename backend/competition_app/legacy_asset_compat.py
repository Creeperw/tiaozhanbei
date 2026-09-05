from __future__ import annotations

from pathlib import Path


# Historical delivery names are isolated here so active configuration and
# business modules can use stable release/component terminology.
LEGACY_KNOWLEDGE_DELIVERY_NAME = "知识星球视频知识库_前端交接包_2026-07-18"
LEGACY_PLATFORM_BACKEND_NAME = "backend-handoff-20260720"


def knowledge_component_root(release_root: Path) -> Path:
    canonical = release_root / "component"
    legacy = release_root / "知识库管理组件"
    return canonical if canonical.exists() or not legacy.exists() else legacy


def knowledge_video_root(release_root: Path) -> Path:
    canonical = release_root / "video"
    legacy = release_root / "bilibili_video_page" / "runtime"
    return canonical if canonical.exists() or not legacy.exists() else legacy
