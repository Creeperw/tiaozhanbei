from __future__ import annotations

import json
from datetime import datetime, timezone
from threading import RLock
from typing import Any, Protocol
from uuid import uuid4

from sqlalchemy import Engine, text


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc_datetime(value: Any) -> datetime:
    if isinstance(value, str):
        value = datetime.fromisoformat(value)
    if value is None:
        return _utc_now()
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _json_value(value: Any, fallback: Any) -> Any:
    if value is None:
        return fallback
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return fallback
    return value


class WorkshopLibraryRepository(Protocol):
    def list_folders(self, user_id: str) -> list[dict[str, Any]]: ...

    def create_folder(self, user_id: str, name: str) -> dict[str, Any]: ...

    def delete_folder(self, user_id: str, folder_id: str) -> bool: ...

    def list_favorites(
        self, user_id: str, *, folder_id: str | None = None
    ) -> list[dict[str, Any]]: ...

    def save_favorite(
        self,
        user_id: str,
        *,
        folder_id: str,
        resource_type: str,
        resource_id: str,
        title: str,
        content: dict[str, Any],
        source: str,
    ) -> dict[str, Any]: ...

    def delete_favorite(self, user_id: str, favorite_id: str) -> bool: ...

    def list_note_folders(self, user_id: str) -> list[dict[str, Any]]: ...

    def create_note_folder(self, user_id: str, name: str) -> dict[str, Any]: ...

    def list_notes(
        self,
        user_id: str,
        *,
        source: str | None = None,
        note_type: str | None = None,
        query: str | None = None,
    ) -> list[dict[str, Any]]: ...

    def create_note(
        self,
        user_id: str,
        *,
        title: str,
        content: str,
        note_type: str,
        source: str,
        resource_type: str | None,
        resource_id: str | None,
        context: dict[str, Any],
    ) -> dict[str, Any]: ...

    def update_note(
        self, user_id: str, note_id: str, *, title: str, content: str
    ) -> dict[str, Any] | None: ...

    def delete_note(self, user_id: str, note_id: str) -> bool: ...


