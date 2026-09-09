"""Manage committed personal deliveries, never public indexes or question activation."""
from collections import Counter
import json
from pathlib import Path
import shutil

from competition_app.services.personal_knowledge_library import PersonalKnowledgeLibrary
from competition_app.services.personal_knowledge_storage import (
    PersonalPublication, checked, owner_lock, remove, write_json,
)


def rows(path):
    if not path.exists():
        return []
    if path.suffix == '.jsonl':
        return [json.loads(line) for line in path.read_text(encoding='utf-8-sig').splitlines() if line.strip()]
    return json.loads(path.read_text(encoding='utf-8-sig'))


def save_rows(path, value):
    if path.suffix == '.jsonl':
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(''.join(json.dumps(row, ensure_ascii=False) + '\n' for row in value), encoding='utf-8')
    else:
        write_json(path, value)


def targets_for(backend, owner):
    library = PersonalKnowledgeLibrary(backend.paths.runtime_root, owner)
    root = library.root
    targets = {
        'delivery': checked(library.delivery, root),
        'exam': checked(root / 'exam_customers' / owner / 'current', root),
        'vector': checked(root / 'user_vdb' / owner / 'indexes' / '知识点', root),
    }
    component_runtime = (backend.paths.component_root / 'runtime').resolve()
    if component_runtime != root:
        targets['component_vector'] = checked(component_runtime / 'user_vdb' / owner / 'indexes' / '知识点', component_runtime)
    return targets


def recover_personal(backend, owner):
    return PersonalPublication(backend.paths.runtime_root, owner, targets_for(backend, owner))


def _delete_source(delivery, document_id, exam):
    registry_path = delivery / '09_ingestion/ingestion_registry.jsonl'
    registry = rows(registry_path)
    source = next((row['source_id'] for row in registry if PersonalKnowledgeLibrary._id(row['source_id']) == document_id), None)
    if source is None:
        raise KeyError('个人资料不存在')
    chunks_path = delivery / '03_pipeline_chunks/source_chunks.jsonl'
    chunks = rows(chunks_path)
    removed_chunks = {row['chunk_uid'] for row in chunks if row.get('metadata', {}).get('source_id') == source}
    remaining = [row for row in chunks if row['chunk_uid'] not in removed_chunks]
    keep_chunks = {row['chunk_uid'] for row in remaining}
    point_path = delivery / '04_knowledge_points/final_knowledge_points.json'
    points = rows(point_path)
    kp = lambda row: row.get('kp', row)
    high_path = delivery / '09_ingestion/kp_id_high_water.json'
    high = json.loads(high_path.read_text()) if high_path.exists() else 0
    write_json(high_path, max([int(high), *(int(kp(row)['kp_id']) for row in points if str(kp(row)['kp_id']).isdigit())]))
    removed_points = {str(kp(row)['kp_id']) for row in points
                      if removed_chunks.intersection(kp(row).get('raw_content') or [])
                      and not keep_chunks.intersection(kp(row).get('raw_content') or [])}
    for name in ('04_knowledge_points/final_knowledge_points.json',
                 '04_knowledge_points/final_knowledge_points.jsonl',
                 '04_knowledge_points/final_knowledge_points.with_meta.json',
                 '07_exam_bridge/knowledge_points.with_exam_tags.json'):
        path = delivery / name
        if not path.exists():
            continue
        kept = []
        for row in rows(path):
            point = kp(row)
            if str(point['kp_id']) in removed_points:
                continue
            if 'raw_content' in point:
                point['raw_content'] = [uid for uid in point['raw_content'] if uid not in removed_chunks]
            kept.append(row)
        save_rows(path, kept)
    save_rows(chunks_path, remaining)
    save_rows(registry_path, [row for row in registry if row['source_id'] != source])
    for path in (delivery / '02_raw_chunks').glob('*.jsonl'):
        kept = [row for row in rows(path) if row.get('source_id', row.get('metadata', {}).get('source_id')) != source]
        if kept:
            save_rows(path, kept)
        else:
            path.unlink()
    # Source directory is a structured pipeline key, never a display title/path.
    markdown_root = delivery / '09_ingestion/source_markdown'
    markdown = checked(markdown_root / source, markdown_root)
    if markdown == markdown_root or Path(source).name != source:
        raise ValueError('资料来源标识无效')
    remove(markdown)
    for name in ('question_kp_incremental_matches.jsonl', 'question_kp_incremental_vector.jsonl'):
        path = delivery / '05_bridge' / name
        if not path.exists():
            continue
        kept = []
        for row in rows(path):
            if str(row.get('kp_id')) in removed_points or row.get('evidence_chunk_uid') in removed_chunks:
                continue
            if 'kp_raw_content' in row:
                row['kp_raw_content'] = [uid for uid in row['kp_raw_content'] if uid not in removed_chunks]
            kept.append(row)
        ranks = Counter()
        for row in kept:
            ranks[str(row.get('题目id'))] += 1
            row['rank'] = ranks[str(row.get('题目id'))]
        save_rows(path, kept)
    write_json(delivery / '05_bridge/question_kp_all_summary.json', {
        'customer_package': True,
        'bridge_rows': len(rows(delivery / '05_bridge/question_kp_incremental_matches.jsonl')),
    })
    # Recompute the surviving chapter tree using explicit chunk/node relationships.
    links_path = delivery / '03_pipeline_chunks/chunk_chapter_links.jsonl'
    links = [row for row in rows(links_path) if row['chunk_uid'] in keep_chunks]
    nodes_path = delivery / '03_pipeline_chunks/chapter_nodes.jsonl'
    nodes = rows(nodes_path)
    by_id = {row['node_id']: row for row in nodes}
    node_chunks = {}
    for link in links:
        for field in ('chapter_id', 'section_id'):
            node = link.get(field)
            visited = set()
            while node and node in by_id and node not in visited:
                visited.add(node)
                node_chunks.setdefault(node, set()).add(link['chunk_uid'])
                node = by_id[node].get('parent_id')
    kept_nodes = []
    for node in nodes:
        if node['node_id'] in node_chunks:
            node['chunk_count'] = len(node_chunks[node['node_id']])
            kept_nodes.append(node)
    save_rows(links_path, links)
    save_rows(nodes_path, kept_nodes)
    # Historical report belongs to its original run; do not present it as current.
    report_path = delivery / '03_pipeline_chunks/chapter_hierarchy_report.json'
    if report_path.exists():
        report_path.unlink()
    if exam.exists():
        matches = exam / 'exam_kp_matches.jsonl'
        kept_matches = [row for row in rows(matches)
                        if not (row.get('kp_scope') == 'user' and str(row.get('kp_id')) in removed_points)]
        save_rows(matches, kept_matches)
        tags = exam / 'user_kp_exam_tags.json'
        save_rows(tags, [row for row in rows(tags) if str(row.get('kp_id')) not in removed_points])
        manifest = exam / 'manifest.json'
        if manifest.exists():
            summary = json.loads(manifest.read_text())
            active = [row for row in kept_matches if row.get('review_status') == 'auto_accepted']
            summary.update(match_count=len(kept_matches), accepted_match_count=len(active),
                           needs_human_review_count=len(kept_matches) - len(active),
                           user_kp_match_count=sum(row.get('kp_scope') == 'user' for row in active))
            write_json(manifest, summary)
    return {'removed_chunks': len(removed_chunks), 'removed_knowledge_points': len(removed_points)}


