"""Read-only production grading metadata inspection; never starts an app."""
import argparse
import json
import os
from pathlib import Path
import sys
from sqlalchemy import URL, create_engine, text

parser = argparse.ArgumentParser()
parser.add_argument('--pid', type=int, required=True)
args = parser.parse_args()
for entry in Path(f'/proc/{args.pid}/environ').read_bytes().split(b'\0'):
    if b'=' in entry:
        key, value = entry.split(b'=', 1)
        os.environ[key.decode()] = value.decode()
sys.path.insert(0, '/srv/tiaozhanbei-releases/20260905-live/backend')
from competition_app.config import Settings
s = Settings.from_env()
assert s.mode == 'live' and s.mysql_password
schema = s.backend_handoff_mysql_database
assert schema.replace('_', '').isalnum()
engine = create_engine(URL.create('mysql+pymysql', username=s.mysql_user, password=s.mysql_password,
    host=s.mysql_host, port=s.mysql_port, database=schema), hide_parameters=True)
with engine.connect() as c:
    c.execute(text('SET TRANSACTION READ ONLY'))
    uid = c.execute(text('SELECT id FROM users WHERE username=:name'), {'name': 'judge_nupt_imis'}).scalar_one()
    rows = c.execute(text('''
        SELECT a.request_id, a.created_at, a.daily_task_item_id,
               i.question_version_id, COALESCE(v.question_type, b.question_type) question_type,
               g.payload_json, CHAR_LENGTH(v.analysis) version_analysis_chars,
               CHAR_LENGTH(b.analysis) bank_analysis_chars,
             CHAR_LENGTH(q.explaination) learning_analysis_chars
        FROM learning_attempts a
        JOIN learning_attempt_items i ON i.attempt_id=a.attempt_id
        LEFT JOIN grading_result_records g ON g.attempt_item_id=i.attempt_item_id
        LEFT JOIN question_version_records v ON v.question_version_id=i.question_version_id
        LEFT JOIN question_bank_items b ON b.question_id=COALESCE(v.question_id,i.question_version_id)
        LEFT JOIN question q ON q.question_id=COALESCE(v.question_id,i.question_version_id)
        WHERE a.learner_id=:uid ORDER BY a.created_at DESC LIMIT 12
    '''), {'uid': uid}).mappings().all()
    output = []
    for row in rows:
        item = dict(row)
        payload = json.loads(item.pop('payload_json') or '{}')
        item.update({key: payload.get(key) for key in ('grading_source', 'explanation_source')})
        item['explanation_chars'] = len(payload.get('question_explanation') or '')
        output.append(item)
    coverage = c.execute(text("SELECT COUNT(*) total, SUM(CHAR_LENGTH(TRIM(COALESCE(analysis,'')))>0) with_nonempty_analysis FROM question_bank_items WHERE status='active'")).mappings().one()
    print(json.dumps({'recent_attempts': output, 'projected_bank_coverage': dict(coverage)}, ensure_ascii=False, default=str))

from competition_app.legacy_asset_compat import knowledge_component_root
source_path = knowledge_component_root(s.knowledge_handoff_root) / 'data/backend_delivery/01_question_bank/formatted_questions.json'
wanted_ids = {item['question_version_id'].split(':atlas:', 1)[0] for item in output}
markers = ('客观题由系统依据标准答案自动判分', '错因暂不自动下结论', '请到错题变式中补充', '回答正确。', '回答错误。')
source_metadata = []
for question in json.loads(source_path.read_text(encoding='utf-8-sig')):
    question_id = str(question.get('question_id') or question.get('题目id') or '')
    if question_id not in wanted_ids:
        continue
    fields = ('explanation', 'explaination', '题目解析', 'analysis')
    analysis = str(next((question.get(key) for key in fields if question.get(key)), '')).strip()
    source_metadata.append({
        'question_id': question_id,
        'source_analysis_chars': len(analysis),
        'source_analysis_fields': {key: len(str(question.get(key) or '')) for key in fields if key in question},
        'rejected_by_current_cache_filter': any(marker in analysis for marker in markers),
    })
print(json.dumps({'formal_source_metadata': source_metadata}, ensure_ascii=False))