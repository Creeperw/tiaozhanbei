from __future__ import annotations

from typing import Any

from competition_app.repositories.workshop_library import WorkshopLibraryRepository


class WorkshopLibraryService:
    def __init__(self, repository: WorkshopLibraryRepository) -> None:
        self.repository = repository

    @staticmethod
    def _required(value: str, label: str, *, limit: int) -> str:
        normalized = str(value or "").strip()
        if not normalized:
            raise ValueError(f"{label}不能为空")
        if len(normalized) > limit:
            raise ValueError(f"{label}不能超过 {limit} 个字符")
        return normalized

    def list_folders(self, user_id: str) -> list[dict[str, Any]]:
        return self.repository.list_folders(user_id)

    def create_folder(self, user_id: str, name: str) -> dict[str, Any]:
        return self.repository.create_folder(
            user_id, self._required(name, "收藏簿名称", limit=80)
        )

    def delete_folder(self, user_id: str, folder_id: str) -> bool:
        return self.repository.delete_folder(user_id, folder_id)

    def list_favorites(
        self, user_id: str, *, folder_id: str | None = None
    ) -> list[dict[str, Any]]:
        return self.repository.list_favorites(user_id, folder_id=folder_id)

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
        return self.repository.save_favorite(
            user_id,
            folder_id=self._required(folder_id, "收藏簿", limit=128),
            resource_type=self._required(resource_type, "资源类型", limit=32),
            resource_id=self._required(resource_id, "资源标识", limit=255),
            title=self._required(title, "收藏标题", limit=500),
            content=dict(content or {}),
            source=self._required(source, "收藏来源", limit=128),
        )

    def delete_favorite(self, user_id: str, favorite_id: str) -> bool:
        return self.repository.delete_favorite(user_id, favorite_id)

    def list_note_folders(self, user_id: str) -> list[dict[str, Any]]:
        return self.repository.list_note_folders(user_id)

    def create_note_folder(self, user_id: str, name: str) -> dict[str, Any]:
        return self.repository.create_note_folder(
            user_id, self._required(name, "笔记本名称", limit=80)
        )

    def list_notes(
        self,
        user_id: str,
        *,
        source: str | None = None,
        note_type: str | None = None,
        query: str | None = None,
    ) -> list[dict[str, Any]]:
        return self.repository.list_notes(
            user_id,
            source=str(source).strip() if source else None,
            note_type=str(note_type).strip() if note_type else None,
            query=str(query).strip() if query else None,
        )

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
        context_payload = dict(context or {})
        notebook = self._required(
            str(context_payload.get("notebook") or "默认笔记本"),
            "笔记本名称",
            limit=80,
        )
        self.repository.create_note_folder(user_id, notebook)
        context_payload["notebook"] = notebook
        return self.repository.create_note(
            user_id,
            title=self._required(title, "笔记标题", limit=200),
            content=self._required(content, "笔记内容", limit=20_000),
            note_type=self._required(note_type, "笔记类型", limit=64),
            source=self._required(source, "笔记来源", limit=128),
            resource_type=str(resource_type or "").strip() or None,
            resource_id=str(resource_id or "").strip() or None,
            context=context_payload,
        )

    def update_note(
        self, user_id: str, note_id: str, *, title: str, content: str
    ) -> dict[str, Any] | None:
        return self.repository.update_note(
            user_id,
            note_id,
            title=self._required(title, "笔记标题", limit=200),
            content=self._required(content, "笔记内容", limit=20_000),
        )

    def delete_note(self, user_id: str, note_id: str) -> bool:
        return self.repository.delete_note(user_id, note_id)
