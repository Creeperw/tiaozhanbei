import pytest

from competition_app.runtime.data_permissions import AgentDataPermissionGateway


def test_profile_write_requires_allowlisted_and_confirmed_field() -> None:
    gateway = AgentDataPermissionGateway()
    gateway.authorize(
        agent="diagnosis_agent",
        domain="learner_profile",
        action="write",
        fields={"learning_background"},
        confirmed_fields={"learning_background"},
    )

    with pytest.raises(PermissionError):
        gateway.authorize(
            agent="diagnosis_agent",
            domain="learner_profile",
            action="write",
            fields={"medical_history"},
            confirmed_fields={"medical_history"},
        )


def test_capability_manifest_does_not_expose_storage_details() -> None:
    manifest = AgentDataPermissionGateway().manifest()

    assert manifest["schema_version"] == "1.0"
    assert "database" not in str(manifest).lower()
