"""Provider metadata for import jobs; never inferred from uploaded content."""
from urllib.parse import urlsplit
from uuid import uuid4


def upload_provider_headers(base_url: str, session: str) -> dict[str, str]:
    if urlsplit(str(base_url)).hostname != "opencode.ai":
        return {}
    return {
        "x-opencode-session": session,
        "User-Agent": "ShizhenTrainingAssistant/1.0 (OpenCode-compatible client)",
    }


def new_upload_session() -> str:
    return "tcm-upload-" + uuid4().hex