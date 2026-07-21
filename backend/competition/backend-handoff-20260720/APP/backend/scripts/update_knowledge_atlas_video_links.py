"""Administrator CLI for staging and atomically publishing video-link updates.

Credentials are accepted only through environment variables.  The teammate
pipeline runs against an isolated staging copy; the currently active Atlas
video release is never mutated in place.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from APP.backend.config import KNOWLEDGE_ATLAS_DATA_ROOT, KNOWLEDGE_ATLAS_VIDEO_ROOT
from APP.backend.knowledge_atlas_video_pipeline import (
    VideoPipelineContractError,
    active_video_release_root,
    pipeline_credentials_from_environment,
    publish_video_candidate,
    validate_video_candidate,
    validate_video_version,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PIPELINE_SOURCE = (
    REPOSITORY_ROOT
    / "division of labor"
    / "知识星球视频知识库_前端交接包_2026-07-18"
    / "bilibili_video_page"
)


def _copy_pipeline_program(source: Path, target: Path) -> None:
    if not (source / "sync_changed_links.py").is_file():
        raise RuntimeError(f"teammate video pipeline is unavailable: {source}")
    ignored = shutil.ignore_patterns(
        "runtime",
        "DATA",
        ".venv",
        "__pycache__",
        ".pytest_cache",
        "config.local.json",
        "bilibili_session.json",
    )
    shutil.copytree(source, target, ignore=ignored)


def _prepare_staging_worktree(
    *,
    pipeline_source: Path,
    video_root: Path,
    worktree: Path,
    bilibili_session: dict,
) -> None:
    _copy_pipeline_program(pipeline_source, worktree)
    active = active_video_release_root(video_root)
    runtime = worktree / "runtime"
    runtime.mkdir(parents=True, exist_ok=True)
    for name in ("full_batch", "full_batch_results"):
        source = active / name
        if not source.is_dir():
            raise RuntimeError(f"active video dataset is missing: {source}")
        shutil.copytree(source, runtime / name)
    excel_source = video_root / "source_excel"
    if not excel_source.is_dir():
        raise RuntimeError(f"video source Excel directory is missing: {excel_source}")
    shutil.copytree(excel_source, worktree / "DATA" / "预处理")
    (runtime / "bilibili_session.json").write_text(
        json.dumps(bilibili_session, ensure_ascii=False),
        encoding="utf-8",
    )


def _run_staged_pipeline(
    worktree: Path,
    *,
    knowledge_data_root: Path,
    harvest_workers: int,
    page_workers: int,
    api_workers: int,
) -> None:
    command = [
        sys.executable,
        "sync_changed_links.py",
        "--harvest-workers",
        str(harvest_workers),
        "--page-workers",
        str(page_workers),
        "--api-workers",
        str(api_workers),
    ]
    environment = os.environ.copy()
    environment["KNOWLEDGE_PUBLIC_DATA"] = str(knowledge_data_root.resolve())
    subprocess.run(command, cwd=worktree, env=environment, check=True)


def _default_version() -> str:
    return datetime.now(timezone.utc).strftime("video-%Y%m%dT%H%M%SZ")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="在 staging 中更新知识星球视频链接，校验后原子切换 active release"
    )
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--run", action="store_true", help="运行交接包增量流水线后发布")
    action.add_argument("--publish-candidate", type=Path, help="发布已生成的候选 runtime")
    action.add_argument("--validate-candidate", type=Path, help="只验证候选 runtime")
    parser.add_argument("--video-root", type=Path, default=KNOWLEDGE_ATLAS_VIDEO_ROOT)
    parser.add_argument(
        "--pipeline-source",
        type=Path,
        default=Path(os.environ.get("KNOWLEDGE_ATLAS_PIPELINE_SOURCE") or DEFAULT_PIPELINE_SOURCE),
    )
    parser.add_argument("--version", default="")
    parser.add_argument("--harvest-workers", type=int, default=16)
    parser.add_argument("--page-workers", type=int, default=8)
    parser.add_argument("--api-workers", type=int, default=200)
    args = parser.parse_args()

    video_root = args.video_root.resolve()
    version = args.version or _default_version()
    try:
        if args.validate_candidate:
            report = {"ok": True, "validated": True, **validate_video_candidate(args.validate_candidate)}
        elif args.publish_candidate:
            version = validate_video_version(version)
            report = {
                "ok": True,
                **publish_video_candidate(args.publish_candidate, video_root, version=version),
            }
        else:
            version = validate_video_version(version)
            credentials = pipeline_credentials_from_environment()
            worktree = video_root / "staging" / version
            if worktree.exists():
                raise RuntimeError(f"staging worktree already exists: {worktree}")
            _prepare_staging_worktree(
                pipeline_source=args.pipeline_source.resolve(),
                video_root=video_root,
                worktree=worktree,
                bilibili_session=credentials["bilibili_session"],
            )
            session_path = worktree / "runtime" / "bilibili_session.json"
            try:
                _run_staged_pipeline(
                    worktree,
                    knowledge_data_root=KNOWLEDGE_ATLAS_DATA_ROOT,
                    harvest_workers=max(1, args.harvest_workers),
                    page_workers=max(1, args.page_workers),
                    api_workers=max(1, args.api_workers),
                )
            finally:
                session_path.unlink(missing_ok=True)
            candidate = worktree / "runtime"
            report = {
                "ok": True,
                **publish_video_candidate(candidate, video_root, version=version),
                "staging": str(worktree),
            }
    except (VideoPipelineContractError, RuntimeError, OSError, subprocess.SubprocessError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, sort_keys=True))
        return 2
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
