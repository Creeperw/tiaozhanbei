from fastapi.testclient import TestClient

from competition_app.api.app import create_app
from competition_app.application.container import ApplicationContainer
from competition_app.config import Settings
from competition_app.contracts.evolution import FailureSignature


def _container(tmp_path):
    return ApplicationContainer.build(
        Settings(
            mode="stub",
            embedding_mode="disabled",
            runtime_root=tmp_path,
            evolution_enabled=True,
            preference_training_enabled=True,
            evolution_data_root=tmp_path / "evolution",
            admin_default_password="admin-password-2026",
        ),
        snapshot_root=tmp_path / "snapshots",
        include_backend_handoff=False,
    )


def test_evolution_management_is_admin_only_and_user_feedback_is_accepted(tmp_path):
    app = create_app(_container(tmp_path), auth_required=True)
    user = TestClient(app)
    assert user.post("/api/v1/auth/register", json={
        "username": "learner", "password": "password-2026", "email": "learner@example.com"
    }).status_code == 201
    assert user.post("/api/v1/evolution/feedback", json={
        "feedback_type": "dislike",
        "issue_type": "other",
        "comment": "回答不够具体",
        "task_type": "general_learning_support",
    }).status_code == 200
    assert user.get("/api/v1/evolution/status").status_code == 403
    assert user.get(
        "/api/v1/evolution/feedback/admin/classifications"
    ).status_code == 403

    admin = TestClient(app)
    assert admin.post("/api/v1/auth/login", json={
        "username": "admin", "password": "admin-password-2026"
    }).status_code == 200
    status = admin.get("/api/v1/evolution/status")
    assert status.status_code == 200
    assert status.json()["governance_enabled"] is True
    rows = admin.get("/api/v1/evolution/feedback/admin").json()
    assert len(rows) == 1
    assert rows[0]["status"] == "pending"
    classifications = admin.get(
        "/api/v1/evolution/feedback/admin/classifications"
    )
    assert classifications.status_code == 200
    assert {
        item["classification_id"] for item in classifications.json()
    } >= {"expert_evidence_reference"}
    missing_classification = admin.patch(
        f"/api/v1/evolution/feedback/admin/{rows[0]['feedback_id']}",
        json={"status": "validated"},
    )
    assert missing_classification.status_code == 409
    reviewed = admin.patch(
        f"/api/v1/evolution/feedback/admin/{rows[0]['feedback_id']}",
        json={
            "status": "validated",
            "classification_id": "expert_evidence_reference",
            "issue_type": "client_cannot_override_classification",
            "target_agent": "planner_agent",
            "owner_step_id": "planner",
        },
    )
    assert reviewed.status_code == 200
    assert reviewed.json()["feedback"]["field_path"] == "claims[].evidence_ids[]"
    assert reviewed.json()["feedback"]["target_agent"] == "expert_agent"


def test_signature_api_distinguishes_threshold_from_current_candidate_eligibility(tmp_path):
    container = _container(tmp_path)
    container.evolution_repository.save_signature(FailureSignature(
        signature_id="SIG_API_STALE",
        signature_key="f" * 64,
        task_type="general_learning_support",
        owner_step_id="expert",
        target_agent="expert_agent",
        issue_type="missing_evidence",
        field_path="resources[].source_id",
        constraint_category="evidence_boundary",
        case_count=3,
        execution_count=3,
        high_trust_count=3,
        source_case_ids=["CASE_1", "CASE_2", "CASE_3"],
        source_execution_ids=["EXE_1", "EXE_2", "EXE_3"],
        candidate_ready=True,
    ))
    app = create_app(container, auth_required=True)
    admin = TestClient(app)
    assert admin.post("/api/v1/auth/login", json={
        "username": "admin", "password": "admin-password-2026"
    }).status_code == 200

    response = admin.get("/api/v1/evolution/signatures")

    assert response.status_code == 200
    row = next(item for item in response.json() if item["signature_id"] == "SIG_API_STALE")
    assert row["threshold_ready"] is True
    assert row["effective_candidate_ready"] is False
    assert "stale_or_unsupported_field_path" in row["candidate_gate"]["reason_codes"]
    blocked = admin.post(
        "/api/v1/evolution/rules/generate",
        json={"signature_id": "SIG_API_STALE"},
    )
    assert blocked.status_code == 409
    assert "当前签名没有可用的封闭规则模板" in blocked.json()["detail"]


def test_sql_admin_replay_remains_listable_after_container_recreation(tmp_path):
    settings = Settings(
        mode="stub",
        embedding_mode="disabled",
        runtime_root=tmp_path / "runtime",
        use_sqlite=True,
        sqlite_path=tmp_path / "evolution-api.sqlite3",
        evolution_enabled=True,
        evolution_rules_enabled=True,
        admin_default_password="admin-password-2026",
    )
    first_container = ApplicationContainer.build(
        settings,
        snapshot_root=tmp_path / "snapshots-first",
        include_backend_handoff=False,
    )
    signature = first_container.evolution_repository.save_signature(FailureSignature(
        signature_id="SIG_API_SQL_REPLAY",
        signature_key="b" * 64,
        task_type="personalized_review_card",
        owner_step_id="expert",
        target_agent="expert_agent",
        issue_type="missing_evidence",
        field_path="claims[].evidence_ids[]",
        constraint_category="evidence_boundary",
        case_count=3,
        execution_count=3,
        high_trust_count=3,
        source_case_ids=["CASE_1", "CASE_2", "CASE_3"],
        source_execution_ids=["EXE_1", "EXE_2", "EXE_3"],
        candidate_ready=True,
    ))
    draft = first_container.evolution_rule_service.create_from_signature(
        signature.signature_id,
        analysis="API 持久化回放验收。",
        template_id="require_evidence_ids_from_current_pack",
    )
    first_client = TestClient(create_app(first_container, auth_required=True))
    assert first_client.post("/api/v1/auth/login", json={
        "username": "admin", "password": "admin-password-2026"
    }).status_code == 200

    replay = first_client.post(f"/api/v1/evolution/rules/{draft.rule_id}/replay")
    listed = first_client.get("/api/v1/evolution/rules")

    assert replay.status_code == 200
    assert replay.json()["status"] == "safety_replay_passed"
    assert listed.status_code == 200
    assert next(
        item for item in listed.json() if item["rule_id"] == draft.rule_id
    )["status"] == "safety_replay_passed"
    first_client.close()
    first_container.evolution_repository.engine.dispose()

    second_container = ApplicationContainer.build(
        settings,
        snapshot_root=tmp_path / "snapshots-second",
        include_backend_handoff=False,
    )
    second_client = TestClient(create_app(second_container, auth_required=True))
    assert second_client.post("/api/v1/auth/login", json={
        "username": "admin", "password": "admin-password-2026"
    }).status_code == 200

    restored = second_client.get("/api/v1/evolution/rules")

    assert restored.status_code == 200
    assert next(
        item for item in restored.json() if item["rule_id"] == draft.rule_id
    )["status"] == "safety_replay_passed"
    second_client.close()
    second_container.evolution_repository.engine.dispose()
