import os
import json
import logging
from typing import Dict
from APP.backend.config import METADATA_FILE

logger = logging.getLogger(__name__)

# 内存数据库 - Session 已移除，改为 MySQL 存储
# SESSIONS: Dict[str, SessionModel] = {} 

# 文件元数据仍然使用 JSON 文件存储（也可以迁移到 DB，但目前保留）
FILES: Dict[str, Dict] = {} 

if os.path.exists(METADATA_FILE):
    try:
        with open(METADATA_FILE, "r", encoding="utf-8") as f:
            FILES = json.load(f)
    except Exception as e:
        logger.warning("加载文件元数据失败: %s", e)

def save_file_metadata():
    """持久化文件元数据到 JSON"""
    with open(METADATA_FILE, "w", encoding="utf-8") as f:
        json.dump(FILES, f, ensure_ascii=False, indent=2)