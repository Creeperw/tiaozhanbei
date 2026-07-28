from __future__ import annotations

import json

from sqlalchemy import Engine, text
from sqlalchemy.exc import IntegrityError

from competition_app.contracts.base import WritebackIntent


class WritebackExecutor:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine
        self._ensure_workshop_outbox()

    def _ensure_workshop_outbox(self) -> None:
        payload_type = "JSON" if self.engine.dialect.name == "mysql" else "TEXT"
        delivered_type = "DATETIME" if self.engine.dialect.name == "sqlite" else "TIMESTAMP NULL"
        with self.engine.begin() as connection:
            connection.execute(text(
                "CREATE TABLE IF NOT EXISTS workshop_publication_outbox ("
                "operation_id VARCHAR(128) PRIMARY KEY, "
                "artifact_type VARCHAR(64) NOT NULL, "
                "learner_id VARCHAR(128) NOT NULL, "
                "status VARCHAR(32) NOT NULL, "
                f"payload_json {payload_type} NOT NULL, "
                "attempt_count INT NOT NULL DEFAULT 0, "
                "last_error VARCHAR(255) NULL, "
                f"delivered_at {delivered_type})"
            ))

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
            "enqueue_workshop_publication": self._enqueue_workshop_publication,
        }

    def dispatch_workshop_publication(self, operation_id: str, runtime) -> dict | None:
        with self.engine.connect() as connection:
            row = connection.execute(
                text(
                    "SELECT artifact_type, learner_id, status, payload_json "
                    "FROM workshop_publication_outbox WHERE operation_id=:operation_id"
                ),
                {"operation_id": operation_id},
            ).mappings().first()
        if row is None:
            return None
        if row["status"] == "delivered":
            return None
        with self.engine.begin() as connection:
            claimed = connection.execute(
                text(
                    "UPDATE workshop_publication_outbox SET status='dispatching' "
                    "WHERE operation_id=:operation_id AND status='pending'"
                ),
                {"operation_id": operation_id},
            ).rowcount
        if claimed != 1:
            return None
        payload = row["payload_json"]
        if isinstance(payload, str):
            payload = json.loads(payload)
        try:
            if row["artifact_type"] == "knowledge_card":
                result = runtime.save_knowledge_card(
                    row["learner_id"],
                    kp_id=payload["kp_id"],
                    title=payload["title"],
                    resource_bundle=payload["resource_bundle"],
                    source_execution_id=operation_id,
                )
            elif row["artifact_type"] == "paper":
                result = runtime.publish_agent_paper(
                    row["learner_id"],
                    execution_id=operation_id,
                    paper=payload["paper"],
                    blueprint=payload["blueprint"],
                    evidence_pack=payload["evidence_pack"],
                    daily_task_item_id=payload.get("daily_task_item_id"),
                )
            else:
                raise ValueError("unsupported workshop publication artifact")
        except Exception as exc:
            with self.engine.begin() as connection:
                connection.execute(
                    text(
                        "UPDATE workshop_publication_outbox SET status='pending', "
                        "attempt_count=attempt_count+1, last_error=:last_error "
                        "WHERE operation_id=:operation_id AND status='dispatching'"
                    ),
                    {
                        "operation_id": operation_id,
                        "last_error": str(exc).replace("\n", " ")[:255],
                    },
                )
            raise
        with self.engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE workshop_publication_outbox SET status='delivered', "
                    "attempt_count=attempt_count+1, last_error=NULL, "
                    "delivered_at=CURRENT_TIMESTAMP WHERE operation_id=:operation_id "
                    "AND status='dispatching'"
                ),
                {"operation_id": operation_id},
            )
        return result

    def dispatch_pending_workshop_publications(
        self,
        runtime,
        *,
        learner_id: str | None = None,
        limit: int = 20,
    ) -> int:
        if limit <= 0:
            return 0
        query = (
            "SELECT operation_id FROM workshop_publication_outbox "
            "WHERE status='pending'"
        )
        parameters: dict[str, object] = {"limit": limit}
        if learner_id is not None:
            query += " AND learner_id=:learner_id"
            parameters["learner_id"] = learner_id
        query += " ORDER BY operation_id LIMIT :limit"
        with self.engine.connect() as connection:
            operation_ids = list(connection.execute(
                text(query), parameters
            ).scalars())
        delivered = 0
        for operation_id in operation_ids:
            try:
                self.dispatch_workshop_publication(operation_id, runtime)
            except Exception:
                continue
            with self.engine.connect() as connection:
                status = connection.execute(
                    text(
                        "SELECT status FROM workshop_publication_outbox "
                        "WHERE operation_id=:operation_id"
                    ),
                    {"operation_id": operation_id},
                ).scalar_one_or_none()
            if status == "delivered":
                delivered += 1
        return delivered

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
    def _enqueue_workshop_publication(connection, payload: dict) -> None:
        connection.execute(
            text(
                "INSERT INTO workshop_publication_outbox "
                "(operation_id, artifact_type, learner_id, status, payload_json, attempt_count) "
                "VALUES (:operation_id, :artifact_type, :learner_id, 'pending', :payload_json, 0)"
            ),
            {
                "operation_id": payload["operation_id"],
                "artifact_type": payload["artifact_type"],
                "learner_id": payload["learner_id"],
                "payload_json": json.dumps(payload["publication"], ensure_ascii=False),
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
