"""Validated, versioned publication primitives for Knowledge Atlas video data."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ACTIVE_VIDEO_POINTER = ".video-active.json"
VIDEO_RELEASE_MANIFEST = ".video-release.json"
_SAFE_VERSION = re.compile(r"^[0-9A-Za-z][0-9A-Za-z._-]{0,95}$")
_SAFE_BVID = re.compile(r"^BV[0-9A-Za-z]{10}$")


class VideoPipelineContractError(RuntimeError):
    """A candidate cannot be published without violating the video contract."""


def validate_video_version(version: Any) -> str:
    value = str(version or "")
    if not _SAFE_VERSION.fullmatch(value):
        raise VideoPipelineContractError(f"invalid video release version: {version!r}")
    return value


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_json(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (FileNotFoundError, OSError, json.JSONDecodeError) as exc:
        raise VideoPipelineContractError(f"invalid {label}: {path}") from exc
    if not isinstance(value, dict):
        raise VideoPipelineContractError(f"{label} must be a JSON object: {path}")
    return value


def _contract_int(value: Any, label: str, *, default: int | None = None) -> int | None:
    if value is None:
        return default
    if isinstance(value, bool) or (isinstance(value, float) and not value.is_integer()):
        raise VideoPipelineContractError(f"{label} must be an integer")
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise VideoPipelineContractError(f"{label} must be an integer") from exc


def _tree_contract(root: Path) -> tuple[str, int, int]:
    digest = hashlib.sha256()
    file_count = 0
    total_bytes = 0
    for path in sorted((item for item in root.rglob("*") if item.is_file()), key=lambda item: item.as_posix()):
        relative = path.relative_to(root).as_posix()
        if relative == VIDEO_RELEASE_MANIFEST:
            continue
        size = path.stat().st_size
        file_hash = _sha256_file(path)
        digest.update(f"{relative}\0{size}\0{file_hash}\n".encode("utf-8"))
        file_count += 1
        total_bytes += size
    return digest.hexdigest(), file_count, total_bytes


def _collect_bvids(value: Any, label: str, *, ready_only: bool = False) -> set[str]:
    if not isinstance(value, list):
        raise VideoPipelineContractError(f"{label} videos must be a list")
    result: set[str] = set()
    for item in value:
        if ready_only and isinstance(item, dict) and item.get("status") != "ready":
            continue
        if not isinstance(item, dict):
            raise VideoPipelineContractError(f"{label} video entry is invalid")
        bvid = str(item.get("bvid") or "").strip()
        if not _SAFE_BVID.fullmatch(bvid):
            raise VideoPipelineContractError(f"{label} BVID is invalid: {bvid!r}")
        if bvid in result:
            raise VideoPipelineContractError(f"{label} contains duplicate BVID: {bvid}")
        result.add(bvid)
    return result


def validate_video_candidate(candidate_root: str | Path) -> dict[str, Any]:
    root = Path(candidate_root).resolve()
    source_root = root / "full_batch"
    result_root = root / "full_batch_results"
    manifest = _read_json(source_root / "manifest.json", "video source manifest")
    catalog = _read_json(result_root / "catalog.json", "video result catalog")
    manifest_videos = _collect_bvids(
        manifest.get("videos"), "video source manifest", ready_only=True
    )
    catalog_videos = _collect_bvids(catalog.get("videos"), "video result catalog")
    if not manifest_videos:
        raise VideoPipelineContractError("video source manifest has no ready BVIDs")
    if manifest_videos != catalog_videos:
        raise VideoPipelineContractError(
            "video source/result BVID mismatch: "
            f"source={len(manifest_videos)}, results={len(catalog_videos)}"
        )
    declared_source_count = manifest.get("valid_primary_bvid_count")
    declared_result_count = catalog.get("video_count")
    if _contract_int(declared_source_count, "valid_primary_bvid_count") not in (
        None,
        len(manifest_videos),
    ):
        raise VideoPipelineContractError("video source manifest count mismatch")
    if _contract_int(declared_result_count, "video_count") not in (None, len(catalog_videos)):
        raise VideoPipelineContractError("video result catalog count mismatch")
    actual_segment_count = 0
    actual_matched_segment_count = 0
    for bvid in sorted(catalog_videos):
        result_path = result_root / bvid / "classification_result.json"
        result = _read_json(result_path, f"classification result for {bvid}")
        if str(result.get("bvid") or "").strip() != bvid:
            raise VideoPipelineContractError(f"classification result BVID mismatch: {bvid}")
        pages = result.get("pages")
        if not isinstance(pages, list):
            raise VideoPipelineContractError(f"classification pages must be a list: {bvid}")
        result_segment_count = 0
        result_matched_count = 0
        for page in pages:
            if not isinstance(page, dict) or not isinstance(page.get("page"), int):
                raise VideoPipelineContractError(f"classification page schema is invalid: {bvid}")
            segments = page.get("segments")
            if not isinstance(segments, list):
                raise VideoPipelineContractError(f"classification segments must be a list: {bvid}")
            for segment in segments:
                if not isinstance(segment, dict):
                    raise VideoPipelineContractError(f"classification segment schema is invalid: {bvid}")
                for field in ("start_seconds", "end_seconds"):
                    value = segment.get(field)
                    if isinstance(value, bool) or not isinstance(value, (int, float)):
                        raise VideoPipelineContractError(
                            f"classification segment {field} is invalid: {bvid}"
                        )
                if float(segment["start_seconds"]) < 0 or float(segment["end_seconds"]) < float(segment["start_seconds"]):
                    raise VideoPipelineContractError(f"classification segment time range is invalid: {bvid}")
                if not isinstance(segment.get("transcript"), str):
                    raise VideoPipelineContractError(
                        f"classification segment transcript is invalid: {bvid}"
                    )
                matches = segment.get("kp_matches")
                if not isinstance(matches, list):
                    raise VideoPipelineContractError(
                        f"classification segment kp_matches is invalid: {bvid}"
                    )
                for match in matches:
                    if not isinstance(match, dict) or not str(match.get("kp_id") or "").strip():
                        raise VideoPipelineContractError(
                            f"classification kp_match schema is invalid: {bvid}"
                        )
                result_segment_count += 1
                if matches:
                    result_matched_count += 1
        declared_segments = result.get("segment_count")
        declared_matched = result.get("matched_segment_count")
        if _contract_int(declared_segments, f"segment_count for {bvid}") not in (
            None,
            result_segment_count,
        ):
            raise VideoPipelineContractError(f"classification segment count mismatch: {bvid}")
        if _contract_int(declared_matched, f"matched_segment_count for {bvid}") not in (
            None,
            result_matched_count,
        ):
            raise VideoPipelineContractError(f"classification matched segment count mismatch: {bvid}")
        actual_segment_count += result_segment_count
        actual_matched_segment_count += result_matched_count

    segment_count = _contract_int(catalog.get("segment_count"), "segment_count", default=0)
    matched_segment_count = _contract_int(
        catalog.get("matched_segment_count"),
        "matched_segment_count",
        default=0,
    )
    assert segment_count is not None and matched_segment_count is not None
    if segment_count < 0 or matched_segment_count < 0:
        raise VideoPipelineContractError("video segment count cannot be negative")
    if segment_count != actual_segment_count:
        raise VideoPipelineContractError(
            f"video catalog segment count mismatch: catalog={segment_count}, actual={actual_segment_count}"
        )
    if matched_segment_count != actual_matched_segment_count:
        raise VideoPipelineContractError(
            "video catalog matched segment count mismatch: "
            f"catalog={matched_segment_count}, actual={actual_matched_segment_count}"
        )
    content_sha256, file_count, total_bytes = _tree_contract(root)
    return {
        "root": str(root),
        "video_count": len(catalog_videos),
        "segment_count": segment_count,
        "matched_segment_count": matched_segment_count,
        "file_count": file_count,
        "total_bytes": total_bytes,
        "content_sha256": content_sha256,
        "source_manifest_sha256": _sha256_file(source_root / "manifest.json"),
        "result_catalog_sha256": _sha256_file(result_root / "catalog.json"),
    }


def active_video_release_root(video_root: str | Path) -> Path:
    root = Path(video_root).resolve()
    pointer_path = root / ACTIVE_VIDEO_POINTER
    try:
        pointer = json.loads(pointer_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return root
    version = pointer.get("version") if isinstance(pointer, dict) else None
    if not isinstance(version, str) or not _SAFE_VERSION.fullmatch(version):
        return root
    release = root / "versions" / version
    release_manifest_path = release / VIDEO_RELEASE_MANIFEST
    if not (
        (release / "full_batch" / "manifest.json").is_file()
        and (release / "full_batch_results" / "catalog.json").is_file()
        and release_manifest_path.is_file()
    ):
        return root
    expected_hash = pointer.get("release_manifest_sha256")
    if not expected_hash or _sha256_file(release_manifest_path) != expected_hash:
        return root
    try:
        release_manifest = _read_json(release_manifest_path, "video release manifest")
    except VideoPipelineContractError:
        return root
    if release_manifest.get("version") != version:
        return root
    return release


def publish_video_candidate(
    candidate_root: str | Path,
    video_root: str | Path,
    *,
    version: str,
) -> dict[str, Any]:
    version = validate_video_version(version)
    candidate = Path(candidate_root).resolve()
    runtime = Path(video_root).resolve()
    candidate_contract = validate_video_candidate(candidate)
    versions = runtime / "versions"
    target = versions / version
    if (
        candidate == target
        or candidate.is_relative_to(target)
        or target.is_relative_to(candidate)
    ):
        raise VideoPipelineContractError(
            "video candidate and release target must not contain one another"
        )
    release_payload = {
        "schema_version": 1,
        "version": version,
        "published_at": datetime.now(timezone.utc).isoformat(),
        **{key: value for key, value in candidate_contract.items() if key != "root"},
    }
    skipped = False
    if target.exists():
        existing = _read_json(target / VIDEO_RELEASE_MANIFEST, "existing video release manifest")
        existing_contract = validate_video_candidate(target)
        contract_keys = tuple(key for key in candidate_contract if key != "root")
        if (
            existing.get("schema_version") != 1
            or existing.get("version") != version
            or not isinstance(existing.get("published_at"), str)
            or any(existing.get(key) != existing_contract[key] for key in contract_keys)
            or any(existing_contract[key] != candidate_contract[key] for key in contract_keys)
        ):
            raise VideoPipelineContractError(
                f"existing video release differs from candidate: {target}"
            )
        release_payload = existing
        skipped = True
    else:
        versions.mkdir(parents=True, exist_ok=True)
        staging = versions / f".{version}.staging-{os.getpid()}"
        if staging.exists():
            raise VideoPipelineContractError(f"video staging target already exists: {staging}")
        shutil.copytree(candidate, staging)
        copied_contract = validate_video_candidate(staging)
        if copied_contract["content_sha256"] != candidate_contract["content_sha256"]:
            raise VideoPipelineContractError("video candidate changed during staging copy")
        (staging / VIDEO_RELEASE_MANIFEST).write_text(
            json.dumps(release_payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        staging.replace(target)

    runtime.mkdir(parents=True, exist_ok=True)
    pointer_path = runtime / ACTIVE_VIDEO_POINTER
    previous_root = active_video_release_root(runtime)
    previous_version = previous_root.name if previous_root.parent.name == "versions" else None
    pointer = {
        "schema_version": 1,
        "version": version,
        "previous_version": previous_version,
        "release_manifest_sha256": _sha256_file(target / VIDEO_RELEASE_MANIFEST),
        "activated_at": datetime.now(timezone.utc).isoformat(),
    }
    temporary = pointer_path.with_name(f"{pointer_path.name}.tmp-{os.getpid()}")
    temporary.write_text(
        json.dumps(pointer, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(pointer_path)
    return {
        **{key: value for key, value in release_payload.items() if key != "published_at"},
        "target": str(target),
        "active": True,
        "skipped": skipped,
    }


def pipeline_credentials_from_environment() -> dict[str, Any]:
    required = ("DEEPSEEK_BASE_URL", "DEEPSEEK_API_KEY", "BILIBILI_SESSION_JSON")
    missing = [name for name in required if not str(os.environ.get(name) or "").strip()]
    if missing:
        raise RuntimeError(f"video pipeline requires environment variables: {', '.join(missing)}")
    try:
        session = json.loads(os.environ["BILIBILI_SESSION_JSON"])
    except json.JSONDecodeError as exc:
        raise RuntimeError("BILIBILI_SESSION_JSON must be valid JSON") from exc
    if not isinstance(session, dict) or not isinstance(session.get("cookies"), dict):
        raise RuntimeError("BILIBILI_SESSION_JSON must contain a cookies object")
    return {
        "deepseek_base_url": os.environ["DEEPSEEK_BASE_URL"],
        "deepseek_api_key": os.environ["DEEPSEEK_API_KEY"],
        "bilibili_session": session,
    }