class InMemoryWorkshopLibraryRepository:
    def __init__(self) -> None:
        self._folders: dict[str, dict[str, Any]] = {}
        self._favorites: dict[str, dict[str, Any]] = {}
        self._note_folders: dict[str, dict[str, Any]] = {}
        self._notes: dict[str, dict[str, Any]] = {}
        self._lock = RLock()

    @staticmethod
    def _copy(item: dict[str, Any]) -> dict[str, Any]:
        return json.loads(json.dumps(item, ensure_ascii=False, default=str))

    def list_folders(self, user_id: str) -> list[dict[str, Any]]:
        with self._lock:
            folders = [item for item in self._folders.values() if item["user_id"] == user_id]
            favorites = list(self._favorites.values())
            result = []
            for folder in sorted(folders, key=lambda item: item["updated_at"], reverse=True):
                payload = self._copy(folder)
                payload["favorite_count"] = sum(
                    item["user_id"] == user_id and item["folder_id"] == folder["folder_id"]
                    for item in favorites
                )
                result.append(payload)
            return result

    def create_folder(self, user_id: str, name: str) -> dict[str, Any]:
        with self._lock:
            existing = next(
                (
                    item for item in self._folders.values()
                    if item["user_id"] == user_id and item["name"].casefold() == name.casefold()
                ),
                None,
            )
            if existing is not None:
                return self._copy({**existing, "favorite_count": 0})
            now = _utc_now().isoformat()
            folder = {
                "folder_id": f"FOLDER_{uuid4().hex}",
                "user_id": user_id,
                "name": name,
                "created_at": now,
                "updated_at": now,
            }
            self._folders[folder["folder_id"]] = folder
            return self._copy({**folder, "favorite_count": 0})

    def delete_folder(self, user_id: str, folder_id: str) -> bool:
        with self._lock:
            folder = self._folders.get(folder_id)
            if folder is None or folder["user_id"] != user_id:
                return False
            del self._folders[folder_id]
            self._favorites = {
                key: item for key, item in self._favorites.items()
                if not (item["user_id"] == user_id and item["folder_id"] == folder_id)
            }
            return True

    def list_favorites(
        self, user_id: str, *, folder_id: str | None = None
    ) -> list[dict[str, Any]]:
        with self._lock:
            folder_names = {
                item["folder_id"]: item["name"]
                for item in self._folders.values()
                if item["user_id"] == user_id
            }
            result = [
                {**item, "folder_name": folder_names.get(item["folder_id"], "")}
                for item in self._favorites.values()
                if item["user_id"] == user_id
                and (folder_id is None or item["folder_id"] == folder_id)
            ]
            return [self._copy(item) for item in sorted(result, key=lambda item: item["updated_at"], reverse=True)]

    def save_favorite(
        self,
        user_id: str,
        *,
        folder_id: str,
        resource_type: str,
        resource_id: str,
        title: str,
        content: dict[str, Any],
        source: str,
    ) -> dict[str, Any]:
        with self._lock:
            folder = self._folders.get(folder_id)
            if folder is None or folder["user_id"] != user_id:
                raise KeyError("收藏簿不存在")
            existing = next(
                (
                    item for item in self._favorites.values()
                    if item["user_id"] == user_id
                    and item["folder_id"] == folder_id
                    and item["resource_type"] == resource_type
                    and item["resource_id"] == resource_id
                ),
                None,
            )
            now = _utc_now().isoformat()
            favorite = {
                "favorite_id": existing["favorite_id"] if existing else f"FAVORITE_{uuid4().hex}",
                "user_id": user_id,
                "folder_id": folder_id,
                "folder_name": folder["name"],
                "resource_type": resource_type,
                "resource_id": resource_id,
                "title": title,
                "content": content,
                "source": source,
                "created_at": existing["created_at"] if existing else now,
                "updated_at": now,
            }
            self._favorites[favorite["favorite_id"]] = favorite
            folder["updated_at"] = now
            return self._copy(favorite)

    def delete_favorite(self, user_id: str, favorite_id: str) -> bool:
        with self._lock:
            favorite = self._favorites.get(favorite_id)
            if favorite is None or favorite["user_id"] != user_id:
                return False
            del self._favorites[favorite_id]
            return True

    def list_note_folders(self, user_id: str) -> list[dict[str, Any]]:
        with self._lock:
            folders = [
                item for item in self._note_folders.values()
                if item["user_id"] == user_id
            ]
            notes = [item for item in self._notes.values() if item["user_id"] == user_id]
            result = []
            for folder in sorted(
                folders, key=lambda item: item["updated_at"], reverse=True
            ):
                payload = self._copy(folder)
                payload["note_count"] = sum(
                    str(item.get("context", {}).get("notebook") or "默认笔记本")
                    == folder["name"]
                    for item in notes
                )
                result.append(payload)
            return result

    def create_note_folder(self, user_id: str, name: str) -> dict[str, Any]:
        with self._lock:
            existing = next(
                (
                    item for item in self._note_folders.values()
                    if item["user_id"] == user_id
                    and item["name"].casefold() == name.casefold()
                ),
                None,
            )
            if existing is not None:
                note_count = sum(
                    item["user_id"] == user_id
                    and str(item.get("context", {}).get("notebook") or "默认笔记本")
                    == existing["name"]
                    for item in self._notes.values()
                )
                return self._copy({**existing, "note_count": note_count})
            now = _utc_now().isoformat()
            folder = {
                "folder_id": f"NOTEBOOK_{uuid4().hex}",
                "user_id": user_id,
                "name": name,
                "created_at": now,
                "updated_at": now,
            }
            self._note_folders[folder["folder_id"]] = folder
            return self._copy({**folder, "note_count": 0})

    def list_notes(
        self,
        user_id: str,
        *,
        source: str | None = None,
        note_type: str | None = None,
        query: str | None = None,
    ) -> list[dict[str, Any]]:
        normalized_query = (query or "").casefold()
        with self._lock:
            result = [
                item for item in self._notes.values()
                if item["user_id"] == user_id
                and (source is None or item["source"] == source)
                and (note_type is None or item["note_type"] == note_type)
                and (
                    not normalized_query
                    or normalized_query in item["title"].casefold()
                    or normalized_query in item["content"].casefold()
                )
            ]
            return [self._copy(item) for item in sorted(result, key=lambda item: item["updated_at"], reverse=True)]

    def create_note(
        self,
        user_id: str,
        *,
        title: str,
        content: str,
        note_type: str,
        source: str,
        resource_type: str | None,
        resource_id: str | None,
        context: dict[str, Any],
    ) -> dict[str, Any]:
        with self._lock:
            now = _utc_now().isoformat()
            note = {
                "note_id": f"NOTE_{uuid4().hex}",
                "user_id": user_id,
                "title": title,
                "content": content,
                "note_type": note_type,
                "source": source,
                "resource_type": resource_type,
                "resource_id": resource_id,
                "context": context,
                "created_at": now,
                "updated_at": now,
            }
            self._notes[note["note_id"]] = note
            return self._copy(note)

    def update_note(
        self, user_id: str, note_id: str, *, title: str, content: str
    ) -> dict[str, Any] | None:
        with self._lock:
            note = self._notes.get(note_id)
            if note is None or note["user_id"] != user_id:
                return None
            note.update(title=title, content=content, updated_at=_utc_now().isoformat())
            return self._copy(note)

    def delete_note(self, user_id: str, note_id: str) -> bool:
        with self._lock:
            note = self._notes.get(note_id)
            if note is None or note["user_id"] != user_id:
                return False
            del self._notes[note_id]
            return True


