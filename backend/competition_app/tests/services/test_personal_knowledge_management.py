import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from competition_app.services.personal_knowledge_library import PersonalKnowledgeLibrary
from competition_app.services.personal_knowledge_management import (
    manage_personal_knowledge, recover_personal, rows, save_rows, targets_for,
)
from competition_app.services.personal_knowledge_storage import (
    PersonalKnowledgeBusy, PersonalPublication, owner_lock, reserve_personal_kp_ids, write_json,
)


class Vectors:
    @staticmethod
    def knowledge_point_records(delivery, owner):
        return [{'entity_id': row['kp']['kp_id'], 'content': 'point'} for row in rows(delivery / '04_knowledge_points/final_knowledge_points.json')]

    @staticmethod
    def sync_collection(path, records, model, embed, **kwargs):
        embed([row['content'] for row in records])
        path.mkdir(parents=True)
        (path / 'index.faiss').write_text(str(len(records)))
        save_rows(path / 'metadata.jsonl', [dict(row, owner_id=kwargs['owner_id']) for row in records])
        write_json(path / 'manifest.json', {'count': len(records), 'owner_id': kwargs['owner_id'], 'collection': '知识点', 'embedding_model': model, 'dimension': 1})
        return {'ok': True, 'count': len(records)}

    @staticmethod
    def _load_index(path):
        return SimpleNamespace(ntotal=int(path.read_text()), d=1)


def backend(root, embed=lambda texts: [[1.0] for _ in texts]):
    return SimpleNamespace(paths=SimpleNamespace(runtime_root=root, component_root=root / 'component'),
                           _module=lambda name: Vectors,
                           _sync_embedder=lambda: SimpleNamespace(name='fake', embed_many=embed))


def seed(root, owner='alice'):
    b = backend(root)
    paths = targets_for(b, owner)
    d = paths['delivery']
    save_rows(d / '09_ingestion/ingestion_registry.jsonl', [
        {'source_id': s, 'source_title': s} for s in ['s1', 's2']])
    save_rows(d / '03_pipeline_chunks/source_chunks.jsonl', [
        {'chunk_uid': 'c' + s, 'metadata': {'source_id': 's' + s}, 'text': s} for s in ['1', '2']])
    points = [{'kp_id': '000001', 'raw_content': ['c1']},
              {'kp_id': '000002', 'raw_content': ['c2']},
              {'kp_id': '000003', 'raw_content': ['c1', 'c2']}]
    for name in ('final_knowledge_points.json', 'final_knowledge_points.jsonl'):
        save_rows(d / '04_knowledge_points' / name, [{'kp': point} for point in points])
    save_rows(d / '04_knowledge_points/final_knowledge_points.with_meta.json', points)
    save_rows(d / '07_exam_bridge/knowledge_points.with_exam_tags.json', points)
    for s in ['1', '2']:
        save_rows(d / f'02_raw_chunks/s{s}.jsonl', [{'source_id': 's' + s}])
        m = d / f'09_ingestion/source_markdown/s{s}/text.md'
        m.parent.mkdir(parents=True)
        m.write_text(s)
    save_rows(d / '03_pipeline_chunks/chapter_nodes.jsonl', [
        {'node_id': 'n' + s, 'parent_id': None, 'chunk_count': 1} for s in ['1', '2']])
    save_rows(d / '03_pipeline_chunks/chunk_chapter_links.jsonl', [
        {'chunk_uid': 'c' + s, 'chapter_id': 'n' + s} for s in ['1', '2']])
    links = [{'kp_id': '00000' + s, '题目id': 'q', 'evidence_chunk_uid': 'c' + s, 'rank': int(s)} for s in ['1', '2']]
    for name in ('question_kp_incremental_matches.jsonl', 'question_kp_incremental_vector.jsonl'):
        save_rows(d / '05_bridge' / name, links)
    save_rows(paths['exam'] / 'exam_kp_matches.jsonl', [
        {'kp_id': '000001', 'kp_scope': 'user'}, {'kp_id': '000001', 'kp_scope': 'public'}])
    save_rows(paths['exam'] / 'user_kp_exam_tags.json', [{'kp_id': '000001'}])
    for key in ('vector', 'component_vector'):
        paths[key].mkdir(parents=True)
        (paths[key] / 'index.faiss').write_text('corrupt')
    return b, d


def snapshot(root):
    return {str(path.relative_to(root)): path.read_bytes() for path in root.rglob('*') if path.is_file()}


def test_delete_retains_shared_points_other_sources_and_public_exam(tmp_path):
    b, d = seed(tmp_path)
    seed(tmp_path, 'bob')
    bob = snapshot(tmp_path / 'knowledge_customers/bob')
    result = manage_personal_knowledge(b, 'alice', PersonalKnowledgeLibrary._id('s1'))
    assert result['removed_knowledge_points'] == 1
    assert result['total_vectors'] == 2
    points = rows(d / '04_knowledge_points/final_knowledge_points.json')
    assert [row['kp']['kp_id'] for row in points] == ['000002', '000003']
    assert points[1]['kp']['raw_content'] == ['c2']
    assert not (d / '09_ingestion/source_markdown/s1').exists()
    assert rows(d / '03_pipeline_chunks/chapter_nodes.jsonl')[0]['node_id'] == 'n2'
    assert rows(d / '05_bridge/question_kp_incremental_matches.jsonl')[0]['rank'] == 1
    paths = targets_for(b, 'alice')
    assert rows(paths['exam'] / 'exam_kp_matches.jsonl') == [{'kp_id': '000001', 'kp_scope': 'public'}]
    assert snapshot(paths['vector']) == snapshot(paths['component_vector'])
    assert snapshot(tmp_path / 'knowledge_customers/bob') == bob


