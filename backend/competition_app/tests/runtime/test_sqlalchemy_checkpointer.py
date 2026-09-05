from pathlib import Path
from typing import TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt
from sqlalchemy import create_engine

from competition_app.db.migrations import MigrationRunner
from competition_app.application.container import ApplicationContainer
from competition_app.config import Settings
from competition_app.runtime.langgraph_orchestrator import LangGraphOrchestrator
from competition_app.runtime.sqlalchemy_checkpointer import SqlAlchemyCheckpointSaver


class RecoveryState(TypedDict):
    answer: str


def _compile(saver):
    def ask(_: RecoveryState):
        answer = interrupt({"interrupt_type": "clarification", "prompt": "请补充目标"})
        return {"answer": str(answer)}

    builder = StateGraph(RecoveryState)
    builder.add_node("ask", ask)
    builder.add_edge(START, "ask")
    builder.add_edge("ask", END)
    return builder.compile(checkpointer=saver)


def test_sql_checkpoint_resumes_after_engine_and_graph_recreation(tmp_path: Path) -> None:
    database_path = tmp_path / "restart.sqlite"
    database_url = f"sqlite:///{database_path}"
    migration_dir = Path(__file__).parents[2] / "migrations"

    first_engine = create_engine(database_url)
    MigrationRunner(first_engine, migration_dir).run()
    first_graph = _compile(SqlAlchemyCheckpointSaver(first_engine))
    config = {"configurable": {"thread_id": "THREAD_RESTART"}}

    interrupted = first_graph.invoke({"answer": ""}, config=config)
    assert interrupted["__interrupt__"][0].value["prompt"] == "请补充目标"
    first_engine.dispose()

    second_engine = create_engine(database_url)
    MigrationRunner(second_engine, migration_dir).run()
    second_graph = _compile(SqlAlchemyCheckpointSaver(second_engine))
    resumed = second_graph.invoke(Command(resume="中医执业医师"), config=config)

    assert resumed["answer"] == "中医执业医师"
    assert second_graph.get_state(config).values["answer"] == "中医执业医师"
    second_engine.dispose()


def test_database_container_enables_persistent_graph_capability(tmp_path: Path) -> None:
    container = ApplicationContainer.build(
        Settings(mode="stub", use_sqlite=True, sqlite_path=tmp_path / "container.sqlite"),
        snapshot_root=tmp_path / "snapshots",
        include_backend_handoff=False,
    )

    orchestrator = container.review_card_use_case.orchestrator
    assert isinstance(orchestrator, LangGraphOrchestrator)
    assert orchestrator.persistent_checkpoints is True
