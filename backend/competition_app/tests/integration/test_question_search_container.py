from pathlib import Path

from competition_app.application.container import ApplicationContainer
from competition_app.config import Settings


def test_live_container_uses_configured_question_vector_store_root(tmp_path: Path) -> None:
    vector_store_root = Path("/tmp/question-indexes")
    settings = Settings(
        mode="live",
        llm_api_key="x",
        embedding_api_key="y",
        question_vector_store_root=vector_store_root,
        backend_handoff_enabled=True,
        backend_handoff_runtime_root=tmp_path / "handoff-runtime",
    )

    container = ApplicationContainer.build(settings)

    assert container.question_retrieval_tool is not None
    assert container.knowledge_backend is not None
    assert container.knowledge_backend.paths.public_vector_store == vector_store_root
    assert container.question_retrieval_tool.delivery_backend is container.knowledge_backend
    adapter = container.review_card_use_case.orchestrator.agent_registry.get(
        "learning_plan_service"
    )
    plan_kp_resolver = adapter.service.knowledge_point_resolver
    refresh_kp_resolver = container.daily_task_refresh_service.knowledge_point_resolver
    plan_video_resolver = adapter.service.video_resource_resolver
    refresh_video_resolver = container.daily_task_refresh_service.video_resource_resolver

    assert plan_kp_resolver is not None
    assert refresh_kp_resolver is not None
    assert plan_kp_resolver is refresh_kp_resolver
    assert plan_kp_resolver("不存在的知识点") is None
    assert plan_video_resolver is not None
    assert refresh_video_resolver is not None
    assert plan_video_resolver is refresh_video_resolver
