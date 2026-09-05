from __future__ import annotations

import csv
import hashlib

import pytest

from competition_app.evaluation.d1_v2_registry import (
    D1V2DatasetRegistryService,
    V2_FORMAL_EVIDENCE_SAMPLE_CASE_IDS,
    V2_HUMAN_REVIEW_SAMPLE_CASE_IDS,
)


HUMAN_ATTESTATION = {
    "human_reviewer": True,
    "independent_review": True,
    "gold_labels_not_seen": True,
    "prior_review_decisions_not_seen": True,
}


def _record(
    service: D1V2DatasetRegistryService,
    reviews: list[dict[str, str]],
    *,
    submitted_by: str = "review-user-id",
    attempt_number: int = 1,
):
    return service.record_human_review(
        reviews,
        submitted_by=submitted_by,
        expected_attempt_number=attempt_number,
        attestation=HUMAN_ATTESTATION,
    )


def _completed_reviews(service: D1V2DatasetRegistryService) -> list[dict[str, str]]:
    _registry, cases = service.load()
    gold = {
        case.case_id: str(case.frozen_evidence.gold_relation)
        for case in cases
    }
    return [
        {
            "case_id": case_id,
            "reviewer_relation": gold[case_id],
            "reviewer_name": "independent-reviewer",
            "review_notes": "已独立比较主张与两条证据。",
        }
        for case_id in V2_HUMAN_REVIEW_SAMPLE_CASE_IDS
    ]


def test_v2_review_package_is_fixed_and_blinded(tmp_path):
    service = D1V2DatasetRegistryService(
        human_review_path=tmp_path / "independent_review.csv"
    )

    package = service.review_package_payload()

    assert package["gold_labels_included"] is False
    assert package["gold_rationales_included"] is False
    assert package["prior_review_decisions_included"] is False
    assert package["attempt_number"] == 1
    assert package["submission_allowed"] is True
    assert package["human_attestation_required"] is True
    assert "同一目标声明" in package["relation_definitions"]["compatible"]
    assert "自然语义上相容" in package["relation_definitions"]["not_applicable"]
    assert [item["case_id"] for item in package["items"]] == list(
        V2_HUMAN_REVIEW_SAMPLE_CASE_IDS
    )
    assert len(package["items"]) == 10
    for forbidden in (
        "gold_relation",
        "relation_dimension",
        "gold_rationale",
        "expected_rule_exposure",
        "synthetic_fault",
        "reviewer_decision",
    ):
        assert all(forbidden not in item for item in package["items"])


def test_v2_formal_evidence_sample_is_preregistered_stratified_and_disjoint(tmp_path):
    service = D1V2DatasetRegistryService(
        human_review_path=tmp_path / "independent_review.csv"
    )
    _registry, cases = service.load()
    by_id = {case.case_id: case for case in cases}
    selected = [by_id[case_id] for case_id in V2_FORMAL_EVIDENCE_SAMPLE_CASE_IDS]

    assert len(selected) == 20
    assert not set(V2_FORMAL_EVIDENCE_SAMPLE_CASE_IDS).intersection(
        V2_HUMAN_REVIEW_SAMPLE_CASE_IDS
    )
    assert {
        group: sum(case.case_group == group for case in selected)
        for group in {
            "target_fault",
            "non_regression_control",
            "targeting_negative_control",
        }
    } == {
        "target_fault": 12,
        "non_regression_control": 6,
        "targeting_negative_control": 2,
    }
    assert {
        order: sum(case.pair_order == order for case in selected)
        for order in {"AB", "BA"}
    } == {"AB": 10, "BA": 10}

    package = service.formal_review_package_payload()
    assert package["model_outcomes_included"] is False
    assert [item["case_id"] for item in package["items"]] == list(
        V2_FORMAL_EVIDENCE_SAMPLE_CASE_IDS
    )
    assert all(
        "gold_relation" not in item and "model_output" not in item
        for item in package["items"]
    )


def test_v2_review_is_persisted_without_gold_and_evaluated_after_submission(tmp_path):
    path = tmp_path / "independent_review.csv"
    service = D1V2DatasetRegistryService(human_review_path=path)

    result = _record(service, _completed_reviews(service))

    assert result["human_review_complete"] is True
    assert result["human_review_passed"] is True
    assert result["human_review_approved_count"] == 10
    assert result["human_review_active_attempt_number"] == 1
    assert result["human_review_attempt_count"] == 1
    assert result["human_review_retry_allowed"] is False
    rows = list(csv.DictReader(path.open(encoding="utf-8", newline="")))
    assert len(rows) == 10
    assert "gold_relation" not in rows[0]
    assert "reviewer_decision" not in rows[0]


def test_v2_review_disagreement_fails_closed(tmp_path):
    service = D1V2DatasetRegistryService(
        human_review_path=tmp_path / "independent_review.csv"
    )
    reviews = _completed_reviews(service)
    reviews[0]["reviewer_relation"] = (
        "compatible"
        if reviews[0]["reviewer_relation"] != "compatible"
        else "contradiction"
    )

    result = _record(service, reviews)

    assert result["human_review_complete"] is True
    assert result["human_review_passed"] is False
    assert result["human_review_approved_count"] == 9
    assert result["human_review_disagreement_count"] == 1
    assert result["human_review_retry_allowed"] is True
    assert "human_review_rejected_case_ids" not in result


