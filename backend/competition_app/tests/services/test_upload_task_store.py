from concurrent.futures import ThreadPoolExecutor
import asyncio
import json

import pytest

from competition_app.services.upload_tasks import UploadTaskStore, finish_upload_work, textbook_task_response


def test_persistence_ownership_and_idempotency(tmp_path):
    store = UploadTaskStore(tmp_path)
    state, lease = store.start("alice", "fingerprint")
    other = UploadTaskStore(tmp_path)
    duplicate, second_lease = other.start("alice", "fingerprint")
    assert duplicate["task_id"] == state["task_id"] and second_lease is None
    assert other.get("alice", state["task_id"])["status"] == "running"
    with pytest.raises(KeyError):
        other.get("bob", state["task_id"])
    lease.update(step="extract", step_label="解析正文")
    lease.complete({"book_id": "UTB_TEST"})
    lease.update(step="late")
    lease.close()
    restored = other.get("alice", state["task_id"])
    assert restored["status"] == "done" and restored["step"] == "done"
    assert restored["book"] == {"book_id": "UTB_TEST"}
    assert other.start("alice", "fingerprint")[1] is None
    assert not {"owner_id", "fingerprint"} & textbook_task_response(restored).keys()


def test_abandoned_task_is_interrupted_not_replayed(tmp_path):
    store = UploadTaskStore(tmp_path)
    state, lease = store.start("alice", "f")
    lease.close()
    recovered = UploadTaskStore(tmp_path).get("alice", state["task_id"])
    assert recovered["status"] == "failed"
    assert recovered["error"]["code"] == "UPLOAD_INTERRUPTED"
    assert recovered["retry_allowed"] is False
    duplicate, lease = store.start("alice", "f")
    assert duplicate["task_id"] == state["task_id"] and lease is None


def test_known_failure_can_be_retried_with_new_id(tmp_path):
    store = UploadTaskStore(tmp_path)
    first, lease = store.start("alice", "f")
    lease.fail("PARSE_FAILED", "解析失败", retry_allowed=True)
    lease.close()
    second, lease = store.start("alice", "f")
    assert second["task_id"] != first["task_id"]
    lease.complete({"book_id": "book"})
    lease.close()
    assert len(store.list("alice")) == 2


def test_concurrent_duplicate_only_one_executor(tmp_path):
    def submit(_):
        return UploadTaskStore(tmp_path).start("alice", "same")
    with ThreadPoolExecutor(max_workers=6) as pool:
        submissions = list(pool.map(submit, range(12)))
    assert len({s["task_id"] for s, _ in submissions}) == 1
    leases = [lease for _, lease in submissions if lease is not None]
    assert len(leases) == 1
    leases[0].close()


def test_invalid_id_and_symlink_rejected(tmp_path):
    store = UploadTaskStore(tmp_path)
    with pytest.raises(KeyError):
        store.get("alice", "../bob")
    state, lease = store.start("alice", "f")
    lease.close()
    path = store._path("alice", state["task_id"])
    outside = tmp_path.parent / (tmp_path.name + "-external.json")
    outside.write_text(json.dumps(state))
    path.unlink()
    path.symlink_to(outside)
    with pytest.raises(ValueError):
        store.get("alice", state["task_id"])


def test_different_users_and_options_do_not_share_tasks(tmp_path):
    store = UploadTaskStore(tmp_path)
    submissions = [store.start("alice", "same"), store.start("bob", "same"),
                   store.start("alice", "different-options")]
    assert len({state["task_id"] for state, _ in submissions}) == 3
    for _, lease in submissions:
        lease.close()


@pytest.mark.asyncio
async def test_waiter_cancellation_keeps_execution_lock_until_publication(tmp_path):
    store = UploadTaskStore(tmp_path)
    state, lease = store.start("alice", "f")
    started, release = asyncio.Event(), asyncio.Event()
    async def work():
        started.set()
        await release.wait()
        lease.complete({"book_id": "book"})
    async def run():
        try:
            await finish_upload_work(work())
        finally:
            lease.close()
    task = asyncio.create_task(run())
    await started.wait()
    task.cancel()
    await asyncio.sleep(0)
    assert not task.done()
    assert store.get("alice", state["task_id"])["status"] == "running"
    assert store.start("alice", "f")[1] is None
    release.set()
    await task
    assert store.get("alice", state["task_id"])["status"] == "done"