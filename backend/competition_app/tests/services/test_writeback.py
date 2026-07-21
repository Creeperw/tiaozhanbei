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
