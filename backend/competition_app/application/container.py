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
from competition_app.runtime.snapshot import SnapshotExporter
from competition_app.runtime.tool_registry import ToolRegistry
from competition_app.tools.knowledge_assets import KnowledgeAssetPaths, KnowledgeAssetRepository
from competition_app.tools.knowledge_retrieval import KnowledgeRetrievalTool
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
from competition_app.services.daily_task_refresh import DailyTaskRefreshService
from competition_app.services.daily_task_execution import DailyTaskExecutionCoordinator
from competition_app.services.plan_progress import build_plan_progress
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
    learning_plan_service: LearningPlanService
    daily_task_refresh_service: DailyTaskRefreshService
    daily_task_execution_coordinator: DailyTaskExecutionCoordinator | None = None
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
        )
        if settings.mode == "live":
            if not settings.dashscope_api_key or not settings.siliconflow_api_key:
                raise ValueError("live mode requires configured model API keys")
            chat_model = FailoverChatModel(
                [
                    OpenAICompatibleChatModel(
                        settings.chat_base_url,
                        settings.dashscope_api_key,
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
                chat_api_key=settings.dashscope_api_key,
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
        registry.register("diagnosis_agent", DiagnosisAgent(chat_model))
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
            ),
            review_service=review_service,
            authentication_service=authentication_service,
            account_profile_service=account_profile_service,
            workshop_library_service=workshop_library_service,
            textbook_pdf_service=textbook_pdf_service,
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
            call_id=call_id,
            step_id=workflow_step_id,
            request_payload=request_payload,
            response_text=response_text,
            reasoning_text=reasoning_text,
        )

    async def complete_json(self, role, payload, on_delta=None):
        observable_payload = {
            key: value for key, value in payload.items() if key != "_result_validator"
        }
        trace_index = self.model_trace_recorder.begin(role, observable_payload)
        call_id = f"MODEL_CALL_{trace_index + 1}"
        workflow_step_id = str(payload.get("workflow_step_id", role))
        emit_runtime_event(
            "model_input", agent=role, call_id=call_id,
            step_id=workflow_step_id, raw_input=observable_payload,
        )
        stream_callback = on_delta
        if has_event_sink():
            def stream_callback(delta: str) -> None:
                emit_runtime_event(
                    "model_delta", agent=role, call_id=call_id,
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
                    step_id=workflow_step_id, raw_output=result,
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
                    step_id=workflow_step_id, raw_output=result,
                )
                return result
            except BaseException as exc:
                self.model_trace_recorder.fail(trace_index, exc)
                raise
            finally:
                terminal_agent_finished(role)
