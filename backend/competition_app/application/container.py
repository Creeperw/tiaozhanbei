from __future__ import annotations

import asyncio
import json
import os
import re
import threading
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from competition_app.services.planning_metrics import build_planning_metric_evidence

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
from competition_app.agents.evolution import EvolutionAgent
from competition_app.agents.evolution_rule_compiler import EvolutionRuleCompilerAgent
from competition_app.agents.review_scheduler import ReviewSchedulerAdapter
from competition_app.application.personalized_review_card import PersonalizedReviewCardUseCase
from competition_app.config import Settings, SettingsError
from competition_app.evaluation.accountability_faults import (
    AccountabilityEvaluationService,
    AccountabilityFaultController,
    FaultInjectingAgentProxy,
    accountability_fault_specs,
    evolution_effect_fault_specs,
)
from competition_app.llm.stub import StubChatModel
from competition_app.legacy_asset_compat import knowledge_component_root
from competition_app.llm.openai_compatible import OpenAICompatibleChatModel
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
from competition_app.llm.response_diagnostics import safe_response_diagnostics
from competition_app.runtime.debug_trace import (
    DebugTraceConfig,
    DebugTraceManager,
    bind_debug_call_context,
    record_debug_trace,
)
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
from competition_app.repositories.failure_case import (
    InMemoryFailureCaseRepository,
    SqlFailureCaseRepository,
)
from competition_app.repositories.evolution import (
    EvolutionRepository,
    InMemoryEvolutionRepository,
    SqlEvolutionRepository,
)
from competition_app.repositories.preference_training import (
    InMemoryPreferenceTrainingRepository,
    PreferenceTrainingRepository,
    SqlPreferenceTrainingRepository,
)
from competition_app.runtime.evolution_rules import EvolutionRuleRegistry
from competition_app.services.evolution_rule_service import EvolutionRuleService
from competition_app.services.failure_signature import FailureSignatureService
from competition_app.services.feedback_governance import FeedbackGovernanceService
from competition_app.services.preference_training import PreferenceTrainingService
from competition_app.services.question_relevance import (
    OpenAICompatibleRerankClient,
    QuestionRelevanceService,
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


_BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)
_urllib_ua_installed = False
_urllib_ua_lock = threading.Lock()


def _install_browser_user_agent() -> None:
    """为 urllib 全局安装带浏览器 UA 的 opener（幂等）。

    部分 OpenAI 兼容网关（如 opencode.ai）通过 Cloudflare 校验 User-Agent，
    默认的 Python-urllib UA 会被 403 拦截。组件包（question_pipeline）内部
    使用 urllib.request.urlopen，这里统一安装带 UA 的全局 opener。
    """

    global _urllib_ua_installed
    if _urllib_ua_installed:
        return
    with _urllib_ua_lock:
        if _urllib_ua_installed:
            return
        opener = urllib.request.build_opener()
        opener.addheaders = [("User-Agent", _BROWSER_USER_AGENT)]
        urllib.request.install_opener(opener)
        _urllib_ua_installed = True


class _EnvScopedQuestionCleaner:
    """包装 question_pipeline 的 LLM 抽取器，调用时临时注入知识库 Chat Key。

    与 knowledge_delivery 的题目导入管线保持一致：抽取器按环境变量名读取
    Key，调用前后恢复原环境，避免并发污染其他 LLM 调用。
    """

    def __init__(self, backend: Any, api_key: str) -> None:
        self.backend = backend
        self.api_key = api_key
        _install_browser_user_agent()
        markdown_module = backend._module("question_pipeline.markdown_ingest")
        llm_module = backend._module("question_pipeline.llm")
        client = llm_module.OpenAICompatibleChatClient(
            backend.chat_base_url,
            backend.chat_model,
            "COMPETITION_KB_CHAT_KEY",
        )
        self.inner = markdown_module.LLMMarkdownExtractor(client)

    def extract(
        self,
        markdown: str,
        source_ref: str,
        source_type: str,
        owner_id: str | None,
    ) -> list[dict[str, Any]]:
        previous = os.environ.get("COMPETITION_KB_CHAT_KEY")
        os.environ["COMPETITION_KB_CHAT_KEY"] = self.api_key
        try:
            return self.inner.extract(markdown, source_ref, source_type, owner_id)
        finally:
            if previous is None:
                os.environ.pop("COMPETITION_KB_CHAT_KEY", None)
            else:
                os.environ["COMPETITION_KB_CHAT_KEY"] = previous


def _build_web_question_ingest(
    knowledge_backend: Any,
    exa_retriever: ExaVideoRetriever,
) -> Any:
    """组装网络搜索题目补充服务（live 环境）。

    仅当知识库后端与 Exa 均可用时启用；清洗器复用 question_pipeline 的
    LLM 抽取器，不补造原文没有的题目。
    """
    from competition_app.tools.web_question_ingest import WebQuestionIngestService

    chat_api_key = (
        knowledge_backend.chat_api_key
        if getattr(knowledge_backend, "chat_api_key", None)
        else None
    )
    if not chat_api_key:
        return None
    cleaner = _EnvScopedQuestionCleaner(knowledge_backend, chat_api_key)
    return WebQuestionIngestService(
        searcher=exa_retriever,
        cleaner=cleaner,
        store=knowledge_backend.map,
        runtime_dir=knowledge_backend.paths.question_runtime,
    )


