from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from threading import RLock
from typing import Protocol

from sqlalchemy import Engine, text


@dataclass(frozen=True)
class AuditFailureCase:
    """One durable record of an audit that did not pass on the first try.

    This is the user-facing counterpart of the repair loop: every time the
    audit decision is revise/reject/needs_human_review (or passes only after
    a bounded repair), the case is recorded so the system can learn which
    failure modes are frequent, which repairs succeed, and which cases had to
    be escalated.  ``released`` marks whether the final content was still
    delivered to the learner (non-red-line findings are released, red-line
    findings are held for human review).
    """

    case_id: str
    execution_id: str
    learner_id: str
    task_type: str
    audit_result_id: str
    decision: str
    released: bool
    issue_types: list[str]
    findings: list[str]
    repair_json: dict | None
    input_digest: str
    created_at: datetime | None = None


class FailureCaseRepository(Protocol):
    def save(self, case: AuditFailureCase) -> None: ...

    def list_recent(self, limit: int = 50) -> list[AuditFailureCase]: ...

    def count_by_issue_type(self) -> dict[str, int]: ...

    def repair_success_rate(self) -> float: ...

    def escalated_rate(self) -> float: ...

    def released_rate(self) -> float: ...


class InMemoryFailureCaseRepository:
    def __init__(self) -> None:
        self._cases: dict[str, AuditFailureCase] = {}
        self._lock = RLock()

    def save(self, case: AuditFailureCase) -> None:
        with self._lock:
            self._cases[case.case_id] = case

    def list_recent(self, limit: int = 50) -> list[AuditFailureCase]:
        with self._lock:
            values = list(self._cases.values())
        return sorted(
            values, key=lambda item: item.created_at or datetime.min, reverse=True
        )[:limit]

    def count_by_issue_type(self) -> dict[str, int]:
        with self._lock:
            values = list(self._cases.values())
        counts: dict[str, int] = {}
        for case in values:
            for issue_type in case.issue_types:
                counts[issue_type] = counts.get(issue_type, 0) + 1
        return counts

    def repair_success_rate(self) -> float:
        with self._lock:
            values = list(self._cases.values())
        repaired = [case for case in values if case.repair_json]
        if not repaired:
            return 0.0
        succeeded = sum(
            1
            for case in repaired
            if (case.repair_json or {}).get("final_audit_decision") == "pass"
        )
        return round(succeeded / len(repaired), 4)

    def escalated_rate(self) -> float:
        with self._lock:
            values = list(self._cases.values())
        if not values:
            return 0.0
        escalated = sum(
            1
            for case in values
            if case.decision in {"needs_human_review", "reject"}
        )
        return round(escalated / len(values), 4)

    def released_rate(self) -> float:
        with self._lock:
            values = list(self._cases.values())
        if not values:
            return 0.0
        released = sum(1 for case in values if case.released)
        return round(released / len(values), 4)


class SqlFailureCaseRepository:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def save(self, case: AuditFailureCase) -> None:
        with self.engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO audit_failure_cases ("
                    "case_id, execution_id, learner_id, task_type, audit_result_id, "
                    "decision, released, issue_types, findings, repair_json, input_digest"
                    ") VALUES ("
                    ":case_id, :execution_id, :learner_id, :task_type, :audit_result_id, "
                    ":decision, :released, :issue_types, :findings, :repair_json, :input_digest"
                    ") ON DUPLICATE KEY UPDATE "
                    "decision=VALUES(decision), released=VALUES(released), "
                    "issue_types=VALUES(issue_types), findings=VALUES(findings), "
                    "repair_json=VALUES(repair_json)"
                ),
                {
                    "case_id": case.case_id,
                    "execution_id": case.execution_id,
                    "learner_id": case.learner_id,
                    "task_type": case.task_type,
                    "audit_result_id": case.audit_result_id,
                    "decision": case.decision,
                    "released": 1 if case.released else 0,
                    "issue_types": json.dumps(case.issue_types, ensure_ascii=False),
                    "findings": json.dumps(case.findings, ensure_ascii=False),
                    "repair_json": (
                        json.dumps(case.repair_json, ensure_ascii=False)
                        if case.repair_json
                        else None
                    ),
                    "input_digest": case.input_digest,
                },
            )

    def list_recent(self, limit: int = 50) -> list[AuditFailureCase]:
        with self.engine.connect() as connection:
            rows = connection.execute(
                text(
                    "SELECT * FROM audit_failure_cases "
                    "ORDER BY created_at DESC LIMIT :limit"
                ),
                {"limit": limit},
            ).mappings()
            return [self._from_row(dict(row)) for row in rows]

    def count_by_issue_type(self) -> dict[str, int]:
        with self.engine.connect() as connection:
            rows = connection.execute(
                text(
                    "SELECT issue_types FROM audit_failure_cases"
                )
            ).mappings()
        counts: dict[str, int] = {}
        for row in rows:
            try:
                types = json.loads(row["issue_types"] or "[]")
            except (json.JSONDecodeError, TypeError):
                continue
            if not isinstance(types, list):
                continue
            for issue_type in types:
                if isinstance(issue_type, str):
                    counts[issue_type] = counts.get(issue_type, 0) + 1
        return counts

    def repair_success_rate(self) -> float:
        with self.engine.connect() as connection:
            rows = connection.execute(
                text(
                    "SELECT repair_json FROM audit_failure_cases "
                    "WHERE repair_json IS NOT NULL"
                )
            ).mappings()
        repaired = list(rows)
        if not repaired:
            return 0.0
        succeeded = 0
        for row in repaired:
            try:
                payload = json.loads(row["repair_json"] or "{}")
            except (json.JSONDecodeError, TypeError):
                continue
            if (payload or {}).get("final_audit_decision") == "pass":
                succeeded += 1
        return round(succeeded / len(repaired), 4)

    def escalated_rate(self) -> float:
        with self.engine.connect() as connection:
            total, escalated = connection.execute(
                text(
                    "SELECT COUNT(*), "
                    "SUM(CASE WHEN decision IN ('needs_human_review','reject') THEN 1 ELSE 0 END) "
                    "FROM audit_failure_cases"
                )
            ).one()
        if not total:
            return 0.0
        return round((escalated or 0) / total, 4)

    def released_rate(self) -> float:
        with self.engine.connect() as connection:
            total, released = connection.execute(
                text(
                    "SELECT COUNT(*), SUM(released) FROM audit_failure_cases"
                )
            ).one()
        if not total:
            return 0.0
        return round((released or 0) / total, 4)

    @staticmethod
    def _from_row(row: dict) -> AuditFailureCase:
        def _json(value, default=None):
            try:
                return json.loads(value) if isinstance(value, str) else value
            except (json.JSONDecodeError, TypeError):
                return default

        return AuditFailureCase(
            case_id=str(row.get("case_id") or ""),
            execution_id=str(row.get("execution_id") or ""),
            learner_id=str(row.get("learner_id") or ""),
            task_type=str(row.get("task_type") or ""),
            audit_result_id=str(row.get("audit_result_id") or ""),
            decision=str(row.get("decision") or ""),
            released=bool(row.get("released")),
            issue_types=[str(item) for item in (_json(row.get("issue_types")) or [])],
            findings=[str(item) for item in (_json(row.get("findings")) or [])],
            repair_json=_json(row.get("repair_json")),
            input_digest=str(row.get("input_digest") or ""),
            created_at=row.get("created_at"),
        )
