"""Correct only the two previously verified failed upload jobs; dry-run by default."""
import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import subprocess
import sys

from sqlalchemy import URL, create_engine, text


JOBS = ('UQJ_191a16f8f3e94c02', 'UQJ_a45c1140e3fb4a08')
MESSAGE = '历史题目抽取失败（已核实），原任务异常退出后未更新状态；请重新上传。'


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--backup', type=Path)
    args = parser.parse_args()
    if args.apply and not args.backup:
        parser.error('--apply requires --backup')
    pid = subprocess.check_output(['systemctl', 'show', 'tiaozhanbei.service', '--property=MainPID', '--value'], text=True).strip()
    os.environ.update(dict(entry.split('=', 1) for entry in Path(f'/proc/{pid}/environ').read_text().split('\0') if '=' in entry))
    sys.path.insert(0, '/srv/tiaozhanbei-releases/20260905-live/backend')
    from competition_app.config import Settings
    settings = Settings.from_env()
    assert settings.mode == 'live'
    engine = create_engine(URL.create('mysql+pymysql', username=settings.mysql_user,
        password=settings.mysql_password, host=settings.mysql_host, port=settings.mysql_port,
        database=settings.backend_handoff_mysql_database), hide_parameters=True)
    with engine.begin() as conn:
        if not args.apply:
            conn.execute(text('SET TRANSACTION READ ONLY'))
        uid = conn.execute(text('SELECT id FROM users WHERE username=:name'), {'name': 'judge_nupt_imis'}).scalar_one()
        assert uid == 3
        rows = []
        for job in JOBS:
            row = dict(conn.execute(text('SELECT job_id, owner_user_id, original_filename, status, item_count, error_message, created_at, updated_at FROM user_question_import_jobs WHERE job_id=:job' + (' FOR UPDATE' if args.apply else '')), {'job': job}).mappings().one())
            assert row['owner_user_id'] == uid
            assert row['original_filename'] == 'mineru-verification-20260909.pdf'
            assert row['created_at'] < datetime(2026, 9, 9, 5)
            assert row['item_count'] == 0
            assert conn.execute(text('SELECT COUNT(*) FROM user_question_items WHERE job_id=:job'), {'job': job}).scalar_one() == 0
            assert row['status'] == 'processing' or (row['status'] == 'failed' and row['error_message'] == MESSAGE)
            rows.append(row)
        if args.apply and any(row['status'] == 'processing' for row in rows):
            fd = os.open(args.backup, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, 'w') as output:
                json.dump(rows, output, ensure_ascii=False, default=str, indent=2)
                output.flush()
                os.fsync(output.fileno())
            for row in rows:
                if row['status'] != 'processing':
                    continue
                changed = conn.execute(text("UPDATE user_question_import_jobs SET status='failed', error_message=:message, updated_at=:now WHERE job_id=:job AND owner_user_id=:uid AND status='processing' AND item_count=0"),
                    {'message': MESSAGE, 'now': datetime.utcnow(), 'job': row['job_id'], 'uid': uid})
                assert changed.rowcount == 1
        print(json.dumps({'mode': settings.mode, 'apply': args.apply, 'jobs': [
            {'job_id': row['job_id'], 'before': row['status'], 'after': 'failed' if args.apply else row['status']} for row in rows]}, ensure_ascii=False))


if __name__ == '__main__':
    main()