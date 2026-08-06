from __future__ import annotations

import json
import re
import shutil
from pathlib import Path
from threading import RLock
from typing import Any, Protocol

from sqlalchemy import Engine, text


def normalize_book_title(value: str) -> str:
    title = str(value or "").strip()
    title = re.sub(r"_clean(?:_identifier)?$", "", title, flags=re.IGNORECASE)
    title = re.sub(r"[《》〈〉\s·、，。；：:（）()\[\]【】]", "", title)
    return title.casefold()


class TextbookPdfAnnotationRepository(Protocol):
    def get_page(self, user_id: str, book_id: str, page_number: int) -> dict[str, Any]: ...

    def save_page(
        self,
        user_id: str,
        book_id: str,
        page_number: int,
        annotations: list[dict[str, Any]],
    ) -> dict[str, Any]: ...

    def get_reading_state(self, user_id: str, book_id: str) -> dict[str, Any]: ...

    def save_reading_state(
        self, user_id: str, book_id: str, page_number: int, zoom: float
    ) -> dict[str, Any]: ...


class InMemoryTextbookPdfAnnotationRepository:
    def __init__(self) -> None:
        self._annotations: dict[tuple[str, str, int], list[dict[str, Any]]] = {}
        self._reading_state: dict[tuple[str, str], dict[str, Any]] = {}
        self._lock = RLock()

    def get_page(self, user_id: str, book_id: str, page_number: int) -> dict[str, Any]:
        with self._lock:
            return {
                "book_id": book_id,
                "page_number": page_number,
                "annotations": json.loads(json.dumps(
                    self._annotations.get((user_id, book_id, page_number), []),
                    ensure_ascii=False,
                )),
            }

    def save_page(
        self,
        user_id: str,
        book_id: str,
        page_number: int,
        annotations: list[dict[str, Any]],
    ) -> dict[str, Any]:
        with self._lock:
            self._annotations[(user_id, book_id, page_number)] = json.loads(
                json.dumps(annotations, ensure_ascii=False)
            )
        return self.get_page(user_id, book_id, page_number)

    def get_reading_state(self, user_id: str, book_id: str) -> dict[str, Any]:
        with self._lock:
            return dict(self._reading_state.get(
                (user_id, book_id),
                {"book_id": book_id, "page_number": 1, "zoom": 1.0},
            ))

    def save_reading_state(
        self, user_id: str, book_id: str, page_number: int, zoom: float
    ) -> dict[str, Any]:
        state = {"book_id": book_id, "page_number": page_number, "zoom": zoom}
        with self._lock:
            self._reading_state[(user_id, book_id)] = state
        return dict(state)


class SqlTextbookPdfAnnotationRepository:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def get_page(self, user_id: str, book_id: str, page_number: int) -> dict[str, Any]:
        with self.engine.connect() as connection:
            row = connection.execute(text(
                "SELECT annotations_json FROM textbook_pdf_annotations "
                "WHERE user_id=:user_id AND book_id=:book_id AND page_number=:page_number"
            ), {
                "user_id": user_id,
                "book_id": book_id,
                "page_number": page_number,
            }).mappings().first()
        annotations = [] if row is None else row["annotations_json"]
        if isinstance(annotations, str):
            annotations = json.loads(annotations)
        return {
            "book_id": book_id,
            "page_number": page_number,
            "annotations": annotations or [],
        }

    def save_page(
        self,
        user_id: str,
        book_id: str,
        page_number: int,
        annotations: list[dict[str, Any]],
    ) -> dict[str, Any]:
        payload = json.dumps(annotations, ensure_ascii=False)
        with self.engine.begin() as connection:
            if self.engine.dialect.name == "sqlite":
                connection.execute(text(
                    "INSERT INTO textbook_pdf_annotations "
                    "(user_id, book_id, page_number, annotations_json, updated_at) "
                    "VALUES (:user_id, :book_id, :page_number, :annotations, CURRENT_TIMESTAMP) "
                    "ON CONFLICT(user_id, book_id, page_number) DO UPDATE SET "
                    "annotations_json=excluded.annotations_json, updated_at=CURRENT_TIMESTAMP"
                ), {"user_id": user_id, "book_id": book_id, "page_number": page_number, "annotations": payload})
            else:
                connection.execute(text(
                    "INSERT INTO textbook_pdf_annotations "
                    "(user_id, book_id, page_number, annotations_json) "
                    "VALUES (:user_id, :book_id, :page_number, :annotations) "
                    "ON DUPLICATE KEY UPDATE annotations_json=VALUES(annotations_json), "
                    "updated_at=CURRENT_TIMESTAMP(6)"
                ), {"user_id": user_id, "book_id": book_id, "page_number": page_number, "annotations": payload})
        return self.get_page(user_id, book_id, page_number)

    def get_reading_state(self, user_id: str, book_id: str) -> dict[str, Any]:
        with self.engine.connect() as connection:
            row = connection.execute(text(
                "SELECT page_number, zoom FROM textbook_pdf_reading_state "
                "WHERE user_id=:user_id AND book_id=:book_id"
            ), {"user_id": user_id, "book_id": book_id}).mappings().first()
        return {
            "book_id": book_id,
            "page_number": int(row["page_number"]) if row else 1,
            "zoom": float(row["zoom"]) if row else 1.0,
        }

    def save_reading_state(
        self, user_id: str, book_id: str, page_number: int, zoom: float
    ) -> dict[str, Any]:
        params = {
            "user_id": user_id,
            "book_id": book_id,
            "page_number": page_number,
            "zoom": zoom,
        }
        with self.engine.begin() as connection:
            if self.engine.dialect.name == "sqlite":
                connection.execute(text(
                    "INSERT INTO textbook_pdf_reading_state "
                    "(user_id, book_id, page_number, zoom, updated_at) "
                    "VALUES (:user_id, :book_id, :page_number, :zoom, CURRENT_TIMESTAMP) "
                    "ON CONFLICT(user_id, book_id) DO UPDATE SET "
                    "page_number=excluded.page_number, zoom=excluded.zoom, updated_at=CURRENT_TIMESTAMP"
                ), params)
            else:
                connection.execute(text(
                    "INSERT INTO textbook_pdf_reading_state "
                    "(user_id, book_id, page_number, zoom) "
                    "VALUES (:user_id, :book_id, :page_number, :zoom) "
                    "ON DUPLICATE KEY UPDATE page_number=VALUES(page_number), "
                    "zoom=VALUES(zoom), updated_at=CURRENT_TIMESTAMP(6)"
                ), params)
        return self.get_reading_state(user_id, book_id)