class SqlWorkshopLibraryRepository:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    @staticmethod
    def _folder(row: Any) -> dict[str, Any]:
        values = dict(row)
        values["favorite_count"] = int(values.get("favorite_count") or 0)
        values["created_at"] = _as_utc_datetime(values.get("created_at")).isoformat()
        values["updated_at"] = _as_utc_datetime(values.get("updated_at")).isoformat()
        return values

    @staticmethod
    def _note_folder(row: Any) -> dict[str, Any]:
        values = dict(row)
        values["note_count"] = int(values.get("note_count") or 0)
        values["created_at"] = _as_utc_datetime(values.get("created_at")).isoformat()
        values["updated_at"] = _as_utc_datetime(values.get("updated_at")).isoformat()
        return values

    @staticmethod
    def _favorite(row: Any) -> dict[str, Any]:
        values = dict(row)
        values["content"] = _json_value(values.pop("content_json", None), {})
        values["created_at"] = _as_utc_datetime(values.get("created_at")).isoformat()
        values["updated_at"] = _as_utc_datetime(values.get("updated_at")).isoformat()
        return values

    @staticmethod
    def _note(row: Any) -> dict[str, Any]:
        values = dict(row)
        values["context"] = _json_value(values.pop("context_json", None), {})
        values["created_at"] = _as_utc_datetime(values.get("created_at")).isoformat()
        values["updated_at"] = _as_utc_datetime(values.get("updated_at")).isoformat()
        return values

    def list_folders(self, user_id: str) -> list[dict[str, Any]]:
        with self.engine.connect() as connection:
            rows = connection.execute(text(
                "SELECT f.folder_id, f.user_id, f.name, f.created_at, f.updated_at, "
                "COUNT(v.favorite_id) AS favorite_count "
                "FROM workshop_favorite_folders f LEFT JOIN workshop_favorites v "
                "ON v.folder_id=f.folder_id AND v.user_id=f.user_id "
                "WHERE f.user_id=:user_id GROUP BY f.folder_id, f.user_id, f.name, "
                "f.created_at, f.updated_at ORDER BY f.updated_at DESC"
            ), {"user_id": user_id}).mappings().all()
        return [self._folder(row) for row in rows]

    def create_folder(self, user_id: str, name: str) -> dict[str, Any]:
        with self.engine.begin() as connection:
            existing = connection.execute(text(
                "SELECT folder_id FROM workshop_favorite_folders "
                "WHERE user_id=:user_id AND LOWER(name)=LOWER(:name)"
            ), {"user_id": user_id, "name": name}).mappings().first()
            if existing is None:
                folder_id = f"FOLDER_{uuid4().hex}"
                connection.execute(text(
                    "INSERT INTO workshop_favorite_folders (folder_id, user_id, name) "
                    "VALUES (:folder_id, :user_id, :name)"
                ), {"folder_id": folder_id, "user_id": user_id, "name": name})
            else:
                folder_id = existing["folder_id"]
        return next(item for item in self.list_folders(user_id) if item["folder_id"] == folder_id)

    def delete_folder(self, user_id: str, folder_id: str) -> bool:
        with self.engine.begin() as connection:
            connection.execute(text(
                "DELETE FROM workshop_favorites WHERE user_id=:user_id AND folder_id=:folder_id"
            ), {"user_id": user_id, "folder_id": folder_id})
            result = connection.execute(text(
                "DELETE FROM workshop_favorite_folders WHERE user_id=:user_id AND folder_id=:folder_id"
            ), {"user_id": user_id, "folder_id": folder_id})
        return bool(result.rowcount)

    def list_favorites(
        self, user_id: str, *, folder_id: str | None = None
    ) -> list[dict[str, Any]]:
        where = "v.user_id=:user_id"
        params: dict[str, Any] = {"user_id": user_id}
        if folder_id is not None:
            where += " AND v.folder_id=:folder_id"
            params["folder_id"] = folder_id
        with self.engine.connect() as connection:
            rows = connection.execute(text(
                "SELECT v.favorite_id, v.user_id, v.folder_id, f.name AS folder_name, "
                "v.resource_type, v.resource_id, v.title, v.content_json, v.source, "
                "v.created_at, v.updated_at FROM workshop_favorites v "
                "JOIN workshop_favorite_folders f ON f.folder_id=v.folder_id "
                f"WHERE {where} ORDER BY v.updated_at DESC"
            ), params).mappings().all()
        return [self._favorite(row) for row in rows]

    def save_favorite(
        self,
        user_id: str,
        *,
        folder_id: str,
        resource_type: str,
        resource_id: str,
        title: str,
        content: dict[str, Any],
        source: str,
    ) -> dict[str, Any]:
        favorite_id = f"FAVORITE_{uuid4().hex}"
        values = {
            "favorite_id": favorite_id,
            "user_id": user_id,
            "folder_id": folder_id,
            "resource_type": resource_type,
            "resource_id": resource_id,
            "title": title,
            "content_json": json.dumps(content, ensure_ascii=False),
            "source": source,
        }
        with self.engine.begin() as connection:
            folder = connection.execute(text(
                "SELECT folder_id FROM workshop_favorite_folders "
                "WHERE user_id=:user_id AND folder_id=:folder_id"
            ), values).first()
            if folder is None:
                raise KeyError("收藏簿不存在")
            existing = connection.execute(text(
                "SELECT favorite_id FROM workshop_favorites WHERE user_id=:user_id "
                "AND folder_id=:folder_id AND resource_type=:resource_type "
                "AND resource_id=:resource_id"
            ), values).mappings().first()
            if existing is None:
                connection.execute(text(
                    "INSERT INTO workshop_favorites (favorite_id, user_id, folder_id, "
                    "resource_type, resource_id, title, content_json, source) VALUES "
                    "(:favorite_id, :user_id, :folder_id, :resource_type, :resource_id, "
                    ":title, :content_json, :source)"
                ), values)
            else:
                favorite_id = existing["favorite_id"]
                values["favorite_id"] = favorite_id
                connection.execute(text(
                    "UPDATE workshop_favorites SET title=:title, content_json=:content_json, "
                    "source=:source, updated_at=CURRENT_TIMESTAMP WHERE favorite_id=:favorite_id "
                    "AND user_id=:user_id"
                ), values)
        return next(item for item in self.list_favorites(user_id) if item["favorite_id"] == favorite_id)

    def delete_favorite(self, user_id: str, favorite_id: str) -> bool:
        with self.engine.begin() as connection:
            result = connection.execute(text(
                "DELETE FROM workshop_favorites WHERE user_id=:user_id AND favorite_id=:favorite_id"
            ), {"user_id": user_id, "favorite_id": favorite_id})
        return bool(result.rowcount)

    def list_note_folders(self, user_id: str) -> list[dict[str, Any]]:
        with self.engine.connect() as connection:
            rows = connection.execute(text(
                "SELECT folder_id, user_id, name, created_at, updated_at "
                "FROM workshop_note_folders WHERE user_id=:user_id "
                "ORDER BY updated_at DESC"
            ), {"user_id": user_id}).mappings().all()
        counts: dict[str, int] = {}
        for note in self.list_notes(user_id):
            name = str(note.get("context", {}).get("notebook") or "默认笔记本")
            counts[name] = counts.get(name, 0) + 1
        return [
            self._note_folder({**dict(row), "note_count": counts.get(row["name"], 0)})
            for row in rows
        ]

    def create_note_folder(self, user_id: str, name: str) -> dict[str, Any]:
        with self.engine.begin() as connection:
            existing = connection.execute(text(
                "SELECT folder_id FROM workshop_note_folders "
                "WHERE user_id=:user_id AND LOWER(name)=LOWER(:name)"
            ), {"user_id": user_id, "name": name}).mappings().first()
            if existing is None:
                folder_id = f"NOTEBOOK_{uuid4().hex}"
                connection.execute(text(
                    "INSERT INTO workshop_note_folders (folder_id, user_id, name) "
                    "VALUES (:folder_id, :user_id, :name)"
                ), {"folder_id": folder_id, "user_id": user_id, "name": name})
            else:
                folder_id = existing["folder_id"]
        return next(
            item for item in self.list_note_folders(user_id)
            if item["folder_id"] == folder_id
        )

    def list_notes(
        self,
        user_id: str,
        *,
        source: str | None = None,
        note_type: str | None = None,
        query: str | None = None,
    ) -> list[dict[str, Any]]:
        where = ["user_id=:user_id"]
        params: dict[str, Any] = {"user_id": user_id}
        if source:
            where.append("source=:source")
            params["source"] = source
        if note_type:
            where.append("note_type=:note_type")
            params["note_type"] = note_type
        if query:
            where.append("(LOWER(title) LIKE :query OR LOWER(content) LIKE :query)")
            params["query"] = f"%{query.casefold()}%"
        with self.engine.connect() as connection:
            rows = connection.execute(text(
                "SELECT note_id, user_id, title, content, note_type, source, resource_type, "
                "resource_id, context_json, created_at, updated_at FROM workshop_notes WHERE "
                + " AND ".join(where) + " ORDER BY updated_at DESC"
            ), params).mappings().all()
        return [self._note(row) for row in rows]

    def create_note(
        self,
        user_id: str,
        *,
        title: str,
        content: str,
        note_type: str,
        source: str,
        resource_type: str | None,
        resource_id: str | None,
        context: dict[str, Any],
    ) -> dict[str, Any]:
        note_id = f"NOTE_{uuid4().hex}"
        with self.engine.begin() as connection:
            connection.execute(text(
                "INSERT INTO workshop_notes (note_id, user_id, title, content, note_type, "
                "source, resource_type, resource_id, context_json) VALUES (:note_id, :user_id, "
                ":title, :content, :note_type, :source, :resource_type, :resource_id, :context_json)"
            ), {
                "note_id": note_id, "user_id": user_id, "title": title, "content": content,
                "note_type": note_type, "source": source, "resource_type": resource_type,
                "resource_id": resource_id, "context_json": json.dumps(context, ensure_ascii=False),
            })
        return next(item for item in self.list_notes(user_id) if item["note_id"] == note_id)

    def update_note(
        self, user_id: str, note_id: str, *, title: str, content: str
    ) -> dict[str, Any] | None:
        with self.engine.begin() as connection:
            result = connection.execute(text(
                "UPDATE workshop_notes SET title=:title, content=:content, "
                "updated_at=CURRENT_TIMESTAMP WHERE user_id=:user_id AND note_id=:note_id"
            ), {"title": title, "content": content, "user_id": user_id, "note_id": note_id})
        if not result.rowcount:
            return None
        return next(item for item in self.list_notes(user_id) if item["note_id"] == note_id)

    def delete_note(self, user_id: str, note_id: str) -> bool:
        with self.engine.begin() as connection:
            result = connection.execute(text(
                "DELETE FROM workshop_notes WHERE user_id=:user_id AND note_id=:note_id"
            ), {"user_id": user_id, "note_id": note_id})
        return bool(result.rowcount)
