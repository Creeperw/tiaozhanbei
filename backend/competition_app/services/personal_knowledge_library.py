"""Read-only projection of committed personal knowledge deliveries."""
import hashlib
import json
import re
from pathlib import Path


class PersonalKnowledgeLibrary:
    def __init__(self, runtime_root: Path, owner: str):
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", owner):
            raise ValueError("用户标识无效")
        self.root = Path(runtime_root).resolve()
        self.owner = owner
        self.delivery = self.root / "knowledge_customers" / owner / "TCM_backend_delivery"

    def _file(self, path):
        resolved = path.resolve()
        # Do not follow links into another user's data or public assets.
        allowed = (self.delivery, self.root / "user_vdb" / self.owner)
        if not any(resolved.is_relative_to(base) for base in allowed):
            raise ValueError("个人资料路径无效")
        return resolved

    def _json(self, path, default):
        path = self._file(path)
        return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else default

    def _rows(self, path):
        path = self._file(path)
        if not path.is_file():
            return []
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]

    def _data(self):
        registry = self._rows(self.delivery / "09_ingestion/ingestion_registry.jsonl")
        sources = {row["source_id"]: row for row in registry}
        chunks = [row for row in self._rows(self.delivery / "03_pipeline_chunks/source_chunks.jsonl")
                  if row.get("metadata", {}).get("source_id") in sources]
        return sources, chunks

    @staticmethod
    def _id(source):
        return hashlib.sha256(source.encode()).hexdigest()

    def overview(self):
        sources, chunks = self._data()
        points = self._json(self.delivery / "04_knowledge_points/final_knowledge_points.json", [])
        points = [row.get("kp", row) for row in points]
        files = []
        for source, row in sources.items():
            uids = {chunk["chunk_uid"] for chunk in chunks if chunk["metadata"]["source_id"] == source}
            files.append({"id": self._id(source), "name": row["source_title"],
                          "scope": "personal", "storage": "delivery", "can_delete": True,
                          "source_type": row.get("source_type"), "applied_at": row.get("applied_at"),
                          "chunk_count": len(uids),
                          "knowledge_point_count": sum(bool(uids.intersection(kp.get("raw_content") or [])) for kp in points)})
        vector_root = self.root / "user_vdb" / self.owner / "indexes" / "知识点"
        manifest = self._json(vector_root / "manifest.json", {})
        valid = (manifest.get("owner_id") == self.owner and manifest.get("collection") == "知识点"
                 and self._file(vector_root / "index.faiss").is_file()
                 and self._file(vector_root / "metadata.jsonl").is_file())
        vector_count = int(manifest.get("count", 0)) if valid else 0
        if valid and len(self._rows(vector_root / "metadata.jsonl")) != vector_count:
            valid = False
            vector_count = 0
        return {"files": files, "stats": {
            "total_documents": len(files), "total_chunks": len(chunks),
            "total_knowledge_points": len(points), "total_vectors": vector_count,
            "status": "已建立个人索引" if valid and vector_count == len(points) else "索引待核验" if files else "暂无资料",
            "embedding_model": manifest.get("embedding_model") if valid else None,
            "is_processing": False, "progress": 0, "storage": "delivery",
        }}

    def document(self, document_id):
        sources, chunks = self._data()
        source = next((key for key in sources if self._id(key) == document_id), None)
        if source is None:
            raise KeyError("个人资料不存在")
        return {"id": document_id, "name": sources[source]["source_title"], "scope": "personal",
                "chunks": [{"id": row["chunk_uid"], "title": row.get("kp_Lv2", ""), "text": row.get("text", "")}
                           for row in chunks if row["metadata"]["source_id"] == source]}