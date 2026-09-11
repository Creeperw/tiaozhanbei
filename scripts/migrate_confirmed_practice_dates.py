"""One-off authorized date migration. Dry-run unless --apply is supplied.

Only event timestamps change. Shared content, audit payloads, future review
schedules, account/login records and ordinary browsing remain untouched.
"""
import argparse
from collections import Counter, defaultdict
from datetime import datetime, timedelta
import hashlib
import json
import os
from pathlib import Path

from sqlalchemy import MetaData, Table, select, text

from inspect_practice_dates import connect, emit

UID = 3
TABLES = (
    'learning_activity_records', 'question_attempts', 'question_attempt',
    'learning_attempts', 'learning_attempt_items', 'learning_writeback_receipts',
    'mastery_history_records', 'mistake_records', 'learner_exam_attempt_memberships',
    'daily_task_items', 'daily_task_question_snapshots', 'learning_task',
    'knowledge_mastery_states', 'learner_exam_progress_states',
    'learner_kp_review_states', 'learner_knowledge_mastery',
    'learning_focus_sessions', 'grading_result_records', 'audit_result_records',
    'paper_instances', 'paper_submissions', 'case_session_records',
    'training_task_records',
)


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def make_targets(activities):
    """Keep order and within-session gaps; compact old inter-day gaps."""
    targets = {}
    n = len(activities)
    for day in range(3):
        subset = [r for i, r in enumerate(activities) if i * 3 // n == day]
        target_date = datetime(2026, 9, 1) + timedelta(days=day)
        previous = None
        old_date = None
        delta = None
        proposed = []
        for row in subset:
            old = row['created_at']
            if old.date() != old_date:
                candidate = target_date.replace(hour=old.hour, minute=old.minute, second=old.second)
                if previous is not None:
                    candidate = max(candidate, previous + timedelta(minutes=5))
                delta = candidate - old
                old_date = old.date()
            new = old + delta
            assert previous is None or new > previous
            proposed.append((row['id'], new))
            previous = new
        # Both database UTC and Asia/Shanghai display must stay within Sep 1-3.
        latest = target_date.replace(hour=15, minute=45)
        offset = max(timedelta(), proposed[-1][1] - latest)
        for key, new in proposed:
            targets[key] = new - offset
            assert target_date <= targets[key] < target_date + timedelta(hours=16)
    ordered = [targets[r['id']] for r in activities]
    assert ordered == sorted(ordered) and len(set(ordered)) == n
    return targets


def migrate(args):
    engine, settings = connect(args.pid)
    metadata = MetaData()
    tables = {name: Table(name, metadata, autoload_with=engine) for name in TABLES}
    with engine.begin() as conn:
        conn.execute(text("SET time_zone = '+00:00'"))
        if not args.apply:
            conn.execute(text('SET TRANSACTION READ ONLY'))
        uid = conn.execute(text('SELECT id FROM users WHERE username=:name'), {'name': 'judge_nupt_imis'}).scalar_one()
        assert uid == UID
        external = conn.execute(text("SELECT external_user_id FROM external_identity_links WHERE user_id=:uid AND provider='competition_app'"), {'uid': UID}).scalar_one()
        for name in ('review_attempts', 'review_state_events'):
            assert conn.execute(text(f'SELECT COUNT(*) FROM `{settings.mysql_database}`.`{name}` WHERE learner_id=:uid'), {'uid': external}).scalar_one() == 0
        data = {}
        for name, table in tables.items():
            stmt = select(table).order_by(table.c.id)
            if args.apply:
                stmt = stmt.with_for_update()
            data[name] = [dict(r) for r in conn.execute(stmt).mappings()]

        def owned(name):
            return [r for r in data[name] if r.get('user_id', r.get('learner_id')) == UID]

        for name in ('paper_instances', 'paper_submissions', 'case_session_records', 'training_task_records'):
            assert not owned(name), f'Unexpected additional practice type: {name}'
        activities = sorted([r for r in owned('learning_activity_records') if r['activity_type'] == 'question_attempt'], key=lambda r: (r['created_at'], r['id']))
        assert len(activities) == 65 and len(owned('learning_attempts')) == 11 and len(owned('question_attempts')) == 54
        assert all(r['resource_type'] == 'question' for r in activities)
        assert any(r['created_at'] >= datetime(2026, 9, 6) for r in activities), 'Already migrated or unexpected dataset'
        targets = make_targets(activities)
        changes = {}

        def change(name, row, values):
            assert row.get('user_id', row.get('learner_id', UID)) == UID
            values = {k: v for k, v in values.items() if row[k] != v}
            assert all(isinstance(row[k], datetime) and isinstance(v, datetime) for k, v in values.items())
            key = (name, row['id'])
            if key not in changes:
                changes[key] = {'table': name, 'id': row['id'], 'before': row, 'values': {}}
            for k, v in values.items():
                assert k not in changes[key]['values'] or changes[key]['values'][k] == v
                changes[key]['values'][k] = v

        def shift(name, row, delta, fields):
            change(name, row, {f: row[f] + delta for f in fields if row.get(f) is not None})

        request_delta = {}
        event_delta = {}
        for row in activities:
            delta = targets[row['id']] - row['created_at']
            shift('learning_activity_records', row, delta, ('created_at',))
            event_delta[(row['resource_id'], row['created_at'])] = delta
            payload = json.loads(row['payload_json'] or '{}')
            if payload.get('request_id'):
                request_delta[payload['request_id']] = delta
            else:
                assert payload.get('source') == 'synthetic_usage_v1'
        for row in owned('question_attempts'):
            shift('question_attempts', row, event_delta[(row['question_id'], row['created_at'])], ('created_at',))
        for row in owned('question_attempt'):
            shift('question_attempt', row, request_delta[row['request_id']], ('answered_at',))
        attempt_delta = {}
        daily = defaultdict(list)
        for row in owned('learning_attempts'):
            delta = request_delta[row['request_id']]
            attempt_delta[row['attempt_id']] = delta
            shift('learning_attempts', row, delta, ('created_at', 'submitted_at'))
            if row['daily_task_item_id']:
                daily[row['daily_task_item_id']].append((row, delta))
        item_delta = {}
        item_rows = {}
        for row in data['learning_attempt_items']:
            if row['attempt_id'] in attempt_delta:
                delta = attempt_delta[row['attempt_id']]
                item_delta[row['attempt_item_id']] = delta
                item_rows[row['attempt_item_id']] = row
                shift('learning_attempt_items', row, delta, ('created_at',))
        for name, link, fields in (
            ('learning_writeback_receipts', 'attempt_item_id', ('created_at',)),
            ('mastery_history_records', 'trigger_attempt_item_id', ('calculated_at',)),
            ('learner_exam_attempt_memberships', 'attempt_id', ('created_at',)),
        ):
            deltas = attempt_delta if link == 'attempt_id' else item_delta
            for row in data[name]:
                if row[link] in deltas:
                    shift(name, row, deltas[row[link]], fields)
        for row in owned('mistake_records'):
            delta = item_delta[row['attempt_item_id']] if row['attempt_item_id'] else event_delta[(row['question_id'], row['created_at'])]
            shift('mistake_records', row, delta, ('created_at', 'updated_at'))
        for row in owned('daily_task_items'):
            if row['task_item_id'] in daily:
                deltas = {delta for _, delta in daily[row['task_item_id']]}
                assert len(deltas) == 1
                shift('daily_task_items', row, next(iter(deltas)), ('created_at', 'updated_at', 'completed_at'))
        for row in owned('daily_task_question_snapshots'):
            if row['task_item_id'] in daily and row['attempt_status'] == 'reviewed':
                shift('daily_task_question_snapshots', row, daily[row['task_item_id']][0][1], ('created_at', 'updated_at'))
        # Update only existing assessment/review timestamps with exact source evidence.
        # Keep calculation timestamps, future schedules and audit trail contents real.
        kp_times = defaultdict(dict)
        for row in owned('mastery_history_records'):
            kp_times[row['kp_id']][row['calculated_at']] = item_delta[row['trigger_attempt_item_id']]
        for row in owned('question_attempts'):
            delta = event_delta[(row['question_id'], row['created_at'])]
            for kp in json.loads(row['kp_ids_json'] or '[]'):
                kp_times[kp][row['created_at']] = delta
        for name in ('knowledge_mastery_states', 'learner_exam_progress_states', 'learner_kp_review_states', 'learner_knowledge_mastery'):
            for row in owned(name):
                values = {}
                for field in ('last_assessed_at', 'last_review_at'):
                    old = row.get(field)
                    if old in kp_times[row['kp_id']]:
                        values[field] = old + kp_times[row['kp_id']][old]
                if values:
                    change(name, row, values)
        # Focus sessions are included only where an actual current practice event
        # occurred inside the interval; plain page views/daily resource browsing stay.
        for row in owned('learning_focus_sessions'):
            if row['resource_type'] != 'training_workspace' or not row['ended_at']:
                continue
            deltas = {targets[a['id']] - a['created_at'] for a in activities if row['started_at'] <= a['created_at'] <= row['ended_at']}
            if deltas:
                assert len(deltas) == 1
                shift('learning_focus_sessions', row, next(iter(deltas)), ('started_at', 'ended_at', 'last_interaction_at', 'updated_at'))

        changes = {k: v for k, v in changes.items() if v['values']}
        plan = {'account': 'judge_nupt_imis', 'user_id': UID, 'events': len(activities),
                'distribution': dict(Counter(str(v.date()) for v in targets.values())),
                'tables': dict(Counter(c['table'] for c in changes.values())),
                'changes': list(changes.values()),
                'activity_mapping': [{'id': a['id'], 'before': a['created_at'], 'after': targets[a['id']]} for a in activities]}
        digest = hashlib.sha256(encoded(plan).encode()).hexdigest()
        emit({k: v for k, v in plan.items() if k not in ('changes', 'activity_mapping')})
        emit({'plan_sha256': digest, 'apply': args.apply})
        if not args.apply:
            return
        assert args.expected_sha256 == digest, 'Dry-run plan changed; refusing to write'
        directory = Path(args.backup_dir)
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(directory, 0o700)
        backup = directory / 'practice-dates-before.json'
        with backup.open('x', encoding='utf-8') as stream:
            os.chmod(backup, 0o600)
            stream.write(encoded(plan))
            stream.flush()
            os.fsync(stream.fileno())
        assert hashlib.sha256(backup.read_bytes()).hexdigest() == digest
        for (name, key), item in changes.items():
            table = tables[name]
            stmt = table.update().where(table.c.id == key)
            for owner in ('user_id', 'learner_id'):
                if owner in table.c:
                    stmt = stmt.where(table.c[owner] == UID)
            for field in item['values']:
                stmt = stmt.where(table.c[field] == item['before'][field])
            assert conn.execute(stmt.values(**item['values'])).rowcount == 1
        # Compare every field of every row in every covered table, including all
        # other users, unchanged activity types, scores and answer/audit payloads.
        for name, table in tables.items():
            after = [dict(r) for r in conn.execute(select(table).order_by(table.c.id)).mappings()]
            assert len(after) == len(data[name])
            for old, new in zip(data[name], after):
                expected = dict(old)
                expected.update(changes.get((name, old['id']), {}).get('values', {}))
                assert encoded(new) == encoded(expected), f'Unexpected field change: {name}/{old["id"]}'
        emit({'transaction_verified': True, 'changed_rows': len(changes), 'backup': str(backup)})
    emit({'committed': True})


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--pid', type=int, required=True)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--expected-sha256', default='')
    parser.add_argument('--backup-dir', default='/srv/tiaozhanbei-backups/practice-dates-ssw-20260906')
    migrate(parser.parse_args())