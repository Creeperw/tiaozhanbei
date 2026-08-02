from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from pathlib import Path

from competition_app.agents.audit import AuditAgent
from competition_app.agents.diagnosis import DiagnosisAgent
from competition_app.agents.default_route_resolver import DefaultRouteResolverAgent
from competition_app.agents.expert import ExpertAgent
from competition_app.agents.knowledge_base import KnowledgeBaseAgent
from competition_app.agents.learning_plan_service import LearningPlanServiceAdapter
from competition_app.agents.memory import MemoryAgent
from competition_app.services.memory_retrieval import MemoryRetrievalService
from competition_app.agents.planner import PlannerAgent
from competition_app.agents.paper_blueprint import PaperBlueprintAgent
from competition_app.agents.paper_assembly import PaperAssemblyAgent
from competition_app.agents.knowledge_explanation import KnowledgeExplanationAgent
from competition_app.agents.review_scheduler import ReviewSchedulerAdapter
from competition_app.application.personalized_review_card import PersonalizedReviewCardUseCase
from competition_app.config import Settings
from competition_app.llm.stub import StubChatModel
from competition_app.llm.openai_compatible import OpenAICompatibleChatModel
from competition_app.llm.failover import FailoverChatModel
from competition_app.embeddings.stub import StubEmbeddingModel
from competition_app.embeddings.siliconflow import SiliconFlowEmbeddingModel
from competition_app.db.bootstrap import DatabaseBootstrap
from competition_app.runtime.agent_registry import AgentRegistry
from competition_app.runtime.orchestrator import Orchestrator
from competition_app.runtime.snapshot import SnapshotExporter, _sanitize
from competition_app.runtime.tool_registry import ToolRegistry
from competition_app.tools.knowledge_assets import KnowledgeAssetPaths, KnowledgeAssetRepository
from competition_app.tools.knowledge_retrieval import KnowledgeRetrievalTool
from competition_app.tools.current_page import ALL_PAGE_CONTEXT_AGENTS, CurrentPageReadTool
from competition_app.tools.exa_retrieval import ExaVideoRetriever
from competition_app.tools.knowledge_delivery import (
    KnowledgeDeliveryBackend,
    KnowledgeDeliveryPaths,
)
from competition_app.tools.stub_question_retrieval import StubQuestionRetriever
from competition_app.services.writeback import WritebackExecutor
from competition_app.services.default_route import DefaultRouteRepository
from competition_app.services.textbook_route import TextbookRouteRepository
from competition_app.services.learning_plan import LearningPlanService
from competition_app.services.learning_path_projection import LearningPathProjectionService
from competition_app.services.daily_task_refresh import DailyTaskRefreshService
from competition_app.services.daily_task_execution import DailyTaskExecutionCoordinator
from competition_app.services.plan_progress import build_plan_progress
from competition_app.services.learning_monitoring import LearningMonitoringService
from competition_app.services.review import ReviewService
from competition_app.llm.terminal import (
    terminal_agent_finished,
    terminal_agent_started,
    terminal_delta_printer,
)
from competition_app.runtime.terminal_trace import TerminalTrace
from competition_app.runtime.model_trace import ModelTraceRecorder
from competition_app.runtime.langgraph_orchestrator import LangGraphOrchestrator
from competition_app.runtime.sqlalchemy_checkpointer import SqlAlchemyCheckpointSaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from competition_app.runtime.event_stream import emit_runtime_event, has_event_sink
from competition_app.repositories.learning_plan import (
    InMemoryLearningPlanRepository,
    SqlLearningPlanRepository,
)
from competition_app.repositories.runtime import (
    InMemoryConversationRepository,
    InMemoryRunStateRepository,
    SqlConversationRepository,
    SqlRunStateRepository,
)
from competition_app.repositories.review import (
    InMemoryReviewRepository,
    SqlReviewRepository,
)
from competition_app.repositories.auth import InMemoryAuthRepository, SqlAuthRepository
from competition_app.services.auth import AuthenticationService
from competition_app.repositories.account_profile import (
    InMemoryAccountProfileRepository,
    SqlAccountProfileRepository,
)
from competition_app.repositories.workshop_library import (
    InMemoryWorkshopLibraryRepository,
    SqlWorkshopLibraryRepository,
)
from competition_app.services.account_profile import AccountProfileService
from competition_app.services.workshop_library import WorkshopLibraryService
from competition_app.services.textbook_pdf import (
    InMemoryTextbookPdfAnnotationRepository,
    SqlTextbookPdfAnnotationRepository,
    TextbookPdfService,
)
from competition_app.services.textbook_import import TextbookImportService
from competition_app.services.user_syllabus import UserSyllabusService
from competition_app.services.textbook_pdf_ai import (
    TextbookPdfAiService,
    TextbookPdfAiSettings,
)
from competition_app.integrations.backend_handoff import (
    BackendHandoffRuntime,
    load_backend_handoff,
)


