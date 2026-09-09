import json

import httpx
import pytest

from competition_app.llm import upload_json_client as upload
from competition_app.services.textbook_import import TextbookImportError, TextbookImportService
from competition_app.services.user_syllabus import UserSyllabusError, UserSyllabusService


def install_transport(monkeypatch, handler):
    original = httpx.AsyncClient
    monkeypatch.setattr(upload.httpx, "AsyncClient", lambda **kw: original(
        transport=httpx.MockTransport(handler), **kw,
    ))
    async def no_delay(_):
        pass
    monkeypatch.setattr(upload.asyncio, "sleep", no_delay)


def success(content='{"ok":true}'):
    return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})


async def complete(**kwargs):
    return await upload.complete_upload_json(
        upload.UploadModelEndpoint("https://opencode.ai/zen/go/v1", "test-model", "secret"),
        system="test", user_content=[{"type": "text", "text": "test"}],
        max_tokens=100, timeout_seconds=60, **kwargs,
    )


@pytest.mark.asyncio
async def test_optional_parameters_retry_same_session_and_endpoint(monkeypatch):
    calls = []
    def handler(request):
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(400, text="response_format unsupported")
        if len(calls) == 2:
            return httpx.Response(400, text="reasoning_effort unsupported")
        return success()
    install_transport(monkeypatch, handler)
    assert await complete(reasoning_effort="none") == {"ok": True}
    assert len(calls) == 3
    assert len({r.headers["x-opencode-session"] for r in calls}) == 1
    assert len({str(r.url) for r in calls}) == 1
    assert {json.loads(r.content)["model"] for r in calls} == {"test-model"}
    assert "response_format" not in json.loads(calls[-1].content)
    assert "reasoning_effort" not in json.loads(calls[-1].content)
    await complete()
    assert calls[-1].headers["x-opencode-session"] != calls[0].headers["x-opencode-session"]


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [400, 401, 403])
async def test_permanent_failure_no_retry_or_sensitive_body(monkeypatch, status):
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(status, text="secret private-document")
    install_transport(monkeypatch, handler)
    with pytest.raises(upload.UploadModelError) as caught:
        await complete(attempts=3)
    assert len(calls) == 1
    assert f"HTTP {status}" in str(caught.value)
    assert "secret" not in str(caught.value) and "private" not in str(caught.value)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["transient", "transport", "invalid", "empty", "bad_envelope"])
async def test_transient_and_invalid_response_retry(monkeypatch, failure):
    calls = []
    def handler(request):
        calls.append(request)
        if len(calls) == 1:
            if failure == "transient":
                return httpx.Response(503, text="secret")
            if failure == "transport":
                raise httpx.ConnectError("secret", request=request)
            if failure == "bad_envelope":
                return httpx.Response(200, json={"choices": None})
            return success("" if failure == "empty" else "{secret broken}")
        return success()
    install_transport(monkeypatch, handler)
    assert await complete(attempts=3) == {"ok": True}
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_invalid_json_exhaustion_is_safe(monkeypatch):
    calls = []
    def handler(request):
        calls.append(request)
        return success("{secret private-document}")
    install_transport(monkeypatch, handler)
    with pytest.raises(upload.UploadModelError) as caught:
        await complete(attempts=3)
    assert len(calls) == 3 and caught.value.invalid_structure
    assert "secret" not in str(caught.value)


@pytest.mark.parametrize("raw", [{"a": 1}, '```json\n{"a":1}\n```',
                                  'prefix {"a":1} suffix', [{"text": '{"a":1}'}]])
def test_json_forms(raw):
    assert upload.parse_upload_json(raw) == {"a": 1}


def test_endpoint_selection_never_mixes_credentials():
    chat = upload.UploadModelEndpoint("https://opencode.ai/v1", "chat", "chat-key")
    assert upload.select_upload_endpoint(chat, vision_model="default-vision") == chat
    with pytest.raises(upload.UploadModelError):
        upload.select_upload_endpoint(chat, vision_base_url="https://vision.test/v1", vision_model="vision")
    with pytest.raises(upload.UploadModelError):
        upload.select_upload_endpoint(chat, vision_api_key="vision-key", vision_model="vision")
    vision = upload.select_upload_endpoint(chat, vision_base_url="https://vision.test/v1",
                                           vision_model="vision", vision_api_key="vision-key")
    assert vision.api_key == "vision-key" and vision.model == "vision"
    assert "vision-key" not in repr(vision)


@pytest.mark.asyncio
async def test_textbook_vision_fallback_keeps_dedicated_provider(monkeypatch, tmp_path):
    calls = []
    def handler(request):
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(400, text="response_format unsupported")
        return success()
    install_transport(monkeypatch, handler)
    service = TextbookImportService(runtime_root=tmp_path, chat_base_url="https://opencode.ai/v1",
        chat_model="chat", chat_api_key="chat-key", mineru_token="t", mineru_pipeline_root=tmp_path,
        vision_base_url="https://vision.test/v1", vision_model="vision", vision_api_key="vision-key")
    assert await service._vision_json([], 100) == {"ok": True}
    assert len(calls) == 2
    for request in calls:
        assert request.url.host == "vision.test"
        assert request.headers["authorization"] == "Bearer vision-key"
        assert "x-opencode-session" not in request.headers


@pytest.mark.asyncio
async def test_services_share_headers_and_preserve_business_errors(monkeypatch, tmp_path):
    calls = []
    fail = False
    def handler(request):
        calls.append(request)
        return httpx.Response(401, text="secret") if fail else success()
    install_transport(monkeypatch, handler)
    kwargs = dict(chat_base_url="https://opencode.ai/v1", chat_model="chat", chat_api_key="secret",
                  mineru_token="t", mineru_pipeline_root=tmp_path)
    textbook = TextbookImportService(runtime_root=tmp_path, vision_model="unused-default", **kwargs)
    syllabus = UserSyllabusService(tmp_path, **kwargs)
    assert await textbook._vision_json([], 100) == {"ok": True}
    assert await syllabus._vision_json([], 100) == {"ok": True}
    assert all(r.headers.get("x-opencode-session") for r in calls)
    assert all(json.loads(r.content)["model"] == "chat" for r in calls)
    fail = True
    with pytest.raises(TextbookImportError, match="HTTP 401"):
        await textbook._vision_json([], 100)
    with pytest.raises(UserSyllabusError) as caught:
        await syllabus._vision_json([], 100)
    assert caught.value.code == "USER_SYLLABUS_EXTRACTION_FAILED"