def test_delete_last_document_clears_both_indexes_and_retains_high_water(tmp_path):
    b, d = seed(tmp_path)
    for source in ('s1', 's2'):
        manage_personal_knowledge(b, 'alice', PersonalKnowledgeLibrary._id(source))
    paths = targets_for(b, 'alice')
    assert not paths['vector'].exists() and not paths['component_vector'].exists()
    assert rows(d / '09_ingestion/ingestion_registry.jsonl') == []
    assert json.loads((d / '09_ingestion/kp_id_high_water.json').read_text()) == 3
    assert PersonalKnowledgeLibrary(tmp_path, 'alice').overview()['stats']['status'] == '暂无资料'
    assert manage_personal_knowledge(b, 'alice')['total_vectors'] == 0


def test_embedding_failure_preserves_all_live_targets(tmp_path):
    b, _ = seed(tmp_path)
    before = {k: snapshot(v) for k, v in targets_for(b, 'alice').items()}
    def fail(texts):
        raise RuntimeError('fake failure')
    with pytest.raises(RuntimeError):
        manage_personal_knowledge(backend(tmp_path, fail), 'alice', PersonalKnowledgeLibrary._id('s1'))
    assert {k: snapshot(v) for k, v in targets_for(b, 'alice').items()} == before


def test_force_rebuild_does_not_trust_corrupt_old_index(tmp_path):
    b, d = seed(tmp_path)
    before = snapshot(d)
    result = manage_personal_knowledge(b, 'alice')
    assert result['total_vectors'] == 3
    assert snapshot(d) == before
    assert (targets_for(b, 'alice')['vector'] / 'index.faiss').read_text() == '3'


def test_lock_shared_reads_exclusive_writes_and_owner_isolation(tmp_path):
    with owner_lock(tmp_path, 'alice', read=True):
        with owner_lock(tmp_path, 'alice', read=True):
            pass
        with pytest.raises(PersonalKnowledgeBusy), owner_lock(tmp_path, 'alice'):
            pass
        with owner_lock(tmp_path, 'bob'):
            pass


def test_publication_failure_rolls_back_all_targets(tmp_path, monkeypatch):
    import competition_app.services.personal_knowledge_storage as storage
    b, _ = seed(tmp_path)
    paths = targets_for(b, 'alice')
    before = {k: snapshot(v) for k, v in paths.items()}
    original = storage.os.replace
    def replace(source, target):
        if Path(source).name == 'vector' and Path(source).parent.name == 'after':
            raise OSError('injected switch failure')
        return original(source, target)
    monkeypatch.setattr(storage.os, 'replace', replace)
    with pytest.raises(OSError):
        manage_personal_knowledge(b, 'alice', PersonalKnowledgeLibrary._id('s1'))
    assert {k: snapshot(v) for k, v in paths.items()} == before


def test_interrupted_publication_recovers_before_read(tmp_path):
    b, _ = seed(tmp_path)
    with owner_lock(tmp_path, 'alice'):
        t = recover_personal(b, 'alice')
        staged = t.stage(['delivery'])
        old = snapshot(t.targets['delivery'])
        write_json(t.journal, {'operation': t.operation, 'state': 'publishing', 'targets': {'delivery': True}})
        t.targets['delivery'].rename(t.work / 'before/delivery')
        staged['delivery'].rename(t.targets['delivery'])
        (t.targets['delivery'] / 'bad').write_text('partial')
        recover_personal(b, 'alice')
        assert snapshot(t.targets['delivery']) == old


def test_high_water_allocator_and_unknown_document(tmp_path):
    b, d = seed(tmp_path)
    write_json(d / '09_ingestion/kp_id_high_water.json', 100)
    stage = tmp_path / 'stage'
    for name in ('final_knowledge_points.json', 'final_knowledge_points.with_meta.json'):
        save_rows(stage / '04_knowledge_points' / name, [{'kp_id': '1', 'order_code': 'old'}])
    assert reserve_personal_kp_ids(stage, d, ['order_code']) == {'1': '000101'}
    assert 'order_code' not in rows(stage / '04_knowledge_points/final_knowledge_points.json')[0]
    with pytest.raises(KeyError):
        manage_personal_knowledge(b, 'alice', 'unknown')
    with pytest.raises(ValueError):
        manage_personal_knowledge(b, '..')


def test_symlink_inside_delivery_is_rejected_without_touching_target(tmp_path):
    b, d = seed(tmp_path)
    public = tmp_path / 'public.txt'
    public.write_text('public')
    (d / 'linked').symlink_to(public)
    with pytest.raises(ValueError):
        manage_personal_knowledge(b, 'alice')
    assert public.read_text() == 'public'


def test_real_faiss_rebuild_and_delete_without_network(tmp_path, monkeypatch):
    import importlib
    import os
    component = os.environ.get('KNOWLEDGE_TEST_COMPONENT_ROOT')
    if not component:
        pytest.skip('external component fixture not configured')
    monkeypatch.syspath_prepend(component)
    module = importlib.import_module('retrieval.user_vector_store')
    b, _ = seed(tmp_path)
    b._module = lambda name: module
    result = manage_personal_knowledge(b, 'alice')
    assert result['total_vectors'] == 3
    paths = targets_for(b, 'alice')
    assert module._load_index(paths['vector'] / 'index.faiss').ntotal == 3
    result = manage_personal_knowledge(b, 'alice', PersonalKnowledgeLibrary._id('s1'))
    assert result['total_vectors'] == 2
    assert module._load_index(paths['component_vector'] / 'index.faiss').ntotal == 2