from __future__ import annotations

import argparse
import json
from pathlib import Path

from APP.backend.database import SessionLocal
from APP.backend.formal_content_import_service import import_formal_learning_content


def main() -> int:
    parser = argparse.ArgumentParser(description="导入正式知识点、题目及可选题目知识点关联")
    parser.add_argument("--knowledge-points", type=Path, required=True)
    parser.add_argument("--questions", type=Path, required=True)
    parser.add_argument("--question-kp-links", type=Path)
    parser.add_argument("--data-version", required=True)
    args = parser.parse_args()

    db = SessionLocal()
    try:
        summary = import_formal_learning_content(
            db,
            knowledge_points_path=args.knowledge_points,
            questions_path=args.questions,
            question_kp_links_path=args.question_kp_links,
            data_version=args.data_version,
        )
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
    print(json.dumps(summary.__dict__, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
