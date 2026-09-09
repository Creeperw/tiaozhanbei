"""Small shared client for upload extraction, independent of business services."""
from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

import httpx

from competition_app.llm.upload_provider import new_upload_session, upload_provider_headers


class UploadModelError(RuntimeError):
    def __init__(self, message: str, *, invalid_structure: bool = False):
        super().__init__(message)
        self.invalid_structure = invalid_structure


@dataclass(frozen=True)
class UploadModelEndpoint:
    base_url: str
    model: str
    api_key: str = field(repr=False)

    def validate(self) -> None:
        url = urlsplit(self.base_url)
        if (url.scheme not in {"https", "http"} or not url.hostname
                or url.username or url.password or url.query or url.fragment
                or not self.model.strip() or not self.api_key.strip()):
            raise UploadModelError("上传解析模型配置不完整或无效")


def select_upload_endpoint(
    chat: UploadModelEndpoint, *, vision_base_url: str = "",
    vision_model: str = "", vision_api_key: str = "",
) -> UploadModelEndpoint:
    # A default model name alone does not configure a second provider. Never
    # combine another provider's model/key/address with the chat endpoint.
    if vision_base_url or vision_api_key:
        endpoint = UploadModelEndpoint(vision_base_url, vision_model, vision_api_key)
    else:
        endpoint = chat
    endpoint.validate()
    return endpoint


def parse_upload_json(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, list):
        raw = "".join(str(item.get("text") or item.get("content") or "")
                      if isinstance(item, dict) else str(item) for item in raw)
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", str(raw or "").strip(), flags=re.I)
    try:
        try:
            value = json.loads(text)
        except json.JSONDecodeError:
            start, end = text.find("{"), text.rfind("}")
            if start < 0 or end <= start:
                raise ValueError("no object")
            value = json.loads(text[start:end + 1])
        if not isinstance(value, dict):
            raise ValueError("not an object")
        return value
    except (ValueError, TypeError):
        raise UploadModelError("上传解析模型未返回有效 JSON 对象", invalid_structure=True) from None


async def complete_upload_json(
    endpoint: UploadModelEndpoint, *, system: str, user_content: list[dict[str, Any]],
    max_tokens: int, timeout_seconds: float, attempts: int = 1,
    reasoning_effort: str | None = None,
) -> dict[str, Any]:
    endpoint.validate()
    session = new_upload_session()
    headers = {"Authorization": f"Bearer {endpoint.api_key}",
               **upload_provider_headers(endpoint.base_url, session)}
    payload: dict[str, Any] = {
        "model": endpoint.model,
        "messages": [{"role": "system", "content": system},
                     {"role": "user", "content": user_content}],
        "response_format": {"type": "json_object"}, "temperature": 0,
        "max_tokens": max_tokens,
    }
    if reasoning_effort is not None:
        payload["reasoning_effort"] = reasoning_effort
    attempts = max(1, min(attempts, 3))
    timeout = httpx.Timeout(max(timeout_seconds, 600.0), connect=30.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        for attempt in range(attempts):
            try:
                # At most two optional fields can be removed. Keep the same
                # endpoint, model, credential and session on every retry.
                for _ in range(3):
                    response = await client.post(
                        endpoint.base_url.rstrip("/") + "/chat/completions",
                        headers=headers, json=payload,
                    )
                    if response.status_code != 400:
                        break
                    unsupported = next((key for key in ("response_format", "reasoning_effort")
                                        if key in payload and key in response.text.lower()), None)
                    if unsupported is None:
                        break
                    payload.pop(unsupported)
                if response.is_error:
                    error = UploadModelError(f"上传解析模型调用失败（HTTP {response.status_code}，记录：{session}）")
                    if response.status_code not in (408, 429, 500, 502, 503, 504):
                        raise error
                else:
                    try:
                        body = response.json()
                        raw = body["choices"][0]["message"]["content"]
                    except (ValueError, KeyError, IndexError, TypeError):
                        raw = None
                    try:
                        return parse_upload_json(raw)
                    except UploadModelError as exc:
                        error = exc
            except httpx.TransportError:
                error = UploadModelError(f"上传解析模型连接失败或超时（记录：{session}）")
            if attempt + 1 >= attempts:
                raise error
            await asyncio.sleep(2 * (attempt + 1))
    raise UploadModelError("上传解析模型调用未完成")