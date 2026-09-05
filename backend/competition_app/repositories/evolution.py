from __future__ import annotations

import json
from datetime import datetime, timezone
from threading import RLock
from typing import Protocol

from sqlalchemy import Engine, text

from competition_app.contracts.evolution import (
    EvolutionEvaluationSummary,
    EvolutionFeedback,
    EvolutionRule,
    EvolutionRuleContract,
    EvolutionRuleRun,
    FailureSignature,
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _json(value, default):
    if value is None:
        return default
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return default


class EvolutionRepository(Protocol):
    def save_feedback(self, feedback: EvolutionFeedback) -> EvolutionFeedback: ...
    def list_feedback(self, *, status: str | None = None, limit: int = 100) -> list[EvolutionFeedback]: ...
    def get_feedback(self, feedback_id: str) -> EvolutionFeedback | None: ...
    def save_signature(self, signature: FailureSignature) -> FailureSignature: ...
    def get_signature(self, signature_id: str) -> FailureSignature | None: ...
    def get_signature_by_key(self, signature_key: str) -> FailureSignature | None: ...
    def list_signatures(self, *, ready_only: bool = False, limit: int = 100) -> list[FailureSignature]: ...
    def save_rule(self, rule: EvolutionRule) -> EvolutionRule: ...
    def get_rule(self, rule_id: str, version: int | None = None) -> EvolutionRule | None: ...
    def list_rules(self, *, status: str | None = None, limit: int = 100) -> list[EvolutionRule]: ...
    def save_run(self, run: EvolutionRuleRun) -> EvolutionRuleRun: ...
    def list_runs(self, *, rule_id: str | None = None, limit: int = 100) -> list[EvolutionRuleRun]: ...
    def save_evaluation(self, summary: EvolutionEvaluationSummary) -> EvolutionEvaluationSummary: ...
    def get_evaluation(self, evaluation_id: str) -> EvolutionEvaluationSummary | None: ...
    def list_evaluations(self, *, rule_id: str | None = None, limit: int = 100) -> list[EvolutionEvaluationSummary]: ...


class InMemoryEvolutionRepository:
    def __init__(self) -> None:
        self._feedback: dict[str, EvolutionFeedback] = {}
        self._signatures: dict[str, FailureSignature] = {}
        self._rules: dict[tuple[str, int], EvolutionRule] = {}
        self._runs: dict[str, EvolutionRuleRun] = {}
        self._evaluations: dict[str, EvolutionEvaluationSummary] = {}
        self._lock = RLock()

    def save_feedback(self, feedback: EvolutionFeedback) -> EvolutionFeedback:
        item = feedback.model_copy(
            update={"created_at": feedback.created_at or _now()}
        )
        with self._lock:
            duplicate = next(
                (row for row in self._feedback.values() if row.dedup_key == item.dedup_key),
                None,
            )
            if duplicate is not None and duplicate.feedback_id != item.feedback_id:
                if duplicate.status == "rejected" and item.status == "validated":
                    self._feedback[item.feedback_id] = item
                    return item
                return duplicate
            self._feedback[item.feedback_id] = item
        return item

    def list_feedback(self, *, status: str | None = None, limit: int = 100) -> list[EvolutionFeedback]:
        with self._lock:
            rows = list(self._feedback.values())
        if status:
            rows = [row for row in rows if row.status == status]
        return sorted(rows, key=lambda row: row.created_at or datetime.min.replace(tzinfo=timezone.utc), reverse=True)[:limit]

    def get_feedback(self, feedback_id: str) -> EvolutionFeedback | None:
        with self._lock:
            return self._feedback.get(feedback_id)

    def save_signature(self, signature: FailureSignature) -> FailureSignature:
        item = signature.model_copy(
            update={
                "first_seen_at": signature.first_seen_at or _now(),
                "last_seen_at": signature.last_seen_at or _now(),
            }
        )
        with self._lock:
            self._signatures[item.signature_id] = item
        return item

    def get_signature(self, signature_id: str) -> FailureSignature | None:
        with self._lock:
            return self._signatures.get(signature_id)

    def get_signature_by_key(self, signature_key: str) -> FailureSignature | None:
        with self._lock:
            return next((row for row in self._signatures.values() if row.signature_key == signature_key), None)

    def list_signatures(self, *, ready_only: bool = False, limit: int = 100) -> list[FailureSignature]:
        with self._lock:
            rows = list(self._signatures.values())
        if ready_only:
            rows = [row for row in rows if row.candidate_ready]
        return sorted(rows, key=lambda row: row.last_seen_at or datetime.min.replace(tzinfo=timezone.utc), reverse=True)[:limit]

    def save_rule(self, rule: EvolutionRule) -> EvolutionRule:
        now = _now()
        item = rule.model_copy(
            update={
                "created_at": rule.created_at or now,
                "updated_at": now,
            }
        )
        with self._lock:
            self._rules[(item.rule_id, item.version)] = item
        return item

    def get_rule(self, rule_id: str, version: int | None = None) -> EvolutionRule | None:
        with self._lock:
            rows = [row for (rid, _), row in self._rules.items() if rid == rule_id]
        if not rows:
            return None
        if version is not None:
            return next((row for row in rows if row.version == version), None)
        return max(rows, key=lambda row: row.version)

    def list_rules(self, *, status: str | None = None, limit: int = 100) -> list[EvolutionRule]:
        with self._lock:
            rows = list(self._rules.values())
        if status:
            rows = [row for row in rows if row.status == status]
        return sorted(rows, key=lambda row: row.updated_at or datetime.min.replace(tzinfo=timezone.utc), reverse=True)[:limit]

    def save_run(self, run: EvolutionRuleRun) -> EvolutionRuleRun:
        item = run.model_copy(update={"created_at": run.created_at or _now()})
        with self._lock:
            self._runs[item.run_id] = item
        return item

    def list_runs(self, *, rule_id: str | None = None, limit: int = 100) -> list[EvolutionRuleRun]:
        with self._lock:
            rows = list(self._runs.values())
        if rule_id:
            rows = [row for row in rows if row.rule_id == rule_id]
        return sorted(rows, key=lambda row: row.created_at or datetime.min.replace(tzinfo=timezone.utc), reverse=True)[:limit]

    def save_evaluation(self, summary: EvolutionEvaluationSummary) -> EvolutionEvaluationSummary:
        item = summary.model_copy(update={"created_at": summary.created_at or _now()})
        with self._lock:
            duplicate = next(
                (
                    value
                    for value in self._evaluations.values()
                    if value.rule_id == item.rule_id
                    and value.rule_version == item.rule_version
                    and value.dataset_id == item.dataset_id
                    and value.dataset_version == item.dataset_version
                    and value.dataset_digest == item.dataset_digest
                ),
                None,
            )
            if duplicate is not None and duplicate.evaluation_id != item.evaluation_id:
                raise ValueError("evaluation already exists for this rule version and dataset")
            self._evaluations[item.evaluation_id] = item
        return item

    def get_evaluation(self, evaluation_id: str) -> EvolutionEvaluationSummary | None:
        with self._lock:
            return self._evaluations.get(evaluation_id)

    def list_evaluations(
        self, *, rule_id: str | None = None, limit: int = 100
    ) -> list[EvolutionEvaluationSummary]:
        with self._lock:
            rows = list(self._evaluations.values())
        if rule_id:
            rows = [row for row in rows if row.rule_id == rule_id]
        return sorted(
            rows,
            key=lambda row: row.created_at or datetime.min.replace(tzinfo=timezone.utc),
            reverse=True,
        )[:limit]


class SqlEvolutionRepository(InMemoryEvolutionRepository):
    """SQL implementation with dialect-neutral select-then-write semantics."""

    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def save_evaluation(self, summary: EvolutionEvaluationSummary) -> EvolutionEvaluationSummary:
        """Validate that the service-owned gate run already persists the summary."""

        existing = self._one(
            "SELECT run_id FROM evolution_rule_runs "
            "WHERE rule_id=:rule_id AND rule_version=:rule_version "
            "AND run_type=:run_type AND input_digest=:input_digest LIMIT 1",
            {
                "rule_id": summary.rule_id,
                "rule_version": summary.rule_version,
                "run_type": "behavior_eval_gate",
                "input_digest": summary.dataset_digest,
            },
        )
        if existing is None:
            raise ValueError("behavior evaluation gate run must be saved first")
        return summary

    def save_feedback(self, feedback: EvolutionFeedback) -> EvolutionFeedback:
        existing = self._one(
            "SELECT * FROM evolution_feedback WHERE dedup_key=:key",
            {"key": feedback.dedup_key},
        )
        if existing and str(existing.get("feedback_id")) != feedback.feedback_id:
            existing_item = self._feedback(existing)
            if not (existing_item.status == "rejected" and feedback.status == "validated"):
                return existing_item
        with self.engine.begin() as connection:
            if existing:
                feedback = feedback.model_copy(
                    update={"feedback_id": str(existing.get("feedback_id"))}
                )
                connection.execute(text(
                    "UPDATE evolution_feedback SET trust_level=:trust_level,status=:status,target_agent=:target_agent,owner_step_id=:owner_step_id,issue_type=:issue_type,severity=:severity,field_path=:field_path,constraint_category=:constraint_category,summary=:summary,reviewer_id=:reviewer_id,reviewed_at=:reviewed_at WHERE feedback_id=:feedback_id"
                ), feedback.model_dump(mode="python", exclude={"created_at"}))
            else:
                connection.execute(text(
                    "INSERT INTO evolution_feedback (feedback_id,source_type,trust_level,status,execution_id,conversation_id,message_id,learner_id,task_type,target_agent,owner_step_id,issue_type,severity,field_path,constraint_category,summary,source_case_id,dedup_key,reviewer_id,reviewed_at) "
                    "VALUES (:feedback_id,:source_type,:trust_level,:status,:execution_id,:conversation_id,:message_id,:learner_id,:task_type,:target_agent,:owner_step_id,:issue_type,:severity,:field_path,:constraint_category,:summary,:source_case_id,:dedup_key,:reviewer_id,:reviewed_at)"
                ), feedback.model_dump(mode="python", exclude={"created_at"}))
        return self.get_feedback(feedback.feedback_id) or feedback

    def list_feedback(self, *, status: str | None = None, limit: int = 100) -> list[EvolutionFeedback]:
        where = " WHERE status=:status" if status else ""
        params = {"limit": limit, **({"status": status} if status else {})}
        return [self._feedback(row) for row in self._all(
            f"SELECT * FROM evolution_feedback{where} ORDER BY created_at DESC LIMIT :limit", params
        )]

    def get_feedback(self, feedback_id: str) -> EvolutionFeedback | None:
        row = self._one("SELECT * FROM evolution_feedback WHERE feedback_id=:id", {"id": feedback_id})
        return self._feedback(row) if row else None

    def save_signature(self, signature: FailureSignature) -> FailureSignature:
        existing = self.get_signature_by_key(signature.signature_key)
        payload = signature.model_dump(mode="python", exclude={"first_seen_at", "last_seen_at"})
        payload["source_case_ids"] = json.dumps(payload["source_case_ids"], ensure_ascii=False)
        payload["source_execution_ids"] = json.dumps(payload["source_execution_ids"], ensure_ascii=False)
        payload["candidate_ready"] = 1 if signature.candidate_ready else 0
        with self.engine.begin() as connection:
            if existing:
                payload["signature_id"] = existing.signature_id
                connection.execute(text(
                    "UPDATE evolution_signatures SET case_count=:case_count,execution_count=:execution_count,high_trust_count=:high_trust_count,source_case_ids=:source_case_ids,source_execution_ids=:source_execution_ids,candidate_ready=:candidate_ready,last_seen_at=CURRENT_TIMESTAMP WHERE signature_id=:signature_id"
                ), payload)
            else:
                connection.execute(text(
                    "INSERT INTO evolution_signatures (signature_id,signature_key,task_type,owner_step_id,target_agent,issue_type,field_path,constraint_category,case_count,execution_count,high_trust_count,source_case_ids,source_execution_ids,candidate_ready) VALUES (:signature_id,:signature_key,:task_type,:owner_step_id,:target_agent,:issue_type,:field_path,:constraint_category,:case_count,:execution_count,:high_trust_count,:source_case_ids,:source_execution_ids,:candidate_ready)"
                ), payload)
        return self.get_signature(existing.signature_id if existing else signature.signature_id) or signature

    def get_signature(self, signature_id: str) -> FailureSignature | None:
        row = self._one("SELECT * FROM evolution_signatures WHERE signature_id=:id", {"id": signature_id})
        return self._signature(row) if row else None

    def get_signature_by_key(self, signature_key: str) -> FailureSignature | None:
        row = self._one("SELECT * FROM evolution_signatures WHERE signature_key=:key", {"key": signature_key})
        return self._signature(row) if row else None

    def list_signatures(self, *, ready_only: bool = False, limit: int = 100) -> list[FailureSignature]:
        where = " WHERE candidate_ready=1" if ready_only else ""
        return [self._signature(row) for row in self._all(
            f"SELECT * FROM evolution_signatures{where} ORDER BY last_seen_at DESC LIMIT :limit", {"limit": limit}
        )]

    def save_rule(self, rule: EvolutionRule) -> EvolutionRule:
        payload = rule.model_dump(mode="python", exclude={"contract", "created_at", "updated_at"})
        payload["contract_json"] = json.dumps(rule.contract.model_dump(mode="json"), ensure_ascii=False)
        payload["replay_metrics"] = json.dumps(rule.replay_metrics, ensure_ascii=False)
        existing = self.get_rule(rule.rule_id, rule.version)
        with self.engine.begin() as connection:
            if existing:
                connection.execute(text(
                    "UPDATE evolution_rules SET signature_id=:signature_id,natural_language_analysis=:natural_language_analysis,contract_json=:contract_json,strategy_text=:strategy_text,status=:status,replay_metrics=:replay_metrics,approved_by=:approved_by,reviewer_domain=:reviewer_domain,approval_note=:approval_note,updated_at=CURRENT_TIMESTAMP WHERE rule_id=:rule_id AND version=:version"
                ), payload)
            else:
                connection.execute(text(
                    "INSERT INTO evolution_rules (rule_id,version,signature_id,natural_language_analysis,contract_json,strategy_text,status,replay_metrics,approved_by,reviewer_domain,approval_note) VALUES (:rule_id,:version,:signature_id,:natural_language_analysis,:contract_json,:strategy_text,:status,:replay_metrics,:approved_by,:reviewer_domain,:approval_note)"
                ), payload)
        return self.get_rule(rule.rule_id, rule.version) or rule

    def get_rule(self, rule_id: str, version: int | None = None) -> EvolutionRule | None:
        if version is None:
            row = self._one("SELECT * FROM evolution_rules WHERE rule_id=:id ORDER BY version DESC LIMIT 1", {"id": rule_id})
        else:
            row = self._one("SELECT * FROM evolution_rules WHERE rule_id=:id AND version=:version", {"id": rule_id, "version": version})
        return self._rule(row) if row else None

    def list_rules(self, *, status: str | None = None, limit: int = 100) -> list[EvolutionRule]:
        where = " WHERE status=:status" if status else ""
        params = {"limit": limit, **({"status": status} if status else {})}
        return [self._rule(row) for row in self._all(
            f"SELECT * FROM evolution_rules{where} ORDER BY updated_at DESC LIMIT :limit", params
        )]

    def save_run(self, run: EvolutionRuleRun) -> EvolutionRuleRun:
        payload = run.model_dump(mode="python", exclude={"created_at"})
        payload["matched"] = 1 if run.matched else 0
        payload["released"] = 1 if run.released else 0
        payload["metrics"] = json.dumps(run.metrics, ensure_ascii=False)
        with self.engine.begin() as connection:
            connection.execute(text(
                "INSERT INTO evolution_rule_runs (run_id,rule_id,rule_version,run_type,execution_id,target_agent,matched,first_audit_decision,final_audit_decision,repair_count,released,metrics,input_digest) VALUES (:run_id,:rule_id,:rule_version,:run_type,:execution_id,:target_agent,:matched,:first_audit_decision,:final_audit_decision,:repair_count,:released,:metrics,:input_digest)"
            ), payload)
        return run

    def list_runs(self, *, rule_id: str | None = None, limit: int = 100) -> list[EvolutionRuleRun]:
        where = " WHERE rule_id=:rule_id" if rule_id else ""
        params = {"limit": limit, **({"rule_id": rule_id} if rule_id else {})}
        return [self._run(row) for row in self._all(
            f"SELECT * FROM evolution_rule_runs{where} ORDER BY created_at DESC LIMIT :limit", params
        )]

    def get_evaluation(self, evaluation_id: str) -> EvolutionEvaluationSummary | None:
        rows = self._all(
            "SELECT metrics FROM evolution_rule_runs "
            "WHERE run_type=:run_type ORDER BY created_at DESC LIMIT 500",
            {"run_type": "behavior_eval_gate"},
        )
        for row in rows:
            metrics = _json(row.get("metrics"), {})
            if metrics.get("schema_version") in {
                "evolution-evaluation-summary-1.0",
                "evolution-evaluation-summary-1.1",
            }:
                metrics.pop("schema_version", None)
            if metrics.get("evaluation_id") == evaluation_id:
                try:
                    return EvolutionEvaluationSummary.model_validate(metrics)
                except Exception:
                    continue
        return None

    def list_evaluations(
        self, *, rule_id: str | None = None, limit: int = 100
    ) -> list[EvolutionEvaluationSummary]:
        where = " WHERE run_type=:run_type"
        params: dict[str, object] = {"run_type": "behavior_eval_gate", "limit": limit}
        if rule_id:
            where += " AND rule_id=:rule_id"
            params["rule_id"] = rule_id
        result: list[EvolutionEvaluationSummary] = []
        for row in self._all(
            f"SELECT metrics FROM evolution_rule_runs{where} ORDER BY created_at DESC LIMIT :limit",
            params,
        ):
            metrics = _json(row.get("metrics"), {})
            if metrics.get("schema_version") in {
                "evolution-evaluation-summary-1.0",
                "evolution-evaluation-summary-1.1",
            }:
                metrics.pop("schema_version", None)
            try:
                result.append(EvolutionEvaluationSummary.model_validate(metrics))
            except Exception:
                continue
        return result

    def _one(self, sql: str, params: dict) -> dict | None:
        with self.engine.connect() as connection:
            row = connection.execute(text(sql), params).mappings().first()
        return dict(row) if row else None

    def _all(self, sql: str, params: dict) -> list[dict]:
        with self.engine.connect() as connection:
            return [dict(row) for row in connection.execute(text(sql), params).mappings()]

    @staticmethod
    def _feedback(row: dict) -> EvolutionFeedback:
        return EvolutionFeedback.model_validate(row)

    @staticmethod
    def _signature(row: dict) -> FailureSignature:
        row = dict(row)
        row["candidate_ready"] = bool(row.get("candidate_ready"))
        row["source_case_ids"] = _json(row.get("source_case_ids"), [])
        row["source_execution_ids"] = _json(row.get("source_execution_ids"), [])
        return FailureSignature.model_validate(row)

    @staticmethod
    def _rule(row: dict) -> EvolutionRule:
        row = dict(row)
        row["contract"] = EvolutionRuleContract.model_validate(_json(row.pop("contract_json", None), {}))
        row["replay_metrics"] = _json(row.get("replay_metrics"), {})
        return EvolutionRule.model_validate(row)

    @staticmethod
    def _run(row: dict) -> EvolutionRuleRun:
        row = dict(row)
        row["matched"] = bool(row.get("matched"))
        row["released"] = bool(row.get("released"))
        row["metrics"] = _json(row.get("metrics"), {})
        return EvolutionRuleRun.model_validate(row)