@dataclass
class ApplicationContainer:
    review_card_use_case: PersonalizedReviewCardUseCase
    review_service: ReviewService
    failure_case_repository: FailureCaseRepository
    evolution_repository: EvolutionRepository
    preference_training_repository: PreferenceTrainingRepository
    feedback_governance_service: FeedbackGovernanceService
    failure_signature_service: FailureSignatureService
    evolution_rule_service: EvolutionRuleService
    preference_training_service: PreferenceTrainingService
    evolution_agent: EvolutionAgent
    evolution_rule_compiler_agent: EvolutionRuleCompilerAgent
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
    debug_trace_manager: DebugTraceManager | None = None
    auth_cookie_secure: bool = False
    backend_handoff_runtime: BackendHandoffRuntime | None = None
    frontend_dist_root: Path | None = None
    default_route_repository: DefaultRouteRepository | None = None
    textbook_route_repository: TextbookRouteRepository | None = None
    accountability_evaluation_service: AccountabilityEvaluationService | None = None
    accountability_evaluation_token: str | None = field(default=None, repr=False)
    runtime_root: Path | None = None

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
            failure_case_repository = SqlFailureCaseRepository(database_engine)
            evolution_repository = SqlEvolutionRepository(database_engine)
            preference_training_repository = SqlPreferenceTrainingRepository(
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
            failure_case_repository = InMemoryFailureCaseRepository()
            evolution_repository = InMemoryEvolutionRepository()
            preference_training_repository = InMemoryPreferenceTrainingRepository()
        feedback_governance_service = FeedbackGovernanceService(
            evolution_repository,
            enabled=settings.evolution_enabled,
        )
        failure_signature_service = FailureSignatureService(
            evolution_repository,
            min_cases=settings.evolution_min_cases,
            min_executions=settings.evolution_min_executions,
            min_high_trust=settings.evolution_min_high_trust,
        )
        evolution_rule_service = EvolutionRuleService(
            evolution_repository,
            enabled=settings.evolution_enabled,
        )
        preference_training_service = PreferenceTrainingService(
            preference_training_repository,
            settings.evolution_data_root,
            enabled=settings.preference_training_enabled,
            allow_trl_dpo=settings.preference_training_allow_trl_dpo,
        )
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
        chat_api_key = settings.llm_api_key
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
            if not chat_api_key:
                raise ValueError("live mode requires a configured chat API key")
            # 不使用 failover 候选切换：固定使用配置的第一个模型（如 deepseek-v4-flash），
            # 空响应/瞬态失败由传输层 bounded 重试与可重试错误码兜底。
            chat_model = OpenAICompatibleChatModel(
                settings.chat_base_url,
                chat_api_key,
                settings.chat_models[0],
                api_keys=(
                    settings.llm_api_keys
                    if settings.llm_api_keys
                    else (chat_api_key,)
                ),
                timeout_seconds=settings.llm_timeout_seconds,
                # LLM_TIMEOUT_SECONDS is the complete provider-call budget in
                # live configuration, not merely the socket idle interval.
                total_timeout_seconds=settings.llm_timeout_seconds,
            )
            embedding_model = SiliconFlowEmbeddingModel(
                settings.embedding_base_url,
                settings.embedding_api_key,
                settings.embedding_model,
            )
            delivery_paths = KnowledgeDeliveryPaths.from_release_root(
                settings.knowledge_release_root,
                runtime_root=settings.knowledge_runtime_root,
                public_vector_store=settings.question_vector_store_root,
            )
            knowledge_backend = KnowledgeDeliveryBackend(
                delivery_paths,
                embedding_base_url=settings.embedding_base_url,
                embedding_model=settings.embedding_model,
                embedding_api_key=settings.embedding_api_key,
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
        knowledge_component = knowledge_component_root(settings.knowledge_release_root)
        textbook_import_service = TextbookImportService(
            runtime_root=settings.runtime_root,
            chat_base_url=settings.chat_base_url,
            chat_model=settings.chat_model,
            chat_api_key=chat_api_key,
            mineru_token=settings.mineru_token,
            mineru_pipeline_root=(
                knowledge_component / "knowledge_upload_pipeline"
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
                knowledge_component / "knowledge_upload_pipeline"
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
        backend_handoff_runtime = None
        if include_backend_handoff:

            def build_review_context(
                external_user_id: str, history_limit: int = 100
            ) -> dict[str, Any]:
                """合并新库（competition_app）复习数据，供智能体复习状态查询使用。

                前端 `/api/v1/review-dashboard` 与智能体 `get_review_status` 共用
                `load_review_dashboard`：旧库部分（mastery/review_states/历史任务）
                由 backend_handoff 提供，此处补充新库的到期队列、最新调度与活跃任务，
                保证两端看到同一份“复习安排”。
                """
                del history_limit
                extra: dict[str, Any] = {
                    "due_queue": [],
                    "latest_schedule": None,
                    "active_review_tasks": [],
                }
                try:
                    queue = review_service.get_queue(external_user_id, limit=200)
                    deliveries = review_service.list_active_deliveries(
                        external_user_id
                    )
                    schedule = review_service.get_latest_schedule(external_user_id)
                except Exception:
                    return extra
                due_queue = []
                for entry in queue.entries:
                    unit = entry.memory_unit
                    due_queue.append({
                        "kp_id": unit.kp_id,
                        "kp_name": (
                            str(unit.prompt_abstract or "").strip()
                            or unit.kp_id
                        ),
                        "is_due": bool(entry.is_due),
                        "next_review_at": (
                            unit.next_review_at.isoformat()
                            if unit.next_review_at
                            else None
                        ),
                        "retention_estimate": float(entry.retention_estimate or 0.0),
                        "mastery_score": float(unit.mastery_score or 0.0),
                        "review_stage": unit.review_stage,
                        "requires_remediation": bool(unit.requires_remediation),
                        "reason_codes": list(entry.reason_codes or []),
                        "has_task": entry.task is not None,
                        "has_resource": entry.resource is not None,
                    })
                extra["due_queue"] = due_queue
                extra["active_review_tasks"] = [
                    {
                        "review_task_id": delivery.task.review_task_id,
                        "kp_id": delivery.task.primary_kp_id,
                        "kp_name": str(delivery.task.primary_kp_id),
                        "review_type": delivery.task.review_type,
                        "status": delivery.task.status,
                        "priority_score": float(
                            delivery.task.priority_score or 0.0
                        ),
                    }
                    for delivery in deliveries
                ]
                if schedule is not None:
                    extra["latest_schedule"] = schedule.model_dump(mode="json")
                return extra

            backend_handoff_runtime = load_backend_handoff(
                settings,
                review_context_provider=build_review_context,
            )
        knowledge_point_resolver = None
        if backend_handoff_runtime is not None:

            def canonical_review_mapping(
                external_user_id: str,
                kp_ids: tuple[str, ...],
            ) -> dict[str, str]:
                return backend_handoff_runtime.canonicalize_knowledge_point_ids(
                    external_user_id,
                    kp_ids,
                )

            review_service.canonical_mapping_provider = canonical_review_mapping

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
                    # 知识库缺失/题量不足时，兜底查询网络搜索补充题库
                    # （已清洗去重入库的知识点直接复用，不再重复搜索）。
                    bundle = knowledge_backend.map.resolve_web_question_bundle(
                        name,
                        required_question_count=3,
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
        exa_retriever = (
            ExaVideoRetriever(settings.exa_api_key)
            if settings.mode == "live" and settings.exa_api_key
            else None
        )
        web_question_ingest = None
        if knowledge_backend is not None and exa_retriever is not None:
            web_question_ingest = _build_web_question_ingest(
                knowledge_backend, exa_retriever
            )

        def load_review_knowledge_points(learner_id: str) -> list[str]:
            """Due-for-review knowledge point names, for the daily quiz pool."""
            try:
                queue = review_service.get_queue(learner_id, limit=100)
            except Exception:
                return []
            names: list[str] = []
            for entry in queue.entries:
                if not (entry.is_due or entry.retention_estimate < 0.85):
                    continue
                name = str(entry.memory_unit.prompt_abstract or "").strip()
                if not name or name == "知识点名称待补充":
                    continue
                if name not in names:
                    names.append(name)
                if len(names) >= 5:
                    break
            return names

        learning_plan_service = LearningPlanService(
            default_route_repository,
            plan_repository,
            knowledge_point_resolver=knowledge_point_resolver,
            video_resource_resolver=video_resource_resolver,
            review_knowledge_point_loader=load_review_knowledge_points,
            web_question_ingest=web_question_ingest,
        )
        task_load_policy_loader = None
        path_candidate_loader = None
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
            path_candidate_loader = backend_handoff_runtime.load_path_candidates
        daily_task_refresh_service = DailyTaskRefreshService(
            plan_repository,
            knowledge_point_resolver=knowledge_point_resolver,
            video_resource_resolver=video_resource_resolver,
            task_load_policy_loader=task_load_policy_loader,
            path_candidate_loader=path_candidate_loader,
            review_knowledge_point_loader=load_review_knowledge_points,
        )
        knowledge_tool = KnowledgeRetrievalTool(
            repository,
            embedding_model,
            question_retriever=question_retriever,
            textbook_retriever=textbook_retriever,
            exa_retriever=exa_retriever,
            delivery_backend=knowledge_backend,
            exa_limit=settings.knowledge_retrieval_exa_limit,
            textbook_limit=settings.knowledge_retrieval_textbook_limit,
        )
        rerank_client = (
            OpenAICompatibleRerankClient(
                base_url=settings.question_rerank_base_url,
                api_key=settings.question_rerank_api_key or "",
                model=settings.question_rerank_model,
                timeout_seconds=settings.question_rerank_timeout_seconds,
            )
            if settings.question_rerank_mode != "disabled"
            and settings.question_rerank_base_url
            and settings.question_rerank_api_key
            else None
        )
        question_relevance_service = QuestionRelevanceService(
            rerank_client,
            mode=settings.question_rerank_mode,
            top_n=settings.question_rerank_top_n,
            batch_size=settings.question_rerank_batch_size,
            eligible_threshold=settings.question_rerank_eligible_threshold,
            reject_threshold=settings.question_rerank_reject_threshold,
        )
        terminal_trace = TerminalTrace(enabled=stream_model_output, level=trace_level)
        # Normal web/worker runs retain only bounded diagnostics.  Complete
        # model boundaries require either the explicit CLI full-trace mode or
        # the context-local capture used by the isolated evaluation service.
        model_trace_recorder = ModelTraceRecorder(full_capture=trace_level == "full")
        debug_trace_manager = DebugTraceManager(
            DebugTraceConfig(
                enabled=settings.debug_trace_enabled,
                root=settings.debug_trace_root,
                max_bytes=settings.debug_trace_max_bytes,
                rotate_bytes=settings.debug_trace_rotate_bytes,
                flush=settings.debug_trace_flush,
                run_ids=settings.debug_trace_run_ids,
                allow_live=settings.debug_trace_allow_live,
            ),
            mode=settings.mode,
        )
        chat_model = StreamingChatModel(
            chat_model,
            terminal_trace,
            model_trace_recorder,
            debug_trace_manager=debug_trace_manager,
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
        registry.register(
            "knowledge_base_agent",
            KnowledgeBaseAgent(
                knowledge_tool,
                chat_model,
                question_relevance_service=question_relevance_service,
                supplement_max_rounds=settings.knowledge_supplement_max_rounds,
                supplement_max_queries=settings.knowledge_supplement_max_queries,
                supplement_query_max_length=settings.knowledge_supplement_query_max_length,
            ),
        )
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
        registry.register(
            "review_scheduler",
            ReviewSchedulerAdapter(review_service=review_service),
        )
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
        review_status_handler_with_dispatch = None
        if backend_handoff_runtime is not None:
            recent_learning_handler = (
                backend_handoff_runtime.load_recent_learning_summary
            )
            learning_progress_handler = backend_handoff_runtime.load_learning_statistics
            review_status_handler = backend_handoff_runtime.load_review_dashboard

        # 到期复习自动调度：当智能体（或前端）查询复习状态发现存在“已到期但
        # 还没有任务/资源”的知识点时，在独立线程中触发 due_review_dispatch
        # 快速路径（diagnosis 无模型 + scheduler 确定性算法 + expert 生成复习
        # 卡并绑定资源），让“安排”真实落库，而不是只在回复里口头提及。
        # 触发条件是查询行为本身（review_status），掌握度快照
        # （get_mastery_snapshot）复用同一 handler，但不得触发资源生成。
        # 与 app.py 的 schedule_due_review_resource 一致，使用事件循环内
        # create_task：后台任务可继承当前 Context（SSE sink），且避免
        # 新线程 + 新事件循环导致的 ContextVar 丢失。
        _due_review_dispatch_guard = threading.Lock()
        _due_review_dispatch_running: set[str] = set()
        _due_review_dispatch_tasks: set[asyncio.Task] = set()

        def _spawn_due_review_dispatch(learner_id: str) -> None:
            with _due_review_dispatch_guard:
                if learner_id in _due_review_dispatch_running:
                    return
                _due_review_dispatch_running.add(learner_id)

            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                with _due_review_dispatch_guard:
                    _due_review_dispatch_running.discard(learner_id)
                return

            async def _tracked() -> None:
                try:
                    await _run_due_review_dispatch(learner_id)
                finally:
                    with _due_review_dispatch_guard:
                        _due_review_dispatch_running.discard(learner_id)
                    _due_review_dispatch_tasks.discard(asyncio.current_task())

            task = loop.create_task(_tracked())
            _due_review_dispatch_tasks.add(task)

        async def _run_due_review_dispatch(learner_id: str) -> None:
            from competition_app.application.personalized_review_card import (
                ReviewCardRequest,
            )

            try:
                queue = review_service.get_queue(learner_id, limit=200)
            except Exception:
                return
            entry = next(
                (
                    item
                    for item in queue.entries
                    if item.is_due
                    and (item.task is None or item.resource is None)
                ),
                None,
            )
            if entry is None:
                return
            unit = entry.memory_unit
            topic = str(unit.prompt_abstract or "").strip()
            for suffix in ("个性化复习卡", "个性化练习", "复习卡片", "复习卡"):
                if topic.endswith(suffix):
                    topic = topic[: -len(suffix)].strip()
            if topic in {"", "知识点名称待补充", "待补充知识点", "知识点待确认"}:
                topic = str(unit.kp_id)
            if not topic:
                return
            try:
                await review_card_use_case.execute(
                    ReviewCardRequest(
                        learner_id=learner_id,
                        user_request=(
                            "请为以下已到期知识点生成一张可立即学习的复习卡："
                            f"{topic}"
                        ),
                        available_minutes=15,
                        system_operation="due_review_dispatch",
                        user_knowledge_state=[
                            {
                                "user_id": learner_id,
                                "kp_id": unit.kp_id,
                                "knowledge_mastery": float(
                                    (unit.mastery_score or 0.0) / 100
                                ),
                                "answer_accuracy": float(
                                    (unit.mastery_score or 0.0) / 100
                                ),
                                "forgetting_coefficient": float(
                                    unit.lambda_per_day or 0.08
                                ),
                                "kp_review_status": "到期",
                                "calculated_at": (
                                    unit.source_calculated_at
                                    or unit.last_review_at
                                    or unit.created_at
                                ),
                            }
                        ],
                    )
                )
            except Exception:
                # 后台生成失败不阻断查询本身；下次查询会再次尝试。
                pass

        if backend_handoff_runtime is not None:
            # 包装 get_review_status 专用 handler：查询后若存在“到期且无任务”
            # 的知识点，后台触发一次快速调度（get_mastery_snapshot 共用
            # load_review_dashboard，但通过此包装只对复习状态查询生效——容器
            # 内两个工具注册同一个 handler，需按工具名区分）。
            async def _review_status_with_dispatch(
                external_user_id: str,
                *,
                history_limit: int = 100,
            ) -> dict[str, Any]:
                dashboard = await asyncio.to_thread(
                    backend_handoff_runtime.load_review_dashboard,
                    external_user_id,
                    history_limit=history_limit,
                )
                due_queue = dashboard.get("due_queue") or []
                if any(
                    item.get("is_due") and not item.get("has_task")
                    for item in due_queue
                ):
                    _spawn_due_review_dispatch(external_user_id)
                return dashboard

            review_status_handler_with_dispatch = _review_status_with_dispatch

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
            available_minutes: int | None = None,
            current_user_request: str = "",
            recent_messages: list[dict[str, Any]] | None = None,
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
                    "prerequisite_turn": {
                        "current_user_request": str(current_user_request or "")[:2000],
                        "recent_messages": [
                            {
                                "role": str(item.get("role") or ""),
                                "message_id": str(item.get("message_id") or ""),
                                "content": str(item.get("content") or "")[:1000],
                            }
                            for item in list(recent_messages or [])[-6:]
                            if isinstance(item, dict)
                        ],
                    },
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
                prerequisite_evidence = (
                    raw_candidates.get("prerequisite_evidence")
                    if isinstance(raw_candidates, dict)
                    and isinstance(raw_candidates.get("prerequisite_evidence"), dict)
                    else {}
                )
            else:
                candidate_items = []
                prerequisite_evidence = {}
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
                "planning_metric_evidence": (
                    build_planning_metric_evidence(behavior)
                    if scope in {"long_term", "short_term"} else None
                ),
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
                    "state_digest": multiscale.get("state_digest"),
                    "prerequisite_evidence": prerequisite_evidence,
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
            (
                review_status_handler_with_dispatch
                if review_status_handler_with_dispatch is not None
                else review_status_handler
            ),
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
        from competition_app.services.current_learning_state import CurrentLearningStateReader

        learning_state_reader = CurrentLearningStateReader(
            plan_repository,
            backend_handoff_runtime.load_current_learning_facts if backend_handoff_runtime is not None else None,
            ReviewService(
                review_service.repository,
                canonical_mapping_provider=(backend_handoff_runtime.read_canonical_knowledge_point_ids
                                            if backend_handoff_runtime is not None else None),
            ),
        )
        async def read_current_learning_state(*, view: str = "overview", book_id: str | None = None,
                                              section_id: str | None = None,
                                              cursor: int | None = None, limit: int = 8) -> dict:
            import asyncio
            return await asyncio.to_thread(
                learning_state_reader.read, view=view, book_id=book_id,
                section_id=section_id, cursor=cursor, limit=limit,
            )

        tool_registry.register(
            "get_current_learning_state",
            read_current_learning_state,
            allowed_agents={"planner_agent", "knowledge_base_agent", "diagnosis_agent", "audit_agent", "learning_plan_service"},
        )
        # Publication holds the repository's reentrant mutation lock. Validation
        # must run on that same thread, not wait for a worker acquiring its lock.
        tool_registry.register(
            "validate_current_learning_state",
            learning_state_reader.read,
            allowed_agents={"learning_plan_service"},
        )
        # Keep the legacy tool only for isolated compatibility callers; live
        # workflows must use the authenticated, canonical state projection.
        def compatible_learning_path_progress(external_user_id: str) -> dict:
            from competition_app.exam_scope import current_exam_workspace
            workspace = current_exam_workspace()
            if workspace is not None and workspace.exam_track_id:
                if workspace.learner_id != external_user_id:
                    raise PermissionError("learning state owner mismatch")
                return learning_state_reader.read()
            if settings.mode == "live":
                raise PermissionError("learning state requires an authenticated exam workspace")
            return load_learning_path_progress(external_user_id)

        tool_registry.register(
            "get_learning_path_progress",
            compatible_learning_path_progress,
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
            "livecrawl_web_resources",
            knowledge_tool.livecrawl_web_resources,
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
        evolution_rule_registry = EvolutionRuleRegistry(
            evolution_repository,
            enabled=(
                settings.evolution_enabled and settings.evolution_rules_enabled
            ),
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
                evolution_rule_registry=evolution_rule_registry,
                provider_timeout_seconds=settings.llm_timeout_seconds,
            )
            if orchestrator_class is LangGraphOrchestrator
            else orchestrator_class(
                registry,
                tool_registry,
                evolution_rule_registry=evolution_rule_registry,
                provider_timeout_seconds=settings.llm_timeout_seconds,
            )
        )
        review_card_use_case = PersonalizedReviewCardUseCase(
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
                failure_case_repository=failure_case_repository,
                feedback_governance_service=feedback_governance_service,
                failure_signature_service=failure_signature_service,
                debug_trace_manager=debug_trace_manager,
            )
        accountability_evaluation_service = None
        if settings.accountability_evaluation_enabled:
            token = str(settings.accountability_evaluation_token or "")
            if len(token) < 16:
                raise SettingsError(
                    "ACCOUNTABILITY_EVALUATION_TOKEN must contain at least 16 characters "
                    "when accountability evaluation is enabled"
                )
            fault_controller = AccountabilityFaultController()
            evaluation_registry = AgentRegistry()
            for agent_name, agent in registry.items():
                evaluation_registry.register(
                    agent_name,
                    (
                        FaultInjectingAgentProxy(
                            (
                                "expert_agent"
                                if agent_name == "knowledge_explanation_agent"
                                else agent_name
                            ),
                            agent,
                            fault_controller,
                        )
                        if agent_name
                        in {
                            "knowledge_base_agent",
                            "knowledge_explanation_agent",
                            "expert_agent",
                            "audit_agent",
                        }
                        else agent
                    ),
                )
            evaluation_orchestrator = (
                orchestrator_class(
                    evaluation_registry,
                    tool_registry,
                    provider_timeout_seconds=settings.llm_timeout_seconds,
                )
                if orchestrator_class is not LangGraphOrchestrator
                else orchestrator_class(
                    evaluation_registry,
                    tool_registry,
                    checkpointer=None,
                    provider_timeout_seconds=settings.llm_timeout_seconds,
                )
            )
            evaluation_use_case = PersonalizedReviewCardUseCase(
                evaluation_orchestrator,
                SnapshotExporter(
                    settings.runtime_root
                    / "evaluation"
                    / "accountability"
                    / "snapshots"
                ),
                terminal_trace=terminal_trace,
                model_trace_recorder=model_trace_recorder,
                plan_repository=InMemoryLearningPlanRepository(),
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
                syllabus_context_loader=user_syllabus_service.load_context,
            )
            accountability_evaluation_service = AccountabilityEvaluationService(
                evaluation_use_case,
                fault_controller,
                {
                    **accountability_fault_specs(),
                    **evolution_effect_fault_specs(),
                },
                expert_agent=registry.get("knowledge_explanation_agent"),
                audit_agent=registry.get("audit_agent"),
                evolution_repository=evolution_repository,
                model_trace_recorder=model_trace_recorder,
            )

        evolution_agent = EvolutionAgent(chat_model)
        evolution_rule_compiler_agent = EvolutionRuleCompilerAgent(chat_model)

        return cls(
            review_card_use_case,
            review_service=review_service,
            failure_case_repository=failure_case_repository,
            evolution_repository=evolution_repository,
            preference_training_repository=preference_training_repository,
            feedback_governance_service=feedback_governance_service,
            failure_signature_service=failure_signature_service,
            evolution_rule_service=evolution_rule_service,
            preference_training_service=preference_training_service,
            evolution_agent=evolution_agent,
            evolution_rule_compiler_agent=evolution_rule_compiler_agent,
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
            debug_trace_manager=debug_trace_manager,
            auth_cookie_secure=settings.auth_cookie_secure,
            backend_handoff_runtime=backend_handoff_runtime,
            frontend_dist_root=settings.frontend_dist_root,
            default_route_repository=default_route_repository,
            textbook_route_repository=textbook_route_repository,
            accountability_evaluation_service=accountability_evaluation_service,
            accountability_evaluation_token=settings.accountability_evaluation_token,
            runtime_root=settings.runtime_root,
        )


class StreamingChatModel:
    """Trace every model boundary; optionally mirror it to the terminal."""

    def __init__(
        self,
        inner,
        terminal_trace: TerminalTrace | None = None,
        model_trace_recorder: ModelTraceRecorder | None = None,
        debug_trace_manager: DebugTraceManager | None = None,
        *,
        stream: bool = True,
    ) -> None:
        self.inner = inner
        self.terminal_trace = terminal_trace or TerminalTrace(enabled=False)
        self.model_trace_recorder = model_trace_recorder or ModelTraceRecorder()
        self.debug_trace_manager = debug_trace_manager
        self.stream = stream

    @property
    def last_response_text(self) -> str | None:
        """Expose the context-local final provider response for stage receipts."""

        return getattr(self.inner, "last_response_text", None)

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

    def _safe_error_details(self) -> dict[str, object]:
        details = getattr(self.inner, "last_error_details", None)
        if not isinstance(details, dict):
            return {}
        safe: dict[str, object] = {}
        for key in ("status_code", "retry_count", "transport_stage"):
            value = details.get(key)
            if key == "status_code":
                try:
                    value = int(value)
                except (TypeError, ValueError):
                    value = None
                if value is not None and not 100 <= value <= 599:
                    value = None
            elif key == "retry_count":
                try:
                    value = int(value)
                except (TypeError, ValueError):
                    value = None
                if value is not None and not 0 <= value <= 100:
                    value = None
            elif key == "transport_stage":
                value = str(value or "").strip().lower()
                if value not in {
                    "connect", "proxy", "read", "write", "pool", "protocol",
                    "http_response", "timeout", "network",
                }:
                    value = None
            if value is not None:
                safe[key] = value
        return safe

    def _safe_failure_event_details(
        self, error: BaseException
    ) -> dict[str, object]:
        """Return only bounded machine diagnostics for a model-failed event.

        Failure events can be observed by more than the browser-facing
        projection (for example, an internal test or recording sink).  Do not
        put exception text or the provider's raw error payload on the event
        even if a later projection would remove it.
        """

        result: dict[str, object] = {"error_type": type(error).__name__}
        inner_details = getattr(self.inner, "last_error_details", None)
        if not isinstance(inner_details, dict):
            inner_details = {}
        reason = str(
            getattr(error, "reason", None) or inner_details.get("reason") or ""
        ).strip().lower()
        if re.fullmatch(r"[a-z0-9_-]{1,80}", reason):
            result["reason"] = reason
        status_code = getattr(error, "status_code", None)
        try:
            status_code = int(status_code)
        except (TypeError, ValueError):
            status_code = None
        if status_code is not None and 100 <= status_code <= 599:
            result["status_code"] = status_code
        safe_details = self._safe_error_details()
        if safe_details:
            result["transport_error"] = safe_details
        return result

    def _attach_failure_diagnostics(self, error: BaseException) -> None:
        safe_details = self._safe_error_details()
        if safe_details:
            setattr(error, "last_error_details", safe_details)
        timing_details = getattr(self.inner, "last_timing_details", None)
        if isinstance(timing_details, dict):
            safe_timing: dict[str, Any] = {}
            response_diagnostics = safe_response_diagnostics(timing_details.get("response_diagnostics"))
            if response_diagnostics:
                safe_timing["response_diagnostics"] = response_diagnostics
            for key, maximum in (
                ("queue_wait_ms", 86_400_000),
                ("provider_duration_ms", 86_400_000),
                ("request_attempt_count", 100),
            ):
                try:
                    value = int(timing_details.get(key, 0))
                except (TypeError, ValueError):
                    continue
                if 0 <= value <= maximum:
                    safe_timing[key] = value
            last_reasoning_at = timing_details.get(
                "last_reasoning_at_monotonic"
            )
            if isinstance(last_reasoning_at, (int, float)) and (
                0 <= float(last_reasoning_at) <= 10_000_000_000
            ):
                safe_timing["last_reasoning_at_monotonic"] = float(
                    last_reasoning_at
                )
            for key, maximum in (
                ("reasoning_delta_count", 10_000_000),
                ("response_chars", 100_000_000),
            ):
                try:
                    value = int(timing_details.get(key, 0))
                except (TypeError, ValueError):
                    continue
                if 0 <= value <= maximum:
                    safe_timing[key] = value
            if safe_timing:
                setattr(error, "last_timing_details", safe_timing)

    def _record_transport(self, trace_index, call_id, workflow_step_id, payload, result):
        request_payload, response_text, reasoning_text = self._transport_details(payload, result)
        timing_details = getattr(self.inner, "last_timing_details", None)
        self.model_trace_recorder.record_transport(
            trace_index,
            request_payload=request_payload,
            response_text=response_text,
            reasoning_text=reasoning_text,
            timing_details=(
                timing_details if isinstance(timing_details, dict) else None
            ),
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

    async def complete_text(self, role, payload, on_delta=None, on_reasoning=None):
        """Trace a business-agent prose call without changing public output."""
        observable_payload = {
            key: value for key, value in payload.items() if key != "_result_validator"
        }
        trace_index = self.model_trace_recorder.begin(role, observable_payload)
        call_id = f"MODEL_CALL_{trace_index + 1}"
        workflow_step_id = str(payload.get("workflow_step_id", role))
        output_kind = "compiler" if "compiler" in str(role).lower() else "business"
        reasoning_emitted = {"started": False}

        with bind_debug_call_context(
            agent=role,
            step_id=workflow_step_id,
            call_id=call_id,
            output_kind=output_kind,
        ):
            record_debug_trace(
                "model_call_started",
                mode="text",
                request_payload=observable_payload,
            )
            emit_runtime_event(
                "model_input", agent=role, call_id=call_id,
                output_kind=output_kind,
                step_id=workflow_step_id, raw_input=_sanitize(observable_payload),
            )
            emit_runtime_event(
                "business_text_started",
                agent=role,
                call_id=call_id,
                output_kind=output_kind,
                step_id=workflow_step_id,
            )
            stream_callback = on_delta
            reasoning_callback = on_reasoning

            if has_event_sink():
                def stream_callback(delta: str) -> None:
                    emit_runtime_event(
                        "model_delta", agent=role, call_id=call_id,
                        output_kind=output_kind,
                        step_id=workflow_step_id, delta=delta,
                    )
                    emit_runtime_event(
                        "business_text_delta",
                        agent=role,
                        call_id=call_id,
                        output_kind=output_kind,
                        step_id=workflow_step_id,
                        delta=delta,
                    )
                    if on_delta:
                        on_delta(delta)

                def reasoning_callback(delta: str) -> None:
                    if not reasoning_emitted["started"]:
                        reasoning_emitted["started"] = True
                        emit_runtime_event(
                            "reasoning_started",
                            agent=role,
                            call_id=call_id,
                            output_kind=output_kind,
                            step_id=workflow_step_id,
                        )
                    emit_runtime_event(
                        "reasoning_delta",
                        agent=role,
                        call_id=call_id,
                        output_kind=output_kind,
                        step_id=workflow_step_id,
                        delta=delta,
                    )
                    if on_reasoning:
                        on_reasoning(delta)

                def reasoning_committed() -> None:
                    emit_runtime_event(
                        "reasoning_committed",
                        agent=role,
                        call_id=call_id,
                        output_kind=output_kind,
                        step_id=workflow_step_id,
                    )

            try:
                inner_reasoning = (
                    reasoning_callback if has_event_sink() else on_reasoning
                )
                result = await self.inner.complete_text(
                    role,
                    payload,
                    on_delta=stream_callback,
                    on_reasoning=inner_reasoning,
                )
                if reasoning_emitted["started"]:
                    reasoning_committed()
                self._record_transport(
                    trace_index, call_id, workflow_step_id, observable_payload, result
                )
                record_debug_trace(
                    "model_call_completed",
                    mode="text",
                    response_text=getattr(self.inner, "last_response_text", None)
                    or str(result),
                    reasoning_text=getattr(self.inner, "last_reasoning_text", None),
                    timing_details=getattr(self.inner, "last_timing_details", None),
                )
                self.model_trace_recorder.record_output_text(trace_index, result)
                emit_runtime_event(
                    "model_output", agent=role, call_id=call_id,
                    output_kind=output_kind,
                    step_id=workflow_step_id, raw_output=str(result),
                )
                emit_runtime_event(
                    "business_text_completed",
                    agent=role,
                    call_id=call_id,
                    output_kind=output_kind,
                    step_id=workflow_step_id,
                )
                return str(result)
            except BaseException as exc:
                self._attach_failure_diagnostics(exc)
                record_debug_trace(
                    "model_call_failed",
                    mode="text",
                    error_type=type(exc).__name__,
                    error_reason=getattr(exc, "reason", None),
                    error_details=getattr(self.inner, "last_error_details", None),
                    response_text=getattr(self.inner, "last_response_text", None),
                    reasoning_text=getattr(self.inner, "last_reasoning_text", None),
                )
                self.model_trace_recorder.fail(trace_index, exc)
                emit_runtime_event(
                    "model_failed",
                    agent=role,
                    call_id=call_id,
                    output_kind=output_kind,
                    step_id=workflow_step_id,
                    **self._safe_failure_event_details(exc),
                )
                emit_runtime_event(
                    "business_text_failed",
                    agent=role,
                    call_id=call_id,
                    output_kind=output_kind,
                    step_id=workflow_step_id,
                    error_type=type(exc).__name__,
                )
                raise

    async def complete_json(self, role, payload, on_delta=None, on_reasoning=None):
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
        with bind_debug_call_context(
            agent=role,
            step_id=workflow_step_id,
            call_id=call_id,
            output_kind=output_kind,
        ):
            record_debug_trace(
                "model_call_started",
                mode="json",
                request_payload=observable_payload,
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
            reasoning_callback = on_reasoning
            reasoning_emitted = {"started": False}
            # Structured calls are internal execution contracts.  The browser
            # never consumes their raw JSON deltas, so merely having an SSE event
            # sink must not force the provider into streaming mode.  Besides
            # leaking no useful business prose, that path made some compatible
            # gateways return an empty SSE body before a transient 5xx retry.
            if has_event_sink() and on_delta is not None:
                def stream_callback(delta: str) -> None:
                    emit_runtime_event(
                        "model_delta", agent=role, call_id=call_id,
                        output_kind=output_kind,
                        step_id=workflow_step_id, delta=delta,
                    )
                    if on_delta:
                        on_delta(delta)
            # Reasoning deltas are forwarded to the browser even when the JSON
            # deltas themselves are not (structured calls); non-streaming inner
            # calls deliver the complete reasoning text in one callback, which is
            # still rendered as a Copilot-style thought block.
            if has_event_sink():
                def reasoning_callback(delta: str) -> None:
                    if not reasoning_emitted["started"]:
                        reasoning_emitted["started"] = True
                        emit_runtime_event(
                            "reasoning_started",
                            agent=role,
                            call_id=call_id,
                            output_kind=output_kind,
                            step_id=workflow_step_id,
                        )
                    emit_runtime_event(
                        "reasoning_delta",
                        agent=role,
                        call_id=call_id,
                        output_kind=output_kind,
                        step_id=workflow_step_id,
                        delta=delta,
                    )
                    if on_reasoning:
                        on_reasoning(delta)

                def reasoning_committed() -> None:
                    emit_runtime_event(
                        "reasoning_committed",
                        agent=role,
                        call_id=call_id,
                        output_kind=output_kind,
                        step_id=workflow_step_id,
                    )
            if not self.stream:
                try:
                    result = await self.inner.complete_json(
                        role, payload,
                        on_delta=stream_callback,
                        on_reasoning=reasoning_callback,
                    )
                    self._record_transport(
                        trace_index, call_id, workflow_step_id, observable_payload, result
                    )
                    record_debug_trace(
                        "model_call_completed",
                        mode="json",
                        response_text=getattr(self.inner, "last_response_text", None),
                        reasoning_text=getattr(self.inner, "last_reasoning_text", None),
                        parsed_json=result,
                        timing_details=getattr(self.inner, "last_timing_details", None),
                    )
                    self.model_trace_recorder.succeed(trace_index, result)
                    if reasoning_emitted["started"]:
                        reasoning_committed()
                    emit_runtime_event(
                        "model_output", agent=role, call_id=call_id,
                        output_kind=output_kind,
                        step_id=workflow_step_id, raw_output=_sanitize(result),
                    )
                    return result
                except BaseException as exc:
                    self._attach_failure_diagnostics(exc)
                    record_debug_trace(
                        "model_call_failed",
                        mode="json",
                        error_type=type(exc).__name__,
                        error_reason=getattr(exc, "reason", None),
                        error_details=getattr(self.inner, "last_error_details", None),
                        response_text=getattr(self.inner, "last_response_text", None),
                        reasoning_text=getattr(self.inner, "last_reasoning_text", None),
                    )
                    self.model_trace_recorder.fail(trace_index, exc)
                    emit_runtime_event(
                        "model_failed",
                        agent=role,
                        call_id=call_id,
                        output_kind=output_kind,
                        step_id=workflow_step_id,
                        **self._safe_failure_event_details(exc),
                    )
                    raise
                finally:
                    # The surrounding ``with`` restores the debug call
                    # context even when an event sink raises above.
                    pass
            # Do not hold a process-wide terminal lock while awaiting the
            # provider. Every runtime/model event carries call_id + step_id,
            # which keeps concurrent output attributable.
            terminal_agent_started(role)
            printer = on_delta
            if printer is None and self.terminal_trace.level in {"model", "full"}:
                printer = terminal_delta_printer(role)
            try:
                self.terminal_trace.model_input(role, observable_payload)
                if has_event_sink() and on_delta is not None:
                    printer = stream_callback
                result = await self.inner.complete_json(
                    role, payload,
                    on_delta=printer,
                    on_reasoning=reasoning_callback,
                )
                self.terminal_trace.model_output(role, result)
                self._record_transport(
                    trace_index, call_id, workflow_step_id, observable_payload, result
                )
                record_debug_trace(
                    "model_call_completed",
                    mode="json",
                    response_text=getattr(self.inner, "last_response_text", None),
                    reasoning_text=getattr(self.inner, "last_reasoning_text", None),
                    parsed_json=result,
                    timing_details=getattr(self.inner, "last_timing_details", None),
                )
                self.model_trace_recorder.succeed(trace_index, result)
                if reasoning_emitted["started"]:
                    reasoning_committed()
                emit_runtime_event(
                    "model_output", agent=role, call_id=call_id,
                    output_kind=output_kind,
                    step_id=workflow_step_id, raw_output=_sanitize(result),
                )
                return result
            except BaseException as exc:
                self._attach_failure_diagnostics(exc)
                record_debug_trace(
                    "model_call_failed",
                    mode="json",
                    error_type=type(exc).__name__,
                    error_reason=getattr(exc, "reason", None),
                    error_details=getattr(self.inner, "last_error_details", None),
                    response_text=getattr(self.inner, "last_response_text", None),
                    reasoning_text=getattr(self.inner, "last_reasoning_text", None),
                )
                self.model_trace_recorder.fail(trace_index, exc)
                emit_runtime_event(
                    "model_failed",
                    agent=role,
                    call_id=call_id,
                    output_kind=output_kind,
                    step_id=workflow_step_id,
                    **self._safe_failure_event_details(exc),
                )
                raise
            finally:
                terminal_agent_finished(role)
