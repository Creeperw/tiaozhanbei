from pathlib import Path

from evaluation.evolution.run_d1_formal_online import (
    AB100_DEFAULT_OUTPUT,
    DEFAULT_OUTPUT,
    _compact_result,
    _existing_ids,
    _technical_error_record,
    _technical_errors_path,
)


def test_compact_result_drops_model_trace_and_user_output():
    compact = _compact_result({
        "case_id": "EVO-D1-AB50-001",
        "pair_order": "AB",
        "formal_environment_write_allowed": False,
        "validation": {"valid": True},
        "arms": {"A": {"user_output": "secret", "target_failure": True}},
    })
    assert compact["case_id"] == "EVO-D1-AB50-001"
    assert "user_output" not in compact["arms"]["A"]
    assert compact["arms"]["A"]["target_failure"] is True


def test_existing_ids_ignores_invalid_lines(tmp_path: Path):
    path = tmp_path / "results.jsonl"
    path.write_text('{"case_id":"EVO-D1-AB50-001"}\nnot-json\n', encoding="utf-8")
    assert _existing_ids(path) == {"EVO-D1-AB50-001"}


def test_technical_error_receipt_is_sanitized():
    error = RuntimeError("secret prompt and api-key must not be persisted")
    receipt = _technical_error_record(
        case_id="EVO-D1-AB50-020",
        attempt=1,
        max_attempts=3,
        error=error,
    )

    assert receipt["error_type"] == "RuntimeError"
    assert receipt["will_retry"] is True
    assert "secret" not in str(receipt)
    assert "api-key" not in str(receipt)


def test_technical_error_path_is_kept_separate_from_business_results(tmp_path: Path):
    output = tmp_path / "formal_results.jsonl"
    assert _technical_errors_path(output) == tmp_path / "formal_results_technical_errors.jsonl"


def test_ab100_uses_a_separate_default_result_file():
    assert AB100_DEFAULT_OUTPUT != DEFAULT_OUTPUT
