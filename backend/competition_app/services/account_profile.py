from __future__ import annotations

import os
from datetime import date
from pathlib import Path
from uuid import uuid4

from competition_app.contracts.auth import (
    AccountProfile,
    AccountProfileUpdateRequest,
    AuthUser,
)
from competition_app.repositories.account_profile import AccountProfileRepository
from competition_app.repositories.auth import AuthRepository


class AccountProfileService:
    _allowed_image_formats = {"JPEG": ("jpg", "image/jpeg"), "PNG": ("png", "image/png"), "WEBP": ("webp", "image/webp")}
    _max_avatar_bytes = 1 * 1024 * 1024
    _max_avatar_pixels = 16_000_000

    def __init__(
        self,
        profile_repository: AccountProfileRepository,
        auth_repository: AuthRepository,
        avatar_root: Path,
    ) -> None:
        self.profile_repository = profile_repository
        self.auth_repository = auth_repository
        self.avatar_root = avatar_root

    def get_profile(self, user: AuthUser) -> AccountProfile:
        return self.profile_repository.get(user.user_id, user.display_name)

    def update_profile(
        self, user: AuthUser, update: AccountProfileUpdateRequest
    ) -> tuple[AuthUser, AccountProfile]:
        if update.birth_date is not None and update.birth_date > date.today():
            raise ValueError("出生日期不能晚于今天")
        updated_user = self.auth_repository.set_display_name(
            user.user_id, update.display_name
        )
        if updated_user is None:
            raise LookupError("用户不存在")
        existing = self.profile_repository.get(user.user_id, updated_user.display_name)
        profile = self.profile_repository.save(
            existing.model_copy(
                update={
                    "display_name": updated_user.display_name,
                    "gender": update.gender,
                    "birth_date": update.birth_date,
                    "region": update.region,
                    "contact_email": update.contact_email,
                    "signature": update.signature,
                }
            )
        )
        public_user = AuthUser.model_validate(
            updated_user.model_dump(
                include={
                    "user_id",
                    "username",
                    "display_name",
                    "role",
                    "status",
                    "onboarding_required",
                    "created_at",
                }
            )
        )
        return public_user, profile

    def update_avatar(
        self, user: AuthUser, content: bytes
    ) -> AccountProfile:
        if not content:
            raise ValueError("请选择头像图片")
        if len(content) > self._max_avatar_bytes:
            raise ValueError("头像图片不能超过 1 MB")
        image_format, width, height = self._image_metadata(content)
        if image_format not in self._allowed_image_formats:
            raise ValueError("仅支持 JPEG、PNG 或 WebP 格式的头像")
        if width <= 0 or height <= 0:
            raise ValueError("头像图片无效")
        if width * height > self._max_avatar_pixels:
            raise ValueError("头像图片尺寸过大")

        extension, _ = self._allowed_image_formats[image_format]
        self.avatar_root.mkdir(parents=True, exist_ok=True)
        profile = self.profile_repository.get(user.user_id, user.display_name)
        avatar_key = f"{user.user_id}-{uuid4().hex}.{extension}"
        target = self.avatar_root / avatar_key
        target.write_bytes(content)
        previous_key = profile.avatar_key
        updated = self.profile_repository.save(
            profile.model_copy(
                update={
                    "avatar_key": avatar_key,
                    "avatar_version": profile.avatar_version + 1,
                }
            )
        )
        if previous_key and previous_key != avatar_key:
            previous = self.avatar_root / Path(previous_key).name
            if previous.is_file():
                try:
                    os.remove(previous)
                except OSError:
                    pass
        return updated

    @staticmethod
    def _image_metadata(content: bytes) -> tuple[str, int, int]:
        if content.startswith(b"\x89PNG\r\n\x1a\n") and len(content) >= 24:
            return "PNG", int.from_bytes(content[16:20], "big"), int.from_bytes(content[20:24], "big")
        if content.startswith(b"RIFF") and content[8:12] == b"WEBP" and len(content) >= 30:
            kind = content[12:16]
            if kind == b"VP8X" and len(content) >= 30:
                return "WEBP", int.from_bytes(content[24:27], "little") + 1, int.from_bytes(content[27:30], "little") + 1
            if kind == b"VP8 " and len(content) >= 30 and content[23:26] == b"\x9d\x01\x2a":
                return "WEBP", int.from_bytes(content[26:28], "little") & 0x3FFF, int.from_bytes(content[28:30], "little") & 0x3FFF
            if kind == b"VP8L" and len(content) >= 25 and content[20] == 0x2F:
                bits = int.from_bytes(content[21:25], "little")
                return "WEBP", (bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1
        if content.startswith(b"\xff\xd8"):
            offset = 2
            while offset + 9 < len(content):
                if content[offset] != 0xFF:
                    offset += 1
                    continue
                marker = content[offset + 1]
                offset += 2
                while marker == 0xFF and offset < len(content):
                    marker = content[offset]
                    offset += 1
                if marker in {0xD8, 0xD9}:
                    continue
                if offset + 2 > len(content):
                    break
                length = int.from_bytes(content[offset:offset + 2], "big")
                if length < 2 or offset + length > len(content):
                    break
                if marker in {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF} and length >= 7:
                    return "JPEG", int.from_bytes(content[offset + 5:offset + 7], "big"), int.from_bytes(content[offset + 3:offset + 5], "big")
                offset += length
        raise ValueError("请上传 JPEG、PNG 或 WebP 图片")

    def avatar_file(self, user: AuthUser) -> tuple[Path, str] | None:
        profile = self.profile_repository.get(user.user_id, user.display_name)
        if not profile.avatar_key:
            return None
        path = self.avatar_root / Path(profile.avatar_key).name
        if not path.is_file():
            return None
        suffix = path.suffix.lower()
        media_type = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp"}.get(suffix, "application/octet-stream")
        return path, media_type