@dataclass
class ApplicationContainer:
    review_card_use_case: PersonalizedReviewCardUseCase
    review_service: ReviewService
    authentication_service: AuthenticationService
    account_profile_service: AccountProfileService
    workshop_library_service: WorkshopLibraryService
    textbook_pdf_service: TextbookPdfService
    textbook_import_service: TextbookImportService
    user_syllabus_service: UserSyllabusService
    learning_plan_service: LearningPlanService
    daily_task_refresh_service: DailyTaskRefreshService
    daily_task_execution_coordinator: DailyTaskExecutionCoordinator | None = None
    textbook_pdf_ai_service: TextbookPdfAiService | None = None
    writeback_executor: WritebackExecutor | None = None
    question_retrieval_tool: KnowledgeRetrievalTool | None = None
    knowledge_backend: KnowledgeDeliveryBackend | None = None
    mode: str = "stub"
    chat_model_name: str = "stub"
    embedding_model_name: str = "stub"
    model_trace_recorder: ModelTraceRecorder | None = None
    auth_cookie_secure: bool = False
    backend_handoff_runtime: BackendHandoffRuntime | None = None
    frontend_dist_root: Path | None = None
    default_route_repository: DefaultRouteRepository | None = None
    textbook_route_repository: TextbookRouteRepository | None = None

    @classmethod
    def build(
        cls,
        settings: Settings,
        snapshot_root: Path | None = None,
        stream_model_output: bool = False,
        trace_level: str = "model",
        include_backend_handoff: bool = True,
    ) -> "ApplicationContainer":
        package_root = Path(__file__).parents[1]
        default_route_repository = DefaultRouteRepository.from_directory(
            package_root / "data" / "default_routes"
        )
        textbook_route_repository = TextbookRouteRepository.from_file(
            package_root
            / "data"
            / "textbook_routes"
            / "tcm_textbook_routes.v1.json"
        )
        database_enabled = bool(
            settings.database_url or settings.use_sqlite or settings.mysql_password
        )
        database_engine = (
            DatabaseBootstrap(settings).ensure_database()
            if database_enabled
            else None
        )
        if database_engine is not None:
            plan_repository = SqlLearningPlanRepository(database_engine)
            run_state_repository = SqlRunStateRepository(database_engine)
            conversation_repository = SqlConversationRepository(database_engine)
            review_repository = SqlReviewRepository(database_engine)
            auth_repository = SqlAuthRepository(database_engine)
            account_profile_repository = SqlAccountProfileRepository(database_engine)
            workshop_library_repository = SqlWorkshopLibraryRepository(database_engine)
            textbook_pdf_annotation_repository = SqlTextbookPdfAnnotationRepository(
                database_engine
            )
        else:
            plan_repository = InMemoryLearningPlanRepository()
            run_state_repository = InMemoryRunStateRepository()
            conversation_repository = InMemoryConversationRepository()
            review_repository = InMemoryReviewRepository()
            auth_repository = InMemoryAuthRepository()
            account_profile_repository = InMemoryAccountProfileRepository()
            workshop_library_repository = InMemoryWorkshopLibraryRepository()
            textbook_pdf_annotation_repository = InMemoryTextbookPdfAnnotationRepository()
        review_service = ReviewService(review_repository)
        authentication_service = AuthenticationService(
            auth_repository,
            session_ttl_hours=settings.auth_session_ttl_hours,
            admin_username=settings.admin_username,
            admin_password=settings.admin_default_password,
        )
        account_profile_service = AccountProfileService(
            account_profile_repository,
            auth_repository,
            settings.avatar_dir,
        )
        workshop_library_service = WorkshopLibraryService(workshop_library_repository)
        textbook_pdf_service = TextbookPdfService(
            settings.textbook_pdf_root,
            settings.textbook_pdf_catalog_path,
            textbook_pdf_annotation_repository,
            settings.runtime_root / "textbook_uploads",
        )
        chat_api_key = (
            settings.siliconflow_api_key
            if "siliconflow.cn" in settings.chat_base_url.lower()
            else settings.dashscope_api_key
        )
        textbook_pdf_ai_service = (
            TextbookPdfAiService(
                textbook_pdf_service,
                TextbookPdfAiSettings(
                    base_url=settings.chat_base_url,
                    api_key=chat_api_key,
                    model=settings.chat_model,
                    timeout_seconds=settings.llm_timeout_seconds,
                ),
                conversation_repository=conversation_repository,
            )
            if settings.mode == "live" and chat_api_key
            else None
        )
        if settings.mode == "live":
            if not settings.dashscope_api_key or not settings.siliconflow_api_key:
                raise ValueError("live mode requires configured model API keys")
            chat_model = FailoverChatModel(
                [
                    OpenAICompatibleChatModel(
                        settings.chat_base_url,
                        chat_api_key,
                        model_name,
                        timeout_seconds=settings.llm_timeout_seconds,
                    )
                    for model_name in settings.chat_models
                ]
            )
            embedding_model = SiliconFlowEmbeddingModel(
                settings.embedding_base_url,
                settings.siliconflow_api_key,
                settings.embedding_model,
            )
            delivery_paths = KnowledgeDeliveryPaths.from_handoff_root(
                settings.knowledge_handoff_root,
                runtime_root=settings.knowledge_runtime_root,
                public_vector_store=settings.question_vector_store_root,
            )
            knowledge_backend = KnowledgeDeliveryBackend(
                delivery_paths,
                embedding_base_url=settings.embedding_base_url,
                embedding_model=settings.embedding_model,
                embedding_api_key=settings.siliconflow_api_key,
                chat_base_url=settings.chat_base_url,
                chat_model=settings.chat_model,
                chat_api_key=chat_api_key,
                mineru_token=settings.mineru_token,
            )
            repository = knowledge_backend.map
            question_retriever = None
            textbook_retriever = None
        else:
            chat_model = StubChatModel()
            embedding_model = StubEmbeddingModel()
            demo_root = package_root / "data" / "demo"
            asset_paths = KnowledgeAssetPaths(
                knowledge_points=demo_root / "knowledge_points.json",
                kp_chunk_links=demo_root / "kp_chunk_links.jsonl",
                source_chunks=demo_root / "source_chunks.jsonl",
            )
            repository = KnowledgeAssetRepository(asset_paths)
            question_retriever = StubQuestionRetriever()
            textbook_retriever = None
            knowledge_backend = None
        textbook_import_service = TextbookImportService(
            runtime_root=settings.runtime_root,
            chat_base_url=settings.chat_base_url,
            chat_model=settings.chat_model,
            chat_api_key=chat_api_key,
            mineru_token=settings.mineru_token,
            mineru_pipeline_root=(
                settings.knowledge_handoff_root
                / "知识库管理组件"
                / "knowledge_upload_pipeline"
            ),
            timeout_seconds=settings.llm_timeout_seconds,
            embedding_model=embedding_model,
            vector_store_root=settings.question_vector_store_root,
            knowledge_resolver=(
                knowledge_backend.map if knowledge_backend is not None else repository
            ),
            vision_base_url=settings.vision_api_base_url,
            vision_model=settings.vision_api_model,
            vision_api_key=settings.vision_api_key or "",
            treekg_root=settings.treekg_root,
            treekg_python=settings.treekg_python,
            treekg_api_key=settings.treekg_api_key or "",
            treekg_api_base=settings.treekg_api_base,
            treekg_model_name=settings.treekg_model_name,
        )
        from competition_app.tools.syllabus_matching import SyllabusVectorMatcher

        user_syllabus_service = UserSyllabusService(
            settings.runtime_root,
            chat_base_url=settings.chat_base_url,
            chat_model=settings.chat_model,
            chat_api_key=chat_api_key or "",
            timeout_seconds=settings.llm_timeout_seconds,
            mineru_token=settings.mineru_token,
            mineru_pipeline_root=(
                settings.knowledge_handoff_root
                / "知识库管理组件"
                / "knowledge_upload_pipeline"
            ),
            knowledge_resolver=(
                knowledge_backend.map if knowledge_backend is not None else repository
            ),
            vector_matcher=SyllabusVectorMatcher(
                embedding_model,
                embedding_model_name=settings.embedding_model,
                vector_store_root=settings.question_vector_store_root,
            ),
        )
        backend_handoff_runtime = (
            load_backend_handoff(settings) if include_backend_handoff else None
        )
        knowledge_point_resolver = None
        if backend_handoff_runtime is not None:

            def resolve_executable_knowledge_point(
                name: str,
                learning_chapter: str = "",
            ) -> str | None:
                current = backend_handoff_runtime.resolve_executable_knowledge_point(name)
                if current is not None or knowledge_backend is None:
                    return current
                bundle = knowledge_backend.map.resolve_executable_bundle(
                    name,
                    required_question_count=3,
                    preferred_scope=learning_chapter,
                )
                if bundle is None:
                    return None
                return backend_handoff_runtime.ensure_executable_knowledge_bundle(
                    bundle,
                    required_question_count=3,
                )

            knowledge_point_resolver = resolve_executable_knowledge_point
        video_resource_resolver = (
            knowledge_backend.map.resolve_trusted_video_resource
            if knowledge_backend is not None
            else None
        )
        learning_plan_service = LearningPlanService(
            default_route_repository,
            plan_repository,
            knowledge_point_resolver=knowledge_point_resolver,
            video_resource_resolver=video_resource_resolver,
        )
        task_load_policy_loader = None
        if backend_handoff_runtime is not None:

            def load_task_load_policy(
                learner_id: str,
                *,
                plan_context: dict,
            ) -> dict:
                queue = review_service.get_queue(learner_id, limit=500)
                return backend_handoff_runtime.load_task_load_policy(
                    learner_id,
                    plan_context=plan_context,
                    review_projection={
                        "source": "canonical_review_memory",
                        "due_count": queue.due_count,
                        "total_count": len(queue.entries),
                        "active_task_count": queue.active_task_count,
                    },
                    days=7,
                )

            task_load_policy_loader = load_task_load_policy
        daily_task_refresh_service = DailyTaskRefreshService(
            plan_repository,
            knowledge_point_resolver=knowledge_point_resolver,
            video_resource_resolver=video_resource_resolver,
            task_load_policy_loader=task_load_policy_loader,
        )
        exa_retriever = (
            ExaVideoRetriever(settings.exa_api_key)
            if settings.mode == "live" and settings.exa_api_key
            else None
        )
        knowledge_tool = KnowledgeRetrievalTool(
            repository,
            embedding_model,
            question_retriever=question_retriever,
            textbook_retriever=textbook_retriever,
            exa_retriever=exa_retriever,
            delivery_backend=knowledge_backend,
        )
        terminal_trace = TerminalTrace(enabled=stream_model_output, level=trace_level)
        model_trace_recorder = ModelTraceRecorder()
        chat_model = StreamingChatModel(
            chat_model,
            terminal_trace,
            model_trace_recorder,
            stream=stream_model_output,
        )
        registry = AgentRegistry()
        registry.register("planner_agent", PlannerAgent(chat_model))
        registry.register("paper_blueprint_agent", PaperBlueprintAgent(chat_model))
        registry.register("paper_assembly_agent", PaperAssemblyAgent(chat_model))
        registry.register(
            "knowledge_explanation_agent", KnowledgeExplanationAgent(chat_model)
        )
        registry.register("memory_agent", MemoryAgent(chat_model))
        registry.register("knowledge_base_agent", KnowledgeBaseAgent(knowledge_tool, chat_model))
        registry.register(
            "default_route_resolver",
            DefaultRouteResolverAgent(
                default_route_repository, textbook_route_repository, chat_model
            ),
        )
        registry.register(
            "diagnosis_agent",
            DiagnosisAgent(chat_model, learning_plan_service=learning_plan_service),
        )
        registry.register("learning_plan_service", LearningPlanServiceAdapter(learning_plan_service))
        registry.register("review_scheduler", ReviewSchedulerAdapter())
        registry.register("expert_agent", ExpertAgent(chat_model))
        registry.register("audit_agent", AuditAgent(chat_model))
        memory_retrieval_service = (
            MemoryRetrievalService(
                embedding_model,
                backend_handoff_runtime.list_active_personalization_memories,
            )
            if backend_handoff_runtime is not None
            else None
        )
        tool_registry = ToolRegistry()
        tool_registry.register(
            "read_current_page",
            CurrentPageReadTool().read,
            allowed_agents=set(ALL_PAGE_CONTEXT_AGENTS),
        )

        def unavailable_learner_data(
            external_user_id: str,
            *,
            days: int = 7,
            recent_limit: int = 20,
        ) -> dict:
            del external_user_id, recent_limit
            return {
                "schema_version": "1.0",
                "window_days": days,
                "evidence_status": "unavailable",
                "reason": "learning data runtime is unavailable",
            }

        recent_learning_handler = unavailable_learner_data
        learning_progress_handler = (
            lambda external_user_id, *, days=30: unavailable_learner_data(
                external_user_id, days=days
            )
        )
        review_status_handler = (
            lambda external_user_id, *, history_limit=100: unavailable_learner_data(
                external_user_id, days=30, recent_limit=history_limit
            )
        )
        if backend_handoff_runtime is not None:
            recent_learning_handler = (
                backend_handoff_runtime.load_recent_learning_summary
            )
            learning_progress_handler = backend_handoff_runtime.load_learning_statistics
            review_status_handler = backend_handoff_runtime.load_review_dashboard

        def load_current_plan_progress(external_user_id: str) -> dict:
            plans = plan_repository.get_current(external_user_id)
            daily_progress = {}
            if (
                plans is not None
                and plans.learning_task is not None
                and backend_handoff_runtime is not None
            ):
                try:
                    daily_progress = backend_handoff_runtime.load_daily_task_progress(
                        external_user_id,
                        plans.learning_task.model_dump(mode="json"),
                    )
                except Exception:
                    daily_progress = {}
            return build_plan_progress(
                plans,
                daily_task_progress=daily_progress,
            )

        def load_learning_planning_context(
            external_user_id: str,
            *,
            scope: str,
            available_minutes: int = 60,
        ) -> dict:
            """Load the bounded learner slice needed by Diagnosis planning.

            Planner never receives this result. Diagnosis explicitly invokes
            the tool after routing has selected a planning task.
            """

            plans = plan_repository.get_current(external_user_id)
            long_term_plan = (
                plans.long_term_plan.model_dump(mode="json")
                if plans is not None and plans.long_term_plan is not None
                else {}
            )
            short_term_plan = (
                plans.short_term_plan.model_dump(mode="json")
                if plans is not None and plans.short_term_plan is not None
                else {}
            )
            learning_task = (
                plans.learning_task.model_dump(mode="json")
                if plans is not None and plans.learning_task is not None
                else {}
            )
            plan_context = {
                key: value
                for key, value in {
                    "long_term_plan": long_term_plan,
                    "short_term_plan": short_term_plan,
                    "learning_task": learning_task,
                    "available_minutes": available_minutes,
                }.items()
                if value not in (None, "", [], {})
            }
            if backend_handoff_runtime is None:
                return {
                    "schema_version": "1.0",
                    "source": "learning_plan_repository",
                    "learning_profile": {},
                    "system_data": {},
                    "user_knowledge_states": [],
                    "question_attempts": [],
                    "question_learning_stats": [],
                    "learning_monitoring": {
                        "evidence_status": "unavailable",
                        "freshness_status": "unknown",
                        "sample_counts": {},
                        "metrics": {},
                    },
                    "current_long_term_plan": long_term_plan,
                    "current_short_term_plan": short_term_plan,
                    "current_learning_task": learning_task,
                    "multi_scale_learning_state": {},
                    "path_candidates": {"eligible": [], "blocked": []},
                    "task_load_policy": {},
                }

            behavior = backend_handoff_runtime.load_learning_context(
                external_user_id, days=7
            )
            monitoring = LearningMonitoringService().build_snapshot(
                external_user_id,
                behavior,
                window_days=7,
            ).model_dump(mode="json")
            multiscale = backend_handoff_runtime.load_multiscale_learning_state(
                external_user_id,
                plan_context=plan_context,
                window_days=30,
            )
            # ``scope`` is normalized here: Planner may legitimately route an
            # underspecified request (e.g. "结合我的学习状态制定学习计划") with
            # plan_scope="unspecified", and Diagnosis invokes this tool before
            # it resolves the layer.  The legacy path-candidate builder only
            # accepts one of the three concrete scopes, so an unspecified
            # request must not leak through to it; candidates are pointless
            # anyway until Diagnosis has pinned the layer.
            if scope in {"long_term", "short_term", "daily_task"}:
                raw_candidates = backend_handoff_runtime.load_path_candidates(
                    external_user_id,
                    plan_context=plan_context,
                    scope=scope,
                    limit=30,
                    include_blocked=True,
                )
                candidate_items = (
                    raw_candidates.get("items", [])
                    if isinstance(raw_candidates, dict)
                    else []
                )
            else:
                candidate_items = []
            review_projection = backend_handoff_runtime.load_review_dashboard(
                external_user_id,
                history_limit=100,
            )
            task_load_policy = backend_handoff_runtime.load_task_load_policy(
                external_user_id,
                plan_context=plan_context,
                review_projection={
                    "source": "review_dashboard",
                    "due_count": sum(
                        1
                        for item in review_projection.get("review_states", [])
                        if str(item.get("status") or "").lower() in {"due", "overdue"}
                    ),
                    "total_count": len(review_projection.get("review_states", [])),
                },
                days=7,
            )
            return {
                "schema_version": "1.0",
                "source": "authorized_learning_planning_tools",
                "learning_profile": behavior.get("learning_profile") or {},
                "system_data": behavior.get("system_data") or {},
                "user_knowledge_states": behavior.get("user_knowledge_state") or [],
                "question_attempts": behavior.get("question_attempt") or [],
                "question_learning_stats": behavior.get("question_learning_stats") or [],
                "learning_monitoring": monitoring,
                "current_long_term_plan": long_term_plan,
                "current_short_term_plan": short_term_plan,
                "current_learning_task": learning_task,
                "multi_scale_learning_state": multiscale,
                "path_candidates": {
                    "eligible": [
                        item for item in candidate_items
                        if isinstance(item, dict) and item.get("eligible") is True
                    ],
                    "blocked": [
                        item for item in candidate_items
                        if isinstance(item, dict) and item.get("eligible") is not True
                    ],
                },
                "task_load_policy": task_load_policy,
            }

        def load_learning_path_progress(external_user_id: str) -> dict:
            """Project the learner's current long-term plan into a stable
            stage → book → chapter → section hierarchy.

            Each section carries its per-learner status (mastery based) and the
            trusted video bound to it when the knowledge map has one. Diagnosis
            uses this to pin today's task to a concrete video section instead of
            a generic "watch this chapter's video".
            """

            plans = plan_repository.get_current(external_user_id)
            if plans is None or plans.long_term_plan is None:
                return {
                    "schema_version": "1.0",
                    "learner_id": external_user_id,
                    "availability": "requires_long_term_plan",
                    "stages": [],
                    "books": [],
                    "current_section": None,
                }
            mastery_rows: list[dict] = []
            if backend_handoff_runtime is not None:
                try:
                    behavior = backend_handoff_runtime.load_learning_context(
                        external_user_id,
                        days=7,
                    )
                    mastery_rows = behavior.get("mastery") or []
                except Exception:
                    mastery_rows = []
            loader = (
                knowledge_backend.map.learning_path_book_knowledge_points
                if knowledge_backend is not None
                else None
            )
            videos_by_kp: dict = {}
            if knowledge_backend is not None:
                try:
                    knowledge_backend.map.ensure_videos()
                    videos_by_kp = knowledge_backend.map.videos_by_kp
                except Exception:
                    videos_by_kp = {}
            service = LearningPathProjectionService(loader)
            try:
                stage_page = service.page(
                    learner_id=external_user_id,
                    plan=plans.long_term_plan,
                    parent_id=None,
                    mastery_rows=mastery_rows,
                    offset=0,
                    limit=50,
                )
            except (KeyError, OSError, TypeError, ValueError):
                return {
                    "schema_version": "1.0",
                    "learner_id": external_user_id,
                    "availability": "unavailable",
                    "stages": [],
                    "books": [],
                    "current_section": None,
                }
            stages: list[dict] = []
            books: list[dict] = []
            current_section: dict | None = None
            for stage_node in stage_page.nodes:
                try:
                    book_page = service.page(
                        learner_id=external_user_id,
                        plan=plans.long_term_plan,
                        parent_id=stage_node.node_id,
                        mastery_rows=mastery_rows,
                        offset=0,
                        limit=50,
                    )
                except (KeyError, OSError, TypeError, ValueError):
                    continue
                stage_books: list[dict] = []
                for book_node in book_page.nodes:
                    book_name = str(book_node.title).strip().strip("《》")
                    section_rows: list[dict] = []
                    if (
                        stage_node.status in {"in_progress", "completed"}
                        and loader is not None
                    ):
                        try:
                            knowledge = loader(book_name, 0, 500)
                        except (KeyError, OSError, TypeError, ValueError):
                            knowledge = {}
                        mastery_by_kp = {
                            str(row.get("kp_id")): float(row.get("mastery") or 0.0)
                            for row in mastery_rows
                            if row.get("kp_id")
                        }
                        for row in knowledge.get("items") or []:
                            kp_id = str(row.get("kp_id") or row.get("id") or "").strip()
                            if not kp_id:
                                continue
                            mastery = mastery_by_kp.get(kp_id)
                            status = (
                                "completed"
                                if mastery is not None and mastery >= 0.8
                                else "in_progress"
                                if mastery is not None and mastery > 0
                                else "unassessed"
                            )
                            video: dict | None = None
                            rows = sorted(
                                videos_by_kp.get(kp_id, []),
                                key=lambda row: (
                                    str(row.get("bvid") or ""),
                                    int(row.get("page") or 0),
                                    float(row.get("start_seconds") or 0),
                                ),
                            )
                            if rows:
                                first = rows[0]
                                try:
                                    video_duration = round(
                                        float(first.get("end_seconds") or 0)
                                        - float(first.get("start_seconds") or 0)
                                    )
                                except (TypeError, ValueError):
                                    video_duration = 0
                                video = {
                                    "bvid": first.get("bvid"),
                                    "aid": first.get("aid"),
                                    "page": first.get("page"),
                                    "start_seconds": first.get("start_seconds"),
                                    "end_seconds": first.get("end_seconds"),
                                    "duration_seconds": max(video_duration, 0) or None,
                                    "video_title": first.get("video_title"),
                                    "part_title": first.get("part_title"),
                                    "topic": first.get("topic"),
                                }
                            section_rows.append(
                                {
                                    "kp_id": kp_id,
                                    "name": str(
                                        row.get("name")
                                        or row.get("kp_lv3")
                                        or kp_id
                                    ),
                                    "chapter": str(
                                        row.get("chapter")
                                        or row.get("kp_lv2")
                                        or ""
                                    ),
                                    "status": status,
                                    "mastery": mastery,
                                    "video": video,
                                }
                            )
                    stage_books.append(
                        {
                            "book": book_name,
                            "node_id": book_node.node_id,
                            "status": book_node.status,
                            "sections": section_rows,
                        }
                    )
                    if current_section is None:
                        current_section = next(
                            (
                                section
                                for section in section_rows
                                if section["status"] != "completed"
                            ),
                            None,
                        )
                books.extend(stage_books)
                stages.append(
                    {
                        "stage_id": stage_node.node_id,
                        "name": stage_node.title,
                        "status": stage_node.status,
                        "order": stage_node.order,
                        "books": [
                            {
                                "book": str(item["book"]),
                                "node_id": item["node_id"],
                                "status": item["status"],
                            }
                            for item in stage_books
                        ],
                    }
                )
            current_stage_name = next(
                (
                    str(stage.get("name") or "")
                    for stage in stages
                    if stage.get("status") == "in_progress"
                ),
                str(stages[0].get("name") or "") if stages else "",
            )
            return {
                "schema_version": "1.0",
                "learner_id": external_user_id,
                "availability": "available" if stages else "unavailable",
                "current_stage_name": current_stage_name or None,
                "stages": stages,
                "books": books,
                "current_section": current_section,
            }

        tool_registry.register(
            "get_recent_learning_summary",
            recent_learning_handler,
            allowed_agents={"diagnosis_agent"},
        )
        tool_registry.register(
            "get_learning_progress",
            learning_progress_handler,
            allowed_agents={"diagnosis_agent"},
        )
        tool_registry.register(
            "get_mastery_snapshot",
            review_status_handler,
            allowed_agents={"diagnosis_agent"},
        )
        tool_registry.register(
            "get_review_status",
            review_status_handler,
            allowed_agents={"diagnosis_agent"},
        )
        tool_registry.register(
            "get_current_plan_progress",
            load_current_plan_progress,
            allowed_agents={"diagnosis_agent"},
        )
        tool_registry.register(
            "get_learning_planning_context",
            load_learning_planning_context,
            allowed_agents={"diagnosis_agent"},
        )
        tool_registry.register(
            "get_learning_path_progress",
            load_learning_path_progress,
            allowed_agents={"diagnosis_agent"},
        )
        tool_registry.register(
            "get_kp_with_content",
            knowledge_tool.get_kp_with_content,
            allowed_agents={"knowledge_base_agent"},
        )
        if knowledge_backend is not None:
            tool_registry.register(
                "get_knowledge_map_routes",
                knowledge_backend.map.routes,
                allowed_agents={"knowledge_base_agent"},
            )
            tool_registry.register(
                "get_knowledge_map_nodes",
                knowledge_backend.map.nodes,
                allowed_agents={"knowledge_base_agent"},
            )
            tool_registry.register(
                "get_knowledge_point_detail",
                knowledge_backend.map.detail,
                allowed_agents={"knowledge_base_agent"},
            )
            tool_registry.register(
                "query_exam_knowledge",
                knowledge_backend.query_exam_knowledge,
                allowed_agents={"knowledge_base_agent"},
            )
            tool_registry.register(
                "get_kp_exam_matches",
                knowledge_backend.kp_exam_matches,
                allowed_agents={"knowledge_base_agent"},
            )
        tool_registry.register(
            "get_question_with_content",
            knowledge_tool.get_question_with_content,
            allowed_agents={"knowledge_base_agent"},
        )
        tool_registry.register(
            "search_video_resources",
            knowledge_tool.search_video_resources,
            allowed_agents={"knowledge_base_agent"},
        )
        tool_registry.register(
            "search_reference_resources",
            knowledge_tool.search_reference_resources,
            allowed_agents={"knowledge_base_agent"},
        )
        tool_registry.register(
            "search_question_resources",
            knowledge_tool.search_question_resources,
            allowed_agents={"knowledge_base_agent"},
        )
        tool_registry.register(
            "search_web_resources",
            knowledge_tool.search_web_resources,
            allowed_agents={"knowledge_base_agent"},
        )
        tool_registry.register(
            "get_external_fact_evidence",
            knowledge_tool.build_external_evidence_pack,
            allowed_agents={"knowledge_base_agent"},
        )
        exporter = SnapshotExporter(snapshot_root or package_root / "snapshots")
        writeback_executor = (
            WritebackExecutor(database_engine) if database_engine is not None else None
        )
        orchestrator_class = (
            LangGraphOrchestrator
            if settings.execution_engine == "langgraph"
            else Orchestrator
        )
        daily_task_execution_coordinator = DailyTaskExecutionCoordinator(
            engine=database_engine,
            plan_repository=plan_repository,
            backend_handoff_runtime=backend_handoff_runtime,
        )
        orchestrator = (
            orchestrator_class(
                registry,
                tool_registry,
                checkpointer=(
                    SqlAlchemyCheckpointSaver(
                        database_engine,
                        serde=JsonPlusSerializer(
                            pickle_fallback=True,
                            allowed_msgpack_modules=True,
                        ),
                    )
                    if database_engine is not None
                    else None
                ),
            )
            if orchestrator_class is LangGraphOrchestrator
            else orchestrator_class(registry, tool_registry)
        )
        return cls(
            PersonalizedReviewCardUseCase(
                orchestrator,
                exporter,
                writeback_executor=writeback_executor,
                terminal_trace=terminal_trace,
                model_trace_recorder=model_trace_recorder,
                plan_repository=plan_repository,
                run_state_repository=run_state_repository,
                conversation_repository=conversation_repository,
                review_service=review_service,
                behavior_context_loader=(
                    backend_handoff_runtime.load_learning_context
                    if backend_handoff_runtime is not None
                    else None
                ),
                multiscale_state_loader=(
                    backend_handoff_runtime.load_multiscale_learning_state
                    if backend_handoff_runtime is not None
                    else None
                ),
                path_candidate_loader=(
                    backend_handoff_runtime.load_path_candidates
                    if backend_handoff_runtime is not None
                    else None
                ),
                memory_retriever=memory_retrieval_service,
                memory_governance_writer=(
                    backend_handoff_runtime.persist_memory_governance
                    if backend_handoff_runtime is not None
                    else None
                ),
                profile_update_writer=(
                    backend_handoff_runtime.update_learning_profile
                    if backend_handoff_runtime is not None
                    else None
                ),
                profile_memory_extractor=None,
                workshop_runtime=backend_handoff_runtime,
                syllabus_context_loader=user_syllabus_service.load_context,
            ),
            review_service=review_service,
            authentication_service=authentication_service,
            account_profile_service=account_profile_service,
            workshop_library_service=workshop_library_service,
            textbook_pdf_service=textbook_pdf_service,
            textbook_import_service=textbook_import_service,
            user_syllabus_service=user_syllabus_service,
            textbook_pdf_ai_service=textbook_pdf_ai_service,
            learning_plan_service=learning_plan_service,
            daily_task_refresh_service=daily_task_refresh_service,
            daily_task_execution_coordinator=daily_task_execution_coordinator,
            writeback_executor=writeback_executor,
            question_retrieval_tool=knowledge_tool,
            knowledge_backend=knowledge_backend,
            mode=settings.mode,
            chat_model_name=(
                " → ".join(settings.chat_models)
                if settings.mode == "live"
                else "StubChatModel"
            ),
            embedding_model_name=(
                settings.embedding_model if settings.mode == "live" else "StubEmbeddingModel"
            ),
            model_trace_recorder=model_trace_recorder,
            auth_cookie_secure=settings.auth_cookie_secure,
            backend_handoff_runtime=backend_handoff_runtime,
            frontend_dist_root=settings.frontend_dist_root,
            default_route_repository=default_route_repository,
            textbook_route_repository=textbook_route_repository,
        )


