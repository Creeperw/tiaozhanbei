"""Read-only inventory of practice timestamps for the confirmed account."""
import argparse
import json
import os
from pathlib import Path
import sys

from sqlalchemy import URL, create_engine, text


def connect(pid):
    for entry in Path(f'/proc/{pid}/environ').read_bytes().split(b'\0'):
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
    return engine, settings


def emit(value):
    print(json.dumps(value, ensure_ascii=False, default=str))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--pid', type=int, required=True)
    args = parser.parse_args()
    engine, settings = connect(args.pid)
    with engine.begin() as conn:
        conn.execute(text('SET TRANSACTION READ ONLY'))
        uid = conn.execute(text('SELECT id FROM users WHERE username=:name'),
                           {'name': 'judge_nupt_imis'}).scalar_one()
        assert uid == 3
        columns = conn.execute(text('SELECT TABLE_NAME, COLUMN_NAME, DATA_TYPE FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=DATABASE() ORDER BY TABLE_NAME, ORDINAL_POSITION')).all()
        tables = {}
        for table, column, kind in columns:
            tables.setdefault(table, {})[column] = kind
        for table, cols in tables.items():
            owner = next((c for c in ('user_id', 'learner_id') if c in cols), None)
            if not owner:
                continue
            count = conn.execute(text(f'SELECT COUNT(*) FROM `{table}` WHERE `{owner}`=:uid'), {'uid': uid}).scalar_one()
            if count:
                emit({'table': table, 'count': count, 'columns': cols})
        for table in ('learning_activity_records', 'learning_attempts', 'question_attempt', 'question_attempts', 'training_task_records', 'paper_instances', 'paper_submissions', 'case_session_records', 'daily_task_items', 'learning_task', 'external_identity_links'):
            if table not in tables:
                continue
            cols = tables[table]
            owner = next((c for c in ('user_id', 'learner_id') if c in cols), None)
            if not owner:
                continue
            safe = [c for c, k in cols.items() if c.endswith('_id') or c in ('id', 'status', 'activity_type', 'resource_type', 'task_type', 'attempt_type', 'item_kind', 'completion_status', 'attempt_status', 'provider') or k in ('datetime', 'timestamp', 'date')]
            if table == 'learning_activity_records':
                safe.append('payload_json')
            rows = conn.execute(text('SELECT '+','.join(f'`{c}`' for c in safe)+f' FROM `{table}` WHERE `{owner}`=:uid ORDER BY id'), {'uid': uid}).mappings().all()
            for row in rows:
                result = dict(row)
                if table == 'learning_activity_records' and result['activity_type'] not in ('question_attempt', 'training_workspace_task', 'paper_submission', 'case_training'):
                    continue
                if 'payload_json' in result:
                    payload = json.loads(result.pop('payload_json') or '{}')
                    result['links'] = {k: v for k, v in payload.items() if k.endswith('_id') or k in ('practice_origin', 'source', 'practice_mode')}
                emit({'table': table, 'row': result})
        emit({'main_schema_tables': [r[0] for r in conn.execute(text('SELECT TABLE_NAME FROM information_schema.TABLES WHERE TABLE_SCHEMA=:db'), {'db': settings.mysql_database})]})
        for table in ('app_users', 'review_attempts', 'review_state_events', 'artifacts', 'execution_runs', 'conversation_sessions'):
            emit({'main_table': table, 'columns': [list(r) for r in conn.execute(text('SELECT COLUMN_NAME, DATA_TYPE FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=:db AND TABLE_NAME=:table ORDER BY ORDINAL_POSITION'), {'db': settings.mysql_database, 'table': table})]})
        external = conn.execute(text("SELECT external_user_id FROM external_identity_links WHERE user_id=:uid AND provider='competition_app'"), {'uid': uid}).scalar_one()
        for table in ('review_attempts', 'review_state_events'):
            emit({'main_count': table, 'count': conn.execute(text(f'SELECT COUNT(*) FROM `{settings.mysql_database}`.`{table}` WHERE learner_id=:uid'), {'uid': external}).scalar_one()})
        for table in ('mistake_records', 'mastery_history_records', 'knowledge_mastery_states', 'learner_kp_review_states', 'learner_knowledge_mastery', 'daily_task_question_snapshots'):
            cols = tables[table]
            owner = 'user_id' if 'user_id' in cols else 'learner_id'
            safe = [c for c, k in cols.items() if c.endswith('_id') or c in ('id', 'status', 'attempt_status') or k in ('datetime', 'timestamp', 'date')]
            for row in conn.execute(text('SELECT '+','.join(f'`{c}`' for c in safe)+f' FROM `{table}` WHERE `{owner}`=:uid ORDER BY id'), {'uid': uid}).mappings():
                emit({'detail': table, 'row': dict(row)})