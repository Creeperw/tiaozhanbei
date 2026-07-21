from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from APP.backend.scripts.reconcile_knowledge_atlas_questions import (
    ReconciliationSafetyError,
    backup_sqlite_database,
    reconcile_with_backup,
)


class ReconcileKnowledgeAtlasQuestionsScriptTests(unittest.TestCase):
    def test_sqlite_backup_is_consistent_and_reported_before_apply(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            database = root / "questions.db"
            with closing(sqlite3.connect(database)) as connection:
                connection.execute("create table sample (value text)")
                connection.execute("insert into sample values ('before')")
                connection.commit()

            backup = backup_sqlite_database(database, root / "backups", stamp="20260719T010203Z")

            self.assertTrue(Path(backup["path"]).is_file())
            self.assertEqual(len(backup["sha256"]), 64)
            with closing(sqlite3.connect(backup["path"])) as connection:
                self.assertEqual(connection.execute("select value from sample").fetchone()[0], "before")

    def test_apply_requires_zero_delete_dry_run_and_writes_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            database = root / "questions.db"
            sqlite3.connect(database).close()

            class Store:
                def __init__(self):
                    self.calls = []

                def reconcile_questions(self, db, *, apply):
                    del db
                    self.calls.append(apply)
                    return {
                        "atlas_total": 93111,
                        "atlas_linked": 71102,
                        "atlas_pending_link": 22009,
                        "db_total": 93275,
                        "matched": 93111,
                        "db_only": 164,
                        "changed": 93111 if apply else 0,
                        "applied": apply,
                        "deleted": 0,
                    }

            store = Store()
            report = reconcile_with_backup(
                db=object(),
                atlas_store=store,
                apply=True,
                database_url=f"sqlite:///{database}",
                backup_dir=root / "backups",
                report_dir=root / "reports",
                stamp="20260719T010203Z",
            )

            self.assertEqual(store.calls, [False, True])
            self.assertEqual(report["applied"]["deleted"], 0)
            self.assertTrue(Path(report["backup"]["path"]).is_file())
            report_path = Path(report["report_path"])
            self.assertEqual(json.loads(report_path.read_text(encoding="utf-8"))["dry_run"]["db_only"], 164)

    def test_apply_refuses_any_reconciliation_that_would_delete(self):
        class UnsafeStore:
            def reconcile_questions(self, db, *, apply):
                del db, apply
                return {"deleted": 1, "atlas_total": 1, "matched": 1}

        with self.assertRaisesRegex(ReconciliationSafetyError, "delete"):
            reconcile_with_backup(
                db=object(),
                atlas_store=UnsafeStore(),
                apply=True,
                database_url="sqlite:///missing.db",
                backup_dir=Path("unused"),
                report_dir=Path("unused"),
            )


if __name__ == "__main__":
    unittest.main()