class StreamingChatModel:
    """Trace every model boundary; optionally mirror it to the terminal."""

    def __init__(
        self,
        inner,
        terminal_trace: TerminalTrace | None = None,
        model_trace_recorder: ModelTraceRecorder | None = None,
        *,
        stream: bool = True,
    ) -> None:
        self.inner = inner
        self._terminal_lock = asyncio.Lock()
        self.terminal_trace = terminal_trace or TerminalTrace(enabled=False)
        self.model_trace_recorder = model_trace_recorder or ModelTraceRecorder()
        self.stream = stream

    def _transport_details(self, payload, result):
        request_payload = getattr(self.inner, "last_request_payload", None)
        response_text = getattr(self.inner, "last_response_text", None)
        reasoning_text = getattr(self.inner, "last_reasoning_text", None)
        if request_payload is None:
            request_payload = {
                "mode": "stub_or_non_http_model",
                "agent_context": payload,
            }
        if response_text is None:
            response_text = json.dumps(result, ensure_ascii=False)
        return request_payload, response_text, reasoning_text

    def _record_transport(self, trace_index, call_id, workflow_step_id, payload, result):
        request_payload, response_text, reasoning_text = self._transport_details(payload, result)
        self.model_trace_recorder.record_transport(
            trace_index,
            request_payload=request_payload,
            response_text=response_text,
            reasoning_text=reasoning_text,
        )
        emit_runtime_event(
            "model_transport",
            agent=str(payload.get("target_agent") or "model"),
            output_kind=(
                "compiler"
                if "compiler" in str(payload.get("target_agent") or "").lower()
                else "business"
            ),
            call_id=call_id,
            step_id=workflow_step_id,
            request_payload=request_payload,
            response_text=response_text,
        )

    async def complete_text(self, role, payload, on_delta=None):
        """Trace a business-agent prose call without exposing provider reasoning."""
        observable_payload = {
            key: value for key, value in payload.items() if key != "_result_validator"
        }
        trace_index = self.model_trace_recorder.begin(role, observable_payload)
        call_id = f"MODEL_CALL_{trace_index + 1}"
        workflow_step_id = str(payload.get("workflow_step_id", role))
        output_kind = "compiler" if "compiler" in str(role).lower() else "business"
        emit_runtime_event(
            "model_input", agent=role, call_id=call_id,
            output_kind=output_kind,
            step_id=workflow_step_id, raw_input=_sanitize(observable_payload),
        )
        stream_callback = on_delta
        if has_event_sink():
            def stream_callback(delta: str) -> None:
                emit_runtime_event(
                    "model_delta", agent=role, call_id=call_id,
                    output_kind=output_kind,
                    step_id=workflow_step_id, delta=delta,
                )
                if on_delta:
                    on_delta(delta)
        try:
            result = await self.inner.complete_text(role, payload, on_delta=stream_callback)
            self._record_transport(
                trace_index, call_id, workflow_step_id, observable_payload, result
            )
            self.model_trace_recorder.record_output_text(trace_index, result)
            emit_runtime_event(
                "model_output", agent=role, call_id=call_id,
                output_kind=output_kind,
                step_id=workflow_step_id, raw_output=str(result),
            )
            return str(result)
        except BaseException as exc:
            self.model_trace_recorder.fail(trace_index, exc)
            raise

    async def complete_json(self, role, payload, on_delta=None):
        observable_payload = {
            key: value for key, value in payload.items() if key != "_result_validator"
        }
        trace_index = self.model_trace_recorder.begin(role, observable_payload)
        call_id = f"MODEL_CALL_{trace_index + 1}"
        workflow_step_id = str(payload.get("workflow_step_id", role))
        output_kind = (
            "compiler" if "compiler" in str(role).lower() or str(role).lower().endswith("_contract")
            else "business"
        )
        emit_runtime_event(
            "model_input", agent=role, call_id=call_id,
            output_kind=output_kind,
            # The collaboration sidebar may expose this event to the browser;
            # keep the complete model boundary while redacting credentials and
            # personal identifiers first.
            step_id=workflow_step_id, raw_input=_sanitize(observable_payload),
        )
        stream_callback = on_delta
        if has_event_sink():
            def stream_callback(delta: str) -> None:
                emit_runtime_event(
                    "model_delta", agent=role, call_id=call_id,
                    output_kind=output_kind,
                    step_id=workflow_step_id, delta=delta,
                )
                if on_delta:
                    on_delta(delta)
        if not self.stream:
            try:
                result = await self.inner.complete_json(role, payload, on_delta=stream_callback)
                self._record_transport(
                    trace_index, call_id, workflow_step_id, observable_payload, result
                )
                self.model_trace_recorder.succeed(trace_index, result)
                emit_runtime_event(
                    "model_output", agent=role, call_id=call_id,
                    output_kind=output_kind,
                    step_id=workflow_step_id, raw_output=_sanitize(result),
                )
                return result
            except BaseException as exc:
                self.model_trace_recorder.fail(trace_index, exc)
                raise
        async with self._terminal_lock:
            terminal_agent_started(role)
            printer = on_delta
            if printer is None and self.terminal_trace.level in {"model", "full"}:
                printer = terminal_delta_printer(role)
            try:
                self.terminal_trace.model_input(role, observable_payload)
                if has_event_sink():
                    printer = stream_callback
                result = await self.inner.complete_json(role, payload, on_delta=printer)
                self.terminal_trace.model_output(role, result)
                self._record_transport(
                    trace_index, call_id, workflow_step_id, observable_payload, result
                )
                self.model_trace_recorder.succeed(trace_index, result)
                emit_runtime_event(
                    "model_output", agent=role, call_id=call_id,
                    output_kind=output_kind,
                    step_id=workflow_step_id, raw_output=_sanitize(result),
                )
                return result
            except BaseException as exc:
                self.model_trace_recorder.fail(trace_index, exc)
                raise
            finally:
                terminal_agent_finished(role)
