from __future__ import annotations

from dataclasses import dataclass
import csv
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
from time import time_ns
from typing import Any

from competition_app.evaluation.d1_ab100_v2_runner import (
    AB100_V2_DATASET,
    load_d1_ab100_v2_cases,
    validate_d1_ab100_v2_cases,
)


V2_MANIFEST = AB100_V2_DATASET.with_suffix(".manifest.json")
V2_HUMAN_REVIEW = AB100_V2_DATASET.with_name(
    f"{AB100_V2_DATASET.stem}.independent_review.csv"
)
V2_DATASET_ID = "semantic_conflict_closure_ab100_v2_20260901"
V2_REQUIRED_ANNOTATION_STATUS = (
    "deterministic_construct_validated_pending_independent_human_review"
)
V2_HUMAN_REVIEW_SAMPLE_VERSION = "d1-v2-stratified-10-v1"
V2_HUMAN_REVIEW_DECISION_RULE_VERSION = "d1-v2-relation-scope-v1"
V2_HUMAN_REVIEW_ATTESTATION_VERSION = "d1-v2-human-independent-v1"
V2_HUMAN_REVIEW_RELATION_DEFINITIONS = {
    "contradiction": "两条证据针对同一目标声明，核心结论不能同时成立。",
    "compatible": "两条证据针对同一目标声明，核心结论可以同时成立或相互补充。",
    "not_applicable": (
        "当前材料未形成目标冲突规则要处理的同一目标声明；即使两条材料在自然语义上相容，"
        "也应选择不适用。"
    ),
}
# Six target faults, two ordinary controls and two compatible boundaries;
# AB/BA are balanced 5/5.  The selection is code-owned so the precheck cannot
# be changed after outcomes are observed.
V2_HUMAN_REVIEW_SAMPLE_CASE_IDS = (
    "EVO-D1-V2-001",
    "EVO-D1-V2-012",
    "EVO-D1-V2-021",
    "EVO-D1-V2-032",
    "EVO-D1-V2-041",
    "EVO-D1-V2-052",
    "EVO-D1-V2-061",
    "EVO-D1-V2-072",
    "EVO-D1-V2-091",
    "EVO-D1-V2-092",
)
# Fixed before any V2 outcomes are observed.  The 20 cases are stratified as
# 12 target faults, 6 ordinary controls and 2 compatible boundaries.  The same
# case set is used for independent blind review and repeatability evidence so
# neither can be selected after looking at the formal outcomes.
V2_FORMAL_EVIDENCE_SAMPLE_VERSION = "d1-v2-stratified-20-v1"
V2_FORMAL_EVIDENCE_SAMPLE_CASE_IDS = (
    "EVO-D1-V2-002",
    "EVO-D1-V2-007",
    "EVO-D1-V2-011",
    "EVO-D1-V2-018",
    "EVO-D1-V2-023",
    "EVO-D1-V2-028",
    "EVO-D1-V2-033",
    "EVO-D1-V2-038",
    "EVO-D1-V2-043",
    "EVO-D1-V2-048",
    "EVO-D1-V2-053",
    "EVO-D1-V2-058",
    "EVO-D1-V2-062",
    "EVO-D1-V2-067",
    "EVO-D1-V2-073",
    "EVO-D1-V2-078",
    "EVO-D1-V2-083",
    "EVO-D1-V2-088",
    "EVO-D1-V2-093",
    "EVO-D1-V2-098",
)


