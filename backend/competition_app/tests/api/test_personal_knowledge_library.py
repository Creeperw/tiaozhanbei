import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from competition_app.api.app import create_app
from competition_app.application.container import ApplicationContainer
from competition_app.config import Settings
from competition_app.services.personal_knowledge_library import PersonalKnowledgeLibrary


def seed(root, owner):
    delivery = root / 'knowledge_customers' / owner / 'TCM_backend_delivery'
    data = {
        '09_ingestion/ingestion_registry.jsonl': {'source_id': 's1', 'source_title': '个人阴阳资料', 'delta_dir': '/private/secret'},
        '03_pipeline_chunks/source_chunks.jsonl': {'chunk_uid': 'c1', 'metadata': {'source_id': 's1'}, 'text': '<script>literal</script>阴阳原文'},
        '04_knowledge_points/final_knowledge_points.json': [{'kp': {'kp_id': '1', 'raw_content': ['c1']}}],
    }
    for name, value in data.items():
        path = delivery / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value))
    vector = root / 'user_vdb' / owner / 'indexes' / '知识点'
    vector.mkdir(parents=True)
    (vector / 'manifest.json').write_text(json.dumps({'owner_id': owner, 'collection': '知识点', 'count': 1}))
    (vector / 'index.faiss').write_bytes(b'test')
    (vector / 'metadata.jsonl').write_text('{}\n')
    return delivery


def test_projection_reads_committed_data_and_no_private_paths(tmp_path):
    seed(tmp_path, 'alice')
    library = PersonalKnowledgeLibrary(tmp_path, 'alice')
    result = library.overview()
    assert result['stats']['total_documents'] == 1
    assert result['stats']['total_chunks'] == 1
    assert result['stats']['total_vectors'] == 1
    assert '/private' not in json.dumps(result)
    assert library.document(result['files'][0]['id'])['chunks'][0]['text'].endswith('阴阳原文')
    assert PersonalKnowledgeLibrary(tmp_path, 'bob').overview()['files'] == []
    with pytest.raises(KeyError):
        PersonalKnowledgeLibrary(tmp_path, 'bob').document(result['files'][0]['id'])


def test_missing_index_does_not_claim_ready(tmp_path):
    seed(tmp_path, 'alice')
    (tmp_path / 'user_vdb/alice/indexes/知识点/index.faiss').unlink()
    assert PersonalKnowledgeLibrary(tmp_path, 'alice').overview()['stats']['status'] == '索引待核验'


def test_rejects_traversal_and_cross_owner_symlinks(tmp_path):
    seed(tmp_path, 'bob')
    with pytest.raises(ValueError):
        PersonalKnowledgeLibrary(tmp_path, '..')
    (tmp_path / 'knowledge_customers/alice').symlink_to(tmp_path / 'knowledge_customers/bob', target_is_directory=True)
    with pytest.raises(ValueError):
        PersonalKnowledgeLibrary(tmp_path, 'alice').overview()


def test_api_uses_authenticated_owner_and_rejects_foreign_document(tmp_path):
    container = ApplicationContainer.build(Settings(mode='stub'), snapshot_root=tmp_path / 'snapshots', include_backend_handoff=False)
    container.knowledge_backend = SimpleNamespace(paths=SimpleNamespace(runtime_root=tmp_path, component_root=tmp_path / 'component'))
    with TestClient(create_app(container, auth_required=True)) as client:
        assert client.get('/api/v1/knowledge/content/library').status_code == 401
        assert client.get('/api/v1/knowledge/content/library/unknown').status_code == 401
        assert client.delete('/api/v1/knowledge/content/library/unknown').status_code == 401
        assert client.post('/api/v1/knowledge/content/library/rebuild').status_code == 401
        r = client.post('/api/v1/auth/register', json={'username': 'library-user', 'display_name': '资料用户', 'password': 'library-test-password-2026'})
        owner = r.json()['user']['user_id']
        seed(tmp_path, owner)
        seed(tmp_path, 'other')
        result = client.get('/api/v1/knowledge/content/library?owner_id=other')
        assert result.status_code == 200
        doc_id = result.json()['files'][0]['id']
        assert client.get('/api/v1/knowledge/content/library/' + doc_id).status_code == 200
        assert client.get('/api/v1/knowledge/content/library/not-owned').status_code == 404


def test_management_api_owner_busy_and_foreign_document(tmp_path):
    from competition_app.services.personal_knowledge_storage import owner_lock
    from competition_app.tests.services.test_personal_knowledge_management import backend, seed as seed_management
    container = ApplicationContainer.build(Settings(mode='stub'), snapshot_root=tmp_path / 'snapshots', include_backend_handoff=False)
    container.knowledge_backend = backend(tmp_path)
    with TestClient(create_app(container, auth_required=True)) as client:
        owner = client.post('/api/v1/auth/register', json={'username': 'manager-user', 'display_name': '管理用户', 'password': 'library-test-password-2026'}).json()['user']['user_id']
        seed_management(tmp_path, owner)
        seed_management(tmp_path, 'other')
        base = '/api/v1/knowledge/content/library'
        with owner_lock(tmp_path, owner):
            assert client.post(base + '/rebuild').status_code == 409
            assert client.get(base).status_code == 409
        assert client.delete(base + '/unknown').status_code == 404
        result = client.delete(base + '/' + PersonalKnowledgeLibrary._id('s1') + '?owner_id=other')
        assert result.status_code == 200
        assert result.json()['total_vectors'] == 2
        assert len(PersonalKnowledgeLibrary(tmp_path, 'other').overview()['files']) == 2
        assert client.post(base + '/rebuild').status_code == 200