"""Persistent upload task metadata. Existing database jobs must not be copied here."""
from __future__ import annotations

from contextlib import contextmanager
import asyncio
from datetime import datetime, timezone
import fcntl
import hashlib
import json
from pathlib import Path
import re
import threading
from uuid import uuid4

from competition_app.services.personal_knowledge_storage import checked, write_json
from competition_app.contracts.upload import upload_progress


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def finish_upload_work(awaitable):
    """Do not release a publication lease merely because its waiter is cancelled.

    A hard process kill still releases flock; the next read marks uncertainty
    and prohibits automatic replay. This is not a durable execution queue.
    """
    work = asyncio.ensure_future(awaitable)
    while True:
        try:
            return await asyncio.shield(work)
        except asyncio.CancelledError:
            if work.done():
                return work.result()


def textbook_fingerprint(content: bytes, cover: bytes | None, options: dict) -> str:
    value = {
        "content": hashlib.sha256(content).hexdigest(),
        "cover": hashlib.sha256(cover or b"").hexdigest(),
        "options": options,
    }
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


class UploadTaskStore:
    def __init__(self, runtime_root: Path):
        self.runtime_root = Path(runtime_root).resolve()
        self.root = self.runtime_root / "upload_tasks"

    def _home(self, owner: str) -> Path:
        if not owner:
            raise ValueError("用户标识不能为空")
        key = hashlib.sha256(owner.encode()).hexdigest()
        home = checked(self.root / key, self.runtime_root)
        home.mkdir(parents=True, exist_ok=True, mode=0o700)
        return home

    def _path(self, owner: str, task_id: str) -> Path:
        if not re.fullmatch(r"TBI_[0-9a-f]{32}", task_id):
            raise KeyError(task_id)
        return checked(self._home(owner) / f"{task_id}.json", self.runtime_root)

    @contextmanager
    def _admission(self, owner: str):
        path = checked(self._home(owner) / "admission.lock", self.runtime_root)
        with path.open("a") as stream:
            fcntl.flock(stream, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(stream, fcntl.LOCK_UN)

    def _execution_lock(self, path: Path):
        lock = checked(path.with_suffix(".lock"), self.runtime_root).open("a")
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            lock.close()
            return None
        return lock

    @staticmethod
    def _read(path: Path, owner: str) -> dict:
        if not path.is_file():
            raise KeyError(path.stem)
        value = json.loads(path.read_text(encoding="utf-8"))
        if value.get("owner_id") != owner or value.get("task_id") != path.stem:
            raise KeyError(path.stem)
        return value

    def get(self, owner: str, task_id: str) -> dict:
        path = self._path(owner, task_id)
        state = self._read(path, owner)
        if state["status"] == "running":
            lock = self._execution_lock(path)
            if lock is not None:
                try:
                    state = self._read(path, owner)
                    if state["status"] == "running":
                        # The holder is gone. Publication might have completed
                        # before the status write: never assume safe replay.
                        state.update(status="failed", step="interrupted",
                            step_label="任务已中断，请先检查教材书架",
                            updated_at=now(), finished_at=now(), retry_allowed=False,
                            error={"code": "UPLOAD_INTERRUPTED",
                                   "message": "处理进程已结束，任务结果待核实；请先检查教材书架，勿重复上传。"})
                        write_json(path, state)
                finally:
                    lock.close()
        return state

    def list(self, owner: str, limit: int = 50) -> list[dict]:
        paths = sorted(self._home(owner).glob("TBI_*.json"),
                       key=lambda p: p.stat().st_mtime, reverse=True)
        return [self.get(owner, p.stem) for p in paths[:limit]]

    def start(self, owner: str, fingerprint: str) -> tuple[dict, UploadTaskLease | None]:
        with self._admission(owner):
            for path in self._home(owner).glob("TBI_*.json"):
                old = self._read(checked(path, self.runtime_root), owner)
                if old.get("fingerprint") != fingerprint:
                    continue
                old = self.get(owner, path.stem)
                if old["status"] != "failed" or not old.get("retry_allowed", False):
                    return old, None
            task_id = "TBI_" + uuid4().hex
            path = self._path(owner, task_id)
            lock = self._execution_lock(path)
            if lock is None:
                raise RuntimeError("上传任务锁冲突")
            state = {
                "task_id": task_id, "owner_id": owner, "kind": "textbook",
                "fingerprint": fingerprint, "status": "running", "step": "upload",
                "step_label": "已接收文件，准备处理", "error": None, "book": None,
                "created_at": now(), "updated_at": now(), "finished_at": None,
                "retry_allowed": False,
            }
            try:
                write_json(path, state)
            except BaseException:
                lock.close()
                raise
            return state, UploadTaskLease(path, state, lock)


class UploadTaskLease:
    def __init__(self, path: Path, state: dict, lock):
        self.path, self.state, self.lock = path, state.copy(), lock
        self.mutex = threading.RLock()

    def update(self, **changes) -> None:
        with self.mutex:
            if self.lock is None or self.state["status"] != "running":
                return
            value = {**self.state, **changes, "updated_at": now()}
            write_json(self.path, value)
            self.state = value

    def complete(self, book: dict) -> None:
        self.update(status="done", step="done", step_label="教材处理完成",
                    book=book, error=None, finished_at=now())

    def fail(self, code: str, message: str, *, retry_allowed: bool = False) -> None:
        self.update(status="failed", step="failed", step_label="教材处理失败",
                    error={"code": code, "message": message}, finished_at=now(),
                    retry_allowed=retry_allowed)

    def close(self) -> None:
        with self.mutex:
            if self.lock is not None:
                self.lock.close()
                self.lock = None


def textbook_task_response(state: dict) -> dict:
    # Deliberately omit owner, content hash, local paths and execution metadata.
    keys = ("task_id", "status", "step", "step_label", "error", "book",
            "created_at", "updated_at", "finished_at", "retry_allowed")
    return {
        **{key: state.get(key) for key in keys},
        "progress": upload_progress("textbook", state["task_id"], state["status"],
                                    stage=state["step"], label=state["step_label"]),
    }