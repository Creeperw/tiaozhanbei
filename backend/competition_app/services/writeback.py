from __future__ import annotations

import json

from sqlalchemy import Engine, text
from sqlalchemy.exc import IntegrityError

from competition_app.contracts.base import WritebackIntent


class WritebackExecutor:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def execute(
        self,
        intent: WritebackIntent,
        *,
        satisfied_preconditions: set[str] | None = None,
    ) -> bool:
        handlers = {
            "publish_resource": self._publish_resource,
            "upsert_review_task": self._upsert_review_task,
            "bind_review_resource": self._bind_review_resource,
        }
        if intent.effect_type not in handlers:
            raise ValueError(f"unsupported writeback effect: {intent.effect_type}")
        satisfied = satisfied_preconditions or set()
        missing = set(intent.preconditions) - satisfied
        if missing:
            raise ValueError(f"writeback precondition not satisfied: {', '.join(sorted(missing))}")
        try:
            with self.engine.begin() as connection:
                handlers[intent.effect_type](connection, intent.payload)
                connection.execute(
                    text(
                        "INSERT INTO writeback_intents "
                        "(idempotency_key, intent_id, status, payload_json) "
                        "VALUES (:idempotency_key, :intent_id, :status, :payload_json)"
                    ),
                    {
                        "idempotency_key": intent.idempotency_key,
                        "intent_id": intent.intent_id,
                        "status": "applied",
                        "payload_json": json.dumps(intent.payload, ensure_ascii=False),
                    },
                )
        except IntegrityError:
            return False
        return True

    def execute_batch(self, intents: list[WritebackIntent]) -> bool:
        if not intents:
            return True
        handlers = self._handlers()
        for intent in intents:
            if intent.effect_type not in handlers:
                raise ValueError(f"unsupported writeback effect: {intent.effect_type}")
        keys = [intent.idempotency_key for intent in intents]
        with self.engine.connect() as connection:
            existing = {
                row[0]
                for key in keys
                for row in connection.execute(
                    text("SELECT idempotency_key FROM writeback_intents WHERE idempotency_key=:key"),
                    {"key": key},
                )
            }
        if len(existing) == len(keys):
            return False
        if existing:
            raise RuntimeError("partial idempotency replay detected")

        with self.engine.begin() as connection:
            for intent in intents:
                if "audit_pass" in intent.preconditions:
                    audit_id = intent.payload.get("audit_result_id")
                    decision = connection.execute(
                        text("SELECT decision FROM audit_results WHERE audit_result_id=:audit_id"),
                        {"audit_id": audit_id},
                    ).scalar_one_or_none()
                    if decision != "pass":
                        raise ValueError("persisted audit_pass precondition not satisfied")
                handlers[intent.effect_type](connection, intent.payload)
                self._record_intent(connection, intent)
        return True

    def _handlers(self):
        return {
            "record_audit": self._record_audit,
            "publish_resource": self._publish_resource,
            "upsert_review_task": self._upsert_review_task,
            "bind_review_resource": self._bind_review_resource,
        }

    @staticmethod
    def _record_intent(connection, intent: WritebackIntent) -> None:
        connection.execute(
            text(
                "INSERT INTO writeback_intents "
                "(idempotency_key, intent_id, status, payload_json) "
                "VALUES (:idempotency_key, :intent_id, :status, :payload_json)"
            ),
            {
                "idempotency_key": intent.idempotency_key,
                "intent_id": intent.intent_id,
                "status": "applied",
                "payload_json": json.dumps(intent.payload, ensure_ascii=False),
            },
        )

    @staticmethod
    def _record_audit(connection, payload: dict) -> None:
        connection.execute(
            text(
                "INSERT INTO audit_results "
                "(audit_result_id, resource_id, decision, payload_json) "
                "VALUES (:audit_result_id, :resource_id, :decision, :payload_json)"
            ),
            {
                "audit_result_id": payload["audit_result_id"],
                "resource_id": payload["resource_id"],
                "decision": payload["decision"],
                "payload_json": json.dumps(payload, ensure_ascii=False),
            },
        )

    @staticmethod
    def _publish_resource(connection, payload: dict) -> None:
        connection.execute(
            text(
                "INSERT INTO resource_versions (resource_id, version, status, payload_json) "
                "VALUES (:resource_id, :version, :status, :payload_json)"
            ),
            {
                "resource_id": payload["resource_id"],
                "version": payload.get("version", 1),
                "status": payload["status"],
                "payload_json": json.dumps(payload, ensure_ascii=False),
            },
        )

    @staticmethod
    def _upsert_review_task(connection, payload: dict) -> None:
        existing = connection.execute(
            text("SELECT review_task_id FROM review_tasks WHERE review_task_id=:review_task_id"),
            {"review_task_id": payload["review_task_id"]},
        ).first()
        values = {
            "review_task_id": payload["review_task_id"],
            "learner_id": payload["learner_id"],
            "primary_kp_id": payload["primary_kp_id"],
            "status": payload["status"],
            "payload_json": json.dumps(payload, ensure_ascii=False),
        }
        if existing:
            connection.execute(
                text(
                    "UPDATE review_tasks SET status=:status, payload_json=:payload_json "
                    "WHERE review_task_id=:review_task_id"
                ),
                values,
            )
        else:
            connection.execute(
                text(
                    "INSERT INTO review_tasks "
                    "(review_task_id, learner_id, primary_kp_id, status, payload_json) "
                    "VALUES (:review_task_id, :learner_id, :primary_kp_id, :status, :payload_json)"
                ),
                values,
            )

    @staticmethod
    def _bind_review_resource(connection, payload: dict) -> None:
        connection.execute(
            text(
                "INSERT INTO review_resource_bindings "
                "(binding_id, review_task_id, resource_id, resource_version, audit_result_id) "
                "VALUES (:binding_id, :review_task_id, :resource_id, :resource_version, :audit_result_id)"
            ),
            payload,
        )
