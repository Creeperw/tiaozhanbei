import json

import faiss
import numpy as np
import pytest

from APP.backend.rag_disk import DiskVectorDatabase
from APP.backend.scripts.verify_legacy_indexes import verify, write_missing


def collection(tmp_path, rows=7):
    root = tmp_path / "教材"
    root.mkdir()
    vectors = np.random.default_rng(42).normal(size=(rows, 4)).astype("float32")
    faiss.normalize_L2(vectors)
    index = faiss.IndexFlatIP(4)
    index.add(vectors)
    faiss.write_index(index, str(root / "index.faiss"))
    (root / "metadata.jsonl").write_text("\n".join(json.dumps({"content": str(i)}) for i in range(rows)) + "\n", encoding="utf-8")
    return root, index, vectors


def test_disk_search_matches_faiss_without_loading_index(tmp_path, monkeypatch):
    root, index, vectors = collection(tmp_path)
    monkeypatch.setattr(faiss, "read_index", lambda *a: pytest.fail("must not load index"))
    database = DiskVectorDatabase(str(root / "index.faiss"), str(root / "metadata.jsonl"))
    scores, ids = database.index.search(vectors[:2], 3)
    expected_scores, expected_ids = index.search(vectors[:2], 3)
    np.testing.assert_array_equal(ids, expected_ids)
    np.testing.assert_allclose(scores, expected_scores, atol=1e-6)
    assert database.metadata[3] == {"content": "3"}
    assert len(database.metadata) == 7
    np.testing.assert_array_equal(database.index.reconstruct(3), vectors[3])


def test_metadata_mismatch_rejected(tmp_path):
    root, _, _ = collection(tmp_path)
    (root / "metadata.jsonl").write_text("{}\n")
    with pytest.raises(RuntimeError, match="count mismatch"):
        DiskVectorDatabase(str(root / "index.faiss"), str(root / "metadata.jsonl"))


def test_changed_assets_rejected(tmp_path):
    root, _, vectors = collection(tmp_path)
    database = DiskVectorDatabase(str(root / "index.faiss"), str(root / "metadata.jsonl"))
    with (root / "metadata.jsonl").open("a") as handle:
        handle.write("{}\n")
    with pytest.raises(RuntimeError, match="changed"):
        database.metadata[0]
    with (root / "index.faiss").open("ab") as handle:
        handle.write(b"x")
    with pytest.raises(RuntimeError, match="changed"):
        database.index.search(vectors[:1], 1)


def test_verify_then_publish_only_measured_manifest(tmp_path):
    root, _, vectors = collection(tmp_path)
    reports = verify(tmp_path, model="test", dimensions=4, encode=lambda texts: [vectors[int(t)] for t in texts])
    assert not (root / "index_manifest.json").exists()
    write_missing(tmp_path, reports)
    manifest = json.loads((root / "index_manifest.json").read_text())
    assert manifest["vector_count"] == 7
    assert manifest["verification"]["samples"]
    with pytest.raises(ValueError, match="already exists"):
        write_missing(tmp_path, reports)


def test_same_dimension_wrong_model_fails_without_manifest(tmp_path):
    root, _, vectors = collection(tmp_path)
    with pytest.raises(ValueError, match="compatibility failed"):
        verify(tmp_path, model="wrong", dimensions=4, encode=lambda texts: [-vectors[int(t)] for t in texts])
    assert not (root / "index_manifest.json").exists()


def test_publish_refuses_asset_changed_after_verification(tmp_path):
    root, _, vectors = collection(tmp_path)
    reports = verify(tmp_path, model="test", dimensions=4, encode=lambda texts: [vectors[int(t)] for t in texts])
    with (root / "metadata.jsonl").open("a") as handle:
        handle.write("\n")
    with pytest.raises(ValueError, match="asset changed"):
        write_missing(tmp_path, reports)
    assert not (root / "index_manifest.json").exists()