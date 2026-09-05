from pathlib import Path

from competition_app.tools.knowledge_repository import KnowledgeRepository, KnowledgeRepositoryPaths


FIXTURE_ROOT = Path(__file__).parents[1] / "fixtures" / "knowledge_delivery"


def build_repository() -> KnowledgeRepository:
    return KnowledgeRepository(
        KnowledgeRepositoryPaths(
            knowledge_points=FIXTURE_ROOT / "knowledge_points.json",
            kp_chunk_links=FIXTURE_ROOT / "kp_chunk_links.jsonl",
            source_chunks=FIXTURE_ROOT / "source_chunks.jsonl",
            questions=FIXTURE_ROOT / "questions.json",
            question_kp_matches=FIXTURE_ROOT / "question_kp_matches.jsonl",
        )
    )


def test_repository_loads_full_question_and_all_valid_bridges() -> None:
    repository = build_repository()

    question = repository.get_question("Q_FJ_1")
    bridges = repository.bridges_for_kp("KP_FJ_001")

    assert question["题目答案"] == "益气健脾"
    assert [bridge.kp_id for bridge in bridges] == ["KP_FJ_001"]
    assert bridges[0].bridge_layer == "strict"
    assert repository.bridges_for_question("Q_FJ_1") == bridges
    assert repository.question_ids_for_kp("KP_FJ_001") == ["Q_FJ_1"]


def test_repository_skips_bridge_rows_with_missing_foreign_keys() -> None:
    repository = build_repository()

    repository.bridges_for_kp("KP_FJ_001")

    assert repository.invalid_bridge_count == 1