"""Repair only three confirmed free special-training activity origins.

Default is read-only. No answer, grade, attempt, or learning state is changed.
Evidence: user-confirmed special-training session and matching successful
2026-09-06 07:31-07:34 UTC public objective/case requests without a kp filter
or daily-task binding. Earlier knowledge-filtered records are excluded.
"""
import argparse
import json
import os
from pathlib import Path
import sys

from sqlalchemy import URL, create_engine, text

parser = argparse.ArgumentParser()
parser.add_argument('--pid', type=int, required=True)
parser.add_argument('--apply', action='store_true')
args = parser.parse_args()
for entry in Path(f'/proc/{args.pid}/environ').read_bytes().split(b'\0'):
    if b'=' in entry:
        key, value = entry.split(b'=', 1)
        os.environ[key.decode()] = value.decode()
sys.path.insert(0, '/srv/tiaozhanbei-releases/20260905-live/backend')
from competition_app.config import Settings

settings = Settings.from_env()
assert settings.mode == 'live'
engine = create_engine(URL.create(
    'mysql+pymysql', username=settings.mysql_user, password=settings.mysql_password,
    host=settings.mysql_host, port=settings.mysql_port,
    database=settings.backend_handoff_mysql_database,
), hide_parameters=True)
expected = {
    594: ('1a6b81bb-3355-45a0-a1e2-ae982e80dbe3', 'generated_临床案例问答__7d826242ab46'),
    595: ('ec45f275-8f39-4ea1-97b8-f7fd7c26ccf6', 'possible_new__03ef1dce94c8'),
    596: ('28064c88-9722-4c16-90d7-59bc4bb3f367', 'generated_判断题__f93f4b6de023'),
}
with engine.begin() as conn:
    if not args.apply:
        conn.execute(text('SET TRANSACTION READ ONLY'))
    uid = conn.execute(text('SELECT id FROM users WHERE username=:name'), {'name': 'judge_nupt_imis'}).scalar_one()
    rows = conn.execute(text(
        'SELECT id, user_id, activity_type, resource_type, resource_id, completion_status, payload_json '
        'FROM learning_activity_records WHERE user_id=:uid AND id IN (594,595,596)'
        + (' FOR UPDATE' if args.apply else '')
    ), {'uid': uid}).mappings().all()
    assert {row['id'] for row in rows} == set(expected)
    changed = 0
    for row in rows:
        request_id, question_id = expected[row['id']]
        payload = json.loads(row['payload_json'])
        assert row['activity_type'] == 'question_attempt' and row['resource_type'] == 'question'
        assert row['completion_status'] == 'completed' and row['resource_id'] == question_id
        assert payload.get('request_id') == request_id
        assert payload.get('practice_origin') in (None, 'special_training')
        attempt = conn.execute(text(
            'SELECT daily_task_item_id FROM learning_attempts WHERE learner_id=:uid AND request_id=:request_id'
        ), {'uid': uid, 'request_id': request_id}).one()
        assert attempt.daily_task_item_id is None
        if args.apply and payload.get('practice_origin') is None:
            payload['practice_origin'] = 'special_training'
            payload['origin_repair_evidence'] = 'confirmed-special-session-20260906-0731-0734'
            updated = conn.execute(text(
                'UPDATE learning_activity_records SET payload_json=:payload WHERE id=:id AND user_id=:uid AND payload_json=:original'
            ), {'payload': json.dumps(payload, ensure_ascii=False), 'id': row['id'], 'uid': uid, 'original': row['payload_json']})
            assert updated.rowcount == 1
            changed += 1
        print(json.dumps({'activity_id': row['id'], 'origin': payload.get('practice_origin'), 'apply': args.apply}, ensure_ascii=False))
print(json.dumps({'changed': changed, 'verified': len(rows)}))