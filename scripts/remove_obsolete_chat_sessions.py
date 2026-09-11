"""Inspect or explicitly delete one account's obsolete chat sessions only."""
import argparse
import json
import os
from pathlib import Path
import sys

from sqlalchemy import URL, bindparam, create_engine, text


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--pid', type=int, required=True)
    parser.add_argument('--username', required=True)
    parser.add_argument('--delete-ids', nargs='+')
    parser.add_argument('--expected-messages', type=int)
    parser.add_argument('--expected-events', type=int)
    args = parser.parse_args()
    for entry in Path(f'/proc/{args.pid}/environ').read_bytes().split(b'\0'):
        if b'=' in entry:
            key, value = entry.split(b'=', 1)
            os.environ[key.decode()] = value.decode()
    sys.path.insert(0, '/srv/tiaozhanbei-releases/20260905-live/backend')
    from competition_app.config import Settings
    settings = Settings.from_env()
    assert settings.mode == 'live' and settings.mysql_password
    engine = create_engine(URL.create(
        'mysql+pymysql', username=settings.mysql_user, password=settings.mysql_password,
        host=settings.mysql_host, port=settings.mysql_port, database=settings.mysql_database,
    ))
    domain = settings.backend_handoff_mysql_database
    assert domain.replace('_', '').isalnum()
    prefix = f'`{domain}`.'
    def query(sql):
        return text(sql).bindparams(bindparam('ids', expanding=True))
    with engine.begin() as db:
        owner = db.execute(text('SELECT user_id, username, display_name FROM app_users WHERE normalized_username=:name OR display_name=:name'),
                           {'name': args.username.lower()}).mappings().one()
        user = db.execute(text(f'SELECT u.id, u.username FROM {prefix}users u JOIN {prefix}external_identity_links l ON l.user_id=u.id WHERE l.external_user_id=:id'),
                          {'id': owner['user_id']}).mappings().one()
        sessions = db.execute(text(f'SELECT id, title FROM {prefix}sessions WHERE user_id=:uid ORDER BY id FOR UPDATE'),
                              {'uid': user['id']}).mappings().all()
        ids = [row['id'] for row in sessions]
        counts = {}
        refs = db.execute(text("SELECT TABLE_NAME, COLUMN_NAME, REFERENCED_TABLE_NAME FROM information_schema.KEY_COLUMN_USAGE WHERE REFERENCED_TABLE_SCHEMA=:schema AND REFERENCED_TABLE_NAME IN ('sessions','messages')"), {'schema': domain}).mappings().all()
        for ref in refs:
            table, column, target = ref['TABLE_NAME'], ref['COLUMN_NAME'], ref['REFERENCED_TABLE_NAME']
            assert table.replace('_', '').isalnum() and column.replace('_', '').isalnum()
            expression = ':ids' if target == 'sessions' else f'(SELECT id FROM {prefix}messages WHERE session_id IN :ids)'
            counts[f'{table}.{column}'] = db.execute(query(f'SELECT COUNT(*) FROM {prefix}`{table}` WHERE `{column}` IN {expression}'), {'ids': ids}).scalar_one()
        formal_before = db.execute(text('SELECT COUNT(*) FROM conversation_sessions WHERE learner_id=:uid'), {'uid': owner['user_id']}).scalar_one()
        print(json.dumps({'account': owner['username'], 'domain_user_id': user['id'], 'sessions': [dict(row) for row in sessions], 'references': counts, 'formal_sessions': formal_before}, ensure_ascii=False))
        if not args.delete_ids:
            return
        if set(args.delete_ids) != set(ids) or not ids:
            raise RuntimeError('Exact inspected session IDs must match; nothing deleted')
        if any(count for key, count in counts.items() if key not in {'messages.session_id', 'agent_events.session_id'}):
            raise RuntimeError('Related records exist; nothing deleted')
        assert counts.get('messages.session_id') == args.expected_messages
        assert counts.get('agent_events.session_id') == args.expected_events
        assert db.execute(query(f'SELECT COUNT(*) FROM {prefix}agent_events WHERE session_id IN :ids AND (user_id<>:uid OR user_id IS NULL)'), {'ids': ids, 'uid': user['id']}).scalar_one() == 0
        other_before = db.execute(text(f'SELECT COUNT(*) FROM {prefix}sessions WHERE user_id<>:uid'), {'uid': user['id']}).scalar_one()
        deleted_events = db.execute(query(f'DELETE FROM {prefix}agent_events WHERE session_id IN :ids AND user_id=:uid'), {'ids': ids, 'uid': user['id']}).rowcount
        deleted_messages = db.execute(query(f'DELETE FROM {prefix}messages WHERE session_id IN :ids'), {'ids': ids}).rowcount
        deleted_sessions = db.execute(query(f'DELETE FROM {prefix}sessions WHERE id IN :ids AND user_id=:uid'), {'ids': ids, 'uid': user['id']}).rowcount
        assert deleted_sessions == len(ids)
        assert deleted_messages == args.expected_messages and deleted_events == args.expected_events
        assert db.execute(text(f'SELECT COUNT(*) FROM {prefix}sessions WHERE user_id<>:uid'), {'uid': user['id']}).scalar_one() == other_before
        assert db.execute(text('SELECT COUNT(*) FROM conversation_sessions WHERE learner_id=:uid'), {'uid': owner['user_id']}).scalar_one() == formal_before
    print(json.dumps({'deleted_sessions': deleted_sessions, 'deleted_messages': deleted_messages, 'deleted_events': deleted_events, 'formal_sessions_unchanged': True, 'other_accounts_unchanged': True}))


if __name__ == '__main__':
    main()