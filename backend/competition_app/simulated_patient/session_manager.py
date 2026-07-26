"""
会话管理器 - 支持内存和 Redis 两种模式
"""

import json
from typing import Dict, Optional


class SessionManager:
    """会话管理"""
    
    def __init__(self, use_redis: bool = False, redis_url: Optional[str] = None, ttl: int = 3600):
        self.use_redis = use_redis
        self._ttl = ttl
        self._sessions: Dict[str, Dict] = {}

        if use_redis:
            try:
                import redis
                self._redis_client = redis.from_url(redis_url)
                self._redis_client.ping()
                print("✅ Redis 连接成功")
            except ImportError:
                print("⚠️ redis 库未安装，回退到内存存储")
                self.use_redis = False
                self._sessions = {}
            except Exception as e:
                print(f"⚠️ Redis 连接失败: {e}，回退到内存存储")
                self.use_redis = False
                self._sessions = {}

    def get(self, session_id: str) -> Dict:
        """获取会话"""
        if self.use_redis:
            data = self._redis_client.get(f"session:{session_id}")
            return json.loads(data) if data else {"status": "init"}
        return self._sessions.get(session_id, {"status": "init"})

    def set(self, session_id: str, state: Dict):
        """保存会话"""
        if self.use_redis:
            self._redis_client.setex(f"session:{session_id}", self._ttl, json.dumps(state))
        else:
            self._sessions[session_id] = state

    def delete(self, session_id: str):
        """删除会话"""
        if self.use_redis:
            self._redis_client.delete(f"session:{session_id}")
        elif session_id in self._sessions:
            del self._sessions[session_id]

    def exists(self, session_id: str) -> bool:
        """检查会话是否存在"""
        if self.use_redis:
            return self._redis_client.exists(f"session:{session_id}") > 0
        return session_id in self._sessions

    def update(self, session_id: str, updates: Dict) -> Dict:
        """更新会话"""
        state = self.get(session_id)
        state.update(updates)
        self.set(session_id, state)
        return state

    def get_status(self, session_id: str) -> str:
        """获取会话状态"""
        return self.get(session_id).get("status", "init")