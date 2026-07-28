import json

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import StatementError

from competition_app.contracts.base import WritebackIntent
from competition_app.services.writeback import WritebackExecutor


def build_engine():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE writeback_intents (idempotency_key VARCHAR(255) PRIMARY KEY, intent_id VARCHAR(255) NOT NULL, status VARCHAR(32) NOT NULL, payload_json TEXT NOT NULL)"))
        connection.execute(text("CREATE TABLE resource_versions (resource_id VARCHAR(128), version INTEGER, status VARCHAR(32), payload_json TEXT, PRIMARY KEY(resource_id, version))"))
        connection.execute(text("CREATE TABLE review_tasks (review_task_id VARCHAR(128) PRIMARY KEY, learner_id VARCHAR(128), primary_kp_id VARCHAR(128), status VARCHAR(32), payload_json TEXT)"))
        connection.execute(text("CREATE TABLE review_resource_bindings (binding_id VARCHAR(128) PRIMARY KEY, review_task_id VARCHAR(128), resource_id VARCHAR(128), resource_version INTEGER, audit_result_id VARCHAR(128))"))
        connection.execute(text("CREATE TABLE audit_results (audit_result_id VARCHAR(128) PRIMARY KEY, resource_id VARCHAR(128), decision VARCHAR(32), payload_json TEXT)"))
        connection.execute(text("CREATE TABLE workshop_publication_outbox (operation_id VARCHAR(128) PRIMARY KEY, artifact_type VARCHAR(64) NOT NULL, learner_id VARCHAR(128) NOT NULL, status VARCHAR(32) NOT NULL, payload_json TEXT NOT NULL, attempt_count INTEGER NOT NULL DEFAULT 0, last_error VARCHAR(255), delivered_at DATETIME)"))
    return engine


def test_writeback_executor_is_idempotent() -> None:
    engine = build_engine()
    executor = WritebackExecutor(engine)
    intent = WritebackIntent(
        intent_id="WBI_1",
        source_artifact_id="ART_1",
        effect_type="publish_resource",
        target_service="resource_service",
        target_entity_type="resource_version",
        payload={"resource_id": "R1", "version": 1, "status": "published"},
        preconditions=["audit_pass"],
        idempotency_key="KEY_1",
    )

    assert executor.execute(intent, satisfied_preconditions={"audit_pass"}) is True
    assert executor.execute(intent, satisfied_preconditions={"audit_pass"}) is False

    with engine.connect() as connection:
        count = connection.execute(text("SELECT COUNT(*) FROM writeback_intents")).scalar_one()
    assert count == 1


def test_writeback_requires_satisfied_preconditions() -> None:
    executor = WritebackExecutor(build_engine())
    intent = WritebackIntent(
        intent_id="W2", source_artifact_id="A2", effect_type="publish_resource",
        target_service="resource_service", target_entity_type="resource_version",
        payload={"resource_id": "R2", "version": 1, "status": "published"},
        preconditions=["audit_pass"], idempotency_key="K2",
    )
    with pytest.raises(ValueError, match="precondition"):
        executor.execute(intent, satisfied_preconditions=set())


def test_writeback_persists_review_task_and_binding() -> None:
    engine = build_engine()
    executor = WritebackExecutor(engine)
    task = WritebackIntent(
        intent_id="WT", source_artifact_id="A", effect_type="upsert_review_task",
        target_service="review_scheduler_service", target_entity_type="review_task",
        payload={"review_task_id": "T1", "learner_id": "L1", "primary_kp_id": "KP1", "status": "bound"},
        idempotency_key="KT",
    )
    binding = WritebackIntent(
        intent_id="WB", source_artifact_id="A", effect_type="bind_review_resource",
        target_service="resource_service", target_entity_type="review_resource_binding",
        payload={"binding_id": "B1", "review_task_id": "T1", "resource_id": "R1", "resource_version": 1, "audit_result_id": "AU1"},
        preconditions=["audit_pass"], idempotency_key="KB",
    )
    assert executor.execute(task)
    assert executor.execute(binding, satisfied_preconditions={"audit_pass"})
    with engine.connect() as connection:
        assert connection.execute(text("SELECT status FROM review_tasks WHERE review_task_id='T1'")).scalar_one() == "bound"
        assert connection.execute(text("SELECT resource_id FROM review_resource_bindings WHERE binding_id='B1'")).scalar_one() == "R1"


def test_writeback_rejects_unknown_effect() -> None:
    executor = WritebackExecutor(build_engine())
    intent = WritebackIntent(
        intent_id="W3", source_artifact_id="A", effect_type="unknown",
        target_service="unknown", target_entity_type="unknown", payload={}, idempotency_key="K3",
    )
    with pytest.raises(ValueError, match="unsupported"):
        executor.execute(intent)


def test_execute_batch_is_atomic_and_uses_persisted_audit() -> None:
    engine = build_engine()
    executor = WritebackExecutor(engine)
    audit = WritebackIntent(
        intent_id="WA", source_artifact_id="D1", effect_type="record_audit",
        target_service="audit_service", target_entity_type="audit_result",
        payload={"audit_result_id": "AU1", "resource_id": "R1", "decision": "pass"},
        idempotency_key="KA",
    )
    resource = WritebackIntent(
        intent_id="WR", source_artifact_id="D1", effect_type="publish_resource",
        target_service="resource_service", target_entity_type="resource_version",
        payload={"resource_id": "R1", "version": 1, "status": "published", "audit_result_id": "AU1"},
        preconditions=["audit_pass"], idempotency_key="KR",
    )
    bad_binding = WritebackIntent(
        intent_id="WB", source_artifact_id="D1", effect_type="bind_review_resource",
        target_service="resource_service", target_entity_type="review_resource_binding",
        payload={"binding_id": "B1", "audit_result_id": "AU1"},
        preconditions=["audit_pass"], idempotency_key="KB",
    )

    with pytest.raises(StatementError):
        executor.execute_batch([audit, resource, bad_binding])
    with engine.connect() as connection:
        assert connection.execute(text("SELECT COUNT(*) FROM audit_results")).scalar_one() == 0
        assert connection.execute(text("SELECT COUNT(*) FROM resource_versions")).scalar_one() == 0
        assert connection.execute(text("SELECT COUNT(*) FROM writeback_intents")).scalar_one() == 0


