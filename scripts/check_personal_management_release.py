"""Read-only production preflight; no credentials or personal content in output."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

from sqlalchemy import URL, create_engine, text

pid = subprocess.check_output(['systemctl', 'show', 'tiaozhanbei.service', '--property=MainPID', '--value'], text=True).strip()
os.environ.update(dict(item.split('=', 1) for item in Path(f'/proc/{pid}/environ').read_text().split('\0') if '=' in item))
sys.path.insert(0, '/srv/tiaozhanbei-releases/20260905-live/backend')
from competition_app.config import Settings

settings = Settings.from_env()
assert settings.mode == 'live'
engine = create_engine(URL.create('mysql+pymysql', username=settings.mysql_user, password=settings.mysql_password,
    host=settings.mysql_host, port=settings.mysql_port, database=settings.mysql_database), hide_parameters=True)
with engine.connect() as connection:
    connection.execute(text('SET TRANSACTION READ ONLY'))
    claims = connection.execute(text('SELECT COUNT(*) FROM workflow_active_run_claims')).scalar_one()
print(json.dumps({'mode': settings.mode, 'active_workflow_claims': claims}))
assert claims == 0, 'Active workflow claims: do not restart'
root = Path('/srv/tiaozhanbei/runtime/knowledge')
owner = 'USER_39029b126f9043fdbfe124a8d61ff642'
delivery = root / 'knowledge_customers' / owner / 'TCM_backend_delivery'
def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None
print(json.dumps({'personal_data_sha256': {name: digest(delivery / name) for name in (
    '09_ingestion/ingestion_registry.jsonl', '03_pipeline_chunks/source_chunks.jsonl',
    '04_knowledge_points/final_knowledge_points.json')},
    'public_indexes': {str(path.relative_to('/srv/tiaozhanbei/data')): digest(path)
        for path in Path('/srv/tiaozhanbei/data/tiaozhanbei_data_2026-07-29/indexes').glob('*/manifest.json')}}, ensure_ascii=False))