def test_v2_review_rejects_partial_or_unsigned_submission(tmp_path):
    service = D1V2DatasetRegistryService(
        human_review_path=tmp_path / "independent_review.csv"
    )
    reviews = _completed_reviews(service)

    with pytest.raises(ValueError, match="fixed 10-case order"):
        _record(service, reviews[:-1])

    reviews[0]["reviewer_name"] = ""
    with pytest.raises(ValueError, match="reviewer_name"):
        _record(service, reviews)


def test_v2_review_requires_explicit_human_attestation(tmp_path):
    service = D1V2DatasetRegistryService(
        human_review_path=tmp_path / "independent_review.csv"
    )

    with pytest.raises(ValueError, match="complete human attestation"):
        service.record_human_review(
            _completed_reviews(service),
            submitted_by="review-user-id",
            expected_attempt_number=1,
            attestation={**HUMAN_ATTESTATION, "human_reviewer": False},
        )


def test_v2_review_cannot_be_overwritten_after_gold_comparison(tmp_path):
    service = D1V2DatasetRegistryService(
        human_review_path=tmp_path / "independent_review.csv"
    )
    reviews = _completed_reviews(service)
    _record(service, reviews)

    with pytest.raises(ValueError, match="already been recorded"):
        _record(service, reviews)


def test_v2_failed_review_allows_one_hash_chained_retry_by_new_account(tmp_path):
    path = tmp_path / "independent_review.csv"
    service = D1V2DatasetRegistryService(human_review_path=path)
    failed = _completed_reviews(service)
    failed[0]["reviewer_relation"] = "compatible"

    first = _record(service, failed, submitted_by="first-reviewer")
    first_bytes = path.read_bytes()
    first_sha256 = hashlib.sha256(first_bytes).hexdigest()
    package = service.review_package_payload()

    assert first["human_review_retry_allowed"] is True
    assert package["attempt_number"] == 2
    assert package["retry_requires_different_authenticated_account"] is True
    assert package["prior_review_decisions_included"] is False
    assert all(not item["reviewer_relation"] for item in package["items"])

    with pytest.raises(ValueError, match="different authenticated account"):
        _record(
            service,
            _completed_reviews(service),
            submitted_by="first-reviewer",
            attempt_number=2,
        )

    result = _record(
        service,
        _completed_reviews(service),
        submitted_by="second-reviewer",
        attempt_number=2,
    )

    assert path.read_bytes() == first_bytes
    assert result["human_review_passed"] is True
    assert result["human_review_active_attempt_number"] == 2
    assert result["human_review_attempt_count"] == 2
    assert result["human_review_retry_allowed"] is False
    assert result["human_review_attempts"][0]["sha256"] == first_sha256
    retry_rows = list(
        csv.DictReader(
            service.human_review_retry_path.open(encoding="utf-8", newline="")
        )
    )
    assert {row["previous_attempt_sha256"] for row in retry_rows} == {first_sha256}

    with pytest.raises(ValueError, match="already been recorded"):
        _record(
            service,
            _completed_reviews(service),
            submitted_by="third-reviewer",
            attempt_number=2,
        )


def test_v2_retry_tampering_fails_closed(tmp_path):
    path = tmp_path / "independent_review.csv"
    service = D1V2DatasetRegistryService(human_review_path=path)
    failed = _completed_reviews(service)
    failed[0]["reviewer_relation"] = "compatible"
    _record(service, failed, submitted_by="first-reviewer")
    _record(
        service,
        _completed_reviews(service),
        submitted_by="second-reviewer",
        attempt_number=2,
    )
    content = service.human_review_retry_path.read_text(encoding="utf-8")
    service.human_review_retry_path.write_text(
        content.replace(
            hashlib.sha256(path.read_bytes()).hexdigest(),
            "0" * 64,
        ),
        encoding="utf-8",
    )

    registry, _cases = service.load()

    assert registry.human_review_complete is False
    assert registry.human_review_passed is False
    assert registry.human_review_retry_allowed is False


def test_v2_corrupt_existing_review_file_cannot_be_overwritten(tmp_path):
    path = tmp_path / "independent_review.csv"
    path.write_text("corrupt,review\n", encoding="utf-8")
    service = D1V2DatasetRegistryService(human_review_path=path)

    with pytest.raises(ValueError, match="already been recorded"):
        _record(service, _completed_reviews(service))


def test_v2_orphan_retry_file_blocks_new_review(tmp_path):
    path = tmp_path / "independent_review.csv"
    retry_path = tmp_path / "independent_review.attempt-2.csv"
    retry_path.write_text("corrupt,orphan\n", encoding="utf-8")
    service = D1V2DatasetRegistryService(human_review_path=path)

    registry, _cases = service.load()
    assert registry.human_review_complete is False
    assert registry.human_review_passed is False

    with pytest.raises(ValueError, match="evidence chain is invalid"):
        _record(service, _completed_reviews(service))
