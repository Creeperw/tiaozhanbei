from pathlib import Path

from competition_app.application.container import ApplicationContainer
from competition_app.config import Settings


def test_live_container_uses_configured_question_vector_store_root() -> None:
    vector_store_root = Path("/tmp/question-indexes")
    settings = Settings(
        mode="live",
        dashscope_api_key="x",
        siliconflow_api_key="y",
        question_vector_store_root=vector_store_root,
    )

    container = ApplicationContainer.build(settings)

    assert container.question_retrieval_tool is not None
    assert container.knowledge_backend is not None
    assert container.knowledge_backend.paths.public_vector_store == vector_store_root
    assert container.question_retrieval_tool.delivery_backend is container.knowledge_backend
