"""
会话管理器 - 支持内存、文件、Redis 三种模式
"""

import json
import os
from pathlib import Path
from typing import Dict, Optional


class SessionManager:
    """会话管理 — 默认使用文件持久化，支持内存和 Redis"""

    def __init__(self, use_redis: bool = False, redis_url: Optional[str] = None,
                 ttl: int = 3600, data_dir: Optional[str] = None):
        self.use_redis = use_redis
        self._ttl = ttl
        self._sessions: Dict[str, Dict] = {}
        self._data_dir = Path(data_dir) / "sessions" if data_dir else None

        if use_redis:
            try:
                import redis
                self._redis_client = redis.from_url(redis_url)
                self._redis_client.ping()
                print("Redis session store connected")
            except ImportError:
                print("redis not installed, falling back to file storage")
                self.use_redis = False
            except Exception as e:
                print(f"Redis connection failed: {e}, falling back to file storage")
                self.use_redis = False

        if not self.use_redis and self._data_dir:
            self._data_dir.mkdir(parents=True, exist_ok=True)

    def _file_path(self, session_id: str) -> Optional[Path]:
        if not self._data_dir:
            return None
        return self._data_dir / f"{session_id}.json"

    def _load_file(self, session_id: str) -> Optional[Dict]:
        path = self._file_path(session_id)
        if not path or not path.is_file():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return None

    def _save_file(self, session_id: str, state: Dict):
        path = self._file_path(session_id)
        if not path:
            return
        path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")

    def _delete_file(self, session_id: str):
        path = self._file_path(session_id)
        if path and path.is_file():
            path.unlink(missing_ok=True)

    def get(self, session_id: str) -> Dict:
        """获取会话 — 优先内存，其次文件，最后返回初始化状态"""
        if self.use_redis:
            data = self._redis_client.get(f"session:{session_id}")
            return json.loads(data) if data else {"status": "init"}
        if session_id in self._sessions:
            return self._sessions[session_id]
        # Try loading from file
        saved = self._load_file(session_id)
        if saved is not None:
            self._sessions[session_id] = saved  # cache in memory
            return saved
        return {"status": "init"}

    def set(self, session_id: str, state: Dict):
        """保存会话 — 同时写入内存和文件"""
        if self.use_redis:
            self._redis_client.setex(f"session:{session_id}", self._ttl, json.dumps(state))
        else:
            self._sessions[session_id] = state
            self._save_file(session_id, state)

    def delete(self, session_id: str):
        """删除会话"""
        if self.use_redis:
            self._redis_client.delete(f"session:{session_id}")
        else:
            self._sessions.pop(session_id, None)
            self._delete_file(session_id)

    def exists(self, session_id: str) -> bool:
        """检查会话是否存在"""
        if self.use_redis:
            return self._redis_client.exists(f"session:{session_id}") > 0
        if session_id in self._sessions:
            return True
        return self._file_path(session_id) is not None and self._file_path(session_id).is_file()

    def update(self, session_id: str, updates: Dict) -> Dict:
        """更新会话"""
        state = self.get(session_id)
        state.update(updates)
        self.set(session_id, state)
        return state

    def get_status(self, session_id: str) -> str:
        """获取会话状态"""
        return self.get(session_id).get("status", "init")
