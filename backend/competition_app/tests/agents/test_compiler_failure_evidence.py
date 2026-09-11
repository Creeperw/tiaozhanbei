import json
import stat
from copy import deepcopy

import pytest

from competition_app.llm import compiler_failure_evidence as evidence
from competition_app.tests.agents.test_fixed_route_binding import fixture, compile_case


@pytest.fixture
def capture_config(tmp_path, monkeypatch):
    path = tmp_path / "config.json"
    monkeypatch.setattr(evidence, "CONFIG_PATH", path)
    path.write_text(json.dumps({"enabled": True, "learner_id": "USER_TEST", "expires_at": "2099-01-01T00:00:00+00:00"}))
    return path


@pytest.mark.asyncio
async def test_exact_formal_failure_saved_without_mutation(capture_config):
    doc, raw, route = fixture()
    raw["contract"]["stages"][0]["acceptance"] = ["并非正文的验收"]
    raw["reasoning"] = "PRIVATE_REASONING"
    before = deepcopy(raw)
    result, _ = await compile_case(doc, raw, route)
    path = capture_config.parent / "failure-1.json"
    saved = json.loads(path.read_text())
    assert saved["document"] == doc
    assert saved["contract"]["stages"][0]["acceptance"] == ["并非正文的验收"]
    assert not saved["sanitization_changed_evidence"]
    assert "PRIVATE_REASONING" not in path.read_text()
    assert saved["issues"][0]["field_path"] == "/stages/0/acceptance"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert result.result.status == "needs_revision" and raw == before


@pytest.mark.asyncio
@pytest.mark.parametrize("change", [{"enabled": False}, {"learner_id": "OTHER"}, {"expires_at": "2000-01-01T00:00:00+00:00"}, {"expires_at": "2099-01-01"}])
async def test_capture_guard_rejects(capture_config, change):
    config = json.loads(capture_config.read_text())
    capture_config.write_text(json.dumps({**config, **change}))
    doc, raw, route = fixture()
    raw["contract"]["stages"][0]["acceptance"] = ["缺失内容"]
    await compile_case(doc, raw, route)
    assert not list(capture_config.parent.glob("failure-*.json"))
    assert not (capture_config.parent / "claimed-run").exists()


@pytest.mark.asyncio
async def test_single_run_and_count_limit(capture_config):
    doc, raw, route = fixture()
    raw["contract"]["stages"][0]["acceptance"] = ["缺失内容"]
    result, _ = await compile_case(doc, raw, route)
    for _ in range(8):
        evidence.capture_failure({"learner_id": "USER_TEST", "request_id": "OTHER"}, {"plan_document": doc}, route, raw, result)
    assert len(list(capture_config.parent.glob("failure-*.json"))) == 2
    for _ in range(8):
        evidence.capture_failure({"learner_id": "USER_TEST", "request_id": "REQUEST_TEST"}, {"plan_document": doc}, route, raw, result)
    assert len(list(capture_config.parent.glob("failure-*.json"))) == 4


@pytest.mark.asyncio
async def test_success_not_captured(capture_config):
    await compile_case(*fixture())
    assert not (capture_config.parent / "claimed-run").exists()


@pytest.mark.asyncio
async def test_redaction_and_sink_failure_do_not_change_result(capture_config, monkeypatch):
    doc, raw, route = fixture()
    raw["contract"]["stages"][0]["acceptance"] = ["password=private-value"]
    result, _ = await compile_case(doc, raw, route)
    saved = (capture_config.parent / "failure-1.json").read_text()
    assert "private-value" not in saved
    assert json.loads(saved)["sanitization_changed_evidence"]
    monkeypatch.setattr(evidence, "capture_failure", lambda *args: (_ for _ in ()).throw(OSError("disk error")))
    second, _ = await compile_case(doc, raw, route)
    assert result == second


