import os
import shutil
import threading
import time
import logging
import json
from pathlib import Path
from collections import defaultdict

from APP.backend.rag_text import Config, TextSplitter, VectorDatabase, iter_single_file
from APP.backend.config import EMBEDDING_MODE
from APP.backend.question_index_v2_service import (
    DEFAULT_QUESTION_COLLECTION,
    V2_QUESTION_COLLECTION,
    active_question_index_name,
)

logger = logging.getLogger(__name__)


class RAGUnavailableError(RuntimeError):
    def __init__(self, *, state: str, message: str):
        self.state = state
        self.message = message
        super().__init__(f"RAG {state}: {message}")


def _create_sentence_transformer(model_path: str, device: str):
    """Import the heavy model only after configuration has been validated."""

    from sentence_transformers import SentenceTransformer

    return SentenceTransformer(model_path, device=device)


def _get_faiss():
    """Import FAISS lazily and wire it into rag_text's module globals."""

    import faiss
    import APP.backend.rag_text as rag_text_module

    # VectorDatabase methods refer to their module global.  Module __getattr__
    # cannot satisfy an internal bare-name lookup, so inject it explicitly.
    rag_text_module.faiss = faiss
    return faiss


def _read_active_question_manifest() -> tuple[str, dict]:
    index_root = Path(Config.PUBLIC_INDEX_DIR)
    collection = active_question_index_name(index_root)
    manifest_path = index_root / collection / "index_manifest.json"
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    except (FileNotFoundError, OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(
            f"active question index manifest is unavailable: {collection}"
        ) from exc
    if not isinstance(payload, dict):
        raise RuntimeError(f"active question index manifest is invalid: {collection}")
    return collection, payload


def _model_embedding_dimension(model) -> int:
    getter = getattr(model, "get_sentence_embedding_dimension", None)
    dimension = getter() if callable(getter) else None
    if dimension is not None:
        return int(dimension)
    probe = model.encode(["embedding contract check"], convert_to_numpy=True)
    if getattr(probe, "ndim", 0) != 2 or probe.shape[0] != 1:
        raise RuntimeError(f"embedding model returned invalid probe shape: {getattr(probe, 'shape', None)}")
    return int(probe.shape[1])


def _validate_embedding_contract(model) -> dict:
    collection, manifest = _read_active_question_manifest()
    expected_model = str(getattr(Config, "EMBEDDING_MODEL_ID", "") or "").strip()
    expected_dimensions = int(getattr(Config, "EMBEDDING_DIMENSIONS", 2560))
    manifest_model = str(
        manifest.get("embedding_model") or manifest.get("model_id") or ""
    ).strip()
    manifest_dimensions = int(manifest.get("dimensions") or 0)
    if manifest_model != expected_model:
        raise RuntimeError(
            "active question index model identity mismatch: "
            f"expected {expected_model}, got {manifest_model or 'missing'}"
        )
    if manifest_dimensions != expected_dimensions:
        raise RuntimeError(
            "active question index dimension contract mismatch: "
            f"expected {expected_dimensions}, got {manifest_dimensions}"
        )
    if manifest.get("normalized") is not True:
        raise RuntimeError("active question index must contain normalized vectors")
    actual_dimensions = _model_embedding_dimension(model)
    if actual_dimensions != manifest_dimensions:
        raise RuntimeError(
            "embedding model dimension mismatch: "
            f"expected {manifest_dimensions}, got {actual_dimensions}"
        )
    return {
        "collection": collection,
        "model_id": manifest_model,
        "dimensions": manifest_dimensions,
        "normalized": True,
    }

def import_torch_safe():
    try:
        import torch
        return torch.cuda.is_available()
    except ImportError: return False

class RAGService:
    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        if not cls._instance:
            with cls._lock:
                if not cls._instance:
                    cls._instance = super(RAGService, cls).__new__(cls)
                    cls._instance.initialize()
        return cls._instance

    def initialize(self):
        logger.info("初始化 RAG 服务 (公共/个人知识库微索引模式)")
        self.dbs = {} # 公共知识库：文件名 -> VectorDatabase
        self.user_dbs = defaultdict(dict) # 个人知识库：user_id -> 文件名 -> VectorDatabase
        # 状态管理
        self.is_processing = False
        self.current_progress = 0
        self.current_status = "ready"
        self.embedding_state = "initializing"
        self.embedding_error = None
        self.embedding_contract = None
        self.question_index_error = None
        self._active_question_collection = None
        self._question_reload_lock = threading.RLock()
        self._metadata_count_cache = {}
        # 禁用模式：不加载 Embedding 模型，检索/构建直接返回空。
        if EMBEDDING_MODE != "enabled":
            logger.info("Embedding/RAG 已禁用（EMBEDDING_MODE != enabled），跳过模型加载")
            self.device = 'cpu'
            self.model = None
            self.embedding_state = "disabled"
            self.current_status = "disabled"
            return
        model_path = str(getattr(Config, "EMBEDDING_MODEL_PATH", "") or "").strip()
        if not model_path or not Path(model_path).is_dir():
            self.device = 'cpu'
            self.model = None
            self.embedding_state = "misconfigured"
            self.embedding_error = (
                "EMBEDDING_MODE=enabled requires EMBEDDING_MODEL_PATH to point "
                "to a compatible local Qwen/Qwen3-Embedding-4B model directory"
            )
            self.current_status = "misconfigured"
            logger.error(self.embedding_error)
            return
        self.device = 'cuda' if import_torch_safe() else 'cpu'
        try:
            self.model = _create_sentence_transformer(model_path, self.device)
            self.embedding_contract = _validate_embedding_contract(self.model)
            self.load_all_dbs()
        except Exception as exc:
            self.model = None
            self.embedding_state = "unavailable"
            self.embedding_error = f"embedding runtime unavailable: {exc}"
            self.current_status = "unavailable"
            logger.exception("Failed to initialize embedding runtime")
            return
        self.embedding_state = "ready"
        self.current_status = "ready"

    def _safe_filename(self, filename: str) -> str:
        name = os.path.basename(filename or "").strip()
        if not name or name in {".", ".."}:
            raise ValueError("Invalid filename")
        return name

    def _paths_for_scope(self, scope: str = "public", user_id: int | None = None) -> tuple[str, str]:
        if scope == "personal":
            if not user_id:
                raise ValueError("Personal knowledge scope requires user_id")
            data_dir = os.path.join(Config.USER_DATA_ROOT, str(user_id))
            index_dir = os.path.join(Config.USER_INDEX_ROOT, str(user_id))
        else:
            data_dir = Config.PUBLIC_DATA_SOURCE_PATH
            index_dir = Config.PUBLIC_INDEX_DIR
        os.makedirs(data_dir, exist_ok=True)
        os.makedirs(index_dir, exist_ok=True)
        return data_dir, index_dir

    def _db_map_for_scope(self, scope: str = "public", user_id: int | None = None) -> dict:
        if scope == "personal":
            if not user_id:
                raise ValueError("Personal knowledge scope requires user_id")
            return self.user_dbs[int(user_id)]
        return self.dbs

    def load_all_dbs(self):
        """扫描本地，将各文件的微向量库加载入内存"""
        _get_faiss()
        self._load_scope_dbs("public")
        if os.path.exists(Config.USER_INDEX_ROOT):
            for user_id in os.listdir(Config.USER_INDEX_ROOT):
                if user_id.isdigit():
                    self._load_scope_dbs("personal", int(user_id))

    def _load_scope_dbs(self, scope: str = "public", user_id: int | None = None):
        _, index_dir = self._paths_for_scope(scope, user_id)
        db_map = self._db_map_for_scope(scope, user_id)
        if not os.path.exists(index_dir): return
        active_question_collection = (
            active_question_index_name(index_dir) if scope == "public" else None
        )
        for filename in os.listdir(index_dir):
            if (
                scope == "public"
                and filename
                in {
                    DEFAULT_QUESTION_COLLECTION,
                    V2_QUESTION_COLLECTION,
                    active_question_collection,
                }
                and filename != active_question_collection
            ):
                continue
            db_dir = os.path.join(index_dir, filename)
            if os.path.isdir(db_dir):
                idx_path = os.path.join(db_dir, "index.faiss")
                meta_path = os.path.join(db_dir, "metadata.jsonl")
                if os.path.exists(idx_path):
                    db_map[filename] = VectorDatabase(idx_path, meta_path)
        if scope == "public" and active_question_collection in db_map:
            self._active_question_collection = active_question_collection

    def ensure_active_question_db(self) -> str:
        """Hot-swap the in-memory question DB after an atomic pointer change."""

        active = active_question_index_name(Config.PUBLIC_INDEX_DIR)
        if (
            active == getattr(self, "_active_question_collection", None)
            and active in self.dbs
        ):
            return active
        lock = getattr(self, "_question_reload_lock", None)
        if lock is None:
            lock = threading.RLock()
            self._question_reload_lock = lock
        with lock:
            active = active_question_index_name(Config.PUBLIC_INDEX_DIR)
            if (
                active == getattr(self, "_active_question_collection", None)
                and active in self.dbs
            ):
                return active
            target = Path(Config.PUBLIC_INDEX_DIR) / active
            try:
                candidate = VectorDatabase(
                    str(target / "index.faiss"),
                    str(target / "metadata.jsonl"),
                )
                if candidate.index is None:
                    raise RuntimeError("FAISS index was not loaded")
                if int(candidate.index.ntotal) != len(candidate.metadata):
                    raise RuntimeError(
                        "FAISS/metadata count mismatch: "
                        f"{candidate.index.ntotal}/{len(candidate.metadata)}"
                    )
                contract = getattr(self, "embedding_contract", None) or {}
                expected_dimensions = int(
                    contract.get("dimensions")
                    or getattr(Config, "EMBEDDING_DIMENSIONS", 2560)
                )
                if int(candidate.index.d) != expected_dimensions:
                    raise RuntimeError(
                        "FAISS dimension mismatch: "
                        f"expected {expected_dimensions}, got {candidate.index.d}"
                    )
            except Exception as exc:
                self.question_index_error = f"active question index reload failed: {exc}"
                raise RAGUnavailableError(
                    state="unavailable",
                    message=self.question_index_error,
                ) from exc

            previous = getattr(self, "_active_question_collection", None)
            next_databases = dict(self.dbs)
            for collection in {
                DEFAULT_QUESTION_COLLECTION,
                V2_QUESTION_COLLECTION,
                previous,
            }:
                if collection and collection != active:
                    next_databases.pop(collection, None)
            next_databases[active] = candidate
            self.dbs = next_databases
            self._active_question_collection = active
            self.question_index_error = None
            return active

    def get_stats(self, scope: str = "all", user_id: int | None = None):
        if self.model is not None and scope in {"all", "public"}:
            try:
                self.ensure_active_question_db()
            except RAGUnavailableError:
                pass
        maps = []
        if scope in {"all", "public"}:
            maps.append(self.dbs)
        if scope in {"all", "personal"} and user_id:
            maps.append(self.user_dbs[int(user_id)])
        total_documents = sum(len(db_map) for db_map in maps)
        total_chunks = sum(len(db.metadata) for db_map in maps for db in db_map.values())
        return {
            "total_documents": total_documents,
            "total_chunks": total_chunks,
            "embedding_model": getattr(Config, "EMBEDDING_MODEL_ID", Config.EMBEDDING_MODEL),
            "embedding_model_path": getattr(Config, "EMBEDDING_MODEL_PATH", ""),
            "embedding_state": self.embedding_state,
            "embedding_error": self.embedding_error,
            "question_index_error": getattr(self, "question_index_error", None),
            "is_processing": self.is_processing,
            "status": self.current_status,
            "progress": self.current_progress
        }

    def list_files(self, scope: str = "all", user_id: int | None = None):
        files = []
        if scope in {"all", "public"}:
            data_dir, _ = self._paths_for_scope("public")
            if os.path.exists(data_dir):
                for name in sorted(os.listdir(data_dir)):
                    if not name.startswith('.') and os.path.isfile(os.path.join(data_dir, name)):
                        files.append({"name": name, "scope": "public", "can_delete": False})
        if scope in {"all", "personal"} and user_id:
            data_dir, _ = self._paths_for_scope("personal", user_id)
            if os.path.exists(data_dir):
                for name in sorted(os.listdir(data_dir)):
                    if not name.startswith('.') and os.path.isfile(os.path.join(data_dir, name)):
                        files.append({"name": name, "scope": "personal", "can_delete": True})
        return files

    def _metadata_count(self, path: Path) -> int:
        if not hasattr(self, "_metadata_count_cache"):
            self._metadata_count_cache = {}
        if not path.is_file():
            return 0
        stat = path.stat()
        signature = (stat.st_size, stat.st_mtime_ns)
        cached = self._metadata_count_cache.get(str(path))
        if cached and cached[0] == signature:
            return cached[1]
        with path.open("rb") as handle:
            count = sum(1 for line in handle if line.strip())
        self._metadata_count_cache[str(path)] = (signature, count)
        return count

    def _catalog_for_scope(self, scope: str, user_id: int | None = None) -> tuple[list[dict], list[dict], list[dict]]:
        data_dir, index_dir = self._paths_for_scope(scope, user_id)
        active_question_collection = (
            active_question_index_name(index_dir) if scope == "public" else None
        )
        documents = [
            {
                "name": path.name,
                "scope": scope,
                "bytes": path.stat().st_size,
                "kind": "document",
            }
            for path in sorted(Path(data_dir).iterdir(), key=lambda item: item.name)
            if path.is_file() and not path.name.startswith(".")
        ]
        datasets = [
            {
                "name": path.name,
                "scope": scope,
                "kind": "dataset",
                "available": True,
            }
            for path in sorted(Path(data_dir).iterdir(), key=lambda item: item.name)
            if path.is_dir() and not path.name.startswith(".")
        ]
        db_map = self._db_map_for_scope(scope, user_id)
        indexes = []
        for path in sorted(Path(index_dir).iterdir(), key=lambda item: item.name):
            if not path.is_dir() or path.name.startswith("."):
                continue
            manifest_path = path / "index_manifest.json"
            manifest = {}
            if manifest_path.is_file():
                try:
                    parsed = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
                    manifest = parsed if isinstance(parsed, dict) else {}
                except (OSError, json.JSONDecodeError):
                    manifest = {"valid": False}
            metadata_path = path / "metadata.jsonl"
            count = manifest.get("count") or manifest.get("vector_count") or manifest.get("total")
            if count is None:
                count = self._metadata_count(metadata_path)
            indexes.append({
                "name": path.name,
                "scope": scope,
                "kind": "faiss",
                "available": (path / "index.faiss").is_file(),
                "loaded": path.name in db_map,
                "count": int(count or 0),
                "embedding_model": manifest.get("embedding_model") or manifest.get("model_id") or "",
                "dimensions": manifest.get("dimensions") or manifest.get("dimension"),
                "normalized": manifest.get("normalized"),
                "active": path.name == active_question_collection,
                "manifest": manifest,
            })
        return documents, datasets, indexes

    def get_catalog(self, scope: str = "all", user_id: int | None = None):
        documents: list[dict] = []
        datasets: list[dict] = []
        indexes: list[dict] = []
        if scope in {"all", "public"}:
            parts = self._catalog_for_scope("public")
            documents.extend(parts[0]); datasets.extend(parts[1]); indexes.extend(parts[2])
        if scope in {"all", "personal"} and user_id:
            parts = self._catalog_for_scope("personal", user_id)
            documents.extend(parts[0]); datasets.extend(parts[1]); indexes.extend(parts[2])
        return {
            "documents": documents,
            "datasets": datasets,
            "indexes": indexes,
            "embedding": {
                "state": self.embedding_state,
                "error": self.embedding_error,
                "model_id": getattr(Config, "EMBEDDING_MODEL_ID", Config.EMBEDDING_MODEL),
            },
        }

    def search(self, query: str, top_k: int = 5, similarity_threshold: float | None = None, user_id: int | None = None, include_public: bool = True, include_personal: bool = True):
        if self.model is None:
            raise RAGUnavailableError(
                state=self.embedding_state,
                message=self.embedding_error
                or (
                    "embedding retrieval is disabled by EMBEDDING_MODE"
                    if self.embedding_state == "disabled"
                    else "embedding model is unavailable"
                ),
            )
        question_collections = {
            DEFAULT_QUESTION_COLLECTION,
            V2_QUESTION_COLLECTION,
            getattr(self, "_active_question_collection", None),
        }
        if include_public:
            desired_question_collection = active_question_index_name(Config.PUBLIC_INDEX_DIR)
            question_collections.add(desired_question_collection)
            try:
                question_collections.add(self.ensure_active_question_db())
            except RAGUnavailableError as exc:
                # Document RAG and the dedicated question index are independent.
                # Keep the question failure explicit in stats, but never let a
                # broken hot-swap suppress otherwise healthy document evidence.
                self.question_index_error = exc.message
        question_collections.discard(None)
        db_sources = []
        if include_public:
            db_sources.extend(
                ("public", filename, db)
                for filename, db in self.dbs.items()
                if filename not in question_collections
            )
        if include_personal and user_id:
            db_sources.extend(("personal", filename, db) for filename, db in self.user_dbs[int(user_id)].items())
        if not db_sources: return []
        threshold = Config.SIMILARITY_THRESHOLD if similarity_threshold is None else similarity_threshold
        q_vec = self.model.encode([query], convert_to_numpy=True).astype('float32')
        _get_faiss().normalize_L2(q_vec)
        
        all_results = []
        # 遍历所有小微向量库
        for scope, filename, db in db_sources:
            if db.index is None or db.index.ntotal == 0: continue
            
            k = min(top_k, db.index.ntotal)
            scores, indices = db.index.search(q_vec, k)
            
            for score, idx in zip(scores[0], indices[0]):
                if idx == -1 or idx >= len(db.metadata): continue
                if float(score) < threshold: continue
                meta = db.metadata[idx]
                content = ""
                if meta['type'] == 'qa':
                    orig = meta.get('original', {})
                    if 'messages' in orig:
                        for msg in orig['messages']:
                            if msg.get('role') == 'assistant': content = msg['content']
                    elif 'answer' in orig: content = orig['answer']
                else:
                    content = meta.get('content', "")
                    
                all_results.append({
                    "score": float(score), 
                    "content": content, 
                    "source": filename,
                    "scope": scope,
                    "type": meta.get('type', 'text')
                })
                
        # 全局按照相似度得分排序并截取Top K
        all_results.sort(key=lambda x: x['score'], reverse=True)
        return all_results[:top_k]

    def rebuild_index(self, scope: str = "public", user_id: int | None = None):
        if self.is_processing: return
        thread = threading.Thread(target=self._process_build, args=(scope, user_id))
        thread.start()

    def _process_build(self, scope: str = "public", user_id: int | None = None):
        self.is_processing = True
        self.current_progress = 0
        if self.model is None:
            self.current_status = "Embedding 已禁用，无法构建索引"
            self.is_processing = False
            return
        try:
            data_dir, index_dir = self._paths_for_scope(scope, user_id)
            db_map = self._db_map_for_scope(scope, user_id)
            splitter = TextSplitter(Config.CHUNK_SIZE, Config.CHUNK_OVERLAP)
            files_to_process = []
            
            # 增量扫描：只处理不在内存中的新文件
            for f in os.listdir(data_dir):
                if f.startswith('.'): continue
                if os.path.isfile(os.path.join(data_dir, f)) and f not in db_map:
                    files_to_process.append(f)
                    
            if not files_to_process:
                self.current_status = "所有文件已构建完毕"
                self.current_progress = 100
                return
                
            total = len(files_to_process)
            for i, filename in enumerate(files_to_process):
                self.current_status = f"正在向量化: {filename} ({i+1}/{total})"
                file_path = Path(data_dir) / filename
                
                # 为该文件创建专属目录
                db_dir = os.path.join(index_dir, filename)
                os.makedirs(db_dir, exist_ok=True)
                db = VectorDatabase(os.path.join(db_dir, "index.faiss"), os.path.join(db_dir, "metadata.jsonl"))
                
                items = list(iter_single_file(file_path, splitter))
                if items:
                    batch_texts = [item['embed_text'] for item in items]
                    batch_metas = [item['metadata'] for item in items]
                    embs = self.model.encode(batch_texts, convert_to_numpy=True)
                    
                    if db.index is None:
                        db.index = _get_faiss().IndexFlatIP(embs.shape[1])
                    db.add_entries(embs, batch_metas)
                    db.save()
                    db_map[filename] = db
                    
                self.current_progress = int(((i + 1) / total) * 100)
                
            self.current_status = "处理完成"
        except Exception as e:
            self.current_status = f"构建错误: {str(e)}"
        finally:
            time.sleep(1)
            self.is_processing = False

    def delete_file(self, filename: str, scope: str = "public", user_id: int | None = None):
        """删除物理文件、向量库缓存和内存实例"""
        filename = self._safe_filename(filename)
        data_dir, index_dir = self._paths_for_scope(scope, user_id)
        db_map = self._db_map_for_scope(scope, user_id)
        # 1. 删除源文件
        src = os.path.join(data_dir, filename)
        if os.path.exists(src): os.remove(src)
        # 2. 删除微向量库文件夹
        db_dir = os.path.join(index_dir, filename)
        if os.path.exists(db_dir): shutil.rmtree(db_dir)
        # 3. 从内存移除
        if filename in db_map: del db_map[filename]

class LazyRAGService:
    """按需初始化 RAG 服务，避免 FastAPI 启动时立即加载 embedding 模型。"""

    def __init__(self):
        self._service = None
        self._lock = threading.Lock()

    def _get_service(self) -> RAGService:
        if self._service is None:
            with self._lock:
                if self._service is None:
                    self._service = RAGService()
        return self._service

    def __getattr__(self, name: str):
        return getattr(self._get_service(), name)


rag_service = LazyRAGService()
