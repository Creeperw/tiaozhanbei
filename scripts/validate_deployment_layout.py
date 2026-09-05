#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = REPOSITORY_ROOT / "backend"
sys.path.insert(0, str(BACKEND_ROOT))

from competition_app.asset_layout import AssetLayout, resolve_platform_backend_root  # noqa: E402


def _load_dotenv(path: Path) -> dict[str, str]:
    if not path.is_file():
        return {}
    values: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip("'\"")
    return values


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate code, assets and writable runtime before deployment.")
    parser.add_argument("--env-file", type=Path)
    parser.add_argument("--require-live-assets", action="store_true")
    args = parser.parse_args()

    values: dict[str, str] = {}
    default_env = BACKEND_ROOT / "competition_app" / ".env.local"
    configured_env = os.environ.get("COMPETITION_ENV_FILE", "").strip()
    if args.env_file or configured_env:
        values.update(_load_dotenv(args.env_file or Path(configured_env).expanduser()))
    else:
        values.update(_load_dotenv(default_env.with_name(".env")))
        values.update(_load_dotenv(default_env))
    values.update(os.environ)
    layout = AssetLayout.resolve(
        values,
        repository_root=REPOSITORY_ROOT,
        backend_root=BACKEND_ROOT,
        package_runtime_root=BACKEND_ROOT / "competition_app" / "runtime",
    )
    platform_backend = resolve_platform_backend_root(values, backend_root=BACKEND_ROOT)
    knowledge_data = layout.knowledge_data_root
    checks = {
        "prompt_skills": BACKEND_ROOT / "competition_app/prompt_skills/expert_agent/general_learning_support.md",
        "acupuncture_cases": BACKEND_ROOT / "competition_app/data/acupuncture_cases.v1.json",
        "treekg_viewer": REPOSITORY_ROOT / "TreeKG-main/src/static/index.html",
        "platform_backend": platform_backend / "APP/backend/main.py",
        "knowledge_release": layout.knowledge_release_root,
        "knowledge_component": layout.knowledge_component_root,
        "question_bank": knowledge_data / "01_question_bank/formatted_questions.json",
        "knowledge_points": knowledge_data / "04_knowledge_points/final_knowledge_points.json",
        "official_exam_data": knowledge_data / "08_exam_learning_path_2025",
        "knowledge_video_results": layout.knowledge_video_root / "full_batch_results",
        "question_vector_store": layout.question_vector_store_root,
        "knowledge_atlas_chapters": layout.knowledge_atlas_chapter_root,
        "textbook_pdfs": layout.textbook_pdf_root,
    }
    required = {"platform_backend"}
    frontend_root = Path(values.get("FRONTEND_DIST_ROOT") or REPOSITORY_ROOT / "frontend/llm/dist").expanduser()
    if not frontend_root.is_absolute():
        frontend_root = BACKEND_ROOT / frontend_root
    checks["frontend_index"] = frontend_root / "index.html"
    if args.require_live_assets:
        required.update(checks)
    statuses = {
        name: {"path": str(path), "exists": path.exists(), "required": name in required}
        for name, path in checks.items()
    }
    payload = {
        "ok": all(item["exists"] for item in statuses.values() if item["required"]),
        "asset_root": str(layout.asset_root),
        "runtime_root": str(layout.runtime_root),
        "knowledge_runtime_root": str(layout.knowledge_runtime_root),
        "release_id": layout.release_id,
        "manifest": str(layout.manifest_path) if layout.manifest_path else None,
        "checks": statuses,
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
