from __future__ import annotations

import argparse
import json
from pathlib import Path

from APP.backend.config import (
    KNOWLEDGE_ATLAS_ASSET_VERSION,
    KNOWLEDGE_ATLAS_DATA_ROOT,
)
from APP.backend.question_index_v2_service import (
    ACTIVE_POINTER_FILENAME,
    DEFAULT_QUESTION_COLLECTION,
    V2_QUESTION_COLLECTION,
    QuestionIndexContractError,
    active_question_index_name,
    build_question_index_v2,
    switch_active_question_index,
)


BACKEND_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INDEX_ROOT = BACKEND_ROOT / "vdb_store" / "indexes"
DEFAULT_ATLAS_QUESTIONS = KNOWLEDGE_ATLAS_DATA_ROOT / "01_question_bank" / "formatted_questions.json"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="从兼容的旧题库向量中筛选 Atlas 93,111 道题并原子切换题库-v2"
    )
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--build", action="store_true")
    action.add_argument("--rollback", action="store_true")
    action.add_argument("--status", action="store_true")
    parser.add_argument("--index-root", type=Path, default=DEFAULT_INDEX_ROOT)
    parser.add_argument("--atlas-questions", type=Path, default=DEFAULT_ATLAS_QUESTIONS)
    parser.add_argument("--no-activate", action="store_true")
    parser.add_argument("--batch-size", type=int, default=512)
    args = parser.parse_args()

    pointer = args.index_root / ACTIVE_POINTER_FILENAME
    try:
        if args.status:
            report = {
                "active": active_question_index_name(args.index_root, pointer_path=pointer),
                "legacy_available": (args.index_root / DEFAULT_QUESTION_COLLECTION / "index.faiss").is_file(),
                "v2_available": (args.index_root / V2_QUESTION_COLLECTION / "index.faiss").is_file(),
            }
        elif args.rollback:
            report = switch_active_question_index(
                index_root=args.index_root,
                collection=DEFAULT_QUESTION_COLLECTION,
                pointer_path=pointer,
                expected_model="Qwen/Qwen3-Embedding-4B",
                expected_dimensions=2560,
                require_normalized=True,
            )
        else:
            report = build_question_index_v2(
                source_dir=args.index_root / DEFAULT_QUESTION_COLLECTION,
                atlas_questions_path=args.atlas_questions,
                target_dir=args.index_root / V2_QUESTION_COLLECTION,
                active_pointer_path=pointer,
                expected_model="Qwen/Qwen3-Embedding-4B",
                expected_dimensions=2560,
                asset_version=KNOWLEDGE_ATLAS_ASSET_VERSION,
                activate=not args.no_activate,
                batch_size=args.batch_size,
            )
    except (QuestionIndexContractError, OSError, ValueError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, sort_keys=True))
        return 2
    print(json.dumps({"ok": True, **report}, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
