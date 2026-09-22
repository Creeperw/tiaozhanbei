"""Verified endpoint/model capabilities; unrelated providers keep their defaults."""

from urllib.parse import urlsplit


# These entries describe endpoint/model transport capabilities only.  They
# never classify user text or introduce agent-specific routing.
STRUCTURED_OUTPUT_MODES = {
    ("https", "opencode.ai", 443, "/zen/go/v1", "deepseek-v4-flash"): "json_object",
    # DeepSeek's official JSON Output contract documents json_object rather
    # than OpenAI's json_schema/strict envelope.  Production evidence on
    # 2026-09-20 showed the strict request returning 400 immediately before
    # the existing json_object fallback returned 200.  Declare that capability
    # up front so every call avoids the deterministic failed request.
    ("https", "api.deepseek.com", 443, "", "deepseek-flash"): "json_object",
    ("https", "api.deepseek.com", 443, "/v1", "deepseek-flash"): "json_object",
}


def structured_output_mode(base_url: str, model: str) -> str:
    url = urlsplit(base_url)
    return STRUCTURED_OUTPUT_MODES.get(
        (url.scheme, url.hostname, url.port or (443 if url.scheme == "https" else 80),
         url.path.rstrip("/"), model),
        "json_schema",
    )