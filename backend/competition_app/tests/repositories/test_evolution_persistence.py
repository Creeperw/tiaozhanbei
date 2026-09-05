from pathlib import Path

from sqlalchemy import text

from competition_app.config import Settings
from competition_app.contracts.evolution import FailureSignature
from competition_app.db.bootstrap import DatabaseBootstrap
from competition_app.repositories.evolution import SqlEvolutionRepository
from competition_app.services.evolution_rule_service import EvolutionRuleService


MIGRATION_NAME = "023_normalize_evolution_replay_status.sql"


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        mode="stub",
        use_sqlite=True,
        sqlite_path=tmp_path / "evolution.sqlite3",
    )


def _candidate_signature(signature_id: str = "SIG_SQL_REPLAY") -> FailureSignature:
    return FailureSignature(
        signature_id=signature_id,
        signature_key="a" * 64,
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
    )


def _draft_rule(repository: SqlEvolutionRepository):
    signature = repository.save_signature(_candidate_signature())
    service = EvolutionRuleService(repository, enabled=True)
    rule = service.create_from_signature(
        signature.signature_id,
        analysis="三个独立案例均引用了当前证据包之外的编号。",
        template_id="require_evidence_ids_from_current_pack",
    )
    return service, rule


def test_sql_safety_replay_survives_engine_and_repository_recreation(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    first_engine = DatabaseBootstrap(settings).ensure_database()
    first_repository = SqlEvolutionRepository(first_engine)
    service, draft = _draft_rule(first_repository)

    replayed = service.run_contract_replay(
        draft.rule_id,
        reviewer_id="admin-acceptance",
    )

    assert replayed.status == "safety_replay_passed"
    assert first_repository.get_rule(draft.rule_id).status == "safety_replay_passed"
    assert [
        item.run_type
        for item in first_repository.list_runs(rule_id=draft.rule_id)
    ] == ["safety_replay"]
    first_engine.dispose()

    second_engine = DatabaseBootstrap(settings).ensure_database()
    second_repository = SqlEvolutionRepository(second_engine)
    restored = second_repository.get_rule(draft.rule_id)

    assert restored is not None
    assert restored.status == "safety_replay_passed"
    assert restored.replay_metrics["replay_kind"] == "deterministic_contract_safety"
    assert [
        item.run_type
        for item in second_repository.list_runs(rule_id=draft.rule_id)
    ] == ["safety_replay"]
    second_engine.dispose()


def test_replay_status_migration_normalizes_legacy_sql_rows(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    first_engine = DatabaseBootstrap(settings).ensure_database()
    first_repository = SqlEvolutionRepository(first_engine)
    _, draft = _draft_rule(first_repository)
    with first_engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE evolution_rules SET status='replay_passed' "
                "WHERE rule_id=:rule_id"
            ),
            {"rule_id": draft.rule_id},
        )
        connection.execute(
            text("DELETE FROM schema_migrations WHERE version=:version"),
            {"version": MIGRATION_NAME},
        )
    first_engine.dispose()

    second_engine = DatabaseBootstrap(settings).ensure_database()
    restored = SqlEvolutionRepository(second_engine).get_rule(draft.rule_id)
    with second_engine.connect() as connection:
        migration_count = connection.execute(
            text("SELECT COUNT(*) FROM schema_migrations WHERE version=:version"),
            {"version": MIGRATION_NAME},
        ).scalar_one()

    assert restored is not None
    assert restored.status == "safety_replay_passed"
    assert migration_count == 1
    second_engine.dispose()