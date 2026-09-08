import json
from copy import deepcopy
from unittest.mock import patch

import pytest

from competition_app.llm.anchor_diagnostics import stage_anchor_diagnostics
from competition_app.tests.agents.test_fixed_route_binding import compile_case, fixture


def observe(doc, raw, route, *, last_position=-1):
    contract = raw["contract"]
    return stage_anchor_diagnostics(
        doc, contract["stages"][0], route["stages"][0], route["stages"],
        contract.get("field_anchors", {}).get("/stages/0/stage_id", []),
        last_position, 0,
    )


@pytest.mark.parametrize("fault,failed_check", [
    ("baseline", None), ("wrong_source", "source_field"),
    ("missing_book", "all_books_in_quote"),
    ("wrong_summary", "summary_in_quote"),
    ("wrong_duration", "duration_in_quote"),
    ("wrong_order", "ordered_after_previous"),
    ("other_stage", "no_other_stage_id"),
    ("missing_id", "stage_id_in_quote"),
])
def test_individual_checks(fault, failed_check):
    doc, raw, route = fixture()
    entry = raw["contract"]["field_anchors"]["/stages/0/stage_id"][0]
    stage = raw["contract"]["stages"][0]
    if fault == "wrong_source":
        entry["source_field"] = "private input"
    elif fault == "missing_book":
        entry["source_quote"] = entry["source_quote"].replace("《基础》", "")
    elif fault == "wrong_summary":
        stage["schedule_summary"] = "原文没有的安排"
    elif fault == "wrong_duration":
        stage["duration_days"] = 99
    elif fault == "other_stage":
        entry["source_quote"] += "stage-2"
    elif fault == "missing_id":
        entry["source_quote"] = entry["source_quote"].replace("stage-1", "")
    before = deepcopy((doc, raw, route))
    evidence = observe(doc, raw, route, last_position=len(doc) if fault == "wrong_order" else -1)
    detail = evidence["entries"][0]
    assert detail["gate_would_accept"] is (fault == "baseline")
    if failed_check:
        assert detail["checks"][failed_check] is False
    assert (doc, raw, route) == before


@pytest.mark.parametrize("fault", ["absent", "empty", "null_quote", "empty_quote", "non_object"])
def test_missing_evidence_is_distinguishable(fault):
    doc, raw, route = fixture()
    path = "/stages/0/stage_id"
    anchors = raw["contract"]["field_anchors"]
    if fault == "absent":
        del anchors[path]
    elif fault == "empty":
        anchors[path] = []
    elif fault == "non_object":
        anchors[path] = [None]
    else:
        anchors[path][0]["source_quote"] = None if fault == "null_quote" else ""
    result = observe(doc, raw, route)
    if fault in {"absent", "empty"}:
        assert result["entry_count"] == 0
    else:
        assert result["entries"][0]["quote_nonempty"] is False


@pytest.mark.parametrize("fault,classification", [
    ("whitespace", "whitespace_only_match"),
    ("markdown", "markdown_markers_only_match"),
    ("combined", "whitespace_and_markers_match"),
])
@pytest.mark.asyncio
async def test_difference_classification_never_authorizes_quote(fault, classification):
    doc, raw, route = fixture()
    entry = raw["contract"]["field_anchors"]["/stages/0/stage_id"][0]
    if fault in {"whitespace", "combined"}:
        entry["source_quote"] = entry["source_quote"].replace("\n", " ")
    if fault in {"markdown", "combined"}:
        doc = doc.replace("《基础》", "**《基础》**")
    evidence = observe(doc, raw, route)["entries"][0]
    assert evidence["difference"][classification] is True
    assert evidence["checks"]["quote_in_document"] is False
    result, _ = await compile_case(doc, raw, route)
    assert result.result.status == "needs_revision"
    assert result.result.issues[0].code == "source_anchor_invalid"


def test_no_arbitrary_text_or_credentials_are_emitted():
    doc, raw, route = fixture()
    secret = "synthetic-person-name access_token=test-secret-12345678901234567890"
    doc += secret
    raw["contract"]["stages"][0]["schedule_summary"] = secret
    entry = raw["contract"]["field_anchors"]["/stages/0/stage_id"][0]
    entry.update(source_field=secret, source_quote=secret)
    serialized = json.dumps(observe(doc, raw, route), ensure_ascii=False)
    assert "synthetic-person-name" not in serialized
    assert "test-secret" not in serialized
    assert "《基础》" not in serialized
    assert "sha256" in serialized


def test_analysis_and_output_are_bounded():
    doc, raw, route = fixture()
    entries = raw["contract"]["field_anchors"]["/stages/0/stage_id"]
    entries[0]["source_quote"] = "x" * 12001
    entries *= 20
    result = observe(doc + "y" * 20001, raw, route)
    assert len(result["entries"]) == 8
    assert result["entries_omitted"] == 12
    assert result["entries"][0]["difference"]["analysis_complete"] is False
    assert len(json.dumps(result)) < 12000


@pytest.mark.asyncio
async def test_failure_log_contains_actionable_evidence(caplog):
    doc, raw, route = fixture()
    raw["contract"].pop("field_anchors")
    result, _ = await compile_case(doc, raw, route)
    records = [r for r in caplog.records if "stage_anchor_rejected" in r.message]
    assert len(records) == 2  # one bounded same-document extraction retry
    evidence = json.loads(records[0].message.split(" details=", 1)[1])
    assert evidence["entry_count"] == 0
    assert evidence["field_path"] == "/stages/0/stage_id"
    assert result.source_digest in records[0].message
    assert doc not in records[0].message


@pytest.mark.asyncio
async def test_diagnostic_exception_cannot_change_result():
    doc, raw, route = fixture()
    raw["contract"]["stages"][0]["schedule_summary"] = "wrong"
    expected, _ = await compile_case(doc, raw, route)
    with patch("competition_app.llm.anchor_diagnostics.stage_anchor_diagnostics", side_effect=RuntimeError("sink error")):
        actual, _ = await compile_case(doc, raw, route)
    assert actual == expected


@pytest.mark.asyncio
async def test_success_does_not_invoke_diagnostics():
    doc, raw, route = fixture()
    with patch("competition_app.llm.anchor_diagnostics.stage_anchor_diagnostics") as diagnostic:
        result, _ = await compile_case(doc, raw, route)
    assert result.result.status == "compiled"
    diagnostic.assert_not_called()