@dataclass(frozen=True)
class D1V2DatasetRegistry:
    dataset_id: str
    version: str
    dataset_path: str
    manifest_path: str
    human_review_path: str
    file_sha256: str
    canonical_case_digest: str
    annotation_status: str
    human_review_complete: bool
    human_review_passed: bool
    human_review_required_count: int
    human_review_completed_count: int
    human_review_approved_count: int
    human_review_sample_version: str
    human_review_sample_case_ids: tuple[str, ...]
    human_review_rejected_case_ids: tuple[str, ...]
    human_review_submitted_by: str | None
    human_review_active_attempt_number: int
    human_review_attempt_count: int
    human_review_retry_allowed: bool
    human_review_attempts: tuple[dict[str, Any], ...]
    human_review_submitter_ids: tuple[str, ...]
    formal_evidence_sample_version: str
    formal_evidence_sample_case_ids: tuple[str, ...]
    case_count: int
    group_counts: dict[str, int]
    relation_counts: dict[str, int]
    exposure_counts: dict[str, int]
    formal_environment_write_allowed: bool

    def payload(self) -> dict[str, Any]:
        return {
            "schema_version": "d1-v2-registry-1.2",
            "dataset_id": self.dataset_id,
            "version": self.version,
            "dataset_artifact": Path(self.dataset_path).name,
            "manifest_artifact": Path(self.manifest_path).name,
            "human_review_artifact": Path(self.human_review_path).name,
            "file_sha256": self.file_sha256,
            "canonical_case_digest": self.canonical_case_digest,
            "annotation_status": self.annotation_status,
            "human_review_complete": self.human_review_complete,
            "human_review_passed": self.human_review_passed,
            "human_review_required_count": self.human_review_required_count,
            "human_review_completed_count": self.human_review_completed_count,
            "human_review_approved_count": self.human_review_approved_count,
            "human_review_sample_version": self.human_review_sample_version,
            "human_review_sample_case_ids": list(self.human_review_sample_case_ids),
            "human_review_disagreement_count": len(
                self.human_review_rejected_case_ids
            ),
            "human_review_submitter_present": bool(self.human_review_submitted_by),
            "human_review_active_attempt_number": self.human_review_active_attempt_number,
            "human_review_attempt_count": self.human_review_attempt_count,
            "human_review_retry_allowed": self.human_review_retry_allowed,
            "human_review_attempts": [dict(item) for item in self.human_review_attempts],
            "formal_evidence_sample_version": self.formal_evidence_sample_version,
            "formal_evidence_sample_case_ids": list(
                self.formal_evidence_sample_case_ids
            ),
            "case_count": self.case_count,
            "group_counts": self.group_counts,
            "relation_counts": self.relation_counts,
            "exposure_counts": self.exposure_counts,
            "formal_environment_write_allowed": self.formal_environment_write_allowed,
        }


