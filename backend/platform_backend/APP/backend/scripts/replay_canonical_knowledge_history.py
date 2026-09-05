from __future__ import annotations

import argparse
import json
from pathlib import Path

from APP.backend.database import SessionLocal
from APP.backend.knowledge_point_history_replay import (
    apply_canonical_replay,
    build_canonical_replay_report,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Dry-run or apply a reviewed knowledge-point canonical history replay."
    )
    parser.add_argument("--canonical-kp-id", required=True)
    parser.add_argument("--source-kp-id", action="append", dest="source_kp_ids", default=[])
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--expected-plan-hash", default="")
    parser.add_argument("--requested-by", default="operator")
    return parser


def main() -> int:
    args = _parser().parse_args()
    db = SessionLocal()
    try:
        if args.apply:
            if not args.report.is_file():
                raise ValueError("the reviewed dry-run report does not exist")
            report = json.loads(args.report.read_text(encoding="utf-8"))
            if report.get("canonical_kp_id") != args.canonical_kp_id:
                raise ValueError("the reviewed report canonical id does not match")
            requested_sources = tuple(dict.fromkeys([
                args.canonical_kp_id,
                *args.source_kp_ids,
            ]))
            if tuple(report.get("source_kp_ids", ())) != requested_sources:
                raise ValueError("the reviewed report source ids do not match")
        else:
            report = build_canonical_replay_report(
                db,
                canonical_kp_id=args.canonical_kp_id,
                source_kp_ids=args.source_kp_ids,
            )
        args.report.parent.mkdir(parents=True, exist_ok=True)
        if not args.apply:
            args.report.write_text(
                json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            db.rollback()
            print(json.dumps({
                "mode": "dry_run",
                "status": report["status"],
                "plan_hash": report["plan_hash"],
                "report": str(args.report),
                "event_count": report["event_count"],
                "learner_count": report["learner_count"],
                "conflicts": report["conflicts"],
            }, ensure_ascii=False))
            return 0 if report["status"] == "ready" else 2
        if not args.expected_plan_hash:
            raise ValueError("--expected-plan-hash is required with --apply")
        migration = apply_canonical_replay(
            db,
            report=report,
            expected_plan_hash=args.expected_plan_hash,
            requested_by=args.requested_by,
        )
        db.commit()
        print(json.dumps({
            "mode": "apply",
            "status": migration.status,
            "migration_id": migration.migration_id,
            "plan_hash": migration.plan_hash,
            "report": str(args.report),
        }, ensure_ascii=False))
        return 0
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())