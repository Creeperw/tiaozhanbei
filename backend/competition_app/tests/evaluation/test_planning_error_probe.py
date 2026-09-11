import json

import httpx
import pytest
from fastapi import HTTPException

from competition_app.evaluation.planning_error_probe import PlanningErrorProbe, safe_error_fields
from competition_app.evaluation.planning_mode_probe import fixed_input
from competition_app.tests.evaluation.test_planning_mode_probe import client_model


@pytest.mark.asyncio
async def test_exact_schema_single_request_no_retry_and_error_retained(tmp_path):
    requests = []
    def handler(request):
        requests.append(json.loads(request.content))
        return httpx.Response(400, json={"error": {"type": "invalid_request_error", "param": "response_format", "message": "minLength is not supported"}, "debug": "private"})
    probe = PlanningErrorProbe(client_model(handler), tmp_path, "live")
    base, schema, _ = fixed_input(probe.model)
    probe.start()
    await probe.task
    assert len(requests) == 1
    assert {k: v for k, v in requests[0].items() if k != "response_format"} == base
    assert requests[0]["response_format"]["json_schema"]["schema"] == schema
    assert probe.state["results"][0]["provider_error"]["message"] == "minLength is not supported"
    assert "private" not in probe.receipt.read_text()
    assert probe.receipt.stat().st_mode & 0o777 == 0o600
    with pytest.raises(HTTPException):
        PlanningErrorProbe(probe.model, tmp_path, "live").start()


@pytest.mark.parametrize("status,body,key", [(400, b"x" * 17000, "error_body_oversize"), (400, b"not-json", "provider_error"), (200, b"private prose and reasoning", "success_body_not_read")])
@pytest.mark.asyncio
async def test_bounded_error_and_no_success_body(tmp_path, status, body, key):
    probe = PlanningErrorProbe(client_model(lambda _: httpx.Response(status, content=body)), tmp_path, "live")
    probe.start()
    await probe.task
    assert key in probe.state["results"][0]
    assert "private" not in probe.receipt.read_text()


def test_error_redaction():
    prompt = "private synthetic input " * 20
    body = {"messages": [{"content": prompt}]}
    headers = {"Authorization": "Bearer private-key"}
    result = safe_error_fields(json.dumps({"error": {"code": "bad", "message": "invalid private-key Bearer abc sk-token", "param": "response_format"}, "request": prompt}), body=body, headers=headers)
    assert result["param"] == "response_format"
    assert not any(t in json.dumps(result) for t in ("private-key", "abc", "sk-token"))
    result = safe_error_fields(json.dumps({"error": {"message": "echo " + prompt[10:200]}}), body=body, headers=headers)
    assert result["message"] == "[request echo suppressed]"