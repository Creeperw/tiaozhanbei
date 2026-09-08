"""Verified endpoint/model capabilities; unrelated providers keep their defaults."""

from urllib.parse import urlsplit


# Verified by frontend Live probe on 2026-09-07. This is a transport capability,
# never a classification of user text or an agent-specific workaround.
STRUCTURED_OUTPUT_MODES = {
    ("https", "opencode.ai", 443, "/zen/go/v1", "deepseek-v4-flash"): "json_object",
}


def structured_output_mode(base_url: str, model: str) -> str:
    url = urlsplit(base_url)
    return STRUCTURED_OUTPUT_MODES.get(
        (url.scheme, url.hostname, url.port or (443 if url.scheme == "https" else 80),
         url.path.rstrip("/"), model),
        "json_schema",
    )