"""Owner-scoped locking and recoverable publication of personal knowledge files."""
from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import re
import shutil
from uuid import uuid4


class PersonalKnowledgeBusy(RuntimeError):
    pass


def checked(path: Path, root: Path) -> Path:
    path, root = Path(path), Path(root).resolve()
    if not path.is_absolute() or not path.resolve().is_relative_to(root):
        raise ValueError("个人资料路径无效")
    current = path
    while current != root:
        if current.is_symlink():
            raise ValueError("个人资料不允许符号链接")
        if current.parent == current:
            raise ValueError("个人资料路径无效")
        current = current.parent
    return path


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + '.tmp-' + uuid4().hex)
    with temp.open('w', encoding='utf-8') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temp, path)
    sync_dir(path.parent)


def sync_dir(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def remove(path):
    if path.is_dir():
        shutil.rmtree(path)
    else:
        path.unlink(missing_ok=True)


def copy_tree(source, target):
    if source.is_dir():
        if any(path.is_symlink() for path in source.rglob('*')):
            raise ValueError('个人资料不允许符号链接')
        shutil.copytree(source, target)
    elif source.exists():
        shutil.copy2(source, target)


def management_root(root, owner):
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', owner):
        raise ValueError('用户标识无效')
    root = Path(root).resolve()
    return checked(root / 'personal_management' / owner, root)


@contextmanager
def owner_lock(root, owner, *, read=False):
    """Nonblocking flock also serializes distinct backend instances/processes."""
    home = management_root(root, owner)
    home.mkdir(parents=True, exist_ok=True, mode=0o700)
    lock = checked(home / 'operation.lock', Path(root).resolve())
    with lock.open('a') as stream:
        try:
            mode = fcntl.LOCK_SH if read and not (home / 'pending.json').exists() else fcntl.LOCK_EX
            fcntl.flock(stream, mode | fcntl.LOCK_NB)
            if mode == fcntl.LOCK_SH and (home / 'pending.json').exists():
                fcntl.flock(stream, fcntl.LOCK_UN)
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise PersonalKnowledgeBusy('个人资料正在处理，请完成后重试') from exc
        try:
            yield
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)


class PersonalPublication:
    """Fixed-target journal; rollback before serving reads after interruption.

    Caller must hold owner_lock. Backups stay private, outside retrieval roots.
    """
    def __init__(self, root, owner, targets):
        self.root = Path(root).resolve()
        self.home = management_root(self.root, owner)
        self.targets = targets
        self.journal = self.home / 'pending.json'
        self.recover()
        self.operation = uuid4().hex
        self.work = self.home / self.operation

    def recover(self):
        checked(self.journal, self.root)
        if not self.journal.exists():
            return
        value = json.loads(self.journal.read_text())
        operation = value['operation']
        if not re.fullmatch(r'[a-f0-9]{32}', operation):
            raise ValueError('个人资料恢复记录无效')
        work = checked(self.home / operation, self.root)
        if value['state'] != 'committed':
            for key, existed in reversed(list(value['targets'].items())):
                target = self.targets[key]  # Never accept paths from a journal.
                checked(target, target.parent.resolve())
                backup = checked(work / 'before' / key, self.root)
                if backup.exists():
                    remove(target)
                    os.replace(backup, target)
                    sync_dir(target.parent)
                elif not existed:
                    remove(target)
        self.journal.unlink()
        sync_dir(self.home)

    def stage(self, keys):
        result = {}
        (self.work / 'after').mkdir(parents=True, mode=0o700)
        (self.work / 'before').mkdir(mode=0o700)
        for key in keys:
            target = self.targets[key]
            path = self.work / 'after' / key
            copy_tree(target, path)
            result[key] = path
        return result

    def publish(self, staged):
        for key in staged:
            ancestor = self.targets[key].parent
            while not ancestor.exists():
                ancestor = ancestor.parent
            if ancestor.stat().st_dev != self.work.stat().st_dev:
                raise ValueError('个人资料与索引必须位于同一文件系统以安全发布')
        # Flush prepared content before the recovery journal becomes visible.
        for path in staged.values():
            for file in ([path] if path.is_file() else path.rglob('*')):
                if file.is_file():
                    with file.open('rb') as stream:
                        os.fsync(stream.fileno())
        value = {'operation': self.operation, 'state': 'publishing',
                 'targets': {key: self.targets[key].exists() for key in staged}}
        write_json(self.journal, value)
        try:
            for key, prepared in staged.items():
                target = self.targets[key]
                target.parent.mkdir(parents=True, exist_ok=True)
                if target.exists():
                    os.replace(target, self.work / 'before' / key)
                    sync_dir(self.work / 'before')
                if prepared.exists():
                    os.replace(prepared, target)
                sync_dir(target.parent)
            value['state'] = 'committed'
            write_json(self.journal, value)
        except BaseException:
            self.recover()
            raise
        self.journal.unlink()
        sync_dir(self.home)
        return self.operation


def reserve_personal_kp_ids(stage, delivery, order_fields=()):
    """Replacement for the delivered allocator, including deleted ID high-water."""
    main_path = stage / '04_knowledge_points/final_knowledge_points.json'
    meta_path = stage / '04_knowledge_points/final_knowledge_points.with_meta.json'
    base_path = delivery / '04_knowledge_points/final_knowledge_points.json'
    high_path = delivery / '09_ingestion/kp_id_high_water.json'
    base = json.loads(base_path.read_text()) if base_path.exists() else []
    high = json.loads(high_path.read_text()) if high_path.exists() else 0
    ids = [str(row.get('kp', row)['kp_id']) for row in base]
    maximum = max([int(high), *(int(key) for key in ids if key.isdigit())])
    main = json.loads(main_path.read_text())
    mapping = {str(row['kp_id']): str(maximum + index).zfill(6) for index, row in enumerate(main, 1)}
    if len(mapping) != len(main):
        raise ValueError('新增知识点编号重复')
    # Reserve before later steps, so a failed run can never reuse its IDs either.
    write_json(high_path, maximum + len(main))
    for rows, path in [(main, main_path), (json.loads(meta_path.read_text()), meta_path)]:
        for row in rows:
            row['kp_id'] = mapping[str(row['kp_id'])]
            for key in order_fields:
                row.pop(key, None)
        write_json(path, rows)
    main_path.with_suffix('.jsonl').write_text(''.join(json.dumps(row, ensure_ascii=False) + '\n' for row in main), encoding='utf-8')
    return mapping