@pytest.mark.asyncio
async def test_stage_anchor_failure_also_captured(capture_config):
    doc, raw, route = fixture()
    raw["contract"]["field_anchors"]["/stages/0/stage_id"] = []
    await compile_case(doc, raw, route)
    saved = json.loads((capture_config.parent / "failure-1.json").read_text())
    assert saved["issues"][0]["code"] == "source_anchor_invalid"


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["absent", "oversized", "invalid_config"])
async def test_unavailable_capture_never_changes_failure(capture_config, monkeypatch, case):
    if case == "absent":
        capture_config.unlink()
    elif case == "oversized":
        monkeypatch.setattr(evidence, "MAX_BYTES", 10)
    else:
        capture_config.write_text("not json")
    doc, raw, route = fixture()
    raw["contract"]["stages"][0]["acceptance"] = ["缺失内容"]
    result, _ = await compile_case(doc, raw, route)
    assert result.result.status == "needs_revision"
    assert not list(capture_config.parent.glob("failure-*.json"))


async def short_failure_case():
    from competition_app.agents.plan_contract_compiler import PlanContractCompilerAgent
    from competition_app.tests.agents.test_plan_contract_compiler import DocumentCompilerModel

    document = (
        "当前阶段stage-1，未来14天使用《方剂学》。"
        "先完成教材核对；再完成闭卷比较。"
        "预期产出：一张类方比较表。完成标准：能够闭卷比较代表方剂。"
    )
    captured = {}

    class Model:
        async def complete_json(self, role, context):
            raw = await DocumentCompilerModel().complete_json(role, context)
            contract = raw["contract"]
            anchors = contract["field_anchors"].pop("/progression_nodes")
            for index, anchor in enumerate(anchors):
                contract["field_anchors"][f"/progression_nodes/{index}"] = [anchor]
            contract["progression_nodes"][1] = "随后进行闭卷比较"
            raw["reasoning"] = "PRIVATE_REASONING"
            captured["raw"] = raw
            captured["before"] = deepcopy(raw)
            return raw

    result = await PlanContractCompilerAgent(Model()).compile(
        {"trace_id": "TRACE_TEST", "request_id": "REQUEST_TEST", "learner_id": "USER_TEST"},
        plan_scope="short_term", diagnosis_output={"plan_document": document},
        trusted_route={}, parent_plan_constraints={},
    )
    assert captured["raw"] == captured["before"]
    assert result.result.status == "needs_revision"
    assert [(issue.code, issue.field_path) for issue in result.result.issues] == [
        ("source_value_not_verbatim", "/progression_nodes/1"),
        ("source_anchor_missing", "/progression_nodes")
    ]
    return document, result


@pytest.mark.asyncio
async def test_short_failure_preserves_raw_values_and_quotes(capture_config):
    document, _ = await short_failure_case()
    saved_text = (capture_config.parent / "failure-1.json").read_text()
    saved = json.loads(saved_text)
    assert saved["document"] == document
    assert saved["plan_scope"] == "short_term"
    assert saved["raw_status"] == "compiled"
    assert saved["contract"]["progression_nodes"] == ["先完成教材核对", "随后进行闭卷比较"]
    assert saved["contract"]["field_anchors"]["/progression_nodes/1"][0]["source_quote"] == "再完成闭卷比较"
    assert "/progression_nodes" not in saved["contract"]["field_anchors"]
    assert saved["contract"]["duration_days"] == 14
    assert saved["contract"]["expected_output"] == "一张类方比较表"
    assert saved["contract"]["completion_criteria"] == "能够闭卷比较代表方剂"
    assert not saved["sanitization_changed_evidence"]
    assert "PRIVATE_REASONING" not in saved_text


@pytest.mark.asyncio
async def test_scope_filter_prevents_other_plan_claim(capture_config):
    config = json.loads(capture_config.read_text())
    capture_config.write_text(json.dumps({**config, "plan_scope": "short_term"}))
    doc, raw, route = fixture()
    raw["contract"]["stages"][0]["acceptance"] = ["缺失内容"]
    await compile_case(doc, raw, route)
    assert not (capture_config.parent / "claimed-run").exists()
    await short_failure_case()
    assert len(list(capture_config.parent.glob("failure-*.json"))) == 1


@pytest.mark.asyncio
async def test_short_evidence_sink_failure_preserves_result(capture_config, monkeypatch):
    _, first = await short_failure_case()

    def unavailable(*args, **kwargs):
        raise OSError("sink unavailable")

    monkeypatch.setattr(evidence, "capture_failure", unavailable)
    _, second = await short_failure_case()
    assert first == second