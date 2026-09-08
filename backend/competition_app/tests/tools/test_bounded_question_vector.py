import json
import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import faiss
import numpy as np
import pytest

from competition_app.tools.bounded_question_vector import flat_ip_topk, vector_question_hits


@pytest.mark.parametrize("rows,dimension,k,block", [(0, 3, 4, 2), (19, 7, 5, 3), (7, 3, 20, 2), (101, 2560, 13, 11)])
def test_matches_faiss_exact_search(tmp_path, rows, dimension, k, block):
    rng = np.random.default_rng(42)
    vectors = rng.normal(size=(rows, dimension)).astype("float32")
    query = rng.normal(size=(1, dimension)).astype("float32")
    index = faiss.IndexFlatIP(dimension)
    index.add(vectors)
    path = tmp_path / "index.faiss"
    faiss.write_index(index, str(path))
    scores, ids = flat_ip_topk(path, query, k, block_rows=block)
    expected_scores, expected_ids = index.search(query, min(k, rows)) if rows else (np.empty((1, 0)), np.empty((1, 0)))
    np.testing.assert_array_equal(ids, expected_ids[0])
    np.testing.assert_allclose(scores, expected_scores[0], rtol=2e-5, atol=2e-5)


def test_ties_match_faiss_across_blocks(tmp_path):
    index = faiss.IndexFlatIP(3)
    index.add(np.ones((10, 3), dtype="float32"))
    path = tmp_path / "index.faiss"
    faiss.write_index(index, str(path))
    query = np.ones((1, 3), dtype="float32")
    _, ids = flat_ip_topk(path, query, 4, block_rows=2)
    np.testing.assert_array_equal(ids, index.search(query, 4)[1][0])


@pytest.mark.parametrize("fault", ["truncated", "metric", "dimension", "nan"])
def test_invalid_index_or_query_fails_explicitly(tmp_path, fault):
    index = faiss.IndexFlatL2(3) if fault == "metric" else faiss.IndexFlatIP(3)
    index.add(np.ones((4, 3), dtype="float32"))
    path = tmp_path / "index.faiss"
    faiss.write_index(index, str(path))
    if fault == "truncated":
        path.write_bytes(path.read_bytes()[:-1])
    query = np.ones(2 if fault == "dimension" else 3, dtype="float32")
    if fault == "nan":
        query[0] = np.nan
    with pytest.raises((RuntimeError, ValueError)):
        flat_ip_topk(path, query, 2)


def test_metadata_resolution_uses_existing_vector_without_embedding(tmp_path):
    directory = tmp_path / "indexes" / "题库"
    directory.mkdir(parents=True)
    index = faiss.IndexFlatIP(2)
    index.add(np.array([[1, 0], [0, 1], [2, 0]], dtype="float32"))
    faiss.write_index(index, str(directory / "index.faiss"))
    (directory / "metadata.jsonl").write_text("\n".join(json.dumps({"original": {"question_id": f"Q{i}"}}) for i in range(3)), encoding="utf-8")
    hits = vector_question_hits(tmp_path, "unused", None, 2, np.array([1, 0]))
    assert hits == [("Q2", 2.0), ("Q0", 1.0)]


def test_reads_vectors_in_bounded_blocks(tmp_path, monkeypatch):
    index = faiss.IndexFlatIP(3)
    index.add(np.ones((17, 3), dtype="float32"))
    path = tmp_path / "index.faiss"
    faiss.write_index(index, str(path))
    original = np.fromfile
    counts = []
    def read(*args, **kwargs):
        counts.append(kwargs["count"])
        return original(*args, **kwargs)
    monkeypatch.setattr(np, "fromfile", read)
    flat_ip_topk(path, np.ones(3), 3, block_rows=4)
    assert max(counts) <= 12
    assert sum(counts) == 51


def test_delivery_installs_bounded_adapter(tmp_path, monkeypatch):
    from competition_app.tools.knowledge_delivery import KnowledgeDeliveryBackend
    backend = object.__new__(KnowledgeDeliveryBackend)
    backend.paths = SimpleNamespace(component_root=tmp_path)
    backend._module_lock = threading.RLock()
    backend._modules = {}
    module = SimpleNamespace(vector_question_hits=lambda: None)
    monkeypatch.setattr("competition_app.tools.knowledge_delivery.importlib.import_module", lambda name: module)
    assert backend._module("retrieval.hybrid_question_retrieval").vector_question_hits is vector_question_hits


def test_delivery_heavy_searches_are_serialized():
    from competition_app.tools.knowledge_delivery import KnowledgeDeliveryBackend, _QUESTION_SEARCH_LOCK
    backend = object.__new__(KnowledgeDeliveryBackend)
    def search(value):
        assert _QUESTION_SEARCH_LOCK.locked()
        return value
    backend._search_questions = search
    with ThreadPoolExecutor(max_workers=3) as pool:
        assert list(pool.map(backend._search_questions_serialized, range(5))) == list(range(5))