class D1V2DatasetRegistryService:
    """Load exactly one code-owned V2 dataset; callers cannot supply paths."""

    def __init__(
        self,
        dataset_path: Path = AB100_V2_DATASET,
        manifest_path: Path = V2_MANIFEST,
        human_review_path: Path = V2_HUMAN_REVIEW,
    ) -> None:
        self.dataset_path = dataset_path
        self.manifest_path = manifest_path
        self.human_review_path = human_review_path
        self._cases = None
        self._registry = None

    @property
    def human_review_retry_path(self) -> Path:
        return self.human_review_path.with_name(
            f"{self.human_review_path.stem}.attempt-2{self.human_review_path.suffix}"
        )

    def load(self) -> tuple[D1V2DatasetRegistry, list]:
        # Re-read all artifacts on every call. Human review attempts are
        # append-only after deployment; caching would leave a completed review
        # blocked until process restart.
        manifest = self._read_manifest()
        if manifest.get("dataset_id") != V2_DATASET_ID:
            raise ValueError("V2 manifest dataset_id mismatch")
        if not self.dataset_path.is_file() or not self.manifest_path.is_file():
            raise ValueError("V2 dataset or manifest is missing")
        actual_sha256 = hashlib.sha256(self.dataset_path.read_bytes()).hexdigest()
        frozen_files = manifest.get("files")
        expected_sha256 = (
            frozen_files.get(self.dataset_path.name)
            if isinstance(frozen_files, dict)
            else None
        )
        if actual_sha256 != expected_sha256:
            raise ValueError("V2 JSONL file hash does not match manifest")
        cases = load_d1_ab100_v2_cases(self.dataset_path)
        summary = validate_d1_ab100_v2_cases(cases)
        expected_contracts = {
            "case_count": summary["case_count"],
            "case_groups": summary["case_groups"],
            "pair_orders": summary["pair_orders"],
            "gold_relations": summary["gold_relations"],
            "rule_exposure_scope": summary["b_rule_exposure_scope"],
        }
        for field_name, expected_value in expected_contracts.items():
            if manifest.get(field_name) != expected_value:
                raise ValueError(f"V2 manifest {field_name} mismatch")
        if manifest.get("formal_environment_write_allowed") is not False:
            raise ValueError("V2 formal environment writeback must be disabled")
        annotation_status = str(manifest.get("annotation_status") or "")
        if annotation_status != V2_REQUIRED_ANNOTATION_STATUS:
            raise ValueError("V2 annotation status is not the frozen pending state")
        review = self._human_review_status(cases)
        registry = D1V2DatasetRegistry(
            dataset_id=V2_DATASET_ID,
            version=str(manifest.get("version") or ""),
            dataset_path=str(self.dataset_path),
            manifest_path=str(self.manifest_path),
            human_review_path=str(self.human_review_path),
            file_sha256=actual_sha256,
            canonical_case_digest=summary["canonical_case_digest"],
            annotation_status=annotation_status,
            human_review_complete=review["complete"],
            human_review_passed=review["passed"],
            human_review_required_count=len(V2_HUMAN_REVIEW_SAMPLE_CASE_IDS),
            human_review_completed_count=review["completed_count"],
            human_review_approved_count=review["approved_count"],
            human_review_sample_version=V2_HUMAN_REVIEW_SAMPLE_VERSION,
            human_review_sample_case_ids=V2_HUMAN_REVIEW_SAMPLE_CASE_IDS,
            human_review_rejected_case_ids=tuple(review["rejected_case_ids"]),
            human_review_submitted_by=review["submitted_by"],
            human_review_active_attempt_number=review["active_attempt_number"],
            human_review_attempt_count=review["attempt_count"],
            human_review_retry_allowed=review["retry_allowed"],
            human_review_attempts=tuple(review["attempts"]),
            human_review_submitter_ids=tuple(review["submitter_ids"]),
            formal_evidence_sample_version=V2_FORMAL_EVIDENCE_SAMPLE_VERSION,
            formal_evidence_sample_case_ids=V2_FORMAL_EVIDENCE_SAMPLE_CASE_IDS,
            case_count=summary["case_count"],
            group_counts=dict(summary["case_groups"]),
            relation_counts=dict(summary["gold_relations"]),
            exposure_counts=dict(summary["b_rule_exposure_scope"]),
            formal_environment_write_allowed=False,
        )
        self._cases = tuple(cases)
        self._registry = registry
        return registry, list(cases)

    def _read_manifest(self) -> dict[str, Any]:
        try:
            value = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError("invalid V2 manifest") from exc
        if not isinstance(value, dict):
            raise ValueError("V2 manifest must be an object")
        return value

    def review_package_payload(self) -> dict[str, Any]:
        """Return the fixed ten-case package without gold labels or rationales."""

        registry, cases = self.load()
        by_id = {case.case_id: case for case in cases}
        attempt_number = (
            2
            if registry.human_review_retry_allowed
            else registry.human_review_active_attempt_number or 1
        )
        submission_allowed = (
            registry.human_review_attempt_count == 0
            or registry.human_review_retry_allowed
        )
        return {
            "schema_version": "d1-v2-independent-review-package-1.1",
            "sample_version": V2_HUMAN_REVIEW_SAMPLE_VERSION,
            "decision_rule_version": V2_HUMAN_REVIEW_DECISION_RULE_VERSION,
            "allowed_relations": ["contradiction", "compatible", "not_applicable"],
            "relation_definitions": dict(V2_HUMAN_REVIEW_RELATION_DEFINITIONS),
            "attempt_number": attempt_number,
            "submission_allowed": submission_allowed,
            "retry_requires_different_authenticated_account": attempt_number == 2,
            "human_attestation_required": True,
            "gold_labels_included": False,
            "gold_rationales_included": False,
            "prior_review_decisions_included": False,
            "items": [
                self._blind_review_item(by_id[case_id])
                for case_id in V2_HUMAN_REVIEW_SAMPLE_CASE_IDS
            ],
        }

    def formal_review_package_payload(self) -> dict[str, Any]:
        """Return the frozen 20-case post-run review sample without outcomes."""

        _registry, cases = self.load()
        by_id = {case.case_id: case for case in cases}
        return {
            "schema_version": "d1-v2-formal-blind-review-package-1.1",
            "sample_version": V2_FORMAL_EVIDENCE_SAMPLE_VERSION,
            "decision_rule_version": V2_HUMAN_REVIEW_DECISION_RULE_VERSION,
            "allowed_relations": ["contradiction", "compatible", "not_applicable"],
            "relation_definitions": dict(V2_HUMAN_REVIEW_RELATION_DEFINITIONS),
            "gold_labels_included": False,
            "gold_rationales_included": False,
            "model_outcomes_included": False,
            "items": [
                self._blind_review_item(by_id[case_id])
                for case_id in V2_FORMAL_EVIDENCE_SAMPLE_CASE_IDS
            ],
        }

    @staticmethod
    def _blind_review_item(case: Any, *, review: dict[str, Any] | None = None) -> dict[str, str]:
        evidence = case.frozen_evidence
        review = review or {}
        return {
            "case_id": case.case_id,
            "prompt": case.prompt,
            "claim_text": evidence.claim_text,
            "evidence_a_text": evidence.evidence_a_text,
            "evidence_b_text": evidence.evidence_b_text,
            "reviewer_relation": str(review.get("reviewer_relation") or ""),
            "reviewer_name": str(review.get("reviewer_name") or ""),
            "review_notes": str(review.get("review_notes") or ""),
        }

    def record_human_review(
        self,
        reviews: list[dict[str, Any]],
        *,
        submitted_by: str,
        expected_attempt_number: int,
        attestation: dict[str, Any] | None,
    ) -> dict[str, Any]:
        """Append one complete blinded review attempt without replacing evidence."""

        submitted_by = str(submitted_by or "").strip()
        if not submitted_by:
            raise ValueError("independent review requires an authenticated submitter")
        expected_attestation = {
            "human_reviewer": True,
            "independent_review": True,
            "gold_labels_not_seen": True,
            "prior_review_decisions_not_seen": True,
        }
        if attestation != expected_attestation:
            raise ValueError("independent review requires the complete human attestation")

        _registry, cases = self.load()
        review_status = self._human_review_status(cases)
        if self.human_review_retry_path.exists() and not self.human_review_path.exists():
            raise ValueError("independent review evidence chain is invalid")
        if not self.human_review_path.exists():
            attempt_number = 1
            target_path = self.human_review_path
        elif review_status["retry_allowed"]:
            attempt_number = 2
            target_path = self.human_review_retry_path
            if submitted_by == review_status["first_submitted_by"]:
                raise ValueError(
                    "second independent review requires a different authenticated account"
                )
        else:
            raise ValueError("independent review has already been recorded")
        if expected_attempt_number != attempt_number:
            raise ValueError("independent review attempt number is stale")
        if target_path.exists():
            raise ValueError("independent review has already been recorded")

        expected_ids = list(V2_HUMAN_REVIEW_SAMPLE_CASE_IDS)
        observed_ids = [str(row.get("case_id") or "") for row in reviews]
        if observed_ids != expected_ids:
            raise ValueError("independent review must use the fixed 10-case order")
        allowed_relations = {"contradiction", "compatible", "not_applicable"}
        normalized: list[dict[str, str]] = []
        reviewed_at = datetime.now(timezone.utc).isoformat()
        previous_attempt_sha256 = (
            str(review_status["attempts"][0].get("sha256") or "")
            if attempt_number == 2
            else ""
        )
        if attempt_number == 2 and len(previous_attempt_sha256) != 64:
            raise ValueError("first independent review artifact cannot be chained")
        for row in reviews:
            relation = str(row.get("reviewer_relation") or "").strip().lower()
            reviewer_name = str(row.get("reviewer_name") or "").strip()
            review_notes = str(row.get("review_notes") or "").strip()
            if relation not in allowed_relations:
                raise ValueError("independent review contains an invalid relation")
            if not reviewer_name:
                raise ValueError("independent review requires reviewer_name")
            if len(review_notes) < 3:
                raise ValueError("independent review requires review_notes")
            normalized.append(
                {
                    "case_id": str(row["case_id"]),
                    "reviewer_relation": relation,
                    "reviewer_name": reviewer_name[:200],
                    "review_notes": review_notes[:2000],
                    "reviewed_at": reviewed_at,
                    "submitted_by": submitted_by[:200],
                    "attempt_number": str(attempt_number),
                    "attestation_version": V2_HUMAN_REVIEW_ATTESTATION_VERSION,
                    "human_reviewer_attested": "true",
                    "independent_review_attested": "true",
                    "gold_labels_not_seen_attested": "true",
                    "prior_review_decisions_not_seen_attested": "true",
                    "previous_attempt_sha256": previous_attempt_sha256,
                }
            )
        self._write_human_review_rows(normalized, path=target_path)
        registry, _cases = self.load()
        return registry.payload()

    def _human_review_status(self, cases: list) -> dict[str, Any]:
        """Evaluate immutable attempts and use the newest attempt as the gate."""

        attempt_statuses: list[dict[str, Any]] = []
        previous_attempt_sha256 = ""
        for attempt_number, path in (
            (1, self.human_review_path),
            (2, self.human_review_retry_path),
        ):
            if not path.exists():
                continue
            status = self._evaluate_human_review_rows(
                cases,
                self._read_human_review_rows(path=path),
                attempt_number=attempt_number,
                require_attestation=attempt_number == 2,
                expected_previous_attempt_sha256=previous_attempt_sha256,
            )
            status.update(
                {
                    "attempt_number": attempt_number,
                    "artifact": path.name,
                    "sha256": self._artifact_sha256(path),
                }
            )
            attempt_statuses.append(status)
            previous_attempt_sha256 = str(status["sha256"] or "")

        if not attempt_statuses:
            return self._empty_review_status()
        active = attempt_statuses[-1]
        first = attempt_statuses[0]
        sequence_valid = [item["attempt_number"] for item in attempt_statuses] == list(
            range(1, len(attempt_statuses) + 1)
        )
        submitter_chain = [
            str(item["submitted_by"] or "") for item in attempt_statuses
        ]
        submitter_chain_valid = (
            all(submitter_chain)
            and len(submitter_chain) == len(set(submitter_chain))
        )
        retry_precondition_valid = (
            len(attempt_statuses) == 1
            or (
                len(attempt_statuses) == 2
                and first["complete"]
                and not first["passed"]
            )
        )
        chain_valid = (
            sequence_valid
            and submitter_chain_valid
            and retry_precondition_valid
        )
        if not chain_valid:
            active = {
                **active,
                "complete": False,
                "passed": False,
                "completed_count": 0,
                "approved_count": 0,
                "rejected_case_ids": [],
                "submitted_by": None,
                "human_attestation_valid": False,
            }
        retry_allowed = (
            chain_valid
            and len(attempt_statuses) == 1
            and first["attempt_number"] == 1
            and first["complete"]
            and not first["passed"]
            and bool(first["submitted_by"])
            and not self.human_review_retry_path.exists()
        )
        return {
            "complete": active["complete"],
            "passed": active["passed"],
            "completed_count": active["completed_count"],
            "approved_count": active["approved_count"],
            "rejected_case_ids": active["rejected_case_ids"],
            "submitted_by": active["submitted_by"],
            "active_attempt_number": active["attempt_number"],
            "attempt_count": len(attempt_statuses),
            "retry_allowed": retry_allowed,
            "first_submitted_by": first["submitted_by"],
            "submitter_ids": tuple(
                str(item["submitted_by"])
                for item in attempt_statuses
                if item["submitted_by"]
            ),
            "attempts": [self._public_attempt_status(item) for item in attempt_statuses],
        }

    def _evaluate_human_review_rows(
        self,
        cases: list,
        rows: list[dict[str, str]],
        *,
        attempt_number: int,
        require_attestation: bool,
        expected_previous_attempt_sha256: str,
    ) -> dict[str, Any]:
        """Compare one fixed review attempt with gold after it is saved."""

        required = {
            "case_id",
            "reviewer_relation",
            "reviewer_name",
            "review_notes",
            "submitted_by",
        }
        attestation_fields = {
            "attempt_number",
            "attestation_version",
            "human_reviewer_attested",
            "independent_review_attested",
            "gold_labels_not_seen_attested",
            "prior_review_decisions_not_seen_attested",
            "previous_attempt_sha256",
        }
        has_attestation_schema = bool(rows) and any(
            bool(attestation_fields.intersection(row)) for row in rows
        )
        if require_attestation or has_attestation_schema:
            required.update(attestation_fields)
        if len(rows) != len(V2_HUMAN_REVIEW_SAMPLE_CASE_IDS) or not rows:
            return self._empty_attempt_status()
        if not all(required.issubset(row) for row in rows):
            return self._empty_attempt_status()
        observed_ids = [str(row.get("case_id") or "") for row in rows]
        if observed_ids != list(V2_HUMAN_REVIEW_SAMPLE_CASE_IDS):
            return self._empty_attempt_status()
        gold_by_id = {
            case.case_id: str(case.frozen_evidence.gold_relation)
            for case in cases
        }
        allowed_relations = {"contradiction", "compatible", "not_applicable"}
        submitters = {
            str(row.get("submitted_by") or "").strip()
            for row in rows
            if str(row.get("submitted_by") or "").strip()
        }
        if len(submitters) != 1:
            return self._empty_attempt_status()
        attestation_valid = all(
            str(row.get("attempt_number") or "") == str(attempt_number)
            and str(row.get("attestation_version") or "")
            == V2_HUMAN_REVIEW_ATTESTATION_VERSION
            and str(row.get("human_reviewer_attested") or "").lower() == "true"
            and str(row.get("independent_review_attested") or "").lower() == "true"
            and str(row.get("gold_labels_not_seen_attested") or "").lower() == "true"
            and str(row.get("prior_review_decisions_not_seen_attested") or "").lower()
            == "true"
            and str(row.get("previous_attempt_sha256") or "")
            == expected_previous_attempt_sha256
            for row in rows
        )
        if (require_attestation or has_attestation_schema) and not attestation_valid:
            return self._empty_attempt_status()
        completed: list[str] = []
        approved: list[str] = []
        rejected: list[str] = []
        for row in rows:
            case_id = str(row.get("case_id") or "")
            relation = str(row.get("reviewer_relation") or "").strip().lower()
            named = bool(str(row.get("reviewer_name") or "").strip())
            noted = bool(str(row.get("review_notes") or "").strip())
            if named and noted and relation in allowed_relations:
                completed.append(case_id)
                if relation == gold_by_id.get(case_id):
                    approved.append(case_id)
                else:
                    rejected.append(case_id)
        required_count = len(V2_HUMAN_REVIEW_SAMPLE_CASE_IDS)
        return {
            "complete": len(completed) == required_count,
            "passed": len(approved) == required_count and not rejected,
            "completed_count": len(completed),
            "approved_count": len(approved),
            "rejected_case_ids": rejected,
            "submitted_by": next(iter(submitters)),
            "human_attestation_valid": attestation_valid,
        }

    def _read_human_review_rows(
        self,
        *,
        path: Path | None = None,
    ) -> list[dict[str, str]]:
        source = path or self.human_review_path
        if not source.is_file():
            return []
        try:
            with source.open(encoding="utf-8", newline="") as handle:
                return [dict(row) for row in csv.DictReader(handle)]
        except (OSError, csv.Error):
            return []

    def _write_human_review_rows(
        self,
        rows: list[dict[str, str]],
        *,
        path: Path,
    ) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(
            f".{path.name}.{os.getpid()}.{time_ns()}.tmp"
        )
        fieldnames = [
            "case_id",
            "reviewer_relation",
            "reviewer_name",
            "review_notes",
            "reviewed_at",
            "submitted_by",
            "attempt_number",
            "attestation_version",
            "human_reviewer_attested",
            "independent_review_attested",
            "gold_labels_not_seen_attested",
            "prior_review_decisions_not_seen_attested",
            "previous_attempt_sha256",
        ]
        try:
            with temporary.open("x", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerows(rows)
                handle.flush()
                os.fsync(handle.fileno())
            try:
                os.link(temporary, path)
            except FileExistsError as exc:
                raise ValueError("independent review has already been recorded") from exc
        finally:
            temporary.unlink(missing_ok=True)

    @staticmethod
    def _artifact_sha256(path: Path) -> str | None:
        try:
            return hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError:
            return None

    @staticmethod
    def _public_attempt_status(status: dict[str, Any]) -> dict[str, Any]:
        return {
            "attempt_number": status["attempt_number"],
            "artifact": status["artifact"],
            "sha256": status["sha256"],
            "complete": status["complete"],
            "passed": status["passed"],
            "completed_count": status["completed_count"],
            "approved_count": status["approved_count"],
            "disagreement_count": len(status["rejected_case_ids"]),
            "submitter_present": bool(status["submitted_by"]),
            "human_attestation_valid": status["human_attestation_valid"],
        }

    @staticmethod
    def _empty_attempt_status() -> dict[str, Any]:
        return {
            "complete": False,
            "passed": False,
            "completed_count": 0,
            "approved_count": 0,
            "rejected_case_ids": [],
            "submitted_by": None,
            "human_attestation_valid": False,
        }

    @staticmethod
    def _empty_review_status() -> dict[str, Any]:
        return {
            "complete": False,
            "passed": False,
            "completed_count": 0,
            "approved_count": 0,
            "rejected_case_ids": [],
            "submitted_by": None,
            "active_attempt_number": 0,
            "attempt_count": 0,
            "retry_allowed": False,
            "first_submitted_by": None,
            "submitter_ids": (),
            "attempts": [],
        }


_DEFAULT_V2_REGISTRY = D1V2DatasetRegistryService()


def default_d1_v2_registry_service() -> D1V2DatasetRegistryService:
    return _DEFAULT_V2_REGISTRY


def load_registered_d1_v2() -> tuple[D1V2DatasetRegistry, list]:
    return _DEFAULT_V2_REGISTRY.load()
