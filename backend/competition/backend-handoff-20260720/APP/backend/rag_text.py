from __future__ import annotations

import os
import json
import re
import pickle
import logging
from pathlib import Path
from typing import List, Dict, Optional, Generator

import numpy as np
from APP.backend.config import EmbeddingConfig
from APP.backend.faiss_io import read_faiss_index, write_faiss_index


def __getattr__(name):
    # 惰性加载 faiss：本地禁用 Embedding 时不安装也能跑通 uvicorn。
    if name == "faiss":
        import faiss as _faiss
        globals()["faiss"] = _faiss
        return _faiss
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

Config = EmbeddingConfig()
# 公共知识库沿用历史索引目录，用户个人知识库由 RAGService 按 user_id 分目录管理。
Config.INDEX_DIR = Config.PUBLIC_INDEX_DIR
os.makedirs(Config.INDEX_DIR, exist_ok=True)
os.makedirs(Config.PUBLIC_DATA_SOURCE_PATH, exist_ok=True)
os.makedirs(Config.USER_DATA_ROOT, exist_ok=True)
os.makedirs(Config.USER_INDEX_ROOT, exist_ok=True)

try:
    import pypdf
    HAS_PDF_SUPPORT = True
except ImportError:
    HAS_PDF_SUPPORT = False

logger = logging.getLogger("RAG_System")

class TextCleaner:
    @staticmethod
    def clean(text: str) -> str:
        if not text: return ""
        text = re.sub(r'!\[.*?\]\(.*?\)', '', text)
        text = re.sub(r'\[(.*?)\]\(.*?\)', r'\1', text)
        text = re.sub(r'(\w+)-\n(\w+)', r'\1\2', text)
        text = re.sub(r'\n{3,}', '\n\n', text)
        text = re.sub(r'[ \t]+', ' ', text)
        text = "".join(ch for ch in text if ch.isprintable() or ch == '\n')
        return text.strip()

class TextSplitter:
    def __init__(self, chunk_size: int = 500, chunk_overlap: int = 50):
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap

    def split_text(self, text: str) -> List[str]:
        text = TextCleaner.clean(text)
        if not text: return []
        chunks = []
        start = 0
        text_len = len(text)
        while start < text_len:
            end = min(start + self.chunk_size, text_len)
            chunk = text[start:end].strip()
            if chunk: chunks.append(chunk)
            if end == text_len: break
            start += self.chunk_size - self.chunk_overlap
        return chunks

class VectorDatabase:
    """管理单个文件的微型向量库"""
    def __init__(self, index_path: str, metadata_path: str):
        self.index_path = index_path
        self.metadata_path = metadata_path
        base_path = os.path.splitext(index_path)[0]
        self.vectors_path = base_path + '_vectors.pkl'
        
        self.index: Optional[faiss.Index] = None
        self.metadata: List[Dict] = []
        self.vectors: Optional[np.ndarray] = None
        
        if os.path.exists(index_path) and os.path.exists(metadata_path):
            self.load()

    def load(self):
        try:
            self.index = read_faiss_index(self.index_path)
            self.metadata = []
            with open(self.metadata_path, 'r', encoding='utf-8') as f:
                for line in f:
                    if line.strip(): self.metadata.append(json.loads(line))
            if int(self.index.ntotal) != len(self.metadata):
                raise ValueError(
                    f"vector/metadata count mismatch: {self.index.ntotal} != {len(self.metadata)}"
                )
        except Exception as exc:
            self.index = None; self.metadata = []; self.vectors = None
            logger.exception("Failed to load vector database: %s", self.index_path)
            raise RuntimeError(f"Failed to load vector database: {self.index_path}") from exc

    def save(self):
        if self.index is None: return
        write_faiss_index(self.index, self.index_path)
        with open(self.metadata_path, 'w', encoding='utf-8') as f:
            for item in self.metadata:
                f.write(json.dumps(item, ensure_ascii=False) + '\n')

    def add_entries(self, embeddings: np.ndarray, metadata_list: List[Dict]):
        embeddings = embeddings.astype('float32')
        faiss.normalize_L2(embeddings)
        self.index.add(embeddings)
        self.metadata.extend(metadata_list)

def extract_qa_content(item: Dict) -> Optional[str]:
    if 'messages' in item:
        for msg in item['messages']:
            if msg.get('role') == 'user': return msg.get('content')
    for key in ['question', 'instruction', 'q']:
        if key in item: return item[key]
    return None

def iter_single_file(file_path: Path, text_splitter: TextSplitter) -> Generator[Dict, None, None]:
    """处理单个文件，输出数据块"""
    if not file_path.exists(): return
    suffix = file_path.suffix.lower()
    file_items = []
    
    try:
        if suffix in ['.jsonl', '.json']:
            with open(file_path, 'r', encoding='utf-8') as f:
                if suffix == '.jsonl':
                    lines = f.readlines()
                    data = [json.loads(line) for line in lines if line.strip()]
                else:
                    data = json.load(f)
                    if not isinstance(data, list): data = []
                    
                for item in data:
                    content = extract_qa_content(item)
                    if content:
                        file_items.append({
                            "embed_text": content,
                            "metadata": {"type": "qa", "source": file_path.name, "original": item}
                        })
        elif suffix in ['.txt', '.md', '.pdf']:
            raw_text = ""
            if suffix == '.pdf' and HAS_PDF_SUPPORT:
                reader = pypdf.PdfReader(file_path)
                raw_text = "\n".join([p.extract_text() for p in reader.pages if p.extract_text()])
            else:
                with open(file_path, 'r', encoding='utf-8') as f:
                    raw_text = f.read()
                    
            chunks = text_splitter.split_text(raw_text)
            for i, chunk in enumerate(chunks):
                file_items.append({
                    "embed_text": chunk,
                    "metadata": {"type": "text", "source": file_path.name, "chunk_id": i, "content": chunk}
                })
                
        for item in file_items:
            yield item
            
    except Exception as e:
        logger.error(f"Error parsing {file_path}: {e}")