def validate_delivery(delivery):
    sources = {row['source_id'] for row in rows(delivery / '09_ingestion/ingestion_registry.jsonl')}
    chunks = rows(delivery / '03_pipeline_chunks/source_chunks.jsonl')
    ids = {row['chunk_uid'] for row in chunks}
    if len(ids) != len(chunks) or any(row.get('metadata', {}).get('source_id') not in sources for row in chunks):
        raise ValueError('个人切片来源校验失败')
    points = [row.get('kp', row) for row in rows(delivery / '04_knowledge_points/final_knowledge_points.json')]
    point_ids = {str(row['kp_id']) for row in points}
    if len(point_ids) != len(points) or any(not row.get('raw_content') or set(row['raw_content']) - ids for row in points):
        raise ValueError('个人知识点引用校验失败')
    for name in ('question_kp_incremental_matches.jsonl', 'question_kp_incremental_vector.jsonl'):
        for row in rows(delivery / '05_bridge' / name):
            if str(row.get('kp_id')) not in point_ids or (row.get('evidence_chunk_uid') and row['evidence_chunk_uid'] not in ids):
                raise ValueError('个人题目关联校验失败')
    return len(points)


def manage_personal_knowledge(backend, owner, document_id=None):
    with owner_lock(backend.paths.runtime_root, owner):
        transaction = recover_personal(backend, owner)
        try:
            return _manage_locked(backend, owner, document_id, transaction)
        finally:
            if not transaction.journal.exists() and transaction.work.exists():
                remove(transaction.work / 'after')
                before = transaction.work / 'before'
                if not before.exists() or not any(before.iterdir()):
                    remove(transaction.work)


def _manage_locked(backend, owner, document_id, transaction):
    vector_keys = [key for key in transaction.targets if key.endswith('vector')]
    keys = ['delivery', *vector_keys]
    if document_id is not None:
        keys.append('exam')
    staged = transaction.stage(keys)
    delivery = staged['delivery']
    delivery.mkdir(parents=True, exist_ok=True)
    details = _delete_source(delivery, document_id, staged['exam']) if document_id is not None else {}
    count = validate_delivery(delivery)
    # New directories bypass sync_collection's unsafe unchanged/empty shortcuts.
    for key in vector_keys:
        remove(staged[key])
    if count:
        module = backend._module('retrieval.user_vector_store')
        embedder = backend._sync_embedder()
        if embedder is None:
            raise RuntimeError('个人索引重建未配置')
        records = module.knowledge_point_records(delivery, owner)
        result = module.sync_collection(staged['vector'], records, embedder.name, embedder.embed_many,
                                        owner_id=owner, collection='知识点')
        metadata = rows(staged['vector'] / 'metadata.jsonl')
        manifest = json.loads((staged['vector'] / 'manifest.json').read_text())
        index = module._load_index(staged['vector'] / 'index.faiss')
        if (not result.get('ok') or result.get('count') != count or index.ntotal != count
                or len(metadata) != count or manifest.get('count') != count
                or manifest.get('dimension') != index.d or index.d < 1):
            raise ValueError('个人索引数量或维度校验失败')
        if (manifest.get('owner_id') != owner or manifest.get('collection') != '知识点'
                or manifest.get('embedding_model') != embedder.name
                or any(row.get('owner_id') != owner for row in metadata)):
            raise ValueError('个人索引归属校验失败')
        if {row['entity_id'] for row in metadata} != {row['entity_id'] for row in records}:
            raise ValueError('个人索引内容校验失败')
        for key in vector_keys:
            if key != 'vector':
                shutil.copytree(staged['vector'], staged[key])
    if document_id is None:
        # A rebuild changes indexes only, never the committed documents.
        del staged['delivery']
    operation = transaction.publish(staged)
    return {'ok': True, 'operation_id': operation, 'total_vectors': count,
            'action': 'deleted' if document_id is not None else 'rebuilt', **details}