def test_execute_batch_replays_only_when_all_idempotency_keys_exist() -> None:
    engine = build_engine()
    executor = WritebackExecutor(engine)
    intents = [WritebackIntent(
        intent_id="WT", source_artifact_id="A", effect_type="upsert_review_task",
        target_service="review_scheduler_service", target_entity_type="review_task",
        payload={"review_task_id": "T2", "learner_id": "L", "primary_kp_id": "KP", "status": "bound"},
        idempotency_key="KT2",
    )]
    assert executor.execute_batch(intents) is True
    assert executor.execute_batch(intents) is False


def test_workshop_publication_is_enqueued_atomically_then_dispatched() -> None:
    engine = build_engine()
    executor = WritebackExecutor(engine)
    audit = WritebackIntent(
        intent_id="WAO", source_artifact_id="D", effect_type="record_audit",
        target_service="audit_service", target_entity_type="audit_result",
        payload={"audit_result_id": "AUO", "resource_id": "RO", "decision": "pass"},
        idempotency_key="KAO",
    )
    resource = WritebackIntent(
        intent_id="WRO", source_artifact_id="D", effect_type="publish_resource",
        target_service="resource_service", target_entity_type="resource_version",
        payload={"resource_id": "RO", "version": 1, "status": "published", "audit_result_id": "AUO"},
        preconditions=["audit_pass"], idempotency_key="KRO",
    )
    publication = WritebackIntent(
        intent_id="WWO", source_artifact_id="D", effect_type="enqueue_workshop_publication",
        target_service="workshop_service", target_entity_type="knowledge_card",
        payload={
            "operation_id": "OP_CARD_1", "artifact_type": "knowledge_card", "learner_id": "L1", "audit_result_id": "AUO",
            "publication": {"kp_id": "KP1", "title": "卡片", "resource_bundle": {"schema_version": "1.0"}},
        },
        preconditions=["audit_pass"], idempotency_key="KWO",
    )

    assert executor.execute_batch([audit, resource, publication]) is True

    class Runtime:
        calls = 0

        def save_knowledge_card(self, learner_id, **kwargs):
            self.calls += 1
            return {"card_id": "CARD1"}

    runtime = Runtime()
    assert executor.dispatch_workshop_publication("OP_CARD_1", runtime) == {"card_id": "CARD1"}
    with engine.connect() as connection:
        assert connection.execute(text("SELECT status FROM workshop_publication_outbox")).scalar_one() == "delivered"
    assert runtime.calls == 1

    assert executor.dispatch_workshop_publication("OP_CARD_1", runtime) is None
    with engine.connect() as connection:
        row = connection.execute(text(
            "SELECT status, attempt_count FROM workshop_publication_outbox"
        )).one()
    assert row.status == "delivered"
    assert row.attempt_count == 1
    assert runtime.calls == 1


def test_pending_workshop_dispatcher_continues_after_failure() -> None:
    engine = build_engine()
    executor = WritebackExecutor(engine)
    with engine.begin() as connection:
        for operation_id, kp_id in (("OP_FAIL", "FAIL"), ("OP_OK", "KP1")):
            connection.execute(text(
                "INSERT INTO workshop_publication_outbox "
                "(operation_id, artifact_type, learner_id, status, payload_json, attempt_count) "
                "VALUES (:operation_id, 'knowledge_card', 'L1', 'pending', :payload_json, 0)"
            ), {
                "operation_id": operation_id,
                "payload_json": json.dumps({
                    "kp_id": kp_id,
                    "title": "卡片",
                    "resource_bundle": {"schema_version": "1.0"},
                }),
            })

    class Runtime:
        def save_knowledge_card(self, learner_id, **kwargs):
            if kwargs["kp_id"] == "FAIL":
                raise RuntimeError("temporary workshop outage")
            return {"card_id": "CARD1"}

    assert executor.dispatch_pending_workshop_publications(Runtime()) == 1
    with engine.connect() as connection:
        statuses = dict(connection.execute(text(
            "SELECT operation_id, status FROM workshop_publication_outbox"
        )).tuples().all())
    assert statuses == {"OP_FAIL": "pending", "OP_OK": "delivered"}


def test_failed_writeback_does_not_call_workshop_runtime() -> None:
    engine = build_engine()
    executor = WritebackExecutor(engine)
    bad_publication = WritebackIntent(
        intent_id="WWF", source_artifact_id="D", effect_type="enqueue_workshop_publication",
        target_service="workshop_service", target_entity_type="knowledge_card",
        payload={"operation_id": "OP_BAD"},
        idempotency_key="KWF",
    )

    with pytest.raises(KeyError):
        executor.execute_batch([bad_publication])

    with engine.connect() as connection:
        assert connection.execute(text("SELECT COUNT(*) FROM workshop_publication_outbox")).scalar_one() == 0
