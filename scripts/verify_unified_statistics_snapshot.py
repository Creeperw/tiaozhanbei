"""Read-only live snapshot, then validate statistics in an isolated stub database."""
import argparse
import json
import os
from pathlib import Path

from sqlalchemy import MetaData, Table, select, text


TABLES = (
    'users', 'question_version_records', 'learning_attempts', 'learning_attempt_items',
    'grading_result_records', 'audit_result_records', 'question_attempt', 'question_attempts',
    'learning_activity_records', 'paper_submissions', 'paper_items', 'mistake_records',
    'learning_focus_sessions', 'knowledge_mastery_states', 'learner_kp_review_states',
    'knowledge_card_records', 'review_tasks',
)


def export_snapshot(pid, output):
    from inspect_practice_dates import connect
    engine, settings = connect(pid)
    payload = {'mode': settings.mode, 'tables': {}}
    with engine.connect() as conn:
        conn.execute(text('SET TRANSACTION READ ONLY'))
        metadata = MetaData()
        # Source table names are resolved from the live schema; never initialize the application ORM.
        existing = set(conn.execute(text('SHOW TABLES')).scalars())
        for name in TABLES:
            if name not in existing:
                continue
            table = Table(name, metadata, autoload_with=conn)
            query = select(table)
            if name == 'learning_activity_records':
                query = query.where(table.c.activity_type.in_(['question_attempt', 'paper_submission', 'case_training']))
            rows = [dict(row) for row in conn.execute(query).mappings()]
            if name == 'users':
                rows = [{'id': r['id'], 'username': r['username']} for r in rows]
            payload['tables'][name] = rows
        conn.rollback()
    descriptor = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, 'w') as handle:
        json.dump(payload, handle, default=str)
    print(json.dumps({'mode': settings.mode, 'tables': {k: len(v) for k, v in payload['tables'].items()}}))


def validate_snapshot(source):
    from datetime import datetime
    from sqlalchemy import DateTime, create_engine
    from sqlalchemy.orm import Session
    from APP.backend import database
    from APP.backend.learning_statistics_service import build_learning_statistics, build_practice_history
    payload = json.loads(Path(source).read_text())
    assert payload['mode'] == 'live'
    engine = create_engine('sqlite://')
    database.Base.metadata.create_all(engine)
    with engine.begin() as conn:
        for name, rows in payload['tables'].items():
            table = database.Base.metadata.tables.get(name)
            if table is None:
                raise AssertionError(f'Unrecognized source table: {name}')
            for row in rows:
                for column in table.columns:
                    if isinstance(column.type, DateTime) and row.get(column.name):
                        row[column.name] = datetime.fromisoformat(row[column.name])
                conn.execute(table.insert().values(**row))
    with Session(engine) as db:
        for user in db.query(database.UserModel).order_by(database.UserModel.id):
            stats = build_learning_statistics(db, user.id)
            history = build_practice_history(db, user.id)
            questions = [r for r in history['recent_activities'] if r['attempt_type'] != 'case']
            cases = [r for r in history['recent_activities'] if r['attempt_type'] == 'case']
            window = stats['current_window']
            assert len(questions) == window['questions_completed']
            assert len(cases) == window['case_sessions_completed']
            print(json.dumps({'user': user.username, 'questions_30d': len(questions),
                'history_total': history['total'], 'cases_30d': len(cases),
                'lifetime_questions': stats['lifetime']['questions_completed'],
                'score_rate': window['score_rate'], 'audited': window['audited_question_items_completed'],
                'legacy': window['legacy_question_items_completed']}, ensure_ascii=False))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--pid', type=int)
    parser.add_argument('--export')
    parser.add_argument('--validate')
    args = parser.parse_args()
    if args.export:
        export_snapshot(args.pid, args.export)
    elif args.validate:
        validate_snapshot(args.validate)