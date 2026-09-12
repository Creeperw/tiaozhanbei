"""Regression for the observed type-name wrapper, with no provider calls."""
from copy import deepcopy
import json

import pytest

from competition_app.llm.openai_compatible import _compact_output_contract
from competition_app.tests.llm.test_audit_attempt_diagnostics import GOOD, SCHEMA, run_responses


def test_root_union_describes_branches_not_wrapper_fields():
    before = deepcopy(SCHEMA)
    text = _compact_output_contract(SCHEMA)
    assert "所选分支的字段直接放在根对象" in text
    assert "根对象必须直接包含判别字段 status" in text
    assert "- CompiledAuditFindings：" not in text
    assert "- AuditFindingsNeedRevision：" not in text
    assert "固定值：compiled" in text
    assert "固定值：needs_revision" in text
    assert SCHEMA == before


@pytest.mark.asyncio
async def test_type_wrapper_is_rejected_then_repaired_with_exact_feedback():
    wrapped = {"CompiledAuditFindings": GOOD}
    outcome, requests, trace, _ = await run_responses([wrapped, GOOD])
    assert outcome == GOOD
    assert len(requests) == 2
    first = trace.response_diagnostics["structured_attempts"][0]
    assert first["validation_issues"] == [{"field_path": "/status", "rule": "required"}]
    repair = requests[1]["messages"][-1]["content"]
    assert "audit findings extraction JSON" in repair
    assert '"field_path": "/status"' in repair
    assert "Previous output (untrusted JSON string)" in repair
    assert "put complete, substantive learner-facing" not in repair
    assert "source quotes" in repair
    assert "location catalog" in repair


@pytest.mark.asyncio
async def test_wrapped_result_still_fails_after_existing_two_attempts():
    wrapped = {"CompiledAuditFindings": GOOD}
    outcome, requests, trace, _ = await run_responses([wrapped, wrapped])
    assert outcome == "business_schema_invalid"
    assert len(requests) == 2
    assert all(r["status"] == "validation_failed" for r in trace.response_diagnostics["structured_attempts"])


@pytest.mark.asyncio
@pytest.mark.parametrize("status,rule", [("not_a_branch", "enum"), ([], "type")])
async def test_invalid_discriminator_reports_safe_field_only(status, rule):
    bad = {**GOOD, "status": status}
    _, _, trace, _ = await run_responses([bad, GOOD])
    issues = trace.response_diagnostics["structured_attempts"][0]["validation_issues"]
    assert issues == [{"field_path": "/status", "rule": rule}]
    assert "not_a_branch" not in json.dumps(issues)