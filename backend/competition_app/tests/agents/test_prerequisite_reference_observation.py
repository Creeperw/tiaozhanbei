import json
import logging
from copy import deepcopy
from unittest.mock import Mock

from competition_app.agents.diagnosis import DiagnosisAgent


def test_reference_observation_is_bounded_private_and_readonly(caplog):
    agent = DiagnosisAgent()
    sources = {"user_request": "private source body"}
    raw = {
        "plan_document": "private document",
        "reasoning": "private reasoning",
        "prerequisite_judgments": [
            {"source_ref": "user_request", "source_quote": "private source", "rationale": "private rationale"},
            {"source_ref": "profile.learning_background", "source_quote": "private quote"},
            {"source_ref": "not an identifier\nprivate text"},
            {"source_ref": "sk-" + "a" * 30},
            {"source_ref": []},
        ] * 5,
    }
    before = deepcopy((sources, raw))
    with caplog.at_level(logging.INFO, logger="competition_app.diagnosis_agent"):
        agent._log_prerequisite_references(
            {"execution_id": "EXE_TEST"}, {"plan_scope": "short_term"},
            raw, sources, phase="draft",
        )
    assert (sources, raw) == before
    message = caplog.records[-1].getMessage()
    assert "private" not in message and "a" * 30 not in message
    data = json.loads(message.split(": ", 1)[1])
    assert data["judgment_count"] == 25 and len(data["references"]) == 20
    assert data["source_keys"] == ["user_request"]
    assert data["references"][0]["authorized"] is True
    assert data["references"][0]["quote_matches"] is True
    assert data["references"][1]["source_ref"] == "profile.learning_background"
    assert data["references"][1]["authorized"] is False
    assert data["references"][2]["source_ref"].startswith("[non_identifier_sha256:")
    assert data["references"][3]["source_ref"] == "[REDACTED]"
    assert data["references"][4]["ref_type"] == "list"


def test_observation_failure_never_blocks_planning():
    agent = DiagnosisAgent()
    agent.logger = Mock()
    agent.logger.info.side_effect = RuntimeError("logging unavailable")
    agent._log_prerequisite_references({}, {"plan_scope": "short_term"}, {}, {}, phase="draft")