class TextbookPdfService:
    def __init__(
        self,
        pdf_root: Path,
        catalog_path: Path,
        annotations: TextbookPdfAnnotationRepository,
        uploaded_root: Path | None = None,
    ) -> None:
        self.pdf_root = pdf_root.resolve()
        self.catalog_path = catalog_path
        self.annotations = annotations
        self.uploaded_root = uploaded_root.resolve() if uploaded_root else None
        self._catalog = self._load_catalog()
        # 平台内置教材的 PDF 可用性预计算结果（懒构建一次，避免每次
        # catalog 请求对每本书做跨文件系统 stat —— NTFS 挂载盘上单次
        # stat 可达 5~30ms，94 本累计约 3 秒）。
        self._catalog_available_ids: set[str] | None = None
        # 启动后立即在后台预热，让首个 catalog 请求也能命中缓存。
        import threading

        threading.Thread(
            target=self._available_catalog_ids,
            name="textbook-catalog-availability-warmup",
            daemon=True,
        ).start()

    def _load_catalog(self) -> list[dict[str, Any]]:
        if not self.catalog_path.is_file():
            return []
        payload = json.loads(self.catalog_path.read_text(encoding="utf-8"))
        default_category = str(payload.get("default_category") or "中医药")
        books = list(payload.get("books") or [])
        for item in books:
            if isinstance(item, dict):
                item.setdefault("category", default_category)
        return books

    def _catalog_file_available(self, item: dict[str, Any]) -> bool:
        path = self._item_path(item, "relative_path")
        return bool(path and path.is_file())

    def _available_catalog_ids(self) -> set[str]:
        """Return the cached set of catalog book ids whose PDF file exists.

        The first call performs one pass over the catalog (a few seconds on
        slow cross-filesystem mounts); subsequent calls are O(1) per book.
        Rebuild by calling :meth:`invalidate_availability`.
        """
        cached = self._catalog_available_ids
        if cached is not None:
            return cached
        available = {
            str(item.get("book_id"))
            for item in self._catalog
            if self._catalog_file_available(item)
        }
        self._catalog_available_ids = available
        return available

    def invalidate_availability(self) -> None:
        """Drop the cached availability set (e.g. after catalog edits)."""
        self._catalog_available_ids = None

    def _uploaded_books(self, owner_id: str | None) -> list[dict[str, Any]]:
        if not owner_id or self.uploaded_root is None:
            return []
        owner = re.sub(r"[^0-9A-Za-z_.-]+", "_", str(owner_id))[:96]
        owner_root = self.uploaded_root / owner
        if not owner_root.is_dir():
            return []
        books: list[dict[str, Any]] = []
        for manifest in owner_root.glob("*/manifest.json"):
            try:
                item = json.loads(manifest.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if isinstance(item, dict):
                item["_upload_dir"] = str(manifest.parent.resolve())
                books.append(item)
        books.sort(key=lambda item: str(item.get("created_at") or ""), reverse=True)
        return books

    def _all_books(self, owner_id: str | None = None) -> list[dict[str, Any]]:
        return [*self._uploaded_books(owner_id), *self._catalog]

    def books(self, owner_id: str | None = None) -> list[dict[str, Any]]:
        return [self._public_book(item) for item in self._all_books(owner_id)]

    def resolve(self, book: str, owner_id: str | None = None) -> dict[str, Any] | None:
        target = normalize_book_title(book)
        candidates: list[dict[str, Any]] = []
        for item in self._all_books(owner_id):
            names = [item.get("title", ""), *(item.get("aliases") or [])]
            if target and target in {normalize_book_title(name) for name in names}:
                candidates.append(item)
        if not candidates:
            return None
        candidates.sort(key=lambda item: (0 if item.get("edition") == "十四五" else 1, item.get("title", "")))
        return self._public_book(candidates[0])

    def by_id(self, book_id: str, owner_id: str | None = None) -> dict[str, Any] | None:
        item = next((row for row in self._all_books(owner_id) if row.get("book_id") == book_id), None)
        return self._public_book(item) if item else None

    def delete_uploaded_book(self, book_id: str, owner_id: str | None) -> bool:
        """删除用户上传的教材（仅限本人上传的；平台内置教材不可删除）。"""
        if not owner_id or self.uploaded_root is None:
            return False
        owner = re.sub(r"[^0-9A-Za-z_.-]+", "_", str(owner_id))[:96]
        owner_root = self.uploaded_root / owner
        if not owner_root.is_dir():
            return False
        for manifest in owner_root.glob("*/manifest.json"):
            try:
                item = json.loads(manifest.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if isinstance(item, dict) and item.get("book_id") == book_id:
                target = manifest.parent.resolve()
                if target.parent.resolve() != owner_root.resolve():
                    continue
                shutil.rmtree(target, ignore_errors=True)
                return True
        return False

    def set_uploaded_book_hidden(self, book_id: str, owner_id: str | None, hidden: bool) -> bool:
        """设置用户上传教材的隐藏标记（仅限本人上传的；平台内置教材不可修改）。"""
        if not owner_id or self.uploaded_root is None:
            return False
        owner = re.sub(r"[^0-9A-Za-z_.-]+", "_", str(owner_id))[:96]
        owner_root = self.uploaded_root / owner
        if not owner_root.is_dir():
            return False
        for manifest_path in owner_root.glob("*/manifest.json"):
            try:
                item = json.loads(manifest_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if isinstance(item, dict) and item.get("book_id") == book_id:
                if manifest_path.parent.resolve().parent.resolve() != owner_root.resolve():
                    continue
                item["hidden"] = bool(hidden)
                manifest_path.write_text(
                    json.dumps(item, ensure_ascii=False, indent=2), encoding="utf-8"
                )
                return True
        return False

    def file_path(self, book_id: str, owner_id: str | None = None) -> Path | None:
        item = next((row for row in self._all_books(owner_id) if row.get("book_id") == book_id), None)
        if item is None:
            return None
        path = self._item_path(item, "relative_path")
        if path is None:
            return None
        return path if path.is_file() else None

    def cover_path(self, book_id: str, owner_id: str | None = None) -> Path | None:
        item = next((row for row in self._all_books(owner_id) if row.get("book_id") == book_id), None)
        if item is None or not item.get("_upload_dir"):
            return None
        path = self._item_path(item, "cover_relative_path")
        if path is None:
            return None
        return path if path.is_file() else None

    def _item_path(self, item: dict[str, Any], field: str) -> Path | None:
        upload_dir = item.get("_upload_dir")
        root = Path(str(upload_dir)).resolve() if upload_dir else self.pdf_root
        path = (root / str(item.get(field) or "")).resolve()
        try:
            path.relative_to(root)
        except ValueError:
            return None
        return path

    def _public_book(self, item: dict[str, Any] | None) -> dict[str, Any] | None:
        if item is None:
            return None
        payload = {
            key: value for key, value in item.items()
            if key not in {"relative_path", "cover_relative_path", "_upload_dir", "owner_id"}
        }
        payload.setdefault("category", "中医药")
        upload_dir = item.get("_upload_dir")
        if upload_dir:
            # 用户上传教材数量少，实时检查即可
            path = self._item_path(item, "relative_path")
            available = bool(path and path.is_file())
        else:
            # 平台内置教材走预计算缓存，避免每次请求跨文件系统 stat
            available = str(item.get("book_id")) in self._available_catalog_ids()
        payload["available"] = available
        payload["file_url"] = f"/api/v1/textbooks/pdfs/{item['book_id']}/file" if available else None
        if upload_dir and item.get("cover_relative_path"):
            payload["cover_url"] = f"/api/v1/textbooks/pdfs/{item['book_id']}/cover"
        return payload
