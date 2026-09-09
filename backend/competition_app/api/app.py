from __future__ import annotations

from pathlib import Path
import asyncio
import hmac
import json
import re
import threading
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any, Literal
from uuid import uuid4

from fastapi import FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from pydantic import BaseModel, ConfigDict, Field
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from starlette.routing import Match

from competition_app.application.container import ApplicationContainer
from competition_app.services.upload_tasks import (
    UploadTaskStore, finish_upload_work, textbook_fingerprint, textbook_task_response,
)
from competition_app.application.personalized_review_card import (
    ReviewCardRequest,
    WorkflowResumeRequest,
)
from competition_app.runtime.event_stream import (
    bind_recording_sink,
    project_public_business_text,
    public_runtime_event,
    public_workflow_result,
    reset_event_sink,
)
from competition_app.runtime.snapshot import _sanitize
from competition_app.runtime.evolution_rules import assess_signature_candidate
from competition_app.evaluation.d1_ab100_runner import D1AB100Runner
from competition_app.evaluation.d1_ab100_v2_runner import D1AB100V2Runner
from competition_app.evaluation.d1_precheck_runner import D1PrecheckRunner
from competition_app.evaluation.d1_precheck_execution import (
    D1ExecutionDependencies,
    D1PrecheckExecutionService,
)
from competition_app.evaluation.d1_semantic_judge import (
    D1LearnerVisibleSemanticJudge,
)
from competition_app.evaluation.d1_experimental_candidate import (
    D1ExperimentalCandidateCompiler,
)
from competition_app.evaluation.d1_v2_batch import (
    DEFAULT_EXECUTION_PROTOCOL_VERSION,
    D1V2BatchService,
)
from competition_app.evaluation.d1_v2_console import d1_v2_console_html
from competition_app.evaluation.d1_v2_registry import D1V2DatasetRegistryService
from competition_app.evaluation.d1_v5_discovery import (
    D1V5DiscoveryBatchService,
    D1V5DiscoveryDataset,
    D1V5DiscoveryExecutor,
)
from competition_app.evaluation.d1_v5_run_gate import D1V5EvaluationRunGate
from competition_app.evaluation.d1_v5_evolution import (
    D1V5CopilotReviewRequest,
    D1V5EvolutionExecutor,
    D1V5EvolutionSandboxService,
    D1V5ExperimentStartRequest,
)
from competition_app.contracts.review import ReviewAttemptSubmission
from competition_app.contracts.auth import (
    AccountProfileUpdateRequest,
    AuthUser,
    LoginRequest,
    RegisterRequest,
)
from competition_app.contracts.difficulty import parse_difficulty, parse_difficulty_source
from competition_app.contracts.evolution import (
    FeedbackReviewRequest,
    RuleApprovalRequest,
    UserFeedbackRequest,
)
from competition_app.contracts.preference_training import (
    PreferenceDatasetCreate,
    PreferenceSampleCreate,
    PreferenceSampleReview,
    TrainingJobCreate,
)
from competition_app.repositories.auth import UsernameTakenError
from competition_app.services.auth import InvalidCredentialsError
from competition_app.services.feedback_governance import (
    list_feedback_rule_classifications,
)
from competition_app.services.intervention_apply import apply_accepted_intervention
from competition_app.services.plan_review_apply import (
    ReplanStarter,
    apply_accepted_plan_review,
)
from competition_app.services.plan_review_replan import PlanReviewReplanCoordinator
from competition_app.services.learning_path_projection import LearningPathProjectionService
from competition_app.services.profile_readiness import ProfileReadinessService
from competition_app.services.planning_readiness import PlanningReadinessService
from competition_app.services.plan_progress import build_plan_progress
from competition_app.services.learning_monitoring import LearningMonitoringService
from competition_app.services.smart_paper import validate_smart_paper_constraints
from competition_app.services.retrieval_fusion import reciprocal_rank_fusion
from competition_app.tools.question_retrieval import _BM25, _tokens
from competition_app.exam_scope import (
    bind_exam_workspace,
    bind_exam_workspace_context,
    current_exam_workspace,
    reset_exam_workspace,
)
from competition_app.services.workshop import WorkshopKnowledgeService
from competition_app.services.textbook_import import (
    TextbookImportError,
    TextbookTocNotFound,
)
from competition_app.services.user_syllabus import (
    USER_SYLLABUS_NOT_FOUND,
    UserSyllabusError,
)
from competition_app.services.qualification_papers import QualificationPaperRepository
from competition_app.application.workflow_presentation import (
    _format_minutes,
    workflow_result_to_markdown,
)
from competition_app.api.simulated_patient_routes import router as sp_router, init_engine as sp_init_engine
from competition_app.api.treekg_routes import mount_treekg, router as treekg_router


SESSION_COOKIE = "competition_session"
_LEARNING_TARGET_CHANGED_HEADER = "X-Competition-Learning-Target-Changed"

# Persist collaboration events needed by the GitHub-main assistant UI, while
# keeping high-volume model payloads exclusively in the live SSE stream.
_NON_TRACE_EVENT_TYPES = frozenset({
    "answer_started",
    "answer_delta",
    "answer_committed",
    "model_delta",
    "model_input",
    "model_output",
    "model_transport",
    "system_output",
    "business_text_started",
    "business_text_delta",
    "business_text_completed",
    "business_text_failed",
})


class SmartPaperHumanReviewAction(BaseModel):
    action: Literal["approve_publish", "reject"]
    note: str = Field(min_length=3, max_length=2000)


class _AsgiDelegateResponse(Response):
    """Pass one matched request to a sibling ASGI application."""

    def __init__(self, application) -> None:
        super().__init__(content=b"")
        self.application = application

    async def __call__(self, scope, receive, send) -> None:
        await self.application(scope, receive, send)


def _public_answer_chunks(message: str, *, max_chunks: int = 72) -> list[str]:
    """Split an already publishable answer into bounded SSE presentation chunks.

    The workflow and Audit finish before this function is called.  Therefore
    no unreviewed draft, compiler JSON or hidden model delta can cross the
    learner-facing publication gate.
    """

    text = str(message or "")
    if not text:
        return []
    chunk_size = max(24, (len(text) + max_chunks - 1) // max_chunks)
    return [text[index : index + chunk_size] for index in range(0, len(text), chunk_size)]


def _public_agent_output_chunks(message: str, *, max_chunks: int = 160) -> list[str]:
    """Stream complete stage prose in readable, bounded presentation chunks."""

    text = str(message or "")
    if not text:
        return []
    chunk_size = max(3, (len(text) + max_chunks - 1) // max_chunks)
    return [text[index : index + chunk_size] for index in range(0, len(text), chunk_size)]


_ASSISTANT_PROTOCOL_TAG = re.compile(
    r"<<(?:STATUS|EV|REFS|VIDEOS|PLAN|EXEC):"
    r"(?:(?!<<(?:STATUS|EV|REFS|VIDEOS|PLAN|EXEC):)[\s\S])*?"
    r"(?:}>>|]>>)",
)


def _public_streamable_answer(message: str) -> str:
    """Remove UI protocol envelopes before revealing approved answer chunks."""

    text = _ASSISTANT_PROTOCOL_TAG.sub("", str(message or ""))
    text = re.sub(r"<think>[\s\S]*?</think>", "", text)
    text = re.sub(r"<think>[\s\S]*$", "", text)
    return text.strip()
# 进行中任务的实时进度事件（仅 trace 级，跳过 model 高音量事件）。
# 与持久化回执 trace_events 口径一致：SSE 断开后，前端通过
# GET /review-cards/runs/{thread_id} 轮询恢复"多智能体协作回执"。
# 单进程部署下有效（SSE 与轮询请求共享该内存表）。
# 2026-08-16: workflow 已移入线程池独立事件循环执行（LangGraph 的同步
# SQLAlchemy checkpoint 写不再占用 uvicorn 主循环），publish 会从工作
# 线程写入该表，需加锁保护并发读写。
_RUNTIME_PROGRESS: dict[str, dict[str, object]] = {}
_RUNTIME_PROGRESS_LOCK = threading.Lock()
QUALIFICATION_TARGET_CATALOG = (
    Path(__file__).resolve().parents[1]
    / "data"
    / "qualification_targets"
    / "tcm_qualification_targets.v1.json"
)
WORKSHOP_NOTE_IMAGE_ROOT = (
    Path(__file__).resolve().parents[1] / "data" / "workshop_note_images"
)

_PRACTICE_TYPE_ALIASES = {
    "单项选择题": "single_choice",
    "单选题": "single_choice",
    "多项选择题": "multiple_choice",
    "多选题": "multiple_choice",
    "判断题": "true_false",
    "填空题": "fill_blank",
    "问答题": "short_answer",
    "简答题": "short_answer",
    "临床案例问答": "case_quiz",
    "病例分析/实践技能": "case_quiz",
    "临床案例分析": "case_quiz",
}
_OBJECTIVE_PRACTICE_TYPES = {
    "single_choice", "multiple_choice", "true_false", "fill_blank",
}
_CASE_PRACTICE_TYPES = {"short_answer", "case_quiz"}


class FavoriteFolderCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=80)


class NoteFolderCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=80)


class FavoriteCreateRequest(BaseModel):
    folder_id: str = Field(min_length=1, max_length=128)
    resource_type: str = Field(default="question", min_length=1, max_length=32)
    resource_id: str = Field(min_length=1, max_length=255)
    title: str = Field(min_length=1, max_length=500)
    content: dict[str, Any] = Field(default_factory=dict)
    source: str = Field(default="训练工坊", min_length=1, max_length=128)


class WorkshopNoteCreateRequest(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    content: str = Field(min_length=1, max_length=20_000)
    note_type: str = Field(default="心得体会", min_length=1, max_length=64)
    source: str = Field(default="训练工坊", min_length=1, max_length=128)
    resource_type: str | None = Field(default=None, max_length=32)
    resource_id: str | None = Field(default=None, max_length=255)
    context: dict[str, Any] = Field(default_factory=dict)


class WorkshopNoteUpdateRequest(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    content: str = Field(min_length=1, max_length=20_000)


class TextbookPdfAnnotationsUpdateRequest(BaseModel):
    annotations: list[dict[str, Any]] = Field(default_factory=list, max_length=2000)


class TextbookPdfReadingStateUpdateRequest(BaseModel):
    page_number: int = Field(default=1, ge=1)
    zoom: float = Field(default=1.0, ge=0.5, le=4.0)


class TextbookPdfAiRequest(BaseModel):
    mode: Literal["summary", "chat"] = "summary"
    question: str = Field(default="", max_length=4000)
    history: list[dict[str, Any]] = Field(default_factory=list, max_length=16)
    page_span: int = Field(default=0, ge=0, le=5)
    session_id: str | None = Field(default=None, max_length=200)


class EvolutionRuleGenerateRequest(BaseModel):
    signature_id: str = Field(min_length=1, max_length=128)


class TextbookPdfAiSessionCreateRequest(BaseModel):
    title: str = Field(default="新对话", min_length=1, max_length=100)


class TextbookPdfAiSessionRenameRequest(BaseModel):
    title: str = Field(min_length=1, max_length=100)


class TextbookPdfHiddenRequest(BaseModel):
    hidden: bool = False


class StageEvidenceRequest(BaseModel):
    requirement: str = Field(min_length=1, max_length=1000)
    task_id: str = Field(min_length=1, max_length=160)


class D1V2BatchStartRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    purpose: Literal["single_trace", "precheck", "formal"]
    case_ids: list[str] = Field(min_length=1, max_length=100)
    prerequisite_run_id: str | None = Field(default=None, max_length=80)


class D1V2PrecheckDecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: Literal["go", "no_go"]
    note: str = Field(min_length=3, max_length=2000)


class D1V2BlindReviewItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_id: str = Field(min_length=1, max_length=80)
    reviewer_relation: Literal["contradiction", "compatible", "not_applicable"]
    reviewer_name: str = Field(min_length=1, max_length=200)
    review_notes: str = Field(min_length=3, max_length=2000)


class D1V2IndependentReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    attempt_number: Literal[1, 2]
    human_reviewer: Literal[True]
    independent_review: Literal[True]
    gold_labels_not_seen: Literal[True]
    prior_review_decisions_not_seen: Literal[True]
    items: list[D1V2BlindReviewItem] = Field(min_length=10, max_length=10)


class D1V2FormalBlindReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[D1V2BlindReviewItem] = Field(min_length=20, max_length=20)


def _practice_question_type(value: object) -> str:
    text = str(value or "").strip()
    return _PRACTICE_TYPE_ALIASES.get(text, text)


def _practice_mode_matches(question_type: object, mode: str) -> bool:
    normalized = _practice_question_type(question_type)
    if mode == "objective":
        return normalized in _OBJECTIVE_PRACTICE_TYPES
    if mode == "case":
        return normalized in _CASE_PRACTICE_TYPES
    return True


def _sanitize_practice_question_labels(payload: dict) -> dict:
    question = payload.get("question") if isinstance(payload, dict) else None
    if not isinstance(question, dict):
        return payload
    kp_ids = {
        str(value).strip()
        for value in question.get("kp_ids") or []
        if str(value).strip()
    }
    raw_names = question.get("kp_names") or []
    if isinstance(raw_names, dict):
        raw_names = raw_names.values()
    question["kp_names"] = list(dict.fromkeys(
        str(value).strip()
        for value in raw_names
        if str(value).strip() and str(value).strip() not in kp_ids
    ))
    return payload


def _candidate_matches_difficulty_filter(
    candidate: dict,
    *,
    difficulty: int | None = None,
    difficulty_min: int | None = None,
    difficulty_max: int | None = None,
) -> bool:
    """Strict difficulty matching on real labels only.

    A candidate without a real difficulty annotation never matches any
    difficulty filter; no inference or default is applied.
    """
    level = candidate.get("difficulty")
    if not isinstance(level, int):
        return False
    if difficulty is not None and level != difficulty:
        return False
    if difficulty_min is not None and level < difficulty_min:
        return False
    if difficulty_max is not None and level > difficulty_max:
        return False
    return True


def _profile_practice_query(context: dict) -> str:
    profile = context.get("user_profile") if isinstance(context, dict) else {}
    profile = profile if isinstance(profile, dict) else {}
    goals = profile.get("goals") if isinstance(profile.get("goals"), dict) else {}
    for value in (
        profile.get("short_term_goal"),
        profile.get("current_focus"),
        goals.get("short_term_goal"),
        goals.get("goal_name"),
        profile.get("learning_goal"),
    ):
        if str(value or "").strip():
            return str(value).strip()
    return "中医基础"


def _formal_question_payload(question: dict, kp_names: dict[str, str]) -> dict:
    raw_answer = question.get("answer", question.get("题目答案", []))
    if isinstance(raw_answer, list):
        standard_answer = ", ".join(str(value) for value in raw_answer)
    else:
        standard_answer = str(raw_answer or "").strip()
    kp_ids = list(dict.fromkeys(
        str(value).strip()
        for value in question.get("kp_ids") or []
        if str(value).strip()
    ))
    raw_difficulty = question.get("difficulty", question.get("难度"))
    difficulty = parse_difficulty(raw_difficulty)
    difficulty_source = (
        parse_difficulty_source(
            question.get("difficulty_source")
            or question.get("难度来源")
            or "source_metadata"
        )
        if difficulty is not None
        else None
    )
    return {
        "question_id": str(question.get("question_id") or question.get("题目id") or "").strip(),
        "question_type": _practice_question_type(
            question.get("question_type") or question.get("题型")
        ),
        "stem": str(
            question.get("question_content")
            or question.get("题目内容")
            or question.get("stem")
            or ""
        ).strip(),
        "options": question.get("options") or [],
        "raw_answer": raw_answer,
        "standard_answer": standard_answer,
        "analysis": str(
            question.get("explanation")
            or question.get("explaination")
            or question.get("题目解析")
            or question.get("analysis")
            or ""
        ).strip(),
        "kp_ids": kp_ids,
        "kp_names": {kp_id: kp_names.get(kp_id, "") for kp_id in kp_ids},
        "difficulty": difficulty,
        "difficulty_source": difficulty_source,
    }


class ReviewDispatchRequest(BaseModel):
    available_minutes: int = Field(default=15, gt=0, le=24 * 60)


class ReviewPreferenceUpdateRequest(BaseModel):
    daily_capacity: int = Field(ge=1, le=50)


class ReviewSnoozeRequest(BaseModel):
    hours: int = Field(default=24, ge=1, le=720)


class KnowledgeQuestionSearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=2000)
    kp_ids: list[str] = Field(default_factory=list)
    limit: int = Field(default=10, ge=1, le=100)
    scope: str = Field(default="all", pattern="^(all|public|user)$")


class ExamKnowledgeQueryRequest(BaseModel):
    query: str = Field(min_length=1, max_length=2000)
    limit: int = Field(default=10, ge=1, le=50)


class MarkdownImportRequest(BaseModel):
    content: str = Field(min_length=1)


class ExamMarkdownImportRequest(MarkdownImportRequest):
    replace: bool = True


class KnowledgeTextImportRequest(MarkdownImportRequest):
    title: str = Field(default="用户资料", min_length=1, max_length=200)
    apply: bool = True


class ConversationCreateRequest(BaseModel):
    title: str = Field(default="新对话", min_length=1, max_length=120)


class DifficultyTagRequest(BaseModel):
    question_id: str = Field(min_length=1, max_length=120)
    difficulty: int = Field(ge=1, le=5)


class ConversationUpdateRequest(BaseModel):
    title: str = Field(min_length=1, max_length=120)


class KnowledgeCardResolveRequest(BaseModel):
    kp_id: str = Field(min_length=1, max_length=120)
    question_limit: int = Field(default=10, ge=1, le=50)
    source_execution_id: str = Field(default="", max_length=120)


class WorkshopPaperAnswersRequest(BaseModel):
    answers: dict[str, str] = Field(default_factory=dict)


class WorkshopPaperSubmitRequest(BaseModel):
    request_id: str = Field(min_length=1, max_length=120)


class MistakeRedoPaperRequest(BaseModel):
    distribution: dict[str, int] = Field(default_factory=dict)
    answer_mode: str = Field(default="practice", pattern="^(practice|test)$")
    duration_minutes: int | None = Field(default=None, ge=10, le=300)


class QualificationAttemptCreateRequest(BaseModel):
    answer_mode: str = Field(pattern="^(practice|test)$")
    duration_minutes: int | None = Field(default=None, ge=10, le=300)


class QualificationAttemptProgressRequest(BaseModel):
    answers: dict[str, str] = Field(default_factory=dict)
    current_position: int = Field(default=1, ge=1)
    marked_positions: list[int] = Field(default_factory=list)
    paused: bool = False


class QualificationAttemptSubmitRequest(BaseModel):
    request_id: str = Field(min_length=1, max_length=120)


class NotificationStatusRequest(BaseModel):
    status: str = Field(pattern="^(read|dismissed)$")


class NotificationPreferenceRequest(BaseModel):
    in_app_enabled: bool | None = None
    categories: dict[str, bool] = Field(default_factory=dict)
    digest_frequency: str | None = Field(
        default=None, pattern="^(realtime|daily|weekly|paused)$"
    )
    quiet_hours: dict[str, str] = Field(default_factory=dict)


class InterventionFeedbackRequest(BaseModel):
    action: str = Field(pattern="^(accept|postpone|not_relevant|too_easy|too_hard)$")
    reason: str = Field(default="", max_length=1000)


class PlanReviewDecisionRequest(BaseModel):
    decision: str = Field(pattern="^(accept|reject)$")


class LearningAutomationRequest(BaseModel):
    days: int = Field(default=30)
    available_minutes: int = Field(default=15, gt=0, le=24 * 60)
    push_due_review_resource: bool = True


class ResourceRecommendationEventRequest(BaseModel):
    event_type: str = Field(pattern="^(impression|click|complete)$")
    recommendation_view_id: str = Field(default="", max_length=180)
    recommendation_credential: str = Field(default="", max_length=20000)
    resource_id: str = Field(min_length=1, max_length=180)
    resource_type: str = Field(default="", max_length=80)
    kp_ids: list[str] = Field(default_factory=list, max_length=50)


def create_app(container: ApplicationContainer, *, auth_required: bool = True) -> FastAPI:
    backend_handoff = container.backend_handoff_runtime
    textbook_task_store = UploadTaskStore(
        container.runtime_root or container.textbook_import_service.runtime_root.parent
    )
    textbook_upload_tasks: set[asyncio.Task] = set()
    review_push_tasks: set[asyncio.Task] = set()
    # A browser SSE connection is only a subscriber. Keeping strong references
    # here makes the workflow itself independent from page/session navigation.
    workflow_tasks: set[asyncio.Task] = set()
    workflow_tasks_by_thread: dict[str, asyncio.Task] = {}
    workflow_worker_loops: dict[str, tuple[asyncio.AbstractEventLoop, asyncio.Task]] = {}
    workflow_worker_loops_lock = threading.Lock()
    review_push_locks: dict[str, asyncio.Lock] = {}
    review_push_states: dict[str, dict[str, Any]] = {}
    plan_replan_tasks: set[asyncio.Task] = set()
    d1_v5_evolution_sandbox: D1V5EvolutionSandboxService | None = None
    d1_v5_discovery_batch: D1V5DiscoveryBatchService | None = None
    qualification_papers = QualificationPaperRepository(
        Path(__file__).resolve().parents[1] / "data" / "qualification_papers",
        runtime_root=Path(__file__).resolve().parents[1] / "runtime" / "qualification_papers",
    )

    def notify_daily_task_refreshed(learner_id: str) -> None:
        """Emit the daily-task-refreshed station notification once per day.

        Called from the background refresh callback and from request-time
        fallback refreshes. Never raises: notification failures must not
        break the learning-task refresh path.
        """

        if backend_handoff is None:
            return
        summary = ""
        try:
            plans = container.learning_plan_service.get_current(learner_id)
            task = getattr(plans, "learning_task", None)
            if task is not None:
                summary = str(getattr(task, "task_content", "") or "").strip()
                if len(summary) > 120:
                    summary = summary[:120]
        except Exception:
            summary = ""
        try:
            backend_handoff.create_daily_task_refreshed_notification(
                learner_id, task_summary=summary
            )
        except Exception:
            pass

    async def notify_plan_review_lifecycle(
        learner_id: str,
        *,
        review_id: str,
        status: str,
        summary: str = "",
    ) -> None:
        """Best-effort projection of execution state into one notification."""

        if backend_handoff is None:
            return
        try:
            await asyncio.to_thread(
                backend_handoff.update_plan_review_lifecycle_notification,
                learner_id,
                review_id=review_id,
                status=status,
                summary=summary,
            )
        except Exception:
            # Notification storage is a projection. It must never determine
            # whether the authoritative plan coordinator succeeded or failed.
            pass

    def make_plan_replan_starter(
        learner_id: str,
        review: dict[str, Any],
        *,
        execution_id: str,
        already_claimed: bool = False,
    ) -> ReplanStarter:
        """Build a starter that runs the multi-agent replan in the background.

        Reuses the chat workflow entry (review_card_use_case) with the
        review's pre-built user_request so the planner routes the forced
        replan through the same pipeline as a user-initiated one. The HTTP
        request returns immediately; a completion notification is emitted
        when the background replan finishes.
        """
        proposal = review.get("proposal") or {}
        review_id = str(review.get("review_id") or "")

        def starter(_learner_id: str, _review: dict[str, Any]) -> None:
            async def run_replan() -> None:
                execution: dict[str, Any] = {"execution_id": execution_id}
                try:
                    if backend_handoff is not None and not already_claimed:
                        claimed = await asyncio.to_thread(
                            backend_handoff.claim_plan_review_execution,
                            learner_id,
                            review_id,
                            execution_id=execution_id,
                        )
                        claimed_execution = claimed.get("execution") or {}
                        if claimed_execution.get("execution_id") != execution_id:
                            return
                    if backend_handoff is not None:
                        await asyncio.to_thread(
                            backend_handoff.update_plan_review_execution,
                            learner_id,
                            review_id,
                            status="running",
                            execution={"started_at": datetime.now(timezone.utc).isoformat()},
                            execution_id=execution_id,
                        )
                        await notify_plan_review_lifecycle(
                            learner_id,
                            review_id=review_id,
                            status="running",
                        )
                    coordinator = PlanReviewReplanCoordinator(
                        review_card_use_case=container.review_card_use_case,
                        plan_service=container.learning_plan_service,
                    )
                    result = await coordinator.run(learner_id, _review)
                    execution.update(result)
                    if backend_handoff is not None:
                        await asyncio.to_thread(
                            backend_handoff.update_plan_review_execution,
                            learner_id,
                            review_id,
                            status="succeeded",
                            execution=execution,
                            execution_id=execution_id,
                        )
                        await notify_plan_review_lifecycle(
                            learner_id,
                            review_id=review_id,
                            status="succeeded",
                            summary="短期计划和今日任务已按新规划完成更新。",
                        )
                except Exception as exc:
                    execution.update(
                        {
                            "error": str(exc)[:500],
                            "failed_at": datetime.now(timezone.utc).isoformat(),
                        }
                    )
                    if backend_handoff is not None:
                        try:
                            await asyncio.to_thread(
                                backend_handoff.update_plan_review_execution,
                                learner_id,
                                review_id,
                                status="failed",
                                execution=execution,
                                execution_id=execution_id,
                            )
                        except Exception:
                            pass
                        await notify_plan_review_lifecycle(
                            learner_id,
                            review_id=review_id,
                            status="failed",
                            summary=str(exc)[:300],
                        )

            task = asyncio.create_task(run_replan())
            plan_replan_tasks.add(task)
            task.add_done_callback(plan_replan_tasks.discard)

        return starter

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        workshop_dispatcher = None
        daily_task_dispatcher = None
        await asyncio.to_thread(
            container.review_card_use_case.recover_abandoned_active_runs
        )
        if backend_handoff is not None:
            await backend_handoff.startup()
        if container.writeback_executor is not None and backend_handoff is not None:
            async def dispatch_workshop_outbox() -> None:
                while True:
                    try:
                        await asyncio.to_thread(
                            container.writeback_executor.dispatch_pending_workshop_publications,
                            backend_handoff,
                        )
                    except Exception:
                        pass
                    await asyncio.sleep(5)

            workshop_dispatcher = asyncio.create_task(dispatch_workshop_outbox())

        def materialize_refreshed_daily_task(
            learner_id: str, _exam_track_id: str
        ) -> None:
            # refresh_due_tasks has already bound the matching exam workspace
            # in this worker thread, so all follow-up writes stay certificate-scoped.
            container.learning_plan_service.ensure_executable_daily_resources(
                learner_id
            )
            coordinator = container.daily_task_execution_coordinator
            if coordinator is not None:
                coordinator.dispatch_pending(learner_id, 20)
                coordinator.reconcile_parent_status(learner_id)
            notify_daily_task_refreshed(learner_id)

        async def dispatch_due_daily_tasks() -> None:
            # Keep app startup fast, then actively materialize due rolling tasks.
            # Request-time refresh remains as an idempotent recovery fallback.
            await asyncio.sleep(5)
            while True:
                try:
                    await asyncio.to_thread(
                        container.daily_task_refresh_service.refresh_due_tasks,
                        on_refreshed=materialize_refreshed_daily_task,
                    )
                except Exception:
                    pass
                await asyncio.sleep(60)

        daily_task_dispatcher = asyncio.create_task(dispatch_due_daily_tasks())
        try:
            yield
        finally:
            # Uploads may be writing in worker threads. Do not cancel their
            # coroutine and release ownership while the thread still publishes.
            if textbook_upload_tasks:
                await finish_upload_work(asyncio.gather(*list(textbook_upload_tasks), return_exceptions=True))
            if daily_task_dispatcher is not None:
                daily_task_dispatcher.cancel()
                try:
                    await daily_task_dispatcher
                except asyncio.CancelledError:
                    pass
            if workshop_dispatcher is not None:
                workshop_dispatcher.cancel()
                try:
                    await workshop_dispatcher
                except asyncio.CancelledError:
                    pass
            pending_review_pushes = list(review_push_tasks)
            for task in pending_review_pushes:
                task.cancel()
            if pending_review_pushes:
                await asyncio.gather(
                    *pending_review_pushes,
                    return_exceptions=True,
                )
            pending_workflows = list(workflow_tasks)
            for task in pending_workflows:
                task.cancel()
            if pending_workflows:
                await asyncio.gather(*pending_workflows, return_exceptions=True)
            if d1_v5_evolution_sandbox is not None:
                await d1_v5_evolution_sandbox.shutdown()
            if d1_v5_discovery_batch is not None:
                await d1_v5_discovery_batch.shutdown()
            if backend_handoff is not None:
                await backend_handoff.shutdown()

    app = FastAPI(title="Competition App", version="0.1.0", lifespan=lifespan)
    static_root = Path(__file__).parents[1] / "static"
    platform_assets_root = static_root / "platform-assets"
    auth_root = Path(__file__).parents[1] / "auth_static"
    frontend_root = container.frontend_dist_root
    frontend_index = frontend_root / "index.html" if frontend_root else None
    if frontend_root and (frontend_root / "assets").is_dir():
        app.mount(
            "/assets",
            StaticFiles(directory=frontend_root / "assets"),
            name="frontend_assets",
        )
    if frontend_root and (frontend_root / "design-images").is_dir():
        app.mount(
            "/design-images",
            StaticFiles(directory=frontend_root / "design-images"),
            name="frontend_design_images",
        )
    if frontend_root and (frontend_root / "assistant-character").is_dir():
        app.mount(
            "/assistant-character",
            StaticFiles(directory=frontend_root / "assistant-character"),
            name="frontend_assistant_character",
        )
    if frontend_root and (frontend_root / "learning-stage").is_dir():
        app.mount(
            "/learning-stage",
            StaticFiles(directory=frontend_root / "learning-stage"),
            name="frontend_learning_stage",
        )
    if frontend_root and (frontend_root / "textbook-covers").is_dir():
        app.mount(
            "/textbook-covers",
            StaticFiles(directory=frontend_root / "textbook-covers"),
            name="frontend_textbook_covers",
        )
    if frontend_root and (frontend_root / "textbook-status-icons").is_dir():
        app.mount(
            "/textbook-status-icons",
            StaticFiles(directory=frontend_root / "textbook-status-icons"),
            name="frontend_textbook_status_icons",
        )
    if frontend_root and (frontend_root / "acupuncture").is_dir():
        app.mount(
            "/acupuncture",
            StaticFiles(directory=frontend_root / "acupuncture"),
            name="frontend_acupuncture",
        )
    if frontend_root and (frontend_root / "knowledge-graph").is_dir():
        app.mount(
            "/knowledge-graph",
            StaticFiles(directory=frontend_root / "knowledge-graph", html=True),
            name="frontend_knowledge_graph",
        )
    if frontend_root and (frontend_root / "blender.yibiaozhu.glb").is_file():
        app.mount(
            "/acupuncture-models",
            StaticFiles(directory=frontend_root),
            name="frontend_acupuncture_models",
        )
    app.mount(
        "/platform-assets",
        StaticFiles(directory=platform_assets_root),
        name="platform_assets",
    )
    app.mount("/auth", StaticFiles(directory=auth_root, html=True), name="auth")

    # ── 模拟病患模块 ────────────────────────────────────
    try:
        sp_init_engine(
            llm_timeout_seconds=120.0,
            activity_projection=(
                backend_handoff.record_legacy_simulated_patient_activity
                if backend_handoff is not None
                else None
            ),
        )
        app.include_router(sp_router)
    except Exception:
        import logging
        _logger = logging.getLogger("competition_app.simulated_patient")
        _logger.warning("模拟病患模块初始化失败", exc_info=True)

    # ── TreeKG 知识图谱 viewer（新版带左侧目录） ────────
    app.include_router(treekg_router)
    mount_treekg(app)

    # 请求边界：考试工作区推断结果按用户 TTL 缓存。该推断在未命中时
    # 需要查询 handoff 数据库或计划表（20~200ms），而 scope 变更频率极低
    # （规划/切考才会变），30s 内复用避免每个请求重复付费。
    exam_scope_cache: dict[str, tuple[float, dict[str, Any]]] = {}
    exam_scope_cache_lock = threading.Lock()
    EXAM_SCOPE_CACHE_TTL_SECONDS = 30.0

    async def resolve_exam_scope(learner_id: str) -> dict[str, Any]:
        cached = None
        with exam_scope_cache_lock:
            hit = exam_scope_cache.get(learner_id)
            if hit is not None:
                cached_at, cached_value = hit
                if time.monotonic() - cached_at < EXAM_SCOPE_CACHE_TTL_SECONDS:
                    cached = cached_value
        if cached is not None:
            return cached
        active_exam: dict[str, Any] = {}
        if (
            backend_handoff is not None
            and hasattr(backend_handoff, "load_active_exam_scope")
        ):
            try:
                active_exam = await asyncio.to_thread(
                    backend_handoff.load_active_exam_scope,
                    learner_id,
                )
            except Exception:
                active_exam = {}
        if not active_exam:
            # Fallback for handoffs without an active-exam lookup: infer the
            # learner's most recently used exam scope from the plan store so
            # pure read APIs resolve the same scoped tables that planning
            # workflows publish into.
            plan_repository = getattr(
                container, "learning_plan_service", None
            )
            plan_repository = (
                plan_repository.plan_repository
                if plan_repository is not None
                else None
            )
            infer = getattr(plan_repository, "infer_active_scope", None)
            if callable(infer):
                try:
                    scope = await asyncio.to_thread(
                        infer, learner_id
                    )
                except Exception:
                    scope = None
                if scope:
                    active_exam = {"exam_track_id": scope}
        with exam_scope_cache_lock:
            exam_scope_cache[learner_id] = (time.monotonic(), active_exam)
        return active_exam

    @app.middleware("http")
    async def authentication_boundary(request: Request, call_next):
        raw_token = request.cookies.get(SESSION_COOKIE)
        current_user = container.authentication_service.authenticate(raw_token)
        request.state.current_user = current_user
        if current_user is not None:
            active_exam = await resolve_exam_scope(current_user.user_id)
            bind_exam_workspace(current_user.user_id, active_exam)
        path = request.url.path
        retired_ui_paths = {
            "/chat",
            "/chat/",
            "/chat/chat.css",
            "/chat/chat.js",
            "/chat/plan_scope.js",
            "/demo",
            "/demo/",
            "/demo/app.js",
            "/demo/styles.css",
            "/demo-app",
        }
        if path in retired_ui_paths:
            return Response(status_code=404)
        # Mounted business routes share the main cookie identity. Their internal
        # dependency maps request.state.current_user to a domain-local user row.
        spa_page_paths = (
            "/practice",
            "/learning-path",
            "/assistant",
            "/knowledge",
            "/personalization",
            "/settings",
            "/resources",
            "/dashboard",
        )
        d1_v5_sandbox_path = path.startswith(
            "/api/v1/internal-eval/d1-v5/evolution/"
        )
        supplied_evaluation_token = str(
            request.headers.get("X-Accountability-Evaluation-Token") or ""
        )
        expected_evaluation_token = str(
            container.accountability_evaluation_token or ""
        )
        valid_d1_v5_sandbox_token = bool(
            d1_v5_sandbox_path
            and expected_evaluation_token
            and hmac.compare_digest(
                supplied_evaluation_token,
                expected_evaluation_token,
            )
        )
        public_path = (
            path == "/"
            or path == "/favicon.ico"
            or path == "/favicon.svg"
            or path == "/hero_word.txt"
            or path == "/health"
            or path == "/openapi.json"
            or path.startswith(
                (
                    "/assets/",
                    "/design-images/",
                    "/assistant-character/",
                    "/learning-stage/",
                    "/textbook-covers/",
                    "/textbook-status-icons/",
                    "/acupuncture/",
                    "/acupuncture-models/",
                    "/knowledge-graph/",
                    "/platform-assets/",
                )
            )
            or path.startswith(("/auth", "/docs", "/redoc"))
            or path.startswith("/api/v1/auth/")
            # This exact evaluation-only namespace uses its own high-entropy
            # header credential and never creates a production auth session.
            or valid_d1_v5_sandbox_token
            # SPA 页面路径：未登录也返回 index.html，由前端引导登录
            or path.startswith(spa_page_paths)
        )
        if auth_required and current_user is None and not public_path:
            return JSONResponse(
                status_code=401,
                content={"detail": "请先登录后继续"},
            )
        response = await call_next(request)
        profile_changed_header = "X-Competition-Learner-Profile-Changed"
        if response.headers.get(profile_changed_header) == "1":
            if response.status_code < 400 and current_user is not None and backend_handoff is not None:
                backend_handoff.invalidate_learning_context(current_user.user_id)
            del response.headers[profile_changed_header]
        learning_target_changed = (
            response.status_code < 400
            and current_user is not None
            and backend_handoff is not None
            and response.headers.get(_LEARNING_TARGET_CHANGED_HEADER) == "1"
        )
        if _LEARNING_TARGET_CHANGED_HEADER in response.headers:
            del response.headers[_LEARNING_TARGET_CHANGED_HEADER]
        if learning_target_changed:
            # The mounted business app has committed a new active target.
            # Invalidate both server-owned projections so the next request is
            # bound to that target rather than the pre-mutation workspace.
            with exam_scope_cache_lock:
                exam_scope_cache.pop(current_user.user_id, None)
            backend_handoff.invalidate_learning_context(current_user.user_id)
        completed_question_submission = (
            path == "/training/practice/grade"
            or path == "/api/training/practice/grade"
            or path == "/api/v1/workshop/practice/grade"
            or (
                path.startswith("/training/workspace/papers/")
                and path.endswith("/submit")
            )
            or (
                path.startswith("/api/v1/workshop/papers/")
                and path.endswith("/submit")
            )
        )
        if (
            completed_question_submission
            and response.status_code < 400
            and current_user is not None
            and backend_handoff is not None
        ):
            try:
                # Release practice claim so next request gets a fresh question
                backend_handoff.release_practice_claim(current_user.user_id)

                # 答题刚提交，行为数据已变化：让下一个 load_learning_context
                # 重建（跳过 TTL 缓存），保证复习队列拿到最新作答。
                backend_handoff.invalidate_learning_context(current_user.user_id)

                behavior = await asyncio.to_thread(
                    backend_handoff.load_learning_context, current_user.user_id
                )
                container.review_service.ingest_question_attempts(
                    learner_id=current_user.user_id,
                    attempts=behavior.get("question_attempt", []),
                )
                review_queue = container.review_service.get_queue(
                    current_user.user_id,
                    limit=200,
                )
                await asyncio.to_thread(
                    backend_handoff.load_learning_insights,
                    current_user.user_id,
                    days=30,
                    plan_context={},
                    run_automation=True,
                    review_projection=canonical_review_projection(review_queue),
                )
            except Exception:
                # The authoritative answer has already been committed. A later
                # context/queue read retries this idempotent projection. Due
                # review resources are opened as practice tasks by the learner;
                # this background path must not create assistant conversations.
                pass
        if path.startswith("/auth"):
            response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
        return response

    @app.middleware("http")
    async def disable_auth_cache(request: Request, call_next):
        response = await call_next(request)
        path = request.url.path
        if (
            path.startswith("/auth")
            or path == "/internal-eval/d1-v2"
            or path.startswith("/api/v1/internal-eval/d1-v2")
            or path == "/api/v1/evolution/evaluation/d1-v2/overview"
        ):
            response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
            response.headers["Pragma"] = "no-cache"
        return response

    def current_user(request: Request) -> AuthUser | None:
        user = getattr(request.state, "current_user", None)
        if auth_required and user is None:
            raise HTTPException(status_code=401, detail="请先登录后继续")
        return user

    def require_owner(request: Request, learner_id: str) -> AuthUser | None:
        user = current_user(request)
        if user is not None and learner_id != user.user_id:
            raise HTTPException(status_code=403, detail="无权访问其他用户的数据")
        return user

    def require_admin(request: Request) -> AuthUser:
        user = current_user(request)
        if user is None or user.role != "admin":
            raise HTTPException(status_code=403, detail="仅管理员可执行此操作")
        return user

    async def canonical_review_queue(learner_id: str, *, limit: int = 200):
        """Refresh and read the single review queue used by every user-facing metric."""

        if backend_handoff is not None and hasattr(
            backend_handoff, "load_learning_context"
        ):
            behavior = await asyncio.to_thread(
                backend_handoff.load_learning_context, learner_id
            )
            container.review_service.ingest_question_attempts(
                learner_id=learner_id,
                attempts=behavior.get("question_attempt", []),
            )
        return container.review_service.get_queue(learner_id, limit=limit)

    def canonical_review_projection(queue: Any) -> dict[str, Any]:
        """Serialize the authoritative queue counters for backend automation."""

        calculated_at = getattr(queue, "calculated_at", None)
        return {
            "source": "canonical_review_memory",
            "total_count": len(getattr(queue, "entries", []) or []),
            "due_count": int(getattr(queue, "due_count", 0) or 0),
            "active_task_count": int(
                getattr(queue, "active_task_count", 0) or 0
            ),
            "calculated_at": (
                calculated_at.isoformat()
                if hasattr(calculated_at, "isoformat")
                else None
            ),
        }

    async def materialize_due_review_resource(
        learner_id: str,
        *,
        available_minutes: int,
    ) -> dict[str, Any]:
        """Publish one missing due-review resource without duplicating bound tasks."""

        lock = review_push_locks.setdefault(learner_id, asyncio.Lock())
        async with lock:
            queue = container.review_service.get_queue(learner_id, limit=200)
            candidates = [
                item
                for item in queue.entries
                if item.is_due and (item.task is None or item.resource is None)
            ]
            kp_names: dict[str, str] = {}
            if (
                candidates
                and backend_handoff is not None
                and hasattr(backend_handoff, "load_review_dashboard")
            ):
                try:
                    dashboard = await asyncio.to_thread(
                        backend_handoff.load_review_dashboard,
                        learner_id,
                        history_limit=1,
                    )
                    for collection in ("mastery", "review_states"):
                        for item in dashboard.get(collection) or []:
                            kp_id = str(item.get("kp_id") or "").strip()
                            kp_name = str(item.get("kp_name") or "").strip()
                            if kp_id and kp_name and kp_name != kp_id:
                                kp_names[kp_id] = kp_name
                except Exception:
                    kp_names = {}

            def dispatch_topic(candidate: Any) -> str:
                unit = candidate.memory_unit
                topic = str(unit.prompt_abstract or "").strip()
                for suffix in ("个性化复习卡", "个性化练习", "复习卡片", "复习卡"):
                    if topic.endswith(suffix):
                        topic = topic[:-len(suffix)].strip()
                if topic in {
                    "",
                    "知识点名称待补充",
                    "待补充知识点",
                    "知识点待确认",
                }:
                    topic = ""
                if not topic:
                    topic = kp_names.get(str(unit.kp_id), "")
                if not topic and re.search(r"[\u4e00-\u9fff]{2,}", str(unit.kp_id)):
                    topic = str(unit.kp_id).strip()
                return topic

            entry = next(
                (item for item in candidates if dispatch_topic(item)),
                None,
            )
            if entry is None:
                return {
                    "status": "empty",
                    "message": (
                        "当前没有等待资源的到期复习知识点。"
                        if not candidates
                        else "到期知识点缺少可解析名称，已跳过自动资源生成。"
                    ),
                }
            unit = entry.memory_unit
            topic = dispatch_topic(entry)
            result = await container.review_card_use_case.execute(
                ReviewCardRequest(
                    learner_id=learner_id,
                    user_request=(
                        "请为以下已到期知识点生成一张可立即学习的复习卡："
                        f"{topic}"
                    ),
                    available_minutes=available_minutes,
                    system_operation="due_review_dispatch",
                    user_knowledge_state=[
                        {
                            "user_id": learner_id,
                            "kp_id": unit.kp_id,
                            "knowledge_mastery": unit.mastery_score / 100,
                            "answer_accuracy": unit.mastery_score / 100,
                            "forgetting_coefficient": unit.lambda_per_day,
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
            if getattr(result, "status", None) == "interrupted":
                raise HTTPException(
                    status_code=409,
                    detail="到期资源生成意外进入追问状态",
                )
            payload = (
                result.model_dump(mode="json")
                if hasattr(result, "model_dump")
                else result
            )
            return {
                "status": "pushed",
                "kp_id": unit.kp_id,
                "review_task_id": entry.task.review_task_id if entry.task else None,
                "result": payload,
            }

    def schedule_due_review_resource(
        learner_id: str,
        *,
        available_minutes: int,
    ) -> None:
        """Start a bounded push after the current HTTP response can proceed."""

        existing_state = review_push_states.get(learner_id) or {}
        if existing_state.get("status") == "running":
            return
        review_push_states[learner_id] = {
            "status": "running",
            "started_at": datetime.now(timezone.utc).isoformat(),
        }
        task = asyncio.create_task(
            materialize_due_review_resource(
                learner_id,
                available_minutes=available_minutes,
            )
        )
        review_push_tasks.add(task)

        def consume_result(done: asyncio.Task) -> None:
            review_push_tasks.discard(done)
            if done.cancelled():
                review_push_states[learner_id] = {
                    **review_push_states.get(learner_id, {}),
                    "status": "cancelled",
                    "finished_at": datetime.now(timezone.utc).isoformat(),
                }
                return
            try:
                result = done.result()
                review_push_states[learner_id] = {
                    **review_push_states.get(learner_id, {}),
                    "status": str(result.get("status") or "completed"),
                    "result": result,
                    "finished_at": datetime.now(timezone.utc).isoformat(),
                }
            except Exception as exc:
                review_push_states[learner_id] = {
                    **review_push_states.get(learner_id, {}),
                    "status": "failed",
                    "error_type": type(exc).__name__,
                    "message": str(exc)[:1000],
                    "finished_at": datetime.now(timezone.utc).isoformat(),
                }

        task.add_done_callback(consume_result)

    @app.get("/api/v1/qualification-papers/catalog")
    async def qualification_paper_catalog(
        request: Request,
        exam_id: str = "",
        year: str = "",
        paper_type: str = "",
    ) -> dict:
        current_user(request)
        return qualification_papers.list_catalog(exam_id=exam_id, year=year, paper_type=paper_type)

    @app.post("/api/v1/workshop/papers/mistake-redo", status_code=501)
    async def create_mistake_redo_paper(
        payload: MistakeRedoPaperRequest, request: Request
    ) -> dict:
        current_user(request)
        del payload
        raise HTTPException(status_code=501, detail="错题集重做组卷接口已预留")

    @app.post("/api/v1/qualification-papers/{template_id}/attempts")
    async def create_qualification_attempt(
        template_id: str, payload: QualificationAttemptCreateRequest, request: Request
    ) -> dict:
        user = current_user(request)
        if user is None:
            raise HTTPException(status_code=401, detail="请先登录后继续")
        try:
            return qualification_papers.create_attempt(
                user.user_id,
                template_id,
                answer_mode=payload.answer_mode,
                duration_minutes=payload.duration_minutes,
            )
        except (KeyError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.get("/api/v1/qualification-paper-attempts")
    async def list_qualification_attempts(request: Request, offset: int = Query(default=0, ge=0), limit: int = Query(default=50, ge=1, le=200)) -> dict:
        user = current_user(request)
        if user is None: raise HTTPException(status_code=401, detail="请先登录后继续")
        return qualification_papers.list_attempts(user.user_id, offset=offset, limit=limit)

    @app.get("/api/v1/qualification-paper-attempts/{attempt_id}")
    async def get_qualification_attempt(attempt_id: str, request: Request) -> dict:
        user = current_user(request)
        if user is None:
            raise HTTPException(status_code=401, detail="请先登录后继续")
        try:
            return qualification_papers.get_attempt(user.user_id, attempt_id)
        except ValueError as exc:
            raise HTTPException(status_code=410, detail=str(exc)) from exc
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.put("/api/v1/qualification-paper-attempts/{attempt_id}/progress")
    async def save_qualification_attempt_progress(
        attempt_id: str, payload: QualificationAttemptProgressRequest, request: Request
    ) -> dict:
        user = current_user(request)
        if user is None:
            raise HTTPException(status_code=401, detail="请先登录后继续")
        try:
            return qualification_papers.save_progress(
                user.user_id,
                attempt_id,
                answers=payload.answers,
                current_position=payload.current_position,
                marked_positions=payload.marked_positions,
                paused=payload.paused,
            )
        except (KeyError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/api/v1/qualification-paper-attempts/{attempt_id}/submit")
    async def submit_qualification_attempt(
        attempt_id: str, payload: QualificationAttemptSubmitRequest, request: Request
    ) -> dict:
        user = current_user(request)
        if user is None:
            raise HTTPException(status_code=401, detail="请先登录后继续")
        try:
            result = qualification_papers.submit_attempt(user.user_id, attempt_id, payload.request_id)
            if backend_handoff is not None and hasattr(
                backend_handoff, "record_qualification_paper_outcomes"
            ):
                try:
                    result["learning_writeback"] = await asyncio.to_thread(
                        backend_handoff.record_qualification_paper_outcomes,
                        user.user_id,
                        attempt_id=attempt_id,
                        outcomes=qualification_papers.submission_outcomes(user.user_id, attempt_id),
                    )
                except Exception:
                    result["learning_writeback"] = {"status": "retry_pending"}
            return result
        except (KeyError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.get("/api/v1/qualification-paper-attempts/{attempt_id}/items/{question_id}/explanation")
    async def qualification_attempt_explanation(attempt_id: str, question_id: str, request: Request) -> dict:
        user = current_user(request)
        if user is None:
            raise HTTPException(status_code=401, detail="请先登录后继续")
        try:
            return qualification_papers.get_explanation(user.user_id, attempt_id, question_id)
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    def knowledge_backend():
        backend = container.knowledge_backend
        if backend is None:
            raise HTTPException(status_code=503, detail="正式知识库后端仅在 live 模式启用")
        return backend

    def knowledge_owner(request: Request) -> str:
        user = current_user(request)
        return user.user_id if user is not None else "anonymous"

    def knowledge_error(exc: Exception) -> HTTPException:
        from competition_app.services.personal_knowledge_storage import PersonalKnowledgeBusy
        if isinstance(exc, PersonalKnowledgeBusy):
            return HTTPException(status_code=409, detail=str(exc))
        if isinstance(exc, KeyError):
            return HTTPException(status_code=404, detail=str(exc).strip("'"))
        if isinstance(exc, (ValueError, LookupError)):
            return HTTPException(status_code=422, detail=str(exc))
        return HTTPException(status_code=500, detail=str(exc))

    def current_plan_context(learner_id: str) -> dict[str, dict]:
        state = container.learning_plan_service.get_current(learner_id)
        if state is None:
            return {}
        context = {
            "long_term_plan": (
                state.long_term_plan.model_dump(mode="json")
                if state.long_term_plan is not None else {}
            ),
            "short_term_plan": (
                state.short_term_plan.model_dump(mode="json")
                if state.short_term_plan is not None else {}
            ),
            "learning_task": (
                state.learning_task.model_dump(mode="json")
                if state.learning_task is not None else {}
            ),
        }
        return {
            key: value
            for key, value in context.items()
            if value
        }

    def coordination_payload(
        execution_id: str,
        state: dict,
    ) -> dict:
        coordination = state.get("coordination")
        coordination = coordination if isinstance(coordination, dict) else {}
        communication = coordination.get("communication_trace")
        communication = communication if isinstance(communication, list) else []
        repairs = coordination.get("repair_trace")
        repairs = repairs if isinstance(repairs, list) else []
        safe_communication = [
            {
                key: item.get(key)
                for key in (
                    "schema_version",
                    "handoff_id",
                    "step_id",
                    "target_agent",
                    "fact_count",
                    "evidence_count",
                    "blocking_field_count",
                    "omitted_categories",
                    "status",
                    "created_at",
                )
                if key in item
            }
            for item in communication
            if isinstance(item, dict)
        ]
        safe_repairs = [
            {
                key: item.get(key)
                for key in (
                    "repair_id",
                    "trigger_step_id",
                    "issue_types",
                    "issue_ids",
                    "location_labels",
                    "rerun_step_ids",
                    "preserved_step_ids",
                    "round",
                    "status",
                    "final_audit_decision",
                    "created_at",
                )
                if key in item
            }
            for item in repairs
            if isinstance(item, dict)
        ]
        result = state.get("result")
        result = result if isinstance(result, dict) else {}
        audit = result.get("audit")
        audit = audit if isinstance(audit, dict) else {}
        final_audit_decision = next(
            (
                str(item.get("final_audit_decision"))
                for item in reversed(safe_repairs)
                if item.get("final_audit_decision")
            ),
            str(
                state.get("final_audit_decision")
                or audit.get("decision")
                or ""
            )
            or None,
        )
        return {
            "schema_version": "1.0",
            "execution_id": execution_id,
            "communication_summary": {
                "total": len(safe_communication),
                "items": safe_communication,
            },
            "repair_summary": {
                "total": len(safe_repairs),
                "items": safe_repairs,
            },
            "final_audit_decision": final_audit_decision,
        }

    def scoped_review_request(
        request: ReviewCardRequest, user: AuthUser | None
    ) -> ReviewCardRequest:
        if user is None:
            return request
        owner = user.user_id

        def scope_mapping(value: dict, *, always: bool = False) -> dict:
            if not value and not always:
                return value
            return {**value, "user_id": owner}

        def scope_learner_mapping(value: dict) -> dict:
            if not value:
                return value
            scoped = dict(value)
            if "learner_id" in scoped:
                scoped["learner_id"] = owner
            if "user_id" in scoped:
                scoped["user_id"] = owner
            return scoped

        return request.model_copy(
            update={
                "learner_id": owner,
                "user_profile": scope_mapping(request.user_profile, always=True),
                "user_knowledge_state": [
                    scope_mapping(item, always=True)
                    for item in request.user_knowledge_state
                ],
                "question_attempt": [
                    scope_learner_mapping(item) for item in request.question_attempt
                ],
                "question_learning_stats": [
                    scope_learner_mapping(item)
                    for item in request.question_learning_stats
                ],
                "long_term_plan": scope_learner_mapping(request.long_term_plan),
                "short_term_plan": scope_learner_mapping(request.short_term_plan),
                "learning_task": scope_learner_mapping(request.learning_task),
            }
        )

    def require_run_owner(request: Request, thread_id: str) -> dict:
        state = container.review_card_use_case.get_run_state(thread_id)
        if state is None:
            raise HTTPException(status_code=404, detail="LangGraph 会话不存在或已过期")
        user = current_user(request)
        if user is not None and state.get("learner_id") != user.user_id:
            raise HTTPException(status_code=404, detail="LangGraph 会话不存在或已过期")
        return state

    def safe_failure_message(error_code: object) -> str:
        messages = {
            "knowledge_timeout": "知识检索超时，已保存当前会话，请稍后重试。",
            "knowledge_step_failed": "知识检索未能完成，请稍后重试。",
            "paper_blueprint_timeout": "试卷蓝图生成超时，请稍后重试。",
            "model_timeout": "模型调用超时，请稍后重试。",
            "model_invalid_output": "模型输出未能通过解析，请重新生成。",
            "workflow_timeout": "本次处理超时，已保存当前会话，请稍后重试。",
            "plan_compilation_failed": "学习规划未能通过结构化校验，请稍后重试。",
            "audit_step_failed": "内容审核未能完成，请稍后重试。",
            "daily_task_publication_failed": "今日任务发布未能完成，请稍后重试。",
            "paper_generation_failed": "试卷生成未能完成，请稍后重试。",
            "persistence_failed": "结果保存失败，请稍后重试。",
            "model_empty_response": "模型暂时没有返回内容，请再试一次。",
            "model_transport_error": "模型连接暂时不稳定，请稍后重试。",
        }
        return messages.get(
            str(error_code or ""),
            "这次处理没有成功完成，请稍后重试。",
        )

    def safe_run_status(state: dict[str, Any]) -> dict[str, Any]:
        result = state.get("result")
        result = public_workflow_result(result)
        payload = {
            "status": state.get("status"),
            "thread_id": state.get("thread_id"),
            "execution_id": state.get("execution_id"),
            "task_type": state.get("task_type")
            or (result or {}).get("task_type"),
            "result": result,
            "message": (
                safe_failure_message(state.get("error_code"))
                if state.get("status") == "failed"
                else None
            ),
            "error_code": state.get("error_code"),
            "error_type": state.get("error_type"),
            "retryable": bool(state.get("retryable", False)),
            "failed_step": state.get("failed_step"),
            "interrupt": _sanitize(state.get("interrupt"))
            if isinstance(state.get("interrupt"), dict)
            else None,
        }
        if state.get("learner_id"):
            payload["learner_id"] = state["learner_id"]
        return payload

    def require_available_thread(request: Request, thread_id: str | None) -> None:
        if not thread_id:
            return
        state = container.review_card_use_case.get_run_state(thread_id)
        if state is None:
            return
        user = current_user(request)
        if user is not None and state.get("learner_id") != user.user_id:
            raise HTTPException(status_code=409, detail="该会话标识已被占用")

    def set_session_cookie(response: JSONResponse, raw_token: str, expires_at) -> None:
        max_age = max(
            1,
            int(
                (
                    expires_at
                    - datetime.now(timezone.utc)
                ).total_seconds()
            ),
        )
        response.set_cookie(
            SESSION_COOKIE,
            raw_token,
            max_age=max_age,
            httponly=True,
            secure=container.auth_cookie_secure,
            samesite="lax",
            path="/",
        )

    @app.post("/api/v1/auth/register", status_code=201)
    async def register(request: RegisterRequest):
        try:
            result, raw_token = container.authentication_service.register(request)
        except UsernameTakenError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        response = JSONResponse(status_code=201, content=result.model_dump(mode="json"))
        set_session_cookie(response, raw_token, result.expires_at)
        if backend_handoff is not None:
            try:
                await asyncio.to_thread(backend_handoff.record_login_activity, result.user.user_id)
            except Exception:
                pass
        return response

    @app.post("/api/v1/auth/login")
    async def login(request: LoginRequest):
        try:
            result, raw_token = container.authentication_service.login(request)
        except InvalidCredentialsError as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from exc
        response = JSONResponse(content=result.model_dump(mode="json"))
        set_session_cookie(response, raw_token, result.expires_at)
        if backend_handoff is not None:
            try:
                await asyncio.to_thread(backend_handoff.record_login_activity, result.user.user_id)
            except Exception:
                pass
        return response

    @app.post("/api/v1/auth/logout")
    async def logout(request: Request):
        container.authentication_service.logout(request.cookies.get(SESSION_COOKIE))
        response = JSONResponse(content={"status": "logged_out"})
        response.delete_cookie(SESSION_COOKIE, path="/", samesite="lax")
        return response

    @app.get("/api/v1/audit-failure-cases/summary")
    async def audit_failure_cases_summary():
        """失败案例库统计：问题类型分布、修复成功率、升级人工率、放行率。"""
        repository = container.failure_case_repository
        return {
            "total_issue_types": repository.count_by_issue_type(),
            "repair_success_rate": repository.repair_success_rate(),
            "escalated_rate": repository.escalated_rate(),
            "released_rate": repository.released_rate(),
            "recent_cases": [
                {
                    "case_id": case.case_id,
                    "execution_id": case.execution_id,
                    "decision": case.decision,
                    "released": case.released,
                    "issue_types": case.issue_types,
                    "repair": case.repair_json,
                    "created_at": (
                        case.created_at.isoformat()
                        if case.created_at is not None
                        else None
                    ),
                }
                for case in repository.list_recent(limit=20)
            ],
        }

    @app.post("/api/v1/evolution/feedback")
    async def submit_evolution_feedback(request: Request, payload: UserFeedbackRequest):
        user = current_user(request)
        if user is None:
            raise HTTPException(status_code=401, detail="请先登录后继续")
        item = container.feedback_governance_service.submit_user_feedback(
            learner_id=user.user_id,
            request=payload,
        )
        return item.model_dump(mode="json")

    @app.get("/api/v1/evolution/status")
    async def evolution_status(request: Request):
        require_admin(request)
        return {
            "governance_enabled": container.evolution_rule_service.enabled,
            "runtime_rules_enabled": bool(
                getattr(
                    getattr(container.review_card_use_case.orchestrator, "evolution_rule_registry", None),
                    "enabled",
                    False,
                )
            ),
            "preference_training_enabled": container.preference_training_service.enabled,
            "trl_dpo_enabled": container.preference_training_service.allow_trl_dpo,
            "safety_boundary": "closed_templates_admin_approval_fail_open",
        }

    @app.get("/api/v1/evolution/feedback/admin")
    async def list_evolution_feedback(
        request: Request,
        status: str | None = Query(default=None),
        limit: int = Query(default=100, ge=1, le=500),
    ):
        require_admin(request)
        return [
            item.model_dump(mode="json")
            for item in container.evolution_repository.list_feedback(
                status=status, limit=limit
            )
        ]

    @app.get("/api/v1/evolution/feedback/admin/classifications")
    async def list_evolution_feedback_classifications(request: Request):
        require_admin(request)
        return list_feedback_rule_classifications()

    @app.patch("/api/v1/evolution/feedback/admin/{feedback_id}")
    async def review_evolution_feedback(
        feedback_id: str,
        request: Request,
        payload: FeedbackReviewRequest,
    ):
        user = require_admin(request)
        try:
            item = container.feedback_governance_service.review(
                feedback_id,
                payload,
                reviewer_id=user.user_id,
            )
            signature = container.failure_signature_service.ingest(item)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="反馈不存在") from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {
            "feedback": item.model_dump(mode="json"),
            "signature": signature.model_dump(mode="json") if signature else None,
        }

    @app.get("/api/v1/evolution/signatures")
    async def list_evolution_signatures(
        request: Request,
        ready_only: bool = Query(default=False),
        limit: int = Query(default=100, ge=1, le=500),
    ):
        require_admin(request)
        result = []
        for item in container.evolution_repository.list_signatures(
            ready_only=ready_only, limit=limit
        ):
            gate = assess_signature_candidate(item)
            result.append({
                **item.model_dump(mode="json"),
                "threshold_ready": item.candidate_ready,
                "effective_candidate_ready": (
                    item.candidate_ready and gate.applicable
                ),
                "candidate_gate": gate.as_dict(),
            })
        return result

    @app.post("/api/v1/evolution/rules/generate")
    async def generate_evolution_rule(
        request: Request,
        payload: EvolutionRuleGenerateRequest,
    ):
        user = require_admin(request)
        signature = container.evolution_repository.get_signature(payload.signature_id)
        if signature is None:
            raise HTTPException(status_code=404, detail="失败签名不存在")
        if not signature.candidate_ready:
            raise HTTPException(status_code=409, detail="失败签名尚未达到候选阈值")
        candidate_gate = assess_signature_candidate(signature)
        if not candidate_gate.applicable:
            raise HTTPException(
                status_code=409,
                detail=(
                    "当前签名没有可用的封闭规则模板："
                    + ",".join(candidate_gate.reason_codes)
                ),
            )
        source_ids = set(signature.source_case_ids)
        summaries = [
            item.summary
            for item in container.evolution_repository.list_feedback(limit=500)
            if (item.source_case_id or item.feedback_id) in source_ids
        ]
        agent_context = {
            "trace_id": f"EVOTRACE_{uuid4().hex}",
            "request_id": f"EVOREQ_{uuid4().hex}",
            "workflow_task_id": f"EVOTASK_{uuid4().hex}",
            "learner_id": user.user_id,
            "user_request": "管理员请求分析已审核的重复失败签名",
            "original_user_request": "管理员请求分析已审核的重复失败签名",
            "messages": [],
            "now": datetime.now(timezone.utc),
        }
        try:
            analysis = await container.evolution_agent.analyze(
                agent_context,
                signature=signature,
                source_summaries=summaries,
            )
            contract = await container.evolution_rule_compiler_agent.compile(
                agent_context,
                signature=signature,
                analysis=analysis,
            )
            rule = container.evolution_rule_service.create_from_compiled(
                contract,
                analysis=analysis,
            )
        except (RuntimeError, ValueError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return rule.model_dump(mode="json")

    @app.get("/api/v1/evolution/rules")
    async def list_evolution_rules(
        request: Request,
        status: str | None = Query(default=None),
        limit: int = Query(default=100, ge=1, le=500),
    ):
        require_admin(request)
        return [
            item.model_dump(mode="json")
            for item in container.evolution_repository.list_rules(
                status=status, limit=limit
            )
        ]

    @app.post("/api/v1/evolution/rules/{rule_id}/replay")
    async def replay_evolution_rule(rule_id: str, request: Request):
        user = require_admin(request)
        try:
            rule = container.evolution_rule_service.run_contract_replay(
                rule_id, reviewer_id=user.user_id
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="规则不存在") from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return rule.model_dump(mode="json")

    @app.post("/api/v1/evolution/rules/{rule_id}/transition")
    async def transition_evolution_rule(
        rule_id: str,
        request: Request,
        payload: RuleApprovalRequest,
    ):
        user = require_admin(request)
        try:
            rule = container.evolution_rule_service.transition(
                rule_id,
                payload,
                reviewer_id=user.user_id,
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="规则不存在") from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return rule.model_dump(mode="json")

    @app.get("/api/v1/evolution/rules/{rule_id}/runs")
    async def list_evolution_rule_runs(rule_id: str, request: Request):
        require_admin(request)
        return [
            item.model_dump(mode="json")
            for item in container.evolution_repository.list_runs(
                rule_id=rule_id, limit=200
            )
        ]

    @app.post("/api/v1/preference-training/samples")
    async def create_preference_sample(
        request: Request,
        payload: PreferenceSampleCreate,
    ):
        require_admin(request)
        try:
            item = container.preference_training_service.create_sample(payload)
        except (RuntimeError, ValueError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return item.model_dump(mode="json")

    @app.get("/api/v1/preference-training/samples")
    async def list_preference_samples(
        request: Request,
        status: str | None = Query(default=None),
    ):
        require_admin(request)
        return [
            item.model_dump(mode="json")
            for item in container.preference_training_repository.list_samples(
                status=status, limit=500
            )
        ]

    @app.patch("/api/v1/preference-training/samples/{sample_id}")
    async def review_preference_sample(
        sample_id: str,
        request: Request,
        payload: PreferenceSampleReview,
    ):
        user = require_admin(request)
        try:
            item = container.preference_training_service.review_sample(
                sample_id, status=payload.status, reviewer_id=user.user_id
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="偏好样本不存在") from exc
        except (RuntimeError, ValueError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return item.model_dump(mode="json")

    @app.post("/api/v1/preference-training/datasets/freeze")
    async def freeze_preference_dataset(
        request: Request,
        payload: PreferenceDatasetCreate,
    ):
        user = require_admin(request)
        try:
            dataset = container.preference_training_service.freeze_dataset(
                payload, creator_id=user.user_id
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="样本不存在") from exc
        except (RuntimeError, ValueError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return dataset.model_dump(mode="json")

    @app.get("/api/v1/preference-training/datasets")
    async def list_preference_datasets(request: Request):
        require_admin(request)
        return [
            item.model_dump(mode="json")
            for item in container.preference_training_repository.list_datasets()
        ]

    @app.post("/api/v1/preference-training/jobs")
    async def create_preference_training_job(
        request: Request,
        payload: TrainingJobCreate,
    ):
        user = require_admin(request)
        try:
            job = container.preference_training_service.create_job(
                payload, creator_id=user.user_id
            )
        except (RuntimeError, ValueError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return job.model_dump(mode="json")

    @app.get("/api/v1/preference-training/jobs")
    async def list_preference_training_jobs(request: Request):
        require_admin(request)
        return [
            item.model_dump(mode="json")
            for item in container.preference_training_repository.list_jobs()
        ]

    @app.get("/api/v1/auth/me")
    async def me(request: Request):
        user = current_user(request)
        if user is None:
            raise HTTPException(status_code=401, detail="请先登录后继续")
        return {"user": user}

    def account_profile_payload(user: AuthUser, profile) -> dict:
        data = profile.model_dump(mode="json", exclude={"avatar_key"})
        data["avatar_url"] = (
            f"/api/v1/auth/me/avatar?v={profile.avatar_version}"
            if profile.avatar_key
            else None
        )
        return {"user": user.model_dump(mode="json"), "profile": data}

    @app.get("/api/v1/auth/me/profile")
    async def account_profile(request: Request) -> dict:
        user = current_user(request)
        if user is None:
            raise HTTPException(status_code=401, detail="请先登录后继续")
        profile = container.account_profile_service.get_profile(user)
        return account_profile_payload(user, profile)

    @app.patch("/api/v1/auth/me/profile")
    async def update_account_profile(
        payload: AccountProfileUpdateRequest, request: Request
    ) -> dict:
        user = current_user(request)
        if user is None:
            raise HTTPException(status_code=401, detail="请先登录后继续")
        try:
            updated_user, profile = container.account_profile_service.update_profile(
                user, payload
            )
        except (ValueError, LookupError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return account_profile_payload(updated_user, profile)

    @app.put("/api/v1/auth/me/avatar")
    async def update_account_avatar(
        request: Request, file: UploadFile = File(...)
    ) -> dict:
        user = current_user(request)
        if user is None:
            raise HTTPException(status_code=401, detail="请先登录后继续")
        content = await file.read()
        try:
            profile = container.account_profile_service.update_avatar(user, content)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return account_profile_payload(user, profile)

    @app.get("/api/v1/auth/me/avatar")
    async def account_avatar(request: Request):
        user = current_user(request)
        if user is None:
            raise HTTPException(status_code=401, detail="请先登录后继续")
        avatar = container.account_profile_service.avatar_file(user)
        if avatar is None:
            raise HTTPException(status_code=404, detail="尚未设置头像")
        path, media_type = avatar
        return FileResponse(
            path,
            media_type=media_type,
            headers={"Cache-Control": "private, max-age=3600"},
        )

    @app.get("/api/v1/workshop/favorite-folders")
    async def list_favorite_folders(request: Request) -> dict:
        user = current_user(request)
        return {"items": container.workshop_library_service.list_folders(user.user_id)}

    @app.post("/api/v1/workshop/favorite-folders", status_code=201)
    async def create_favorite_folder(
        payload: FavoriteFolderCreateRequest, request: Request
    ) -> dict:
        user = current_user(request)
        try:
            folder = container.workshop_library_service.create_folder(
                user.user_id, payload.name
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return {"folder": folder}

    @app.delete("/api/v1/workshop/favorite-folders/{folder_id}", status_code=204)
    async def delete_favorite_folder(folder_id: str, request: Request) -> Response:
        user = current_user(request)
        if not container.workshop_library_service.delete_folder(user.user_id, folder_id):
            raise HTTPException(status_code=404, detail="收藏簿不存在")
        return Response(status_code=204)

    @app.get("/api/v1/workshop/favorites")
    async def list_workshop_favorites(
        request: Request, folder_id: str | None = None
    ) -> dict:
        user = current_user(request)
        items = container.workshop_library_service.list_favorites(
            user.user_id, folder_id=folder_id
        )
        return {"items": items, "total": len(items)}

    @app.post("/api/v1/workshop/favorites", status_code=201)
    async def save_workshop_favorite(
        payload: FavoriteCreateRequest, request: Request
    ) -> dict:
        user = current_user(request)
        try:
            favorite = container.workshop_library_service.save_favorite(
                user.user_id, **payload.model_dump()
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc).strip("'")) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return {"favorite": favorite}

    @app.delete("/api/v1/workshop/favorites/{favorite_id}", status_code=204)
    async def delete_workshop_favorite(favorite_id: str, request: Request) -> Response:
        user = current_user(request)
        if not container.workshop_library_service.delete_favorite(
            user.user_id, favorite_id
        ):
            raise HTTPException(status_code=404, detail="收藏不存在")
        return Response(status_code=204)

    @app.get("/api/v1/workshop/note-folders")
    async def list_note_folders(request: Request) -> dict:
        user = current_user(request)
        items = container.workshop_library_service.list_note_folders(user.user_id)
        return {"items": items, "total": len(items)}

    @app.post("/api/v1/workshop/note-folders", status_code=201)
    async def create_note_folder(
        payload: NoteFolderCreateRequest, request: Request
    ) -> dict:
        user = current_user(request)
        try:
            folder = container.workshop_library_service.create_note_folder(
                user.user_id, payload.name
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return {"folder": folder}

    @app.get("/api/v1/workshop/notes")
    async def list_workshop_notes(
        request: Request,
        source: str | None = None,
        note_type: str | None = None,
        q: str | None = None,
    ) -> dict:
        user = current_user(request)
        items = container.workshop_library_service.list_notes(
            user.user_id, source=source, note_type=note_type, query=q
        )
        return {"items": items, "total": len(items)}

    @app.post("/api/v1/workshop/notes", status_code=201)
    async def create_workshop_note(
        payload: WorkshopNoteCreateRequest, request: Request
    ) -> dict:
        user = current_user(request)
        try:
            note = container.workshop_library_service.create_note(
                user.user_id, **payload.model_dump()
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return {"note": note}

    @app.put("/api/v1/workshop/notes/{note_id}")
    async def update_workshop_note(
        note_id: str, payload: WorkshopNoteUpdateRequest, request: Request
    ) -> dict:
        user = current_user(request)
        try:
            note = container.workshop_library_service.update_note(
                user.user_id, note_id, **payload.model_dump()
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        if note is None:
            raise HTTPException(status_code=404, detail="笔记不存在")
        return {"note": note}

    @app.delete("/api/v1/workshop/notes/{note_id}", status_code=204)
    async def delete_workshop_note(note_id: str, request: Request) -> Response:
        user = current_user(request)
        if not container.workshop_library_service.delete_note(user.user_id, note_id):
            raise HTTPException(status_code=404, detail="笔记不存在")
        return Response(status_code=204)

    def syllabus_owner(request: Request) -> str:
        user = current_user(request)
        if user is None:
            raise HTTPException(status_code=401, detail="请先登录后继续")
        return user.user_id

    def raise_syllabus_error(exc: UserSyllabusError) -> None:
        status = 404 if exc.code == USER_SYLLABUS_NOT_FOUND else 422
        raise HTTPException(status_code=status, detail={"code": exc.code, "message": exc.message}) from exc

    @app.post("/api/v1/user-syllabi", status_code=201)
    async def upload_user_syllabus(request: Request, file: UploadFile = File(...),
                                   title: str = Form(""), subject: str = Form(""),
                                   exam_type: str = Form("")) -> dict:
        try:
            return await asyncio.shield(container.user_syllabus_service.import_file(
                syllabus_owner(request), file.filename or "syllabus", await file.read(),
                title=title, subject=subject, exam_type=exam_type))
        except UserSyllabusError as exc:
            raise_syllabus_error(exc)

    @app.get("/api/v1/user-syllabi")
    async def list_user_syllabi(request: Request) -> dict:
        return {"items": container.user_syllabus_service.list(syllabus_owner(request))}

    @app.get("/api/v1/user-syllabi/{syllabus_id}/requirements")
    async def get_user_syllabus_requirements(syllabus_id: str, request: Request) -> dict:
        try:
            return {"items": container.user_syllabus_service.requirements(syllabus_owner(request), syllabus_id)}
        except UserSyllabusError as exc:
            raise_syllabus_error(exc)

    @app.get("/api/v1/user-syllabi/{syllabus_id}/mappings")
    async def get_user_syllabus_mappings(syllabus_id: str, request: Request) -> dict:
        try:
            return {"items": container.user_syllabus_service.mappings(syllabus_owner(request), syllabus_id)}
        except UserSyllabusError as exc:
            raise_syllabus_error(exc)

    @app.get("/api/v1/user-syllabi/{syllabus_id}")
    async def get_user_syllabus(syllabus_id: str, request: Request) -> dict:
        try:
            return container.user_syllabus_service.get(syllabus_owner(request), syllabus_id)
        except UserSyllabusError as exc:
            raise_syllabus_error(exc)

    @app.put("/api/v1/user-syllabi/{syllabus_id}/activate")
    async def activate_user_syllabus(syllabus_id: str, request: Request) -> dict:
        try:
            return container.user_syllabus_service.activate(syllabus_owner(request), syllabus_id)
        except UserSyllabusError as exc:
            raise_syllabus_error(exc)

    @app.post("/api/v1/user-syllabi/{syllabus_id}/reprocess")
    async def reprocess_user_syllabus(syllabus_id: str, request: Request) -> dict:
        try:
            return await container.user_syllabus_service.reprocess(syllabus_owner(request), syllabus_id)
        except UserSyllabusError as exc:
            raise_syllabus_error(exc)

    @app.delete("/api/v1/user-syllabi/{syllabus_id}", status_code=204)
    async def delete_user_syllabus(syllabus_id: str, request: Request) -> Response:
        try:
            container.user_syllabus_service.delete(syllabus_owner(request), syllabus_id)
        except UserSyllabusError as exc:
            raise_syllabus_error(exc)
        return Response(status_code=204)

    @app.get("/api/v1/textbooks/pdfs/catalog")
    async def textbook_pdf_catalog(request: Request) -> dict:
        user = current_user(request)
        items = container.textbook_pdf_service.books(user.user_id)
        return {"items": items, "total": len(items)}

    @app.get("/api/v1/textbooks/categories")
    async def textbook_categories(request: Request) -> dict:
        current_user(request)
        items = container.textbook_import_service.categories()
        return {"items": items, "total": len(items)}

    @app.post("/api/v1/textbooks/import", status_code=202)
    async def import_textbook(
        request: Request,
        file: UploadFile = File(...),
        title: str = Form(""),
        description: str = Form(""),
        category: str = Form("中医药"),
        new_category: str = Form(""),
        cover: UploadFile | None = File(None),
        match_local: bool = Form(False),
        allow_large: bool = Form(False),
    ) -> dict:
        user = current_user(request)
        content = await file.read()
        if len(content) > 200 * 1024 * 1024 and not allow_large:
            raise HTTPException(
                status_code=422,
                detail={"code": "TEXTBOOK_TOO_LARGE", "message": "当前教材超过大小限制（200MB），解析质量可能下降"},
            )
        cover_content = await cover.read() if cover is not None else None
        filename = file.filename or "textbook.pdf"
        cover_media_type = (cover.content_type or "") if cover is not None else ""
        fingerprint = textbook_fingerprint(content, cover_content, {
            "filename": filename, "title": title, "description": description,
            "category": category, "new_category": new_category,
            "match_local": match_local, "allow_large": allow_large,
            "cover_media_type": cover_media_type,
        })
        state, lease = await finish_upload_work(asyncio.to_thread(textbook_task_store.start, user.user_id, fingerprint))
        if lease is None:
            return textbook_task_response(state)

        def report(step: str, label: str) -> None:
            lease.update(step=step, step_label=label)

        async def run() -> None:
            published = False
            try:
                item = await finish_upload_work(container.textbook_import_service.import_pdf(
                    owner_id=user.user_id,
                    filename=filename,
                    content=content,
                    title=title,
                    description=description,
                    category=category,
                    new_category=new_category,
                    cover_content=cover_content,
                    cover_media_type=cover_media_type,
                    match_local=match_local,
                    allow_large=allow_large,
                    progress=report,
                ))
                published = True
                public_item = container.textbook_pdf_service.by_id(
                    str(item["book_id"]), user.user_id
                )
                if public_item is None:
                    raise RuntimeError("published textbook is unavailable")
                lease.complete(public_item)
            except TextbookTocNotFound as exc:
                lease.fail(exc.code, "目录未提取成功，请检查教材目录页。", retry_allowed=not published)
            except TextbookImportError as exc:
                lease.fail(exc.code, "教材处理失败，请检查文件及解析服务配置。", retry_allowed=not published)
            except asyncio.CancelledError:
                # A forced stop has uncertain publication results; no auto replay.
                raise
            except Exception:
                lease.fail("TEXTBOOK_IMPORT_FAILED", "教材处理异常，结果待核实；请先检查教材书架。")
            finally:
                lease.close()

        task = asyncio.create_task(run())
        textbook_upload_tasks.add(task)
        def upload_done(done: asyncio.Task) -> None:
            textbook_upload_tasks.discard(done)
            if not done.cancelled():
                done.exception()  # Retrieve failures (e.g. disk full); disk state remains recoverable.
        task.add_done_callback(upload_done)
        return textbook_task_response(state)

    @app.get("/api/v1/textbooks/knowledge-graphs")
    async def list_textbook_knowledge_graphs(request: Request) -> dict:
        user = current_user(request)
        return {"items": container.textbook_import_service.list_knowledge_graphs(user.user_id)}

    @app.get("/api/v1/textbooks/knowledge-graphs/{book_id}")
    async def get_textbook_knowledge_graph(book_id: str, request: Request) -> dict:
        user = current_user(request)
        item = container.textbook_import_service.get_knowledge_graph(user.user_id, book_id)
        if item is None:
            raise HTTPException(status_code=404, detail="未找到该教材的知识图谱")
        return item

    @app.get("/api/v1/textbooks/imports")
    async def textbook_import_history(request: Request, limit: int = Query(50, ge=1, le=100)) -> dict:
        user = current_user(request)
        states = await asyncio.to_thread(textbook_task_store.list, user.user_id, limit)
        return {"items": [textbook_task_response(state) for state in states]}

    @app.get("/api/v1/textbooks/import/{task_id}")
    async def textbook_import_status(task_id: str, request: Request) -> dict:
        user = current_user(request)
        try:
            state = await asyncio.to_thread(textbook_task_store.get, user.user_id, task_id)
        except KeyError:
            raise HTTPException(status_code=404, detail="导入任务不存在") from None
        return textbook_task_response(state)

    @app.get("/api/v1/textbooks/pdfs/resolve")
    async def resolve_textbook_pdf(book: str, request: Request) -> dict:
        user = current_user(request)
        item = container.textbook_pdf_service.resolve(book, user.user_id)
        return {"available": bool(item and item.get("available")), "book": item}

    @app.get("/api/v1/textbooks/pdfs/{book_id}")
    async def textbook_pdf_metadata(book_id: str, request: Request) -> dict:
        user = current_user(request)
        item = container.textbook_pdf_service.by_id(book_id, user.user_id)
        if item is None:
            raise HTTPException(status_code=404, detail="教材不存在")
        return {"book": item}

    @app.delete("/api/v1/textbooks/pdfs/{book_id}")
    async def textbook_pdf_delete(book_id: str, request: Request) -> dict:
        user = current_user(request)
        item = container.textbook_pdf_service.by_id(book_id, user.user_id)
        if item is None:
            raise HTTPException(status_code=404, detail="教材不存在")
        if item.get("origin") != "user_upload":
            raise HTTPException(status_code=403, detail="平台内置教材不可删除")
        if not container.textbook_pdf_service.delete_uploaded_book(book_id, user.user_id):
            raise HTTPException(status_code=404, detail="教材不存在或无权删除")
        return {"ok": True}

    @app.patch("/api/v1/textbooks/pdfs/{book_id}")
    async def textbook_pdf_set_hidden(book_id: str, payload: TextbookPdfHiddenRequest, request: Request) -> dict:
        user = current_user(request)
        item = container.textbook_pdf_service.by_id(book_id, user.user_id)
        if item is None:
            raise HTTPException(status_code=404, detail="教材不存在")
        if item.get("origin") != "user_upload":
            raise HTTPException(status_code=403, detail="平台内置教材不可隐藏")
        if not container.textbook_pdf_service.set_uploaded_book_hidden(book_id, user.user_id, payload.hidden):
            raise HTTPException(status_code=404, detail="教材不存在或无权修改")
        return {"ok": True, "hidden": payload.hidden}

    @app.get("/api/v1/textbooks/pdfs/{book_id}/file")
    async def textbook_pdf_file(book_id: str, request: Request):
        user = current_user(request)
        item = container.textbook_pdf_service.by_id(book_id, user.user_id)
        path = container.textbook_pdf_service.file_path(book_id, user.user_id)
        if item is None or path is None:
            raise HTTPException(status_code=404, detail="该教材暂无电子版")
        return FileResponse(
            path,
            media_type="application/pdf",
            filename=path.name,
            content_disposition_type="inline",
            headers={"Cache-Control": "private, max-age=3600"},
        )

    @app.get("/api/v1/textbooks/pdfs/{book_id}/cover")
    async def textbook_pdf_cover(book_id: str, request: Request):
        user = current_user(request)
        path = container.textbook_pdf_service.cover_path(book_id, user.user_id)
        if path is None:
            raise HTTPException(status_code=404, detail="教材封面不存在")
        media_type = {
            ".png": "image/png", ".webp": "image/webp", ".gif": "image/gif",
        }.get(path.suffix.lower(), "image/jpeg")
        return FileResponse(
            path,
            media_type=media_type,
            headers={"Cache-Control": "private, max-age=3600"},
        )

    @app.get("/api/v1/textbooks/pdfs/{book_id}/pages/{page_number}/annotations")
    async def textbook_pdf_annotations(
        book_id: str, page_number: int, request: Request
    ) -> dict:
        user = current_user(request)
        if page_number < 1 or container.textbook_pdf_service.by_id(book_id, user.user_id) is None:
            raise HTTPException(status_code=404, detail="教材页面不存在")
        return container.textbook_pdf_service.annotations.get_page(
            user.user_id, book_id, page_number
        )

    @app.put("/api/v1/textbooks/pdfs/{book_id}/pages/{page_number}/annotations")
    async def save_textbook_pdf_annotations(
        book_id: str,
        page_number: int,
        payload: TextbookPdfAnnotationsUpdateRequest,
        request: Request,
    ) -> dict:
        user = current_user(request)
        if page_number < 1 or container.textbook_pdf_service.by_id(book_id, user.user_id) is None:
            raise HTTPException(status_code=404, detail="教材页面不存在")
        return container.textbook_pdf_service.annotations.save_page(
            user.user_id, book_id, page_number, payload.annotations
        )

    @app.get("/api/v1/textbooks/pdfs/{book_id}/reading-state")
    async def textbook_pdf_reading_state(book_id: str, request: Request) -> dict:
        user = current_user(request)
        if container.textbook_pdf_service.by_id(book_id, user.user_id) is None:
            raise HTTPException(status_code=404, detail="教材不存在")
        return container.textbook_pdf_service.annotations.get_reading_state(
            user.user_id, book_id
        )

    @app.put("/api/v1/textbooks/pdfs/{book_id}/reading-state")
    async def save_textbook_pdf_reading_state(
        book_id: str,
        payload: TextbookPdfReadingStateUpdateRequest,
        request: Request,
    ) -> dict:
        user = current_user(request)
        if container.textbook_pdf_service.by_id(book_id, user.user_id) is None:
            raise HTTPException(status_code=404, detail="教材不存在")
        return container.textbook_pdf_service.annotations.save_reading_state(
            user.user_id, book_id, payload.page_number, payload.zoom
        )

    @app.post("/api/v1/textbooks/pdfs/{book_id}/pages/{page_number}/ai")
    async def textbook_pdf_ai(
        book_id: str,
        page_number: int,
        payload: TextbookPdfAiRequest,
        request: Request,
    ) -> StreamingResponse:
        user = current_user(request)
        if container.textbook_pdf_service.by_id(book_id, user.user_id) is None:
            raise HTTPException(status_code=404, detail="教材不存在")
        if container.textbook_pdf_ai_service is None:
            raise HTTPException(status_code=503, detail="AI 助教服务未启用（当前为演示模式）")

        ai_service = container.textbook_pdf_ai_service
        session_id = str(payload.session_id or "").strip() or None
        if session_id and not session_id.startswith("textbook-ai-"):
            raise HTTPException(status_code=400, detail="无效的会话标识")

        saved_user_text = payload.question.strip() or "总结本页内容"

        async def sse():
            queue: asyncio.Queue[str] = asyncio.Queue()
            full_text: list[str] = []

            def on_delta(text: str) -> None:
                full_text.append(text)
                queue.put_nowait(text)

            async def pump():
                try:
                    if payload.mode == "chat":
                        question = payload.question.strip()
                        if not question:
                            await queue.put("__error__:请输入问题")
                            return
                        await ai_service.chat(
                            book_id,
                            page_number,
                            question,
                            payload.history,
                            user.user_id,
                            page_span=payload.page_span,
                            on_delta=on_delta,
                        )
                    else:
                        await ai_service.summarize(
                            book_id,
                            page_number,
                            user.user_id,
                            page_span=payload.page_span,
                            on_delta=on_delta,
                        )
                    # 流式完成后写入会话（含新建会话）
                    if session_id:
                        try:
                            existing = ai_service.get_messages(session_id, user.user_id)
                            if not existing:
                                ai_service.conversation_repository.create_session(
                                    session_id, user.user_id, "新对话"
                                )
                            ai_service.save_messages(session_id, user.user_id, [
                                *existing,
                                {"role": "user", "content": saved_user_text},
                                {"role": "assistant", "content": "".join(full_text)},
                            ])
                        except Exception:
                            pass
                except ValueError as exc:
                    await queue.put(f"__error__:{exc}")
                except Exception:
                    await queue.put("__error__:AI 生成失败，请稍后重试")
                finally:
                    await queue.put("__done__")

            task = asyncio.create_task(pump())
            try:
                while True:
                    item = await queue.get()
                    if item == "__done__":
                        break
                    if item.startswith("__error__:"):
                        yield f"data: {json.dumps({'event': 'error', 'message': item[10:]}, ensure_ascii=False)}\n\n"
                        break
                    yield f"data: {json.dumps({'event': 'delta', 'text': item}, ensure_ascii=False)}\n\n"
            finally:
                task.cancel()
            yield "data: " + json.dumps({"event": "done"}, ensure_ascii=False) + "\n\n"

        return StreamingResponse(
            sse(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    # ── 教材 AI 会话管理 ──
    @app.get("/api/v1/textbooks/ai/sessions")
    async def textbook_ai_sessions(request: Request) -> dict:
        user = current_user(request)
        if container.textbook_pdf_ai_service is None:
            raise HTTPException(status_code=503, detail="AI 助教服务未启用（当前为演示模式）")
        return {"sessions": container.textbook_pdf_ai_service.list_sessions(user.user_id)}

    @app.post("/api/v1/textbooks/ai/sessions", status_code=201)
    async def textbook_ai_create_session(
        payload: TextbookPdfAiSessionCreateRequest,
        request: Request,
    ) -> dict:
        user = current_user(request)
        if container.textbook_pdf_ai_service is None:
            raise HTTPException(status_code=503, detail="AI 助教服务未启用（当前为演示模式）")
        session_id = container.textbook_pdf_ai_service.create_session(
            user.user_id, payload.title.strip() or "新对话"
        )
        return {"session_id": session_id}

    @app.get("/api/v1/textbooks/ai/sessions/{session_id}/messages")
    async def textbook_ai_session_messages(session_id: str, request: Request) -> dict:
        user = current_user(request)
        if container.textbook_pdf_ai_service is None:
            raise HTTPException(status_code=503, detail="AI 助教服务未启用（当前为演示模式）")
        if not session_id.startswith("textbook-ai-"):
            raise HTTPException(status_code=400, detail="无效的会话标识")
        messages = container.textbook_pdf_ai_service.get_messages(session_id, user.user_id)
        if not messages and not any(
            session.get("id") == session_id
            for session in container.textbook_pdf_ai_service.list_sessions(user.user_id)
        ):
            raise HTTPException(status_code=404, detail="会话不存在")
        return {"messages": messages}

    @app.patch("/api/v1/textbooks/ai/sessions/{session_id}")
    async def textbook_ai_rename_session(
        session_id: str,
        payload: TextbookPdfAiSessionRenameRequest,
        request: Request,
    ) -> dict:
        user = current_user(request)
        if container.textbook_pdf_ai_service is None:
            raise HTTPException(status_code=503, detail="AI 助教服务未启用（当前为演示模式）")
        if not session_id.startswith("textbook-ai-"):
            raise HTTPException(status_code=400, detail="无效的会话标识")
        if not container.textbook_pdf_ai_service.rename_session(
            session_id, user.user_id, payload.title.strip()
        ):
            raise HTTPException(status_code=404, detail="会话不存在")
        return {"ok": True}

    @app.delete("/api/v1/textbooks/ai/sessions/{session_id}")
    async def textbook_ai_delete_session(session_id: str, request: Request) -> dict:
        user = current_user(request)
        if container.textbook_pdf_ai_service is None:
            raise HTTPException(status_code=503, detail="AI 助教服务未启用（当前为演示模式）")
        if not session_id.startswith("textbook-ai-"):
            raise HTTPException(status_code=400, detail="无效的会话标识")
        if not container.textbook_pdf_ai_service.delete_session(session_id, user.user_id):
            raise HTTPException(status_code=404, detail="会话不存在")
        return {"ok": True}

    @app.get("/api/v1/textbooks/pdfs/{book_id}/questions")
    async def get_textbook_matched_questions(book_id: str, request: Request) -> dict:
        user = current_user(request)
        return await container.textbook_import_service.book_matched_questions(user.user_id, book_id)

    @app.post("/api/v1/workshop/note-images", status_code=201)
    async def upload_workshop_note_image(
        request: Request, file: UploadFile = File(...)
    ) -> dict:
        user = current_user(request)
        media_type = str(file.content_type or "").lower()
        extensions = {
            "image/jpeg": ".jpg",
            "image/png": ".png",
            "image/webp": ".webp",
            "image/gif": ".gif",
        }
        if media_type not in extensions:
            raise HTTPException(
                status_code=422, detail="笔记图片仅支持 JPG、PNG、WebP 或 GIF"
            )
        content = await file.read()
        if not content:
            raise HTTPException(status_code=422, detail="上传图片不能为空")
        if len(content) > 5 * 1024 * 1024:
            raise HTTPException(status_code=422, detail="笔记图片不能超过 5 MB")
        user_directory = WORKSHOP_NOTE_IMAGE_ROOT / user.user_id
        user_directory.mkdir(parents=True, exist_ok=True)
        image_id = uuid4().hex
        target = user_directory / f"{image_id}{extensions[media_type]}"
        target.write_bytes(content)
        return {
            "image_id": image_id,
            "url": f"/api/v1/workshop/note-images/{image_id}",
            "media_type": media_type,
        }

    @app.get("/api/v1/workshop/note-images/{image_id}")
    async def get_workshop_note_image(image_id: str, request: Request):
        user = current_user(request)
        if not re.fullmatch(r"[a-f0-9]{32}", image_id):
            raise HTTPException(status_code=404, detail="笔记图片不存在")
        user_directory = WORKSHOP_NOTE_IMAGE_ROOT / user.user_id
        for extension, media_type in (
            (".jpg", "image/jpeg"),
            (".png", "image/png"),
            (".webp", "image/webp"),
            (".gif", "image/gif"),
        ):
            target = user_directory / f"{image_id}{extension}"
            if target.is_file():
                return FileResponse(
                    target,
                    media_type=media_type,
                    headers={"Cache-Control": "private, max-age=86400"},
                )
        raise HTTPException(status_code=404, detail="笔记图片不存在")

    @app.post("/api/v1/auth/onboarding/complete")
    async def complete_registration_onboarding(request: Request):
        user = current_user(request)
        onboarding_status: dict = {"status": "unavailable"}
        if backend_handoff is not None:
            try:
                onboarding_status = await asyncio.to_thread(
                    backend_handoff.get_onboarding_status, user.user_id
                )
            except Exception as exc:
                raise HTTPException(
                    status_code=503,
                    detail="学情调查状态暂时无法核验，请稍后重试",
                ) from exc
            if onboarding_status.get("status") != "onboarding_completed":
                raise HTTPException(
                    status_code=409,
                    detail="请先完成并保存注册学情调查",
                )
        updated_user = container.authentication_service.complete_onboarding(
            user.user_id
        )
        return {
            "user": updated_user,
            "onboarding_status": onboarding_status,
        }

    @app.get("/users/me", include_in_schema=False)
    async def legacy_current_user(request: Request) -> dict:
        user = current_user(request)
        return {
            "id": user.user_id,
            "username": user.username,
            "display_name": user.display_name,
            "role": user.role,
        }

    @app.api_route("/token", methods=["POST"], include_in_schema=False)
    @app.api_route("/register", methods=["POST"], include_in_schema=False)
    @app.api_route("/send-code", methods=["POST"], include_in_schema=False)
    @app.api_route("/reset-password", methods=["POST"], include_in_schema=False)
    async def retired_legacy_auth() -> JSONResponse:
        return JSONResponse(
            status_code=410,
            content={"detail": "旧认证接口已停用，请使用 /api/v1/auth"},
        )

    @app.get("/api/v1/conversations")
    async def list_conversations(request: Request) -> list[dict]:
        user = current_user(request)
        repository = container.review_card_use_case.conversation_repository
        sessions = repository.list_sessions(user.user_id)
        internal_due_review_prefix = "请为以下已到期知识点生成一张可立即学习的复习卡："
        return [
            session
            for session in sessions
            if not str(session.get("title") or "").startswith(
                internal_due_review_prefix
            )
        ]

    @app.post("/api/v1/conversations", status_code=201)
    async def create_conversation(
        payload: ConversationCreateRequest, request: Request
    ) -> dict:
        user = current_user(request)
        session_id = f"CONV_{uuid4().hex}"
        repository = container.review_card_use_case.conversation_repository
        repository.create_session(session_id, user.user_id, payload.title.strip())
        return {"id": session_id, "title": payload.title.strip()}

    @app.get("/api/v1/conversations/{session_id}/messages")
    async def conversation_messages(session_id: str, request: Request) -> list[dict]:
        user = current_user(request)
        repository = container.review_card_use_case.conversation_repository
        rows = repository.get_messages(session_id, user.user_id)
        messages: list[dict] = []
        for row in rows:
            message = {
                "id": row.get("message_id"),
                "role": row.get("role"),
                "content": row.get("content"),
                "timestamp": row.get("created_at"),
            }
            if isinstance(row.get("actions"), list):
                message["actions"] = row["actions"]
            elif str(row.get("content") or "").startswith(
                "试卷已经完成组卷并通过审核。试卷正文已保存到学习工坊"
            ):
                # Messages saved before action metadata was introduced cannot recover
                # their exact paper id, but they should still offer a useful route.
                message["actions"] = [{
                    "action_type": "navigate",
                    "label": "前往试卷列表",
                    "destination": "workshop.paper",
                    "params": {},
                }]
            if isinstance(row.get("trace_events"), list):
                message["trace_events"] = row["trace_events"]
            elif isinstance(row.get("traceEvents"), list):
                message["traceEvents"] = row["traceEvents"]
            messages.append(message)
        return messages

    @app.patch("/api/v1/conversations/{session_id}")
    async def rename_conversation(
        session_id: str, payload: ConversationUpdateRequest, request: Request
    ) -> dict:
        user = current_user(request)
        repository = container.review_card_use_case.conversation_repository
        if not repository.rename_session(session_id, user.user_id, payload.title.strip()):
            raise HTTPException(status_code=404, detail="会话不存在")
        return {"id": session_id, "title": payload.title.strip()}

    @app.delete("/api/v1/conversations/{session_id}")
    async def delete_conversation(session_id: str, request: Request) -> dict:
        user = current_user(request)
        repository = container.review_card_use_case.conversation_repository
        if not repository.delete_session(session_id, user.user_id):
            raise HTTPException(status_code=404, detail="会话不存在")
        return {"status": "deleted", "id": session_id}

    @app.get("/", include_in_schema=False)
    async def root():
        if frontend_index is not None and frontend_index.is_file():
            return FileResponse(frontend_index)
        return HTMLResponse(
            "<!doctype html><html lang='zh-CN'><head><meta charset='utf-8'>"
            "<title>时珍智训</title></head><body><main>"
            "<h1>正式前端尚未构建</h1>"
            "<p>请先在 frontend/llm 执行 npm run build。</p>"
            "</main></body></html>",
            status_code=200,
        )

    @app.get("/favicon.ico", include_in_schema=False)
    async def favicon():
        favicon_path = frontend_root / "favicon.ico" if frontend_root else None
        if favicon_path is None or not favicon_path.is_file():
            return Response(status_code=204)
        return FileResponse(favicon_path)

    @app.get("/favicon.svg", include_in_schema=False)
    async def favicon_svg():
        favicon_path = frontend_root / "favicon.svg" if frontend_root else None
        if favicon_path is None or not favicon_path.is_file():
            return Response(status_code=204)
        return FileResponse(favicon_path, media_type="image/svg+xml")

    @app.get("/hero_word.txt", include_in_schema=False)
    async def hero_word():
        hero_word_path = frontend_root / "hero_word.txt" if frontend_root else None
        if hero_word_path is None or not hero_word_path.is_file():
            return Response(status_code=404)
        return FileResponse(hero_word_path, media_type="text/plain; charset=utf-8")

    @app.get("/health")
    async def health() -> dict[str, str]:
        payload = {
            "status": "ok",
            "mode": container.mode,
            "chat_model": container.chat_model_name,
            "embedding_model": container.embedding_model_name,
            "knowledge_source": "formal" if container.mode == "live" else "demo",
            "execution_engine": getattr(
                container.review_card_use_case.orchestrator,
                "engine_name",
                "legacy",
            ),
        }
        if backend_handoff is not None:
            payload["frontend_backend"] = "mounted"
        return payload

    @app.get("/api/v1/platform/status")
    async def platform_status(request: Request) -> dict:
        current_user(request)
        if backend_handoff is None:
            return {"enabled": False, "mounted": False}
        return backend_handoff.status()

    @app.get("/api/v1/platform/openapi.json", include_in_schema=False)
    async def platform_openapi(request: Request) -> dict:
        current_user(request)
        if backend_handoff is None:
            raise HTTPException(status_code=404, detail="前端后端兼容层未启用")
        return backend_handoff.app.openapi()

    @app.get("/api/v1/qualification-targets")
    async def list_qualification_targets(request: Request) -> dict:
        current_user(request)
        textbook_repository = container.textbook_route_repository
        default_repository = container.default_route_repository
        if textbook_repository is None or default_repository is None:
            raise HTTPException(status_code=503, detail="经典教材路线未启用")
        try:
            catalog = json.loads(
                QUALIFICATION_TARGET_CATALOG.read_text(encoding="utf-8")
            )
        except (OSError, ValueError) as exc:
            raise HTTPException(status_code=503, detail="资格考试目录不可用") from exc

        textbook_routes = {
            route.route_id: route
            for route in textbook_repository.routes
            if route.status == "approved"
        }
        items = []
        for raw_item in catalog.get("items", []):
            textbook_route = textbook_routes.get(raw_item.get("textbook_route_id"))
            planning_route = default_repository.get(
                str(raw_item.get("planning_route_id") or "")
            )
            if textbook_route is None or planning_route is None:
                continue
            items.append(
                {
                    **raw_item,
                    "textbook_route_version": textbook_route.route_version,
                    "planning_route_version": planning_route.route_version,
                    "textbook_stage_count": len(textbook_route.stages),
                    "textbook_route_endpoint": (
                        f"/api/v1/learning-routes/{textbook_route.route_id}"
                    ),
                }
            )
        return {
            "schema_version": catalog.get("schema_version", "1.0"),
            "target_kind": catalog.get("target_kind", "qualification_exam"),
            "items": items,
            "total": len(items),
        }

    @app.get("/api/v1/learning-routes")
    async def list_learning_routes(
        request: Request,
        status: str = Query(default="approved", pattern="^(approved|all)$"),
        q: str = Query(default="", max_length=120),
    ) -> dict:
        current_user(request)
        repository = container.textbook_route_repository
        if repository is None:
            raise HTTPException(status_code=503, detail="经典教材路线未启用")
        keyword = q.strip().casefold()
        routes = [
            route
            for route in repository.routes
            if (status == "all" or route.status == status)
            and (
                not keyword
                or keyword in route.goal_name.casefold()
                or keyword in route.route_id.casefold()
                or any(keyword in alias.casefold() for alias in route.aliases)
                or any(
                    keyword in stage.name.casefold()
                    or any(keyword in book.casefold() for book in stage.books)
                    for stage in route.stages
                )
            )
        ]
        return {
            "schema_version": "1.0",
            "route_kind": "classic_reference",
            "personalized": False,
            "items": [
                {
                    "route_id": route.route_id,
                    "route_version": route.route_version,
                    "status": route.status,
                    "goal_name": route.goal_name,
                    "aliases": route.aliases,
                    "stage_count": len(route.stages),
                    "book_count": len(
                        {book for stage in route.stages for book in stage.books}
                    ),
                    "reviewed_by": route.reviewed_by,
                    "source_refs": route.source_refs,
                    "detail_endpoint": f"/api/v1/learning-routes/{route.route_id}",
                }
                for route in routes
            ],
            "total": len(routes),
        }

    @app.get("/api/v1/learning-routes/{route_id}")
    async def get_learning_route(route_id: str, request: Request) -> dict:
        current_user(request)
        repository = container.textbook_route_repository
        if repository is None:
            raise HTTPException(status_code=503, detail="经典教材路线未启用")
        route = next(
            (item for item in repository.routes if item.route_id == route_id),
            None,
        )
        if route is None:
            raise HTTPException(status_code=404, detail="经典教材路线不存在")
        source_ids = set(route.source_refs)
        source_ids.update(
            ref for stage in route.stages for ref in stage.source_refs
        )
        return {
            "schema_version": "1.0",
            "route_kind": "classic_reference",
            "personalized": False,
            "route": route.model_dump(mode="json"),
            "sources": [
                source.model_dump(mode="json")
                for source in repository.sources
                if source.source_id in source_ids
            ],
            "navigation": {
                "atlas_route_id": "textbook_14_5",
                "stage_endpoint": f"/api/v1/learning-routes/{route.route_id}",
            },
        }

    @app.get("/api/v1/learning-activity/summary")
    async def learning_activity_summary(
        request: Request,
        days: int = Query(default=30),
        recent_limit: int = Query(default=20, ge=1, le=100),
    ) -> dict:
        user = current_user(request)
        if backend_handoff is None:
            raise HTTPException(status_code=503, detail="学习行为持久化服务未启用")
        if days not in {7, 30, 90}:
            raise HTTPException(status_code=422, detail="days 只能是 7、30 或 90")
        return await asyncio.to_thread(
            backend_handoff.load_learning_activity_summary,
            user.user_id,
            days=days,
            recent_limit=recent_limit,
        )

    @app.get("/api/v1/learning-state/multiscale")
    async def multiscale_learning_state(
        request: Request,
        window_days: int = Query(default=30),
        include_recent_events: bool = Query(default=False),
    ) -> dict:
        user = current_user(request)
        if user is None:
            raise HTTPException(status_code=401, detail="请先登录后继续")
        if backend_handoff is None:
            raise HTTPException(status_code=503, detail="多尺度学习状态服务未启用")
        if window_days not in {7, 30, 90}:
            raise HTTPException(
                status_code=422,
                detail="window_days 只能是 7、30 或 90",
            )
        state = await asyncio.to_thread(
            backend_handoff.load_multiscale_learning_state,
            user.user_id,
            plan_context=current_plan_context(user.user_id),
            window_days=window_days,
        )
        if include_recent_events:
            return state
        redacted = dict(state)
        micro = dict(redacted.get("micro") or {})
        for key in (
            "recent_attempts",
            "confirmed_mistake_reasons",
            "recent_question_ids",
            "recent_knowledge_point_ids",
            "recent_resource_ids",
        ):
            if key in micro:
                micro[key] = []
        redacted["micro"] = micro
        return redacted

    @app.get("/api/v1/learning-state/path-candidates")
    async def learning_path_candidates(
        request: Request,
        scope: Literal["long_term", "short_term", "daily_task"],
        limit: int = Query(default=10, ge=1, le=30),
        include_blocked: bool = Query(default=True),
    ) -> dict:
        user = current_user(request)
        if user is None:
            raise HTTPException(status_code=401, detail="请先登录后继续")
        if backend_handoff is None:
            raise HTTPException(status_code=503, detail="学习路径候选服务未启用")
        return await asyncio.to_thread(
            backend_handoff.load_path_candidates,
            user.user_id,
            plan_context=current_plan_context(user.user_id),
            scope=scope,
            limit=limit,
            include_blocked=include_blocked,
        )

    @app.get("/api/v1/executions/{execution_id}/coordination")
    async def execution_coordination(
        execution_id: str,
        request: Request,
    ) -> dict:
        user = current_user(request)
        if user is None:
            raise HTTPException(status_code=401, detail="请先登录后继续")
        state = (
            container.review_card_use_case.run_state_repository.get_by_execution_id(
                execution_id,
                user.user_id,
            )
        )
        if state is None:
            raise HTTPException(status_code=404, detail="执行记录不存在")
        return coordination_payload(execution_id, state)

    @app.get("/api/v1/learning-activity/trends")
    async def learning_activity_trends(
        request: Request,
        days: int = Query(default=30),
    ) -> dict:
        summary = await learning_activity_summary(
            request,
            days=days,
            recent_limit=1,
        )
        return {
            "schema_version": summary["schema_version"],
            **summary["trends"],
        }

    @app.get("/api/v1/learning-statistics/overview")
    async def learning_statistics_overview(
        request: Request,
        days: int = Query(default=30),
    ) -> dict:
        user = current_user(request)
        if backend_handoff is None:
            raise HTTPException(status_code=503, detail="学习成果统计服务未启用")
        if days not in {7, 30, 90}:
            raise HTTPException(status_code=422, detail="days 只能是 7、30 或 90")
        result = await asyncio.to_thread(
            backend_handoff.load_learning_statistics,
            user.user_id,
            days=days,
        )
        queue = await canonical_review_queue(user.user_id)
        lifetime = dict(result.get("lifetime") or {})
        lifetime.update(
            {
                "review_queue_total": len(queue.entries),
                "reviews_due": queue.due_count,
                "review_tasks_pending": queue.active_task_count,
            }
        )
        definitions = dict(result.get("metric_definitions") or {})
        definitions["reviews_due"] = {
            "label": "当前到期复习数",
            "formula": "count(canonical review memory where next_review_at <= calculated_at)",
            "sources": ["canonical_review_memory"],
        }
        return {
            **result,
            "lifetime": lifetime,
            "metric_definitions": definitions,
            "review_projection_source": "canonical_review_memory",
        }

    @app.get("/api/v1/learning-metrics/overview")
    async def learning_metrics_overview(
        request: Request,
        days: int = Query(default=30),
    ) -> dict:
        """Expose one auditable contract for all learner monitoring counters."""

        user = current_user(request)
        if user is None:
            raise HTTPException(status_code=401, detail="请先登录后继续")
        if backend_handoff is None:
            raise HTTPException(status_code=503, detail="学习监测服务未启用")
        if days not in {7, 30, 90}:
            raise HTTPException(status_code=422, detail="days 只能是 7、30 或 90")

        activity, outcomes, checkin, queue = await asyncio.gather(
            asyncio.to_thread(
                backend_handoff.load_learning_activity_summary,
                user.user_id,
                days=days,
                recent_limit=1,
            ),
            asyncio.to_thread(
                backend_handoff.load_learning_statistics,
                user.user_id,
                days=days,
            ),
            asyncio.to_thread(
                backend_handoff.get_checkin_status,
                user.user_id,
                days=days,
            ),
            canonical_review_queue(user.user_id),
        )
        system_metrics = dict(activity.get("system_data") or {})
        counters = dict(activity.get("counters") or {})
        login = dict(counters.get("login") or {})
        focus = dict(counters.get("focus_sessions") or {})
        tasks = dict(counters.get("daily_task_items") or {})
        current = dict(outcomes.get("current_window") or {})
        today = dict(outcomes.get("today") or {})
        lifetime = dict(outcomes.get("lifetime") or {})
        task_rate = dict(
            system_metrics.get("daily_atomic_task_completion_rate") or {}
        )

        def metric(
            value: Any,
            *,
            unit: str,
            formula: str,
            sources: list[str],
            scope: str = "current_window",
            available: bool = True,
            unavailable_reason: str | None = None,
        ) -> dict:
            return {
                "value": value,
                "unit": unit,
                "scope": scope,
                "available": available,
                "unavailable_reason": unavailable_reason,
                "formula": formula,
                "sources": sources,
            }

        metrics = {
            "login_events": metric(
                login.get("events", 0),
                unit="events",
                formula="count(learning_activity_records where activity_type = login)",
                sources=["learning_activity_records"],
            ),
            "distinct_login_days": metric(
                login.get("distinct_login_days", 0),
                unit="days",
                formula="count(distinct Asia/Shanghai date of login events)",
                sources=["learning_activity_records"],
            ),
            "active_days": metric(
                login.get("active_days", 0),
                unit="days",
                formula=(
                    "count(distinct Asia/Shanghai date where a login or daily_checkin "
                    "event exists)"
                ),
                sources=["learning_activity_records"],
            ),
            "checkin_streak": metric(
                int(checkin.get("streak") or 0),
                unit="days",
                scope="lifetime_to_today",
                formula="consecutive checked-in calendar days ending today",
                sources=["learning_activity_records"],
            ),
            "total_checkins": metric(
                int(checkin.get("total_checkins") or 0),
                unit="days",
                scope="lifetime",
                formula="count(distinct persisted daily_checkin date keys)",
                sources=["learning_activity_records"],
            ),
            "focus_seconds": metric(
                int(focus.get("active_seconds") or 0),
                unit="seconds",
                formula=(
                    "sum visible heartbeat intervals with interaction age <= 300 seconds, "
                    "clipped to the requested window"
                ),
                sources=["learning_focus_sessions"],
            ),
            "focus_minutes": metric(
                round(int(focus.get("active_seconds") or 0) / 60, 2),
                unit="minutes",
                formula="focus_seconds / 60",
                sources=["learning_focus_sessions"],
            ),
            "daily_task_items_planned": metric(
                int(tasks.get("total") or 0),
                unit="items",
                formula="count(non-cancelled atomic items in published daily tasks)",
                sources=["daily_task_instances", "daily_task_items"],
            ),
            "daily_task_items_completed": metric(
                int(tasks.get("completed") or 0),
                unit="items",
                formula=(
                    "count(items with status = completed among non-cancelled atomic "
                    "items in published daily tasks)"
                ),
                sources=["daily_task_instances", "daily_task_items"],
            ),
            "daily_task_completion_rate": metric(
                task_rate.get("value"),
                unit="ratio",
                formula="daily_task_items_completed / daily_task_items_planned",
                sources=["daily_task_instances", "daily_task_items"],
                available=bool(task_rate.get("available")),
                unavailable_reason=task_rate.get("unavailable_reason"),
            ),
            "questions_completed": metric(
                int(current.get("questions_completed") or 0),
                unit="items",
                formula=(
                    "accepted non-paper attempt items + max(accepted paper items, "
                    "items in latest completed paper submissions)"
                ),
                sources=[
                    "learning_attempt_items",
                    "grading_result_records",
                    "audit_result_records",
                    "paper_submissions",
                ],
            ),
            "questions_completed_lifetime": metric(
                int(lifetime.get("questions_completed") or 0),
                unit="items",
                scope="lifetime",
                formula=(
                    "accepted non-paper attempt items + max(accepted paper items, "
                    "items in latest completed paper submissions)"
                ),
                sources=[
                    "learning_attempt_items",
                    "grading_result_records",
                    "audit_result_records",
                    "paper_submissions",
                ],
            ),
            "unique_questions_completed": metric(
                int(current.get("unique_questions_completed") or 0),
                unit="questions",
                formula="count(distinct base question_id resolved from accepted versions)",
                sources=["question_version_records"],
            ),
            "correct_answers": metric(
                int(current.get("correct_answers") or 0),
                unit="items",
                formula="count(accepted audited items where is_correct = true)",
                sources=["grading_result_records", "audit_result_records"],
            ),
            "incorrect_answers": metric(
                int(current.get("incorrect_answers") or 0),
                unit="items",
                formula="count(accepted audited items where is_correct = false)",
                sources=["grading_result_records", "audit_result_records"],
            ),
            "score_rate": metric(
                current.get("score_rate"),
                unit="ratio",
                formula="sum(accepted score) / sum(accepted max_score)",
                sources=["grading_result_records", "audit_result_records"],
                available=current.get("score_rate") is not None,
                unavailable_reason=(
                    None
                    if current.get("score_rate") is not None
                    else "no_accepted_scored_items"
                ),
            ),
            "today_score_rate": metric(
                today.get("score_rate"),
                unit="ratio",
                formula="sum(accepted score today) / sum(accepted max_score today)",
                sources=["grading_result_records", "audit_result_records"],
                scope="today",
                available=today.get("score_rate") is not None,
                unavailable_reason=(
                    None
                    if today.get("score_rate") is not None
                    else "no_accepted_scored_items_today"
                ),
            ),
            "paper_attempts_completed": metric(
                int(current.get("paper_attempts_completed") or 0),
                unit="papers",
                formula="count(distinct latest completed paper submissions)",
                sources=["paper_submissions"],
            ),
            "active_mistakes": metric(
                int(current.get("active_mistakes") or 0),
                unit="items",
                formula="count(mistake records with status = active)",
                sources=["mistake_records"],
            ),
            "knowledge_points_assessed": metric(
                int(lifetime.get("knowledge_points_assessed") or 0),
                unit="knowledge_points",
                scope="lifetime",
                formula="count(persisted learner knowledge mastery states)",
                sources=["knowledge_mastery_states"],
            ),
            "knowledge_points_mastered": metric(
                int(lifetime.get("knowledge_points_mastered") or 0),
                unit="knowledge_points",
                scope="lifetime",
                formula="count(mastery states where mastery_score >= 80)",
                sources=["knowledge_mastery_states"],
            ),
            "review_queue_total": metric(
                len(queue.entries),
                unit="knowledge_points",
                scope="current",
                formula="count(active canonical review memory entries)",
                sources=["canonical_review_memory"],
            ),
            "reviews_due": metric(
                queue.due_count,
                unit="knowledge_points",
                scope="current",
                formula="count(canonical review entries due at calculated_at)",
                sources=["canonical_review_memory"],
            ),
        }
        return {
            "schema_version": "1.0",
            "window": {
                "days": days,
                **dict(outcomes.get("window") or {}),
            },
            "calculated_at": activity.get("calculated_at"),
            "recent_activities": list(activity.get("recent_activities") or []),
            "metrics": metrics,
            "source_endpoints": {
                "behavior": "/api/v1/learning-activity/summary",
                "outcomes": "/api/v1/learning-statistics/overview",
                "checkin": "/api/v1/checkin",
                "review": "/api/v1/review-queue",
                "monitoring": "/api/v1/learning-monitoring/snapshot",
            },
        }

    @app.get("/api/v1/learning-context")
    async def learning_context(request: Request) -> dict:
        user = current_user(request)
        if user is None:
            raise HTTPException(status_code=401, detail="请先登录后继续")
        behavior = (
            await asyncio.to_thread(
                backend_handoff.load_learning_context, user.user_id
            )
            if backend_handoff is not None
            else {}
        )
        container.review_service.ingest_question_attempts(
            learner_id=user.user_id,
            attempts=behavior.get("question_attempt", []),
        )
        daily_task_timer = await asyncio.to_thread(
            container.daily_task_refresh_service.ensure_current, user.user_id
        )
        if daily_task_timer.get("refreshed") is True:
            await asyncio.to_thread(notify_daily_task_refreshed, user.user_id)
        plans = container.learning_plan_service.get_current(user.user_id)
        queue = container.review_service.get_queue(user.user_id, limit=12)
        long_term_payload = (
            plans.long_term_plan.model_dump(mode="json")
            if plans is not None and plans.long_term_plan is not None
            else None
        )
        user_profile = behavior.get("user_profile") or {}
        if (
            backend_handoff is not None
            and long_term_payload
            and not str(user_profile.get("learning_goal") or "").strip()
        ):
            planning_route = long_term_payload.get("planning_route") or {}
            legacy_goal = str(planning_route.get("goal_name") or "").strip()
            if legacy_goal:
                migrated_profile = await asyncio.to_thread(
                    backend_handoff.update_learning_profile,
                    user.user_id,
                    {"learning_goal": legacy_goal},
                    "legacy-plan-profile-memory-migration",
                )
                behavior["user_profile"] = {
                    **user_profile,
                    **migrated_profile,
                }
        profile_readiness = ProfileReadinessService().evaluate(
            {
                "user_profile": behavior.get("user_profile") or {},
                "learning_target": behavior.get("learning_target") or {},
                "current_long_term_plan": long_term_payload or {},
            },
            "long_term",
        )
        return {
            **behavior,
            "learner_id": user.user_id,
            "learning_task": (
                plans.learning_task.model_dump(mode="json")
                if plans is not None and plans.learning_task is not None
                else None
            ),
            "long_term_plan": (
                long_term_payload
            ),
            "short_term_plan": (
                plans.short_term_plan.model_dump(mode="json")
                if plans is not None and plans.short_term_plan is not None
                else None
            ),
            "review_queue": queue.model_dump(mode="json"),
            "daily_task_timer": daily_task_timer,
            "profile_readiness": profile_readiness.model_dump(mode="json"),
            "learning_path": {
                "available": long_term_payload is not None,
                "root_endpoint": "/api/v1/learning-path",
                "children_endpoint": "/api/v1/learning-path/nodes?parent_id={node_id}",
                "classic_routes_endpoint": "/api/v1/learning-routes",
                "schema_version": "1.0",
            },
            "capabilities": {
                "behavior_context": backend_handoff is not None,
                "focus_tracking": backend_handoff is not None,
                "task_completion": backend_handoff is not None,
                "learning_trends": backend_handoff is not None,
                "learning_insights": backend_handoff is not None,
                "resource_matching_report": backend_handoff is not None,
                "active_intervention": backend_handoff is not None,
                "notifications": backend_handoff is not None,
                "automatic_plan_review": backend_handoff is not None,
                "daily_task_auto_refresh": True,
                "persistent_graph_resume": getattr(
                    container.review_card_use_case.orchestrator,
                    "persistent_checkpoints",
                    False,
                ),
                "learning_activity_summary_endpoint": (
                    "/api/v1/learning-activity/summary"
                    if backend_handoff is not None
                    else None
                ),
                "learning_statistics_endpoint": (
                    "/api/v1/learning-statistics/overview"
                    if backend_handoff is not None
                    else None
                ),
                "review_feedback": True,
                "execution_graph": True,
            },
        }

    @app.get("/api/v1/planning/readiness")
    async def planning_readiness(
        request: Request,
        scope: str = Query(pattern="^(long_term|short_term|daily_task)$"),
    ) -> dict:
        """Return the same prerequisite decision enforced by the agent workflow."""

        user = current_user(request)
        if user is None:
            raise HTTPException(status_code=401, detail="请先登录后继续")
        behavior = (
            await asyncio.to_thread(
                backend_handoff.load_learning_context,
                user.user_id,
            )
            if backend_handoff is not None
            else {}
        )
        plans = container.learning_plan_service.get_current(user.user_id)
        long_plan = (
            plans.long_term_plan.model_dump(mode="json")
            if plans is not None and plans.long_term_plan is not None
            else {}
        )
        short_plan = (
            plans.short_term_plan.model_dump(mode="json")
            if plans is not None and plans.short_term_plan is not None
            else {}
        )
        readiness = PlanningReadinessService().evaluate(
            {
                "user_profile": behavior.get("user_profile") or {},
                "learning_target": behavior.get("learning_target") or {},
                "current_long_term_plan": long_plan,
                "current_short_term_plan": short_plan,
            },
            scope,
            learner_id=user.user_id,
        )
        return readiness.model_dump(mode="json")

    @app.get("/api/v1/learning-plans/current")
    async def current_learning_plans(request: Request) -> dict:
        """Return plan prose, structured contracts and executable pass gates."""

        user = current_user(request)
        coordinator = container.daily_task_execution_coordinator
        task_progress: dict[str, Any] = {}
        if coordinator is not None:
            try:
                await asyncio.to_thread(coordinator.dispatch_pending, user.user_id, 20)
                ensure_snapshot = getattr(coordinator, "ensure_current_snapshot", None)
                if callable(ensure_snapshot):
                    await asyncio.to_thread(ensure_snapshot, user.user_id)
                await asyncio.to_thread(coordinator.reconcile_parent_status, user.user_id)
                task_progress = await asyncio.to_thread(
                    coordinator.load_current_progress, user.user_id
                )
            except Exception:
                task_progress = {}
        plans = container.learning_plan_service.get_current(user.user_id)
        return build_plan_progress(plans, daily_task_progress=task_progress)

    @app.get("/api/v1/learning-plans/current/context")
    async def current_learning_plans_context(request: Request) -> dict:
        """Return the canonical plan payload used by chat and planning pages."""

        user = current_user(request)
        if user is None:
            raise HTTPException(status_code=401, detail="请先登录后继续")
        plans = container.learning_plan_service.get_current(user.user_id)
        if plans is None:
            return {
                "learner_id": user.user_id,
                "long_term_plan": None,
                "short_term_plan": None,
                "learning_task": None,
                "source": "learning_plan_repository",
            }
        return {
            "learner_id": user.user_id,
            "long_term_plan": (
                plans.long_term_plan.model_dump(mode="json")
                if plans.long_term_plan is not None else None
            ),
            "short_term_plan": (
                plans.short_term_plan.model_dump(mode="json")
                if plans.short_term_plan is not None else None
            ),
            "learning_task": (
                plans.learning_task.model_dump(mode="json")
                if plans.learning_task is not None else None
            ),
            "source": "learning_plan_repository",
        }

    @app.post("/api/v1/learning-plans/current/stages/{stage}/evidence")
    async def record_stage_evidence(
        stage: int,
        payload: StageEvidenceRequest,
        request: Request,
    ) -> dict:
        """Bind a completed server-owned task to one approved stage requirement."""

        if stage < 1:
            raise HTTPException(status_code=422, detail="stage 必须大于等于 1")
        user = current_user(request)
        coordinator = container.daily_task_execution_coordinator
        task_progress: dict[str, Any] = {}
        if coordinator is not None:
            try:
                await asyncio.to_thread(coordinator.dispatch_pending, user.user_id, 20)
                await asyncio.to_thread(coordinator.reconcile_parent_status, user.user_id)
                task_progress = await asyncio.to_thread(
                    coordinator.load_current_progress, user.user_id
                )
            except Exception:
                task_progress = {}
        try:
            plans = await asyncio.to_thread(
                container.learning_plan_service.record_completed_task_stage_evidence,
                user.user_id,
                stage=stage,
                requirement=payload.requirement,
                task_id=payload.task_id,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return build_plan_progress(plans, daily_task_progress=task_progress)

    @app.get("/api/v1/learning-monitoring/snapshot")
    async def learning_monitoring_snapshot(
        request: Request,
        days: int = Query(default=7),
    ) -> dict:
        user = current_user(request)
        if user is None:
            raise HTTPException(status_code=401, detail="请先登录后继续")
        if days not in {7, 30, 90}:
            raise HTTPException(status_code=422, detail="days 只能是 7、30 或 90")
        behavior = (
            await asyncio.to_thread(
                backend_handoff.load_learning_context,
                user.user_id,
                days=days,
            )
            if backend_handoff is not None
            else {}
        )
        return LearningMonitoringService().build_snapshot(
            user.user_id, behavior, window_days=days
        ).model_dump(mode="json")

    @app.get("/api/v1/learning-insights")
    async def learning_insights(
        request: Request,
        days: int = Query(default=30),
        run_automation: bool = Query(default=True),
    ) -> dict:
        user = current_user(request)
        if user is None:
            raise HTTPException(status_code=401, detail="请先登录后继续")
        if backend_handoff is None:
            raise HTTPException(status_code=503, detail="学情洞察服务未启用")
        if days not in {7, 30, 90}:
            raise HTTPException(status_code=422, detail="days 只能是 7、30 或 90")
        queue = await canonical_review_queue(user.user_id)
        result = await asyncio.to_thread(
            backend_handoff.load_learning_insights,
            user.user_id,
            days=days,
            plan_context=current_plan_context(user.user_id),
            run_automation=run_automation,
            review_projection=canonical_review_projection(queue),
        )
        overview = {
            **dict(result.get("overview") or {}),
            "due_review_count": queue.due_count,
            "review_projection_source": "canonical_review_memory",
        }
        data_sources = list(result.get("data_sources") or [])
        has_canonical_review_source = any(
            item == "canonical_review_memory"
            or (
                isinstance(item, dict)
                and item.get("source_id") == "canonical_review_memory"
            )
            for item in data_sources
        )
        if not has_canonical_review_source:
            data_sources.append(
                {
                    "source_id": "canonical_review_memory",
                    "table": "review_memory_units",
                    "status": "authoritative_due_projection",
                }
            )
        return {
            **result,
            "overview": overview,
            "data_sources": data_sources,
        }

    @app.post("/api/v1/learning-automation/run")
    async def run_learning_automation(
        body: LearningAutomationRequest,
        request: Request,
    ) -> dict:
        """Run the feedback loop and materialize one missing due-review resource."""

        user = current_user(request)
        if user is None:
            raise HTTPException(status_code=401, detail="请先登录后继续")
        if body.days not in {7, 30, 90}:
            raise HTTPException(status_code=422, detail="days 只能是 7、30 或 90")
        queue_before = await canonical_review_queue(user.user_id)
        automation: dict[str, Any] = {
            "status": "unavailable",
            "message": "学情治理运行时未启用；复习队列仍可独立派发。",
        }
        if backend_handoff is not None:
            insights = await asyncio.to_thread(
                backend_handoff.load_learning_insights,
                user.user_id,
                days=body.days,
                plan_context=current_plan_context(user.user_id),
                run_automation=True,
                review_projection=canonical_review_projection(queue_before),
            )
            automation = {
                "status": "completed",
                **dict(insights.get("automation") or {}),
            }
        review_resource_push = (
            await materialize_due_review_resource(
                user.user_id,
                available_minutes=body.available_minutes,
            )
            if body.push_due_review_resource
            else {
                "status": "skipped",
                "message": "本次未请求生成到期复习资源。",
            }
        )
        queue_after = await canonical_review_queue(user.user_id)
        return {
            "schema_version": "1.0",
            "learner_id": user.user_id,
            "automation": automation,
            "review_resource_push": review_resource_push,
            "review_queue": queue_after.model_dump(mode="json"),
        }

    @app.get("/api/v1/learning-automation/status")
    async def learning_automation_status(request: Request) -> dict:
        """Expose the current user's asynchronous review-push lifecycle."""

        user = current_user(request)
        if user is None:
            raise HTTPException(status_code=401, detail="请先登录后继续")
        return {
            "schema_version": "1.0",
            "learner_id": user.user_id,
            "review_resource_push": review_push_states.get(
                user.user_id,
                {
                    "status": "idle",
                    "message": "当前没有正在运行或最近完成的异步复习资源派发。",
                },
            ),
        }

    @app.get("/api/v1/resource-match-report")
    async def resource_match_report(
        request: Request,
        limit: int = Query(default=12, ge=1, le=30),
    ) -> dict:
        user = current_user(request)
        if user is None:
            raise HTTPException(status_code=401, detail="请先登录后继续")
        if backend_handoff is None:
            raise HTTPException(status_code=503, detail="资源匹配服务未启用")
        return await asyncio.to_thread(
            backend_handoff.load_resource_match_report,
            user.user_id,
            plan_context=current_plan_context(user.user_id),
            limit=limit,
        )

    @app.get("/api/v1/learning-report")
    async def learning_report(request: Request) -> dict:
        """学习报告：画像总览、掌握度、薄弱点与各难度答题准确率。

        ``difficulty_accuracy.by_difficulty`` 按真实标注难度 1-5 聚合答题
        准确率；未标注难度的题目归入 ``unlabeled``，不推断难度。
        """

        user = current_user(request)
        if user is None:
            raise HTTPException(status_code=401, detail="请先登录后继续")
        if backend_handoff is None:
            raise HTTPException(status_code=503, detail="学习报告服务未启用")
        return await asyncio.to_thread(
            backend_handoff.load_learning_report,
            user.user_id,
        )

    @app.get("/api/v1/task-load-policy")
    async def task_load_policy(request: Request) -> dict:
        user = current_user(request)
        if user is None:
            raise HTTPException(status_code=401, detail="请先登录后继续")
        if backend_handoff is None:
            raise HTTPException(status_code=503, detail="任务负载策略服务未启用")
        queue = await canonical_review_queue(user.user_id)
        return await asyncio.to_thread(
            backend_handoff.load_task_load_policy,
            user.user_id,
            plan_context=current_plan_context(user.user_id),
            review_projection=canonical_review_projection(queue),
            days=7,
        )

    @app.post("/api/v1/resource-recommendations/events")
    async def resource_recommendation_event(
        body: ResourceRecommendationEventRequest,
        request: Request,
    ) -> dict:
        user = current_user(request)
        if user is None:
            raise HTTPException(status_code=401, detail="请先登录后继续")
        if backend_handoff is None:
            raise HTTPException(status_code=503, detail="资源反馈服务未启用")
        try:
            return await asyncio.to_thread(
                backend_handoff.record_resource_recommendation_event,
                user.user_id,
                event_type=body.event_type,
                recommendation_view_id=body.recommendation_view_id,
                recommendation_credential=body.recommendation_credential,
                resource_id=body.resource_id,
                resource_type=body.resource_type,
                kp_ids=body.kp_ids,
            )
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.get("/api/v1/resource-effectiveness")
    async def resource_effectiveness(
        request: Request,
        days: int = Query(default=30),
    ) -> dict:
        user = current_user(request)
        if user is None:
            raise HTTPException(status_code=401, detail="请先登录后继续")
        if backend_handoff is None:
            raise HTTPException(status_code=503, detail="资源反馈服务未启用")
        if days not in {7, 30, 90}:
            raise HTTPException(status_code=422, detail="days 只能是 7、30 或 90")
        return await asyncio.to_thread(
            backend_handoff.load_resource_effectiveness_report,
            user.user_id,
            days=days,
        )

    @app.get("/api/v1/notifications")
    async def notifications(
        request: Request,
        status: str = Query(default="all", pattern="^(all|unread|read|dismissed)$"),
        limit: int = Query(default=50, ge=1, le=100),
    ) -> dict:
        user = current_user(request)
        if user is None:
            raise HTTPException(status_code=401, detail="请先登录后继续")
        if backend_handoff is None:
            raise HTTPException(status_code=503, detail="通知服务未启用")
        return await asyncio.to_thread(
            backend_handoff.list_notifications,
            user.user_id,
            status=status,
            limit=limit,
        )

    @app.patch("/api/v1/notifications/{notification_id}")
    async def update_notification(
        notification_id: str,
        body: NotificationStatusRequest,
        request: Request,
    ) -> dict:
        user = current_user(request)
        if user is None:
            raise HTTPException(status_code=401, detail="请先登录后继续")
        if backend_handoff is None:
            raise HTTPException(status_code=503, detail="通知服务未启用")
        try:
            return await asyncio.to_thread(
                backend_handoff.update_notification_status,
                user.user_id,
                notification_id,
                body.status,
            )
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/api/v1/notification-preferences")
    async def notification_preferences(request: Request) -> dict:
        user = current_user(request)
        if user is None:
            raise HTTPException(status_code=401, detail="请先登录后继续")
        if backend_handoff is None:
            raise HTTPException(status_code=503, detail="通知服务未启用")
        return await asyncio.to_thread(
            backend_handoff.get_notification_preferences, user.user_id
        )

    @app.put("/api/v1/notification-preferences")
    async def save_notification_preferences(
        body: NotificationPreferenceRequest,
        request: Request,
    ) -> dict:
        user = current_user(request)
        if user is None:
            raise HTTPException(status_code=401, detail="请先登录后继续")
        if backend_handoff is None:
            raise HTTPException(status_code=503, detail="通知服务未启用")
        return await asyncio.to_thread(
            backend_handoff.update_notification_preferences,
            user.user_id,
            body.model_dump(exclude_none=True),
        )

    @app.get("/api/v1/interventions")
    async def interventions(
        request: Request,
        limit: int = Query(default=30, ge=1, le=100),
    ) -> dict:
        user = current_user(request)
        if user is None:
            raise HTTPException(status_code=401, detail="请先登录后继续")
        if backend_handoff is None:
            raise HTTPException(status_code=503, detail="主动干预服务未启用")
        return await asyncio.to_thread(
            backend_handoff.list_interventions, user.user_id, limit=limit
        )

    @app.post("/api/v1/interventions/{intervention_id}/feedback")
    async def intervention_feedback(
        intervention_id: int,
        body: InterventionFeedbackRequest,
        request: Request,
    ) -> dict:
        user = current_user(request)
        if user is None:
            raise HTTPException(status_code=401, detail="请先登录后继续")
        if backend_handoff is None:
            raise HTTPException(status_code=503, detail="主动干预服务未启用")
        if body.action == "accept":
            try:
                preview = await asyncio.to_thread(
                    backend_handoff.submit_intervention_feedback,
                    user.user_id,
                    intervention_id,
                    body.action,
                    body.reason,
                    commit=False,
                )
            except LookupError as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from exc
            try:
                applied = apply_accepted_intervention(
                    container.learning_plan_service,
                    user.user_id,
                    preview,
                )
            except Exception:
                applied = {
                    "applied": False,
                    "already_applied": False,
                    "retryable": True,
                    "reason": "安排失败，请稍后重试。",
                    "summary": "",
                    "title": "",
                }
            if not (applied.get("applied") or applied.get("already_applied")):
                return {**preview, "feedback_committed": False, "applied": applied}
            try:
                result = await asyncio.to_thread(
                    backend_handoff.submit_intervention_feedback,
                    user.user_id,
                    intervention_id,
                    body.action,
                    body.reason,
                    commit=True,
                    application_result=applied,
                )
            except LookupError as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from exc
            notification = {
                "created": False,
                "reason": "notification_not_attempted",
            }
            try:
                delivered = await asyncio.to_thread(
                    backend_handoff.create_intervention_applied_notification,
                    user.user_id,
                    intervention_id=result.get("intervention_id"),
                    action=result.get("action") or "学习调整",
                    summary=applied.get("summary") or "",
                )
                notification = {
                    "created": delivered is not None,
                    "notification_id": (
                        delivered.get("notification_id")
                        if isinstance(delivered, dict)
                        else None
                    ),
                    "reason": None if delivered is not None else "skipped_by_preferences",
                }
            except Exception:
                # 任务已经成功落地且接受状态已提交；通知是独立的 best-effort
                # 副作用，失败不得把真实成功结果改报为“安排失败”。
                notification = {
                    "created": False,
                    "notification_id": None,
                    "reason": "notification_failed",
                }
            return {
                **result,
                "feedback_committed": True,
                "applied": applied,
                "notification": notification,
            }
        try:
            result = await asyncio.to_thread(
                backend_handoff.submit_intervention_feedback,
                user.user_id,
                intervention_id,
                body.action,
                body.reason,
            )
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return {
            **result,
            "feedback_committed": True,
            "applied": {
            "applied": False,
            "reason": "仅在接受建议时安排。",
            "summary": "",
            "title": "",
            },
        }

    @app.get("/api/v1/plan-reviews")
    async def plan_reviews(
        request: Request,
        limit: int = Query(default=30, ge=1, le=100),
    ) -> dict:
        user = current_user(request)
        if user is None:
            raise HTTPException(status_code=401, detail="请先登录后继续")
        if backend_handoff is None:
            raise HTTPException(status_code=503, detail="规划复盘服务未启用")
        return await asyncio.to_thread(
            backend_handoff.list_plan_reviews, user.user_id, limit=limit
        )

    @app.post("/api/v1/plan-reviews/run")
    async def trigger_plan_review(request: Request) -> dict:
        user = current_user(request)
        if user is None:
            raise HTTPException(status_code=401, detail="请先登录后继续")
        if backend_handoff is None:
            raise HTTPException(status_code=503, detail="规划复盘服务未启用")
        queue = await canonical_review_queue(user.user_id)
        return await asyncio.to_thread(
            backend_handoff.run_plan_review,
            user.user_id,
            plan_context=current_plan_context(user.user_id),
            trigger_type="manual",
            review_projection=canonical_review_projection(queue),
        )

    @app.post("/api/v1/plan-reviews/{review_id}/decision")
    async def decide_plan_review(
        review_id: str,
        body: PlanReviewDecisionRequest,
        request: Request,
    ) -> dict:
        user = current_user(request)
        if user is None:
            raise HTTPException(status_code=401, detail="请先登录后继续")
        if backend_handoff is None:
            raise HTTPException(status_code=503, detail="规划复盘服务未启用")
        try:
            result = await asyncio.to_thread(
                backend_handoff.decide_plan_review,
                user.user_id,
                review_id,
                body.decision,
            )
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

        # 落地执行端：接受调整时按建议的 operation 落地。
        applied: dict[str, Any] = {
            "applied": False,
            "reason": "仅在接受调整时执行。",
            "summary": "",
        }
        if body.decision == "accept":
            try:
                proposal = result.get("proposal") or {}
                operation = str(proposal.get("operation") or "").strip()
                target_layer = str(proposal.get("target_layer") or "").strip()
                replan_operation = operation in {
                    "replan_for_low_completion",
                    "add_review_window",
                    "slow_progress",
                } and target_layer == "short_term"
                synchronous_daily_operation = (
                    target_layer == "daily_task" and operation == "reduce_load"
                )
                if synchronous_daily_operation and result.get("decision_replayed"):
                    return {
                        **result,
                        "applied": {
                            "applied": False,
                            "already_applied": True,
                            "reason": "该调整已接受并处理，无需重复执行。",
                            "summary": "",
                        },
                    }
                if replan_operation and result.get("execution_status") == "succeeded":
                    return {**result, "applied": applied}
                execution_id = f"PLAN_REPLAN_{review_id}_{uuid4().hex[:10]}"
                already_claimed = False
                if replan_operation:
                    claimed = await asyncio.to_thread(
                        backend_handoff.claim_plan_review_execution,
                        user.user_id,
                        review_id,
                        execution_id=execution_id,
                    )
                    claimed_execution = claimed.get("execution") or {}
                    claimed_id = str(claimed_execution.get("execution_id") or "")
                    claimed_status = str(claimed.get("execution_status") or "")
                    if claimed_id != execution_id:
                        return {
                            **claimed,
                            "applied": {
                                "applied": claimed_status in {"queued", "running", "succeeded"},
                                "replan_started": claimed_status in {"queued", "running"},
                                "execution_status": claimed_status,
                                "reason": "调整已在执行中。" if claimed_status in {"queued", "running"} else "",
                                "summary": "短期计划级联调整已存在，无需重复启动。",
                            },
                        }
                    result = claimed
                    already_claimed = True
                    await notify_plan_review_lifecycle(
                        user.user_id,
                        review_id=review_id,
                        status="queued",
                    )
                applied = apply_accepted_plan_review(
                    container.learning_plan_service,
                    user.user_id,
                    result,
                    replan_starter=(
                        make_plan_replan_starter(
                            user.user_id,
                            result,
                            execution_id=execution_id,
                            already_claimed=already_claimed,
                        )
                        if replan_operation
                        else None
                    ),
                )
                # Asynchronous short-term replans publish their terminal
                # notification from the background coordinator.  Synchronous
                # daily mutations can still acknowledge immediately.
                if applied.get("applied") and not applied.get("replan_started") and (
                    str((result.get("proposal") or {}).get("operation") or "")
                    not in {"replan_for_low_completion", "add_review_window", "slow_progress"}
                ):
                    try:
                        backend_handoff.create_plan_review_applied_notification(
                            user.user_id,
                            review_id=review_id,
                            summary=applied.get("summary") or "",
                            dedupe_key=f"plan-review-lifecycle:{review_id}",
                        )
                    except Exception:
                        # 通知是 best-effort 副作用，不能把已成功的同步计划
                        # 写入改判为失败。
                        pass
            except Exception:
                # 落地失败不影响决策保存，前端仍会收到反馈结果。
                applied = {
                    "applied": False,
                    "reason": "调整执行失败，请稍后重试。",
                    "summary": "",
                }
        return {**result, "applied": applied}

    async def learning_path_page(
        request: Request,
        parent_id: str | None,
        offset: int,
        limit: int,
    ) -> dict:
        user = current_user(request)
        if user is None:
            raise HTTPException(status_code=401, detail="请先登录后继续")
        plans = container.review_card_use_case.plan_repository.get_current(user.user_id)
        if plans is None or plans.long_term_plan is None:
            return {
                "schema_version": "1.0",
                "learner_id": user.user_id,
                "plan_ref": None,
                "parent_id": parent_id,
                "parent_type": None,
                "current_node_id": None,
                "nodes": [],
                "offset": offset,
                "limit": limit,
                "total": 0,
                "has_more": False,
                "availability": "requires_long_term_plan",
                "message": "请先完成长期学习规划，再生成阶段、教材和知识点路径。",
            }
        behavior = (
            await asyncio.to_thread(backend_handoff.load_learning_context, user.user_id)
            if backend_handoff is not None
            else {}
        )
        loader = (
            container.knowledge_backend.map.learning_path_book_knowledge_points
            if container.knowledge_backend is not None
            else None
        )
        try:
            # 当前周期教材选择以最新短期计划为准（读时同步），避免展示层
            # 沿用长期计划内嵌的过期选择（长期副本仅在阶段晋级时推进）。
            current_selection = (
                plans.short_term_plan.textbook_selection
                if plans.short_term_plan is not None
                else None
            )
            page = await asyncio.to_thread(
                LearningPathProjectionService(loader).page,
                learner_id=user.user_id,
                plan=plans.long_term_plan,
                parent_id=parent_id,
                mastery_rows=behavior.get("mastery") or [],
                offset=offset,
                limit=limit,
                selection=current_selection,
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return page.model_dump(mode="json")

    @app.get("/api/v1/learning-path")
    async def get_learning_path(
        request: Request,
        parent_id: str | None = None,
        offset: int = Query(default=0, ge=0),
        limit: int = Query(default=100, ge=1, le=500),
    ) -> dict:
        return await learning_path_page(request, parent_id, offset, limit)

    @app.get("/api/v1/learning-path/nodes")
    async def get_learning_path_nodes(
        request: Request,
        parent_id: str = Query(min_length=1),
        offset: int = Query(default=0, ge=0),
        limit: int = Query(default=100, ge=1, le=500),
    ) -> dict:
        return await learning_path_page(request, parent_id, offset, limit)

    def require_workshop_runtime():
        if backend_handoff is None:
            raise HTTPException(status_code=503, detail="学习工坊持久化服务未启用")
        return backend_handoff

    def _practice_difficulty_coverage(
        user_id: str | None = None,
    ) -> tuple[bool, list[int]]:
        """Report difficulty support from the formal bank using real labels only.

        Returns (has_any_real_difficulty_label, sorted available levels). A bank
        without difficulty annotations reports (False, []) so the UI can hide
        difficulty controls instead of presenting unusable filters.

        When a learner has manually tagged questions, their levels are merged
        into the available set so the filter remains usable even if the bank
        itself carries no annotations.
        """
        levels: set[int] = set()
        backend = container.knowledge_backend
        if backend is not None and getattr(backend, "map", None) is not None:
            store = backend.map
            try:
                store.ensure_questions()
            except Exception:
                store = None
            if store is not None:
                for questions in store.questions_by_kp.values():
                    for question in questions:
                        parsed = parse_difficulty(
                            question.get("difficulty", question.get("难度"))
                        )
                        if parsed is not None:
                            levels.add(parsed)
        if user_id and backend_handoff is not None:
            try:
                user_tags = backend_handoff.load_user_question_difficulty_tags(
                    user_id
                )
                levels.update(int(level) for level in user_tags.values())
            except Exception:
                pass
        return bool(levels), sorted(levels)

    @app.get("/api/v1/workshop")
    async def workshop_overview(request: Request) -> dict:
        current_user(request)
        return require_workshop_runtime().workshop_overview()

    def select_formal_practice_question(
        *,
        query: str,
        kp_id: str | None,
        mode: str,
        attempted_question_ids: set[str],
        preferred_kp_ids: list[str] | None = None,
        difficulty: int | None = None,
        difficulty_min: int | None = None,
        difficulty_max: int | None = None,
        exclude_question_id: str | None = None,
        user_difficulty_tags: dict[str, int] | None = None,
    ) -> dict | None:
        backend = container.knowledge_backend
        if backend is None:
            return None
        store = backend.map
        store.ensure_hierarchy()
        store.ensure_questions()
        selected_kps: list[dict] = []
        if kp_id:
            kp = store.kps.get(str(kp_id))
            if kp is not None:
                selected_kps.append({"kp_id": str(kp_id), "kp": kp})
        if not selected_kps:
            for preferred_kp_id in preferred_kp_ids or []:
                normalized_kp_id = str(preferred_kp_id or "").strip()
                kp = store.kps.get(normalized_kp_id)
                if kp is not None and not any(
                    item["kp_id"] == normalized_kp_id for item in selected_kps
                ):
                    selected_kps.append({"kp_id": normalized_kp_id, "kp": kp})
        if not selected_kps:
            selected_kps = store.resolve_topic(query, limit=8)

        candidates: list[dict] = []
        candidate_source_rank: dict[str, int] = {}
        seen: set[str] = set()
        for source_rank, match in enumerate(selected_kps, 1):
            matched_kp_id = str(match.get("kp_id") or "")
            for question in store.questions_by_kp.get(matched_kp_id, ()):
                question_id = str(question.get("question_id") or question.get("题目id") or "")
                if not question_id or question_id in seen:
                    continue
                seen.add(question_id)
                if not _practice_mode_matches(
                    question.get("question_type") or question.get("题型"), mode
                ):
                    continue
                payload = _formal_question_payload(
                    question,
                    {
                        str(value): str(
                            (store.kps.get(str(value)) or {}).get("kp_lv3")
                            or (store.kps.get(str(value)) or {}).get("other_name")
                            or value
                        )
                        for value in question.get("kp_ids") or []
                    },
                )
                if payload["standard_answer"] and payload["kp_ids"]:
                    candidates.append(payload)
                    candidate_source_rank[question_id] = source_rank

        if not kp_id:
            # A broad credential goal may not resolve to one KP name. The source
            # is still the complete formal bank; choose a linked question of the
            # requested type instead of reporting that the bank is empty. An
            # explicit KP target must fail closed rather than leak another KP.
            for linked_kp_id, questions in store.questions_by_kp.items():
                for question in questions:
                    question_id = str(question.get("question_id") or question.get("题目id") or "")
                    if not question_id or question_id in seen:
                        continue
                    seen.add(question_id)
                    if not _practice_mode_matches(
                        question.get("question_type") or question.get("题型"), mode
                    ):
                        continue
                    payload = _formal_question_payload(
                        question,
                        {
                            str(value): str(
                                (store.kps.get(str(value)) or {}).get("kp_lv3")
                                or (store.kps.get(str(value)) or {}).get("other_name")
                                or value
                            )
                            for value in question.get("kp_ids") or []
                        },
                    )
                    if payload["standard_answer"] and payload["kp_ids"]:
                        candidates.append(payload)
                        candidate_source_rank[question_id] = len(selected_kps) + 1

        # A learner-tagged difficulty counts as a real label for that user:
        # a question the learner marked (even without a bank annotation) is
        # presented with the tagged level and participates in strict matching.
        if user_difficulty_tags:
            for candidate in candidates:
                tagged = user_difficulty_tags.get(candidate["question_id"])
                if tagged is not None and candidate.get("difficulty") is None:
                    candidate["difficulty"] = tagged
                    candidate["difficulty_source"] = "user_tagged"

        difficulty_requested = any(
            value is not None
            for value in (difficulty, difficulty_min, difficulty_max)
        )
        if difficulty_requested:
            # Strict matching on real labels only: an unlabelled question never
            # satisfies a difficulty filter (no inference, no default).
            candidates = [
                candidate
                for candidate in candidates
                if _candidate_matches_difficulty_filter(
                    candidate,
                    difficulty=difficulty,
                    difficulty_min=difficulty_min,
                    difficulty_max=difficulty_max,
                )
            ]
        if not candidates:
            return None

        candidate_by_id = {
            candidate["question_id"]: candidate for candidate in candidates
        }
        bridge_hits = [
            (question_id, 1.0 / max(candidate_source_rank[question_id], 1))
            for question_id in sorted(
                candidate_by_id,
                key=lambda value: (candidate_source_rank[value], value),
            )
        ]
        bm25_scores = _BM25(
            [_tokens(candidate_by_id[question_id]["stem"]) for question_id in sorted(candidate_by_id)]
        ).scores(_tokens(query))
        bm25_hits = sorted(
            (
                (question_id, float(score))
                for question_id, score in zip(sorted(candidate_by_id), bm25_scores)
                if score > 0
            ),
            key=lambda item: (-item[1], item[0]),
        )
        fusion = reciprocal_rank_fusion(
            {"bridge": bridge_hits, "bm25": bm25_hits}
        )
        candidates.sort(
            key=lambda candidate: (
                -fusion[candidate["question_id"]].score,
                candidate["question_id"],
            )
        )
        if preferred_kp_ids:
            priority = {
                str(value): index
                for index, value in enumerate(preferred_kp_ids)
                if str(value).strip()
            }
            candidates.sort(
                key=lambda question: min(
                    (
                        priority[value]
                        for value in question.get("kp_ids") or []
                        if value in priority
                    ),
                    default=len(priority),
                )
            )
        unattempted = [
            question
            for question in candidates
            if question["question_id"] not in attempted_question_ids
        ]
        if unattempted:
            return unattempted[0]
        # All candidates were attempted. When the caller explicitly asks to move
        # on (exclude_question_id), report that the bank is exhausted instead of
        # re-serving the same question forever. A fresh entry (no exclusion)
        # may still review the first candidate.
        if exclude_question_id:
            return None
        return candidates[0]

    @app.get("/api/v1/workshop/practice/next")
    async def next_workshop_practice_question(
        request: Request,
        kp_id: str | None = Query(default=None, min_length=1, max_length=120),
        topic: str | None = Query(default=None, min_length=1, max_length=500),
        scope: str = Query(default="public", pattern="^(public|user|all)$"),
        mode: str = Query(default="objective", pattern="^(all|objective|case)$"),
        difficulty: int | None = Query(default=None, ge=1, le=5),
        difficulty_min: int | None = Query(default=None, ge=1, le=5),
        difficulty_max: int | None = Query(default=None, ge=1, le=5),
        exclude_question_id: str | None = Query(default=None, min_length=1, max_length=120),
    ) -> dict:
        user = current_user(request)
        runtime = require_workshop_runtime()
        if (
            difficulty is not None
            and (difficulty_min is not None or difficulty_max is not None)
        ):
            raise HTTPException(
                status_code=422,
                detail="difficulty cannot be combined with difficulty_min/difficulty_max",
            )
        if (
            difficulty_min is not None
            and difficulty_max is not None
            and difficulty_min > difficulty_max
        ):
            raise HTTPException(
                status_code=422,
                detail="difficulty_min must not exceed difficulty_max",
            )
        difficulty_available, available_difficulties = _practice_difficulty_coverage(
            user.user_id
        )
        user_difficulty_tags: dict[str, int] = {}
        try:
            user_difficulty_tags = runtime.load_user_question_difficulty_tags(
                user.user_id
            )
        except Exception:
            user_difficulty_tags = {}
        difficulty_kwargs = {
            "difficulty": difficulty,
            "difficulty_min": difficulty_min,
            "difficulty_max": difficulty_max,
        }

        def _with_difficulty_meta(payload: dict) -> dict:
            payload["difficulty_available"] = difficulty_available
            payload["available_difficulties"] = available_difficulties
            return payload

        if scope in {"user", "all"}:
            personal = await asyncio.to_thread(
                runtime.issue_personal_practice,
                user.user_id,
                kp_id=kp_id,
                mode=mode,
                **difficulty_kwargs,
            )
            if personal.get("available") or scope == "user":
                return _with_difficulty_meta(
                    _sanitize_practice_question_labels(personal)
                )

        context: dict = {}
        try:
            context = await asyncio.to_thread(runtime.load_learning_context, user.user_id)
        except Exception:
            context = {}
        selection_context: dict = {}
        load_selection_context = getattr(runtime, "load_practice_selection_context", None)
        if callable(load_selection_context):
            try:
                selection_context = await asyncio.to_thread(
                    load_selection_context, user.user_id
                )
            except Exception:
                selection_context = {}
        attempted_ids = {
            str(item.get("question_id") or "")
            for item in context.get("question_attempt") or []
            if isinstance(item, dict) and item.get("question_id")
        }
        attempted_ids.update(
            str(question_id)
            for question_id, history in (
                selection_context.get("attempt_history") or {}
            ).items()
            if str(question_id).strip()
            and isinstance(history, dict)
            and int(history.get("attempt_count") or 0) > 0
        )
        if exclude_question_id:
            attempted_ids.add(str(exclude_question_id).strip())

        difficulty_requested = any(
            value is not None
            for value in (difficulty, difficulty_min, difficulty_max)
        )
        has_explicit_target = bool(kp_id or str(topic or "").strip())
        latest_claim = selection_context.get("latest_active_claim")
        resume_claim = getattr(runtime, "resume_formal_practice_claim", None)
        resume_blocked_by_exclude = bool(
            exclude_question_id
            and isinstance(latest_claim, dict)
            and str(latest_claim.get("question_id") or "").strip()
            == str(exclude_question_id).strip()
        )
        if (
            not has_explicit_target
            and not difficulty_requested
            and not resume_blocked_by_exclude
            and isinstance(latest_claim, dict)
            and callable(resume_claim)
        ):
            question_id = str(latest_claim.get("question_id") or "").strip()
            request_id = str(latest_claim.get("request_id") or "").strip()
            if question_id and request_id:
                try:
                    resumed = await asyncio.to_thread(
                        resume_claim,
                        user.user_id,
                        question_id=question_id,
                        request_id=request_id,
                    )
                except Exception:
                    resumed = None
                resumed_question = resumed.get("question") if isinstance(resumed, dict) else None
                if (
                    isinstance(resumed_question, dict)
                    and _practice_mode_matches(resumed_question.get("question_type"), mode)
                ):
                    resumed["selection"] = {
                        "strategy": "current_learning_adaptive_v1",
                        "reason": "active_claim",
                    }
                    return _with_difficulty_meta(
                        _sanitize_practice_question_labels(resumed)
                    )

        current_task_kp_ids = [
            str(value).strip()
            for value in selection_context.get("current_task_kp_ids") or []
            if str(value).strip()
        ]
        due_review_kp_ids = [
            str(value).strip()
            for value in selection_context.get("due_review_kp_ids") or []
            if str(value).strip()
        ]
        mastery = selection_context.get("mastery") or {}
        low_mastery_kp_ids = sorted(
            (
                str(value).strip()
                for value in mastery
                if str(value).strip()
            ),
            key=lambda value: float((mastery.get(value) or {}).get("mastery") or 0),
        )
        preferred_kp_ids = list(dict.fromkeys(
            current_task_kp_ids + due_review_kp_ids + low_mastery_kp_ids
        ))
        query = str(topic or "").strip() or _profile_practice_query(context)
        try:
            candidate = await asyncio.to_thread(
                select_formal_practice_question,
                query=query,
                kp_id=kp_id,
                mode=mode,
                attempted_question_ids=attempted_ids,
                preferred_kp_ids=preferred_kp_ids if not has_explicit_target else None,
                difficulty=difficulty,
                difficulty_min=difficulty_min,
                difficulty_max=difficulty_max,
                exclude_question_id=exclude_question_id,
                user_difficulty_tags=user_difficulty_tags,
            )
            if candidate is not None:
                issued = await asyncio.to_thread(
                    runtime.issue_formal_practice,
                    user.user_id,
                    candidate,
                )
                candidate_kp_ids = set(candidate.get("kp_ids") or [])
                if candidate_kp_ids.intersection(current_task_kp_ids):
                    reason = "current_task"
                elif candidate_kp_ids.intersection(due_review_kp_ids):
                    reason = "due_review"
                elif candidate_kp_ids.intersection(low_mastery_kp_ids):
                    reason = "low_mastery"
                else:
                    reason = "formal_bank"
                issued["selection"] = {
                    "strategy": "current_learning_adaptive_v1",
                    "reason": reason,
                }
                issued_question = issued.get("question")
                if isinstance(issued_question, dict) and issued_question.get(
                    "difficulty"
                ) is None:
                    issued_question["difficulty"] = candidate.get("difficulty")
                    issued_question["difficulty_source"] = candidate.get(
                        "difficulty_source"
                    )
                return _with_difficulty_meta(
                    _sanitize_practice_question_labels(issued)
                )
        except Exception:
            # Keep projected formal questions usable while the read-only bank
            # is temporarily unavailable; never reinterpret this as an empty bank.
            pass
        cached = await asyncio.to_thread(
            runtime.issue_cached_public_practice,
            user.user_id,
            kp_id=kp_id,
            mode=mode,
            exclude_question_id=exclude_question_id,
            **difficulty_kwargs,
        )
        return _with_difficulty_meta(_sanitize_practice_question_labels(cached))

    @app.post("/api/v1/workshop/practice/skip")
    async def skip_workshop_practice_question(request: Request) -> dict:
        """Consume the active one-use claim of the current question.

        Removes the claim so the question is no longer resumed as an
        in-progress exercise; no grade/mistake record is written.
        """
        user = current_user(request)
        runtime = require_workshop_runtime()
        try:
            body = await request.json()
        except Exception:
            body = {}
        question_id = str(body.get("question_id") or "").strip()
        request_id = str(body.get("request_id") or "").strip()
        if not question_id or not request_id:
            raise HTTPException(
                status_code=422,
                detail="question_id and request_id are required",
            )
        return await asyncio.to_thread(
            runtime.mark_formal_practice_skipped,
            user.user_id,
            question_id=question_id,
            request_id=request_id,
        )

    @app.put("/api/v1/workshop/practice/difficulty-tag")
    async def save_workshop_practice_difficulty_tag(
        payload: DifficultyTagRequest, request: Request
    ) -> dict:
        """Save the learner's manual difficulty label for a question.

        The tag is personal: it enables difficulty filtering for questions
        without a real bank annotation and never overwrites the shared label.
        """
        user = current_user(request)
        runtime = require_workshop_runtime()
        return await asyncio.to_thread(
            runtime.save_user_question_difficulty_tag,
            user.user_id,
            question_id=payload.question_id,
            difficulty=payload.difficulty,
        )

    @app.get("/api/v1/workshop/knowledge-cards")
    async def list_workshop_knowledge_cards(
        request: Request,
        offset: int = Query(default=0, ge=0),
        limit: int = Query(default=50, ge=1, le=200),
    ) -> dict:
        user = current_user(request)
        return await asyncio.to_thread(
            require_workshop_runtime().list_knowledge_cards,
            user.user_id,
            offset=offset,
            limit=limit,
        )

    @app.get("/api/v1/workshop/knowledge-cards/{card_id}")
    async def get_workshop_knowledge_card(card_id: str, request: Request) -> dict:
        user = current_user(request)
        card = await asyncio.to_thread(
            require_workshop_runtime().get_knowledge_card,
            user.user_id,
            card_id,
        )
        if card is None:
            raise HTTPException(status_code=404, detail="知识卡不存在")
        return card

    @app.post("/api/v1/workshop/knowledge-cards/resolve")
    async def resolve_workshop_knowledge_card(
        payload: KnowledgeCardResolveRequest, request: Request
    ) -> dict:
        user = current_user(request)
        if container.knowledge_backend is None or container.question_retrieval_tool is None:
            raise HTTPException(status_code=503, detail="正式知识仓库未启用")
        try:
            bundle = await WorkshopKnowledgeService(
                container.knowledge_backend,
                container.question_retrieval_tool,
            ).resolve(payload.kp_id, question_limit=payload.question_limit)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        card = await asyncio.to_thread(
            require_workshop_runtime().save_knowledge_card,
            user.user_id,
            kp_id=str(bundle.knowledge_point.get("kp_id") or payload.kp_id),
            title=str(bundle.knowledge_point.get("title") or payload.kp_id),
            resource_bundle=bundle.model_dump(mode="json"),
            source_execution_id=payload.source_execution_id,
        )
        return card

    @app.get("/api/v1/workshop/papers")
    async def list_workshop_papers(
        request: Request,
        offset: int = Query(default=0, ge=0),
        limit: int = Query(default=50, ge=1, le=200),
    ) -> dict:
        user = current_user(request)
        return await asyncio.to_thread(
            require_workshop_runtime().list_papers,
            user.user_id,
            offset=offset,
            limit=limit,
        )

    @app.get("/api/v1/workshop/papers/{paper_id}")
    async def get_workshop_paper(paper_id: str, request: Request) -> dict:
        user = current_user(request)
        try:
            return await asyncio.to_thread(
                require_workshop_runtime().get_paper, user.user_id, paper_id
            )
        except ValueError as exc:
            raise HTTPException(status_code=404, detail="试卷不存在") from exc

    @app.put("/api/v1/workshop/papers/{paper_id}/answers")
    async def save_workshop_paper_answers(
        paper_id: str, payload: WorkshopPaperAnswersRequest, request: Request
    ) -> dict:
        user = current_user(request)
        try:
            return await asyncio.to_thread(
                require_workshop_runtime().save_paper_answers,
                user.user_id,
                paper_id,
                payload.answers,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/api/v1/workshop/papers/{paper_id}/timer/pause")
    async def pause_workshop_paper_timer(paper_id: str, request: Request) -> dict:
        user = current_user(request)
        try:
            return await asyncio.to_thread(
                require_workshop_runtime().set_paper_timer_paused,
                user.user_id,
                paper_id,
                paused=True,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/api/v1/workshop/papers/{paper_id}/timer/resume")
    async def resume_workshop_paper_timer(paper_id: str, request: Request) -> dict:
        user = current_user(request)
        try:
            return await asyncio.to_thread(
                require_workshop_runtime().set_paper_timer_paused,
                user.user_id,
                paper_id,
                paused=False,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/api/v1/workshop/papers/{paper_id}/submit")
    async def submit_workshop_paper(
        paper_id: str, payload: WorkshopPaperSubmitRequest, request: Request
    ) -> dict:
        user = current_user(request)
        try:
            return await asyncio.to_thread(
                require_workshop_runtime().submit_paper,
                user.user_id,
                paper_id,
                payload.request_id,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.get("/api/v1/agent-data-capabilities")
    async def agent_data_capabilities(request: Request) -> dict:
        current_user(request)
        return container.review_card_use_case.data_permission_gateway.manifest()

    @app.get("/api/v1/dashboard/home")
    async def dashboard_home(request: Request) -> dict:
        user = current_user(request)
        behavior = {}
        learning_activity = {"recent_activities": []}
        checkin_status = {
            "checked_in_today": False,
            "streak": 0,
            "total_checkins": 0,
            "calendar_days": [],
        }

        # 第一批：互不依赖的读取/刷新并行执行（原实现为串行 await，
        # 每步叠加约 1s；load_learning_context 本身已有 TTL 缓存）。
        batch: dict[str, asyncio.Task] = {}
        if backend_handoff is not None:
            batch["behavior"] = asyncio.create_task(asyncio.to_thread(
                backend_handoff.load_learning_context, user.user_id
            ))
            batch["learning_activity"] = asyncio.create_task(asyncio.to_thread(
                backend_handoff.load_learning_activity_summary,
                user.user_id, days=30, recent_limit=20,
            ))
            batch["checkin_status"] = asyncio.create_task(asyncio.to_thread(
                backend_handoff.get_checkin_status, user.user_id, days=7
            ))
        def refresh_then_repair_resources() -> dict[str, Any]:
            # Both operations can replace the same plan head. Keep them ordered
            # while still running in parallel with independent dashboard reads.
            timer = container.daily_task_refresh_service.ensure_current(user.user_id)
            try:
                container.learning_plan_service.ensure_executable_daily_resources(
                    user.user_id
                )
            except Exception:
                # Legacy resource repair must not make the home portal unavailable.
                pass
            if timer.get("refreshed") is True:
                notify_daily_task_refreshed(user.user_id)
            return timer

        batch["daily_task_timer"] = asyncio.create_task(
            asyncio.to_thread(refresh_then_repair_resources)
        )
        batch_results = await asyncio.gather(*batch.values(), return_exceptions=True)

        def _first_ok(key: str, default):
            if key not in batch:
                return default
            result = batch_results[list(batch.keys()).index(key)]
            return default if isinstance(result, BaseException) else result

        behavior = _first_ok("behavior", {})
        learning_activity = _first_ok("learning_activity", {"recent_activities": []})
        checkin_status = _first_ok(
            "checkin_status",
            {"checked_in_today": False, "streak": 0, "total_checkins": 0, "calendar_days": []},
        )
        daily_task_timer = _first_ok("daily_task_timer", {})

        coordinator = container.daily_task_execution_coordinator
        if coordinator is not None:
            try:
                await asyncio.to_thread(coordinator.dispatch_pending, user.user_id, 20)
                await asyncio.to_thread(coordinator.reconcile_parent_status, user.user_id)
            except Exception:
                # Dashboard content remains available while cross-store sync recovers.
                pass
        plans = container.review_card_use_case.plan_repository.get_current(user.user_id)
        queue = container.review_service.get_queue(user.user_id, limit=12)
        sessions = container.review_card_use_case.conversation_repository.list_sessions(
            user.user_id
        )
        today_tasks: list[dict] = []
        current_learning_task: dict | None = None
        if plans is not None and plans.learning_task is not None:
            task = plans.learning_task
            if task.status != "completed":
                task_progress: dict = {}
                if coordinator is not None:
                    try:
                        load_progress = getattr(coordinator, "load_current_progress", None)
                        if callable(load_progress):
                            task_progress = await asyncio.to_thread(
                                load_progress, user.user_id
                            )
                        elif backend_handoff is not None:
                            task_progress = await asyncio.to_thread(
                                backend_handoff.load_daily_task_progress,
                                user.user_id,
                                {
                                    "task_id": task.task_id,
                                    "host_task_id": task.task_id,
                                    "host_task_version": task.version,
                                    "items": [
                                        {
                                            "task_item_id": item.task_item_id,
                                            "item_type": item.item_type,
                                            "kp_id": item.kp_id,
                                            "required_question_count": item.required_question_count,
                                        }
                                        for item in task.items
                                    ],
                                },
                            )
                    except Exception:
                        task_progress = {}
                progress_items = {
                    str(item.get("task_item_id") or ""): item
                    for item in task_progress.get("items") or []
                    if isinstance(item, dict) and item.get("task_item_id")
                }
                completed_items = int(task_progress.get("completed_items") or sum(
                    1
                    for item in progress_items.values()
                    if str(item.get("status") or "").lower() == "completed"
                ))
                total_items = int(task_progress.get("total_items") or 0)
                resolved_points: list[dict] = []
                seen_kp_ids: set[str] = set()
                task_chapter_text = str(task.learning_chapter or "").strip()
                normalized_task_chapter = re.sub(r"[《》\s]", "", task_chapter_text)
                if container.knowledge_backend is not None:
                    queries = list(task.focus_knowledge_points) or [task.task_content]
                    scoped_queries = queries[:5]

                    def _resolve(query: str):
                        try:
                            return container.knowledge_backend.map.resolve_topic(
                                str(query), limit=10
                            )
                        except Exception:
                            return []

                    matches_by_query = await asyncio.gather(
                        *[
                            asyncio.to_thread(_resolve, query)
                            for query in scoped_queries
                        ]
                    )
                    for query, matches in zip(scoped_queries, matches_by_query):
                        def contextual_rank(match: dict) -> tuple[int, int, int, float]:
                            kp = dict(match.get("kp") or {})
                            book = re.sub(r"[《》\s]", "", str(kp.get("kp_lv1") or ""))
                            chapter = re.sub(
                                r"[《》\s]|第[一二三四五六七八九十百0-9]+[章节篇]",
                                "",
                                str(kp.get("kp_lv2") or ""),
                            )
                            name = re.sub(r"\s", "", str(match.get("name") or ""))
                            normalized_query = re.sub(r"\s", "", str(query))
                            return (
                                int(bool(book and book in normalized_task_chapter)),
                                int(bool(chapter and chapter in normalized_task_chapter)),
                                int(name == normalized_query),
                                float(match.get("score") or 0),
                            )

                        ordered_matches = sorted(matches, key=contextual_rank, reverse=True)
                        selected_matches = ordered_matches[:1] if task.focus_knowledge_points else ordered_matches[:3]
                        for match in selected_matches:
                            kp_id = str(match.get("kp_id") or "").strip()
                            if not kp_id or kp_id in seen_kp_ids:
                                continue
                            seen_kp_ids.add(kp_id)
                            kp = dict(match.get("kp") or {})
                            resolved_points.append(
                                {
                                    "kp_id": kp_id,
                                    "title": str(match.get("name") or kp_id),
                                    "book": str(kp.get("kp_lv1") or ""),
                                    "chapter": str(kp.get("kp_lv2") or ""),
                                    "action": {
                                        "action_type": "navigate",
                                        "label": "学习知识卡",
                                        "destination": "workshop.knowledge_card",
                                        "params": {"kp_id": kp_id},
                                    },
                                }
                            )
                chapter_match = re.match(r"^《([^》]+)》\s*(.*)$", task_chapter_text)
                if chapter_match:
                    resolved_book = chapter_match.group(1).strip()
                    resolved_chapter = chapter_match.group(2).strip()
                else:
                    resolved_book = ""
                    resolved_chapter = task_chapter_text
                if not resolved_chapter:
                    resolved_chapter = next(
                        (item["chapter"] for item in resolved_points if item["chapter"]), ""
                    )
                if not resolved_book:
                    resolved_book = next(
                        (item["book"] for item in resolved_points if item["book"]), ""
                    )
                task_projection = {
                    "task_id": task.task_id,
                    "title": task.task_content,
                    "description": task.completion_criteria,
                    "duration": f"{_format_minutes(task.estimated_minutes)} 分钟",
                    "estimated_minutes": task.estimated_minutes,
                    "expected_output": task.expected_output,
                    "completion_criteria": task.completion_criteria,
                    "status": task.status,
                    "refresh_started_at": task.refresh_started_at,
                    "refresh_due_at": task.refresh_due_at,
                    "source": "daily_task",
                    "scheduling": (
                        {
                            "policy_id": task.daily_task_schedule.policy_id,
                            "target_minutes": task.daily_task_schedule.target_minutes,
                            "summary": task.daily_task_schedule.explanation,
                            "selected": [
                                {
                                    "knowledge_point_name": item.knowledge_point_name,
                                    "kp_id": item.kp_id,
                                    "task_kind": item.task_kind,
                                    "reason": item.reason,
                                }
                                for item in task.daily_task_schedule.selected
                            ],
                            "deferred": [
                                {
                                    "knowledge_point_name": item.knowledge_point_name,
                                    "kp_id": item.kp_id,
                                    "task_kind": item.task_kind,
                                    "reason": item.reason,
                                }
                                for item in task.daily_task_schedule.deferred
                            ],
                            "blocked_count": len(task.daily_task_schedule.blocked),
                        }
                        if task.daily_task_schedule is not None
                        else None
                    ),
                    "progress": {
                        "completed": completed_items,
                        "total": total_items,
                        "rate": round(completed_items / total_items, 4)
                        if total_items
                        else 0.0,
                    },
                    "items": [
                        {
                            "task_item_id": item.task_item_id,
                            "item_type": item.item_type,
                            "title": item.title,
                            "estimated_minutes": item.estimated_minutes,
                            "kp_id": item.kp_id,
                            "kp_name": item.knowledge_point_name,
                            "resource_ref": dict(item.resource_ref),
                            "completion_policy": dict(item.completion_policy),
                            "status": str(
                                progress_items.get(item.task_item_id, {}).get("status")
                                or "pending"
                            ),
                            "progress": {
                                key: value
                                for key, value in dict(
                                    progress_items.get(item.task_item_id, {}).get("progress")
                                    or {}
                                ).items()
                                if key in {
                                    "reviewed_questions",
                                    "required_questions",
                                    "coverage",
                                    "coverage_rate",
                                    "video_coverage",
                                    "active_seconds",
                                }
                            },
                            "action": (
                                {
                                    "action_type": "watch_video",
                                    "destination": "workshop.knowledge_video",
                                    "params": {
                                        "taskItemId": item.task_item_id,
                                        "video": dict(item.resource_ref),
                                    },
                                }
                                if item.item_type == "video_section"
                                else {
                                    "action_type": "practice",
                                    "destination": "workshop.practice",
                                    "params": {
                                        "taskItemId": item.task_item_id,
                                        **(
                                            {
                                                "kpId": item.kp_id,
                                                "kpName": item.knowledge_point_name,
                                            }
                                            if item.item_type == "knowledge_practice"
                                            and item.kp_id
                                            and item.knowledge_point_name
                                            else {}
                                        ),
                                    },
                                }
                            ),
                        }
                        for item in task.items
                    ],
                    "learning_chapter": {
                        "book": resolved_book,
                        "title": resolved_chapter,
                        "source": (
                            "learning_task"
                            if task_chapter_text
                            else "knowledge_repository"
                            if resolved_chapter and resolved_points
                            else "unresolved"
                        ),
                    },
                    "focus_knowledge_points": (
                        [item["title"] for item in resolved_points]
                        or list(task.focus_knowledge_points)
                    ),
                    "knowledge_cards": resolved_points,
                    "recommended_resources": {
                        "chapter_videos": [
                            {
                                "task_item_id": item.task_item_id,
                                "title": item.title,
                                "resource": dict(item.resource_ref),
                                "completion_policy": dict(item.completion_policy),
                            }
                            for item in task.items
                            if item.item_type == "video_section"
                        ],
                        "knowledge_practice": [
                            {
                                "task_item_id": item.task_item_id,
                                "kp_id": item.kp_id,
                                "kp_name": item.knowledge_point_name,
                                "required_question_count": item.required_question_count,
                            }
                            for item in task.items
                            if item.item_type == "knowledge_practice"
                        ],
                    },
                }
                today_tasks.append(task_projection)
                current_learning_task = task_projection
        for entry in queue.entries:
            if entry.task is None:
                continue
            # ReviewTask is an execution binding and intentionally does not carry a
            # duration budget.  Keep the dashboard projection backward-compatible
            # with the front end without coupling it to a removed contract field.
            review_minutes = 10
            today_tasks.append(
                {
                    "task_id": entry.task.review_task_id,
                    "title": entry.memory_unit.prompt_abstract,
                    "duration": f"{review_minutes} 分钟",
                    "estimated_minutes": review_minutes,
                    "status": entry.task.status,
                    "source": "review_queue",
                }
            )
        # checkin_status 已在函数开头与行为数据并行拉取。
        learning_profile = behavior.get("learning_profile") or {}
        accuracy = learning_profile.get("question_accuracy", 0)
        completion = (
            behavior.get("system_data", {})
            .get("task_completion_rate", {})
            .get("value", 0)
        )
        return {
            "continue_learning": sessions[:5],
            "today_tasks": today_tasks,
            "current_learning_task": current_learning_task,
            "daily_task_timer": daily_task_timer,
            "status_cards": [
                {"key": "accuracy", "value": f"{round(float(accuracy or 0) * 100)}%"},
                {"key": "completion", "value": f"{round(float(completion or 0) * 100)}%"},
            ],
            "announcements": [],
            "review_queue": queue.model_dump(mode="json"),
            "checkin_status": checkin_status,
            "learning_activity": learning_activity,
        }

    @app.get("/api/v1/checkin")
    async def get_daily_checkin(request: Request) -> dict:
        user = current_user(request)
        if backend_handoff is None:
            return {"checked_in_today": False, "streak": 0, "total_checkins": 0, "calendar_days": []}
        return await asyncio.to_thread(backend_handoff.get_checkin_status, user.user_id, days=7)

    @app.post("/api/v1/checkin")
    async def post_daily_checkin(request: Request) -> dict:
        user = current_user(request)
        if backend_handoff is None:
            raise HTTPException(status_code=503, detail="学习行为存储暂未启用")
        return await asyncio.to_thread(backend_handoff.record_daily_checkin, user.user_id)

    @app.post("/api/v1/learning-tasks/current/complete")
    async def complete_current_learning_task(request: Request) -> dict:
        user = current_user(request)
        if user is None:
            raise HTTPException(status_code=401, detail="请先登录后继续")
        repository = container.review_card_use_case.plan_repository
        plans = repository.get_current(user.user_id)
        if plans is None or plans.learning_task is None:
            raise HTTPException(status_code=404, detail="当前没有可完成的学习任务")
        task = plans.learning_task
        if task.status == "completed":
            return {"learning_task": task.model_dump(mode="json")}
        coordinator = container.daily_task_execution_coordinator
        reconciled = False
        if coordinator is not None:
            reconciled = await asyncio.to_thread(
                coordinator.reconcile_parent_status, user.user_id
            )
        if not reconciled:
            raw_progress = {}
            if coordinator is not None:
                load_progress = getattr(coordinator, "load_current_progress", None)
                if callable(load_progress):
                    raw_progress = await asyncio.to_thread(load_progress, user.user_id)
            progress_items = [
                item
                for item in raw_progress.get("items") or []
                if isinstance(item, dict)
            ]
            completed = int(raw_progress.get("completed_items") or sum(
                1
                for item in progress_items
                if str(item.get("status") or "").lower() == "completed"
            ))
            total = int(raw_progress.get("total_items") or 0)
            return JSONResponse(
                status_code=409,
                content={
                    "detail": "仍有未完成的每日任务项，不能直接完成父任务",
                    "current_progress": {
                        "completed": completed,
                        "total": total,
                        "rate": round(completed / total, 4) if total else 0.0,
                    },
                },
            )
        plans = repository.get_current(user.user_id)
        if plans is None or plans.learning_task is None:
            raise HTTPException(status_code=404, detail="当前学习任务已不存在")
        task = plans.learning_task
        return {"learning_task": task.model_dump(mode="json")}

    @app.post("/api/v1/learning-tasks/current/refresh")
    async def refresh_current_learning_task(request: Request) -> dict:
        """Idempotently roll an overdue daily task to the next short-term block."""

        user = current_user(request)
        timer = await asyncio.to_thread(
            container.daily_task_refresh_service.ensure_current, user.user_id
        )
        plans = container.review_card_use_case.plan_repository.get_current(user.user_id)
        return {
            "learning_task": (
                plans.learning_task.model_dump(mode="json")
                if plans is not None and plans.learning_task is not None
                else None
            ),
            "daily_task_timer": timer,
        }

    @app.post("/api/v1/learning-tasks/current/materialize-resources")
    async def materialize_current_learning_task_resources(request: Request) -> dict:
        """Idempotently bind the current task to trusted video/question resources."""

        user = current_user(request)
        try:
            task = await asyncio.to_thread(
                container.learning_plan_service.ensure_executable_daily_resources,
                user.user_id,
            )
        except Exception as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        coordinator = container.daily_task_execution_coordinator
        if coordinator is not None:
            await asyncio.to_thread(coordinator.dispatch_pending, user.user_id, 20)
            ensure_snapshot = getattr(coordinator, "ensure_current_snapshot", None)
            if callable(ensure_snapshot):
                await asyncio.to_thread(ensure_snapshot, user.user_id)
        return {
            "learning_task": task.model_dump(mode="json") if task is not None else None
        }

    @app.get("/api/v1/knowledge/routes")
    async def knowledge_routes() -> dict:
        return {"routes": await asyncio.to_thread(knowledge_backend().map.routes)}

    @app.get("/api/v1/knowledge/nodes")
    async def knowledge_nodes(
        level: int = 1,
        lv1: str = "",
        lv2: str = "",
        route: str = "textbook_14_5",
    ) -> dict:
        try:
            return await asyncio.to_thread(
                knowledge_backend().map.nodes, level, lv1, lv2, route
            )
        except Exception as exc:
            raise knowledge_error(exc) from exc

    @app.get("/api/v1/knowledge/points/{kp_id}")
    async def knowledge_point_detail(kp_id: str, question_limit: int = 30) -> dict:
        try:
            limit = min(100, max(0, question_limit))
            return await asyncio.to_thread(knowledge_backend().map.detail, kp_id, limit)
        except Exception as exc:
            raise knowledge_error(exc) from exc

    @app.get("/api/v1/knowledge/images/{filename}")
    async def knowledge_image(filename: str) -> FileResponse:
        if Path(filename).name != filename:
            raise HTTPException(status_code=404, detail="图片不存在")
        path = knowledge_backend().paths.public_data / "04_knowledge_points" / "images" / filename
        if not path.is_file():
            raise HTTPException(status_code=404, detail="图片不存在")
        return FileResponse(path)

    @app.post("/api/v1/knowledge/warm")
    async def warm_knowledge_backend() -> dict:
        return await asyncio.to_thread(knowledge_backend().map.warm)

    @app.post("/api/v1/knowledge/content/search")
    async def search_knowledge_content(
        payload: ExamKnowledgeQueryRequest, request: Request
    ) -> dict:
        if current_user(request) is None:
            raise HTTPException(status_code=401, detail="请先登录后继续")
        query = payload.query.strip()
        if not query:
            raise HTTPException(status_code=422, detail="检索内容不能为空")
        tool = container.question_retrieval_tool
        if tool is None:
            raise HTTPException(status_code=503, detail="多智能体教材检索工具暂不可用")
        try:
            # Reuse the exact tool registered for the knowledge agent. This
            # read-only endpoint neither plans queries nor adds web searches.
            pack = await tool.get_kp_with_content(
                query, limit=payload.limit, local_only=True
            )
        except LookupError:
            return {"evidence_items": [], "risk_notes": [], "scope": "public"}
        except Exception as exc:
            raise HTTPException(status_code=503, detail="多智能体教材检索失败，请稍后重试") from exc
        return {
            "evidence_items": [
                item.model_dump(mode="json") for item in pack.evidence_items
                if item.resource_type == "textbook"
            ],
            "risk_notes": pack.risk_notes,
            "scope": "public",
        }

    @app.post("/api/v1/knowledge/questions/search")
    async def search_knowledge_questions(
        payload: KnowledgeQuestionSearchRequest, request: Request
    ) -> dict:
        try:
            result = await knowledge_backend().search_questions(
                payload.query,
                payload.kp_ids,
                payload.limit,
                owner_id=knowledge_owner(request),
                scope=payload.scope,
            )
            return result.model_dump(mode="json")
        except Exception as exc:
            raise knowledge_error(exc) from exc

    @app.post("/api/v1/knowledge/questions/import-markdown")
    async def import_questions(payload: MarkdownImportRequest, request: Request) -> dict:
        try:
            return await knowledge_backend().ingest_question_markdown(
                payload.content, knowledge_owner(request)
            )
        except Exception as exc:
            raise knowledge_error(exc) from exc

    @app.post("/api/v1/knowledge/questions/import-file")
    async def import_question_file(
        request: Request,
        filename: str,
    ) -> dict:
        try:
            return await knowledge_backend().ingest_question_file(
                filename,
                await request.body(),
                knowledge_owner(request),
                mineru_token=request.headers.get("x-mineru-token", ""),
            )
        except Exception as exc:
            raise knowledge_error(exc) from exc

    @app.post("/api/v1/knowledge/content/import-text")
    async def import_knowledge_text(
        payload: KnowledgeTextImportRequest, request: Request
    ) -> dict:
        try:
            return await knowledge_backend().ingest_knowledge_text(
                payload.content,
                knowledge_owner(request),
                title=payload.title,
                apply=payload.apply,
            )
        except Exception as exc:
            raise knowledge_error(exc) from exc

    @app.post("/api/v1/knowledge/content/import-file")
    async def import_knowledge_file(
        request: Request,
        filename: str,
        title: str = "用户资料",
        apply: bool = True,
    ) -> dict:
        try:
            return await knowledge_backend().ingest_knowledge_file(
                filename,
                await request.body(),
                knowledge_owner(request),
                title=title,
                apply=apply,
                mineru_token=request.headers.get("x-mineru-token", ""),
            )
        except Exception as exc:
            raise knowledge_error(exc) from exc

    @app.get("/api/v1/knowledge/content/library")
    async def personal_knowledge_library(request: Request) -> dict:
        owner = knowledge_owner(request)
        backend = knowledge_backend()
        try:
            return await asyncio.to_thread(personal_library_read, backend, owner)
        except Exception as exc:
            raise personal_library_error(exc, '个人资料目录暂不可用，请稍后重试') from exc

    def personal_library_read(backend, owner, document_id=None):
        from competition_app.services.personal_knowledge_library import PersonalKnowledgeLibrary
        from competition_app.services.personal_knowledge_storage import owner_lock
        from competition_app.services.personal_knowledge_management import recover_personal
        with owner_lock(backend.paths.runtime_root, owner, read=True):
            recover_personal(backend, owner)
            library = PersonalKnowledgeLibrary(backend.paths.runtime_root, owner)
            return library.overview() if document_id is None else library.document(document_id)

    def personal_library_error(exc, message):
        from competition_app.services.personal_knowledge_storage import PersonalKnowledgeBusy
        if isinstance(exc, PersonalKnowledgeBusy):
            return HTTPException(status_code=409, detail='个人资料正在处理，请完成后重试')
        if isinstance(exc, KeyError):
            return HTTPException(status_code=404, detail='个人资料不存在')
        return HTTPException(status_code=503, detail=message)

    @app.post('/api/v1/knowledge/content/library/rebuild')
    async def rebuild_personal_knowledge(request: Request) -> dict:
        from competition_app.services.personal_knowledge_management import manage_personal_knowledge
        owner, backend = knowledge_owner(request), knowledge_backend()
        try:
            return await asyncio.to_thread(manage_personal_knowledge, backend, owner)
        except Exception as exc:
            raise personal_library_error(exc, '个人索引重建失败，未完成的变更将恢复，请稍后重试') from exc

    @app.delete('/api/v1/knowledge/content/library/{document_id}')
    async def delete_personal_knowledge(document_id: str, request: Request) -> dict:
        from competition_app.services.personal_knowledge_management import manage_personal_knowledge
        owner, backend = knowledge_owner(request), knowledge_backend()
        try:
            return await asyncio.to_thread(manage_personal_knowledge, backend, owner, document_id)
        except Exception as exc:
            raise personal_library_error(exc, '个人资料删除失败，未完成的变更将恢复，请稍后重试') from exc

    @app.get("/api/v1/knowledge/content/library/{document_id}")
    async def personal_knowledge_document(document_id: str, request: Request) -> dict:
        owner = knowledge_owner(request)
        backend = knowledge_backend()
        try:
            return await asyncio.to_thread(personal_library_read, backend, owner, document_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="个人资料不存在") from exc
        except Exception as exc:
            raise personal_library_error(exc, '个人资料读取失败，请稍后重试') from exc

    @app.get("/api/v1/knowledge/content/recognition-reports")
    async def list_knowledge_recognition_reports(
        request: Request,
        offset: int = 0,
        limit: int = 20,
    ) -> dict:
        try:
            return await asyncio.to_thread(
                knowledge_backend().list_recognition_reports,
                knowledge_owner(request),
                offset=max(0, offset),
                limit=min(100, max(1, limit)),
            )
        except Exception as exc:
            raise knowledge_error(exc) from exc

    @app.get("/api/v1/knowledge/content/recognition-reports/{report_id}")
    async def get_knowledge_recognition_report(
        report_id: str,
        request: Request,
    ) -> dict:
        try:
            return await asyncio.to_thread(
                knowledge_backend().get_recognition_report,
                knowledge_owner(request),
                report_id,
            )
        except Exception as exc:
            raise knowledge_error(exc) from exc

    @app.get("/api/v1/knowledge/exams/tracks")
    async def official_exam_tracks() -> dict:
        try:
            return {"tracks": await asyncio.to_thread(knowledge_backend().list_exam_tracks)}
        except Exception as exc:
            raise knowledge_error(exc) from exc

    @app.get("/api/v1/knowledge/exams/tracks/{track_id}/stages")
    async def official_exam_stage_graph(track_id: str) -> dict:
        try:
            return await asyncio.to_thread(knowledge_backend().exam_stage_graph, track_id)
        except Exception as exc:
            raise knowledge_error(exc) from exc

    @app.get("/api/v1/knowledge/exams/tracks/{track_id}/catalog")
    async def official_exam_catalog(track_id: str) -> dict:
        try:
            rows = await asyncio.to_thread(knowledge_backend().exam_track_catalog, track_id)
            return {"track_id": track_id, "nodes": rows, "total": len(rows)}
        except Exception as exc:
            raise knowledge_error(exc) from exc

    @app.get("/api/v1/knowledge/exams/stages/{stage_id}/requirements")
    async def official_exam_requirements(
        stage_id: str, offset: int = 0, limit: int = 100
    ) -> dict:
        try:
            rows = await asyncio.to_thread(
                knowledge_backend().exam_stage_requirements, stage_id, offset, min(500, limit)
            )
            return {"stage_id": stage_id, "items": rows, "offset": offset, "total": len(rows)}
        except Exception as exc:
            raise knowledge_error(exc) from exc

    @app.get("/api/v1/knowledge/exams/requirements/{node_id}/matches")
    async def official_exam_requirement_matches(
        node_id: str, include_candidates: bool = True
    ) -> dict:
        try:
            return await asyncio.to_thread(
                knowledge_backend().exam_requirement_matches, node_id, include_candidates
            )
        except Exception as exc:
            raise knowledge_error(exc) from exc

    @app.get("/api/v1/knowledge/exams/catalog/{catalog_node_id}/knowledge-points")
    async def official_exam_catalog_knowledge(catalog_node_id: str) -> dict:
        try:
            return await asyncio.to_thread(
                knowledge_backend().exam_catalog_knowledge_points, catalog_node_id
            )
        except Exception as exc:
            raise knowledge_error(exc) from exc

    @app.get("/api/v1/knowledge/exams/knowledge-points/{kp_id}/matches")
    async def official_kp_exam_matches(kp_id: str) -> dict:
        try:
            rows = await asyncio.to_thread(knowledge_backend().kp_exam_matches, kp_id)
            return {"kp_id": kp_id, "matches": rows, "total": len(rows)}
        except Exception as exc:
            raise knowledge_error(exc) from exc

    @app.get("/api/v1/knowledge/exams/review-queue")
    async def official_exam_review_queue(
        track_id: str | None = None,
        mapping_status: str | None = None,
        limit: int = 100,
    ) -> dict:
        try:
            rows = await asyncio.to_thread(
                knowledge_backend().exam_review_queue,
                track_id=track_id,
                mapping_status=mapping_status,
                limit=limit,
            )
            return {"items": rows, "total": len(rows)}
        except Exception as exc:
            raise knowledge_error(exc) from exc

    @app.get("/api/v1/knowledge/exams/validation-summary")
    async def official_exam_validation_summary() -> dict:
        try:
            return await asyncio.to_thread(
                knowledge_backend().exam_validation_summary
            )
        except Exception as exc:
            raise knowledge_error(exc) from exc

    @app.post("/api/v1/knowledge/exams/query")
    async def query_user_exam_knowledge(
        payload: ExamKnowledgeQueryRequest, request: Request
    ) -> dict:
        try:
            return await knowledge_backend().query_exam_knowledge(
                payload.query, knowledge_owner(request), payload.limit
            )
        except Exception as exc:
            raise knowledge_error(exc) from exc

    @app.post("/api/v1/knowledge/exams/import-markdown")
    async def import_user_exam(
        payload: ExamMarkdownImportRequest, request: Request
    ) -> dict:
        try:
            return await knowledge_backend().ingest_exam_markdown(
                payload.content,
                knowledge_owner(request),
                replace=payload.replace,
            )
        except Exception as exc:
            raise knowledge_error(exc) from exc

    @app.post("/api/v1/knowledge/exams/import-file")
    async def import_exam_file(
        request: Request,
        filename: str,
        replace: bool = True,
    ) -> dict:
        try:
            return await knowledge_backend().ingest_exam_file(
                filename,
                await request.body(),
                knowledge_owner(request),
                replace=replace,
                mineru_token=request.headers.get("x-mineru-token", ""),
            )
        except Exception as exc:
            raise knowledge_error(exc) from exc

    @app.post("/api/v1/review-cards")
    async def create_review_card(request: ReviewCardRequest, http_request: Request):
        request = scoped_review_request(request, current_user(http_request))
        require_available_thread(http_request, request.thread_id)
        try:
            return public_workflow_result(
                await container.review_card_use_case.execute(request)
            )
        except RuntimeError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    accountability_evaluation = container.accountability_evaluation_service
    if accountability_evaluation is not None:
        semantic_judge = D1LearnerVisibleSemanticJudge(
            accountability_evaluation.expert_agent.chat_model
        )
        if container.runtime_root is None:
            raise RuntimeError("D1 evaluation requires a configured runtime root")
        d1_v5_discovery_dataset = D1V5DiscoveryDataset()
        d1_v5_run_gate = D1V5EvaluationRunGate()
        d1_v5_discovery_batch = D1V5DiscoveryBatchService(
            dataset=d1_v5_discovery_dataset,
            executor=D1V5DiscoveryExecutor(
                expert_agent=accountability_evaluation.expert_agent,
                audit_agent=accountability_evaluation.audit_agent,
                semantic_judge=semantic_judge,
                model_trace_recorder=container.model_trace_recorder,
                model_name=container.chat_model_name,
            ),
            runtime_mode=container.mode,
            state_root=container.runtime_root / "evaluation" / "d1-v5-discovery",
            run_gate=d1_v5_run_gate,
        )
        production_registry = getattr(
            container.review_card_use_case.orchestrator,
            "evolution_rule_registry",
            None,
        )
        d1_v5_evolution_sandbox = D1V5EvolutionSandboxService(
            evolution_agent=container.evolution_agent,
            candidate_compiler=D1ExperimentalCandidateCompiler(
                container.evolution_agent.chat_model,
                model_name=container.chat_model_name,
            ),
            executor=D1V5EvolutionExecutor(
                expert_agent=accountability_evaluation.expert_agent,
                audit_agent=accountability_evaluation.audit_agent,
                semantic_judge=semantic_judge,
                model_trace_recorder=container.model_trace_recorder,
                model_name=container.chat_model_name,
                max_repair_attempts=1,
                step_timeout_seconds=300,
                audit_timeout_seconds=600,
                judge_timeout_seconds=240,
                arm_timeout_seconds=2_400,
            ),
            runtime_mode=container.mode,
            state_root=container.runtime_root / "evaluation" / "d1-v5-evolution",
            discovery_state_root=(
                container.runtime_root / "evaluation" / "d1-v5-discovery"
            ),
            production_repository=container.evolution_repository,
            production_evolution_enabled=bool(container.evolution_rule_service.enabled),
            production_rules_enabled=bool(
                getattr(production_registry, "enabled", False)
            ),
            run_gate=d1_v5_run_gate,
        )
        d1_precheck_runner = D1PrecheckRunner()
        d1_precheck_execution = D1PrecheckExecutionService(
            D1ExecutionDependencies(
                retrieval_tool=container.question_retrieval_tool,
                expert_agent=accountability_evaluation.expert_agent,
                audit_agent=accountability_evaluation.audit_agent,
                semantic_judge=semantic_judge,
            ),
            runner=d1_precheck_runner,
        )
        d1_ab100_runner = D1AB100Runner()
        d1_ab100_execution = D1PrecheckExecutionService(
            D1ExecutionDependencies(
                retrieval_tool=container.question_retrieval_tool,
                expert_agent=accountability_evaluation.expert_agent,
                audit_agent=accountability_evaluation.audit_agent,
                semantic_judge=semantic_judge,
            ),
            runner=d1_ab100_runner,
        )
        d1_v2_state_root = container.runtime_root / "evaluation" / "d1-v2"
        d1_v2_registry_service = D1V2DatasetRegistryService(
            human_review_path=d1_v2_state_root / "independent_review.csv"
        )
        d1_v2_registry, d1_v2_cases = d1_v2_registry_service.load()
        d1_v2_runner = D1AB100V2Runner(cases=d1_v2_cases)
        d1_v2_execution = D1PrecheckExecutionService(
            D1ExecutionDependencies(
                retrieval_tool=container.question_retrieval_tool,
                expert_agent=accountability_evaluation.expert_agent,
                audit_agent=accountability_evaluation.audit_agent,
                semantic_judge=semantic_judge,
            ),
            runner=d1_v2_runner,
        )

        async def execute_d1_v2_registered_pair(
            case_id: str,
            learner_id: str,
        ) -> dict[str, Any]:
            return await d1_v2_execution.execute_pair(
                case_id,
                learner_id=learner_id,
            )

        d1_v2_batch = D1V2BatchService(
            registry=d1_v2_registry,
            runner=d1_v2_runner,
            execute_pair=execute_d1_v2_registered_pair,
            registry_service=d1_v2_registry_service,
            runtime_mode=container.mode,
            state_root=d1_v2_state_root,
            model_trace_recorder=container.model_trace_recorder,
            model_name=container.chat_model_name,
            execution_protocol_version=DEFAULT_EXECUTION_PROTOCOL_VERSION,
        )
        d1_ab100_trace_dir = (
            Path(__file__).resolve().parents[3]
            / "evaluation"
            / "evolution"
            / "outputs"
            / "semantic_conflict_closure_ab100_model_io_traces"
        )

        def require_accountability_evaluation(request: Request):
            if current_user(request) is None:
                raise HTTPException(status_code=401, detail="评测接口必须登录")
            expected = str(container.accountability_evaluation_token or "")
            supplied = str(
                request.headers.get("X-Accountability-Evaluation-Token") or ""
            )
            if not expected or not hmac.compare_digest(supplied, expected):
                raise HTTPException(status_code=403, detail="评测凭据无效")
            return accountability_evaluation

        def require_d1_v5_evolution_sandbox(
            request: Request,
        ) -> D1V5EvolutionSandboxService:
            expected = str(container.accountability_evaluation_token or "")
            supplied = str(
                request.headers.get("X-Accountability-Evaluation-Token") or ""
            )
            if not expected or not hmac.compare_digest(supplied, expected):
                raise HTTPException(status_code=403, detail="评测凭据无效")
            if d1_v5_evolution_sandbox is None:
                raise HTTPException(status_code=404, detail="V5 进化评测沙箱未启用")
            return d1_v5_evolution_sandbox

        def require_d1_v2_run_owner(request: Request, run_id: str) -> AuthUser:
            require_accountability_evaluation(request)
            user = current_user(request)
            if user is None:
                raise HTTPException(status_code=401, detail="评测接口必须登录")
            try:
                owned = d1_v2_batch.is_owned_by(run_id, user.user_id)
            except KeyError as exc:
                raise HTTPException(status_code=404, detail="V2 批次不存在") from exc
            if not owned:
                raise HTTPException(
                    status_code=403,
                    detail="无权访问其他评测用户的批次",
                )
            return user

        def require_d1_v5_discovery_run_owner(
            request: Request,
            run_id: str,
        ) -> AuthUser:
            require_accountability_evaluation(request)
            user = current_user(request)
            if user is None:
                raise HTTPException(status_code=401, detail="评测接口必须登录")
            try:
                owned = d1_v5_discovery_batch.is_owned_by(run_id, user.user_id)
            except KeyError as exc:
                raise HTTPException(status_code=404, detail="V5 发现批次不存在") from exc
            if not owned:
                raise HTTPException(status_code=403, detail="无权访问其他评测用户的批次")
            return user

        @app.get("/internal-eval/d1-v2", response_class=HTMLResponse)
        async def d1_v2_runtime_panel() -> HTMLResponse:
            return HTMLResponse(
                d1_v2_console_html(),
                headers={"Cache-Control": "no-store"},
            )

        @app.get("/internal-eval/d1-v5", response_class=HTMLResponse)
        async def d1_v5_runtime_panel() -> HTMLResponse:
            """Evaluation-only one-click V5.1 live workflow console."""

            return HTMLResponse(
                """<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><title>D1 V5.1 自我进化评测</title>
<style>body{font-family:system-ui,sans-serif;max-width:1100px;margin:28px auto;padding:0 18px;color:#172033;background:#f5f7fb}.card{background:#fff;border:1px solid #dbe3ef;border-radius:14px;padding:18px;margin:12px 0}.row{display:flex;gap:10px;align-items:center;flex-wrap:wrap}input{min-width:430px;padding:10px;border:1px solid #aeb9ca;border-radius:8px}button{padding:10px 16px;border:0;border-radius:8px;background:#1769e0;color:#fff;cursor:pointer}button:disabled{opacity:.5;cursor:wait}.meta{color:#60708a;margin:8px 0}progress{width:100%;height:20px}pre{white-space:pre-wrap;word-break:break-word;background:#f6f8fb;padding:12px;border-radius:8px;max-height:560px;overflow:auto}.ok{color:#087443}.bad{color:#b42318}</style></head><body>
<h1>D1 V5.1 自我进化 100 条 Live 评测</h1><div class="card"><p>单次执行：8 条发现 → 模型生成候选 → GitHub Copilot 合同审核 → 8 条技术资格 → 100 条 paired A/B。仅使用评测沙箱，结束自动清理。</p><div class="row"><label>评测令牌 <input id="token" type="password" autocomplete="off"></label><button id="execute">Execute</button></div><div id="mode" class="meta">尚未检查环境</div><div id="status" class="meta">等待执行</div><progress id="progress" value="0" max="100"></progress></div><div class="card"><strong>结果与证据摘要</strong><pre id="output">尚无结果</pre></div>
<script>
const $=s=>document.querySelector(s), token=()=>$('#token').value.trim(), headers=(json=false)=>{const h={'X-Accountability-Evaluation-Token':token()};if(json)h['Content-Type']='application/json';return h};
const req=async(method,path,body)=>{const r=await fetch(path,{method,headers:headers(body!==undefined),body:body===undefined?undefined:JSON.stringify(body)}),text=await r.text();let data={};try{data=JSON.parse(text||'{}')}catch{data={detail:text}}if(!r.ok)throw new Error(`${r.status} ${data.detail||text}`);return data};
const pause=ms=>new Promise(r=>setTimeout(r,ms)), status=(t,bad=false)=>{$('#status').textContent=t;$('#status').className='meta '+(bad?'bad':'')}, out=v=>{$('#output').textContent=JSON.stringify(v,null,2)};
async function waitDiscovery(id){while(true){const s=await req('GET',`/api/v1/internal-eval/d1-v5-discovery/batches/${id}`);status(`发现阶段 ${s.completed_case_count||0}/${s.total_case_count||8} · ${s.active_case_id||s.status}`);$('#progress').value=(s.completed_case_count||0)/8*5;if(['completed','failed','interrupted'].includes(s.status))return s;await pause(3000)}}
async function waitEvolution(id,terminal){while(true){let s=await req('GET',`/api/v1/internal-eval/d1-v5/evolution/runs/${id}`);if(['qualification_interrupted','final_interrupted','interrupted','stale'].includes(s.status)){s=await req('POST',`/api/v1/internal-eval/d1-v5/evolution/runs/${id}/resume`)}const done=s.completed_case_count||0,total=s.total_case_count||0;status(`${s.active_stage||s.status} ${done}/${total} · ${s.active_case_id||''} ${s.active_arm||''}`);$('#progress').value=s.active_stage==='qualification'?5+(done/Math.max(total,1))*5:10+(done/Math.max(total,1))*90;if(terminal.includes(s.status))return s;await pause(3000)}}
$('#execute').onclick=async()=>{const btn=$('#execute');btn.disabled=true;let discoveryId='',runId='',evidence=null,cleanup={};try{const manifest=await req('GET','/api/v1/internal-eval/d1-v5-discovery/manifest');if(manifest.runtime_mode!=='live'||manifest.dataset_id!=='d1_three_stage_v5_advantage'||manifest.version!=='5.1.0')throw new Error('后端不是 V5.1 Live 冻结环境');$('#mode').textContent=`已确认：${manifest.runtime_mode} · ${manifest.dataset_id}@${manifest.version} · 正式写回关闭`;const d=await req('POST','/api/v1/internal-eval/d1-v5-discovery/batches');discoveryId=d.run_id;const ds=await waitDiscovery(discoveryId);if(ds.status!=='completed')throw new Error('发现阶段未完成');const receipts=await req('GET',`/api/v1/internal-eval/d1-v5-discovery/batches/${discoveryId}/receipts`);const failures=receipts.receipts.filter(x=>x.semantic_verdict?.acceptable===false&&['relationship_mishandled','unsupported_resolution'].includes(x.semantic_verdict?.rationale_code));if(failures.length<2)throw new Error('发现阶段不足两条机制匹配失败，不能生成候选');const packet=await req('POST','/api/v1/internal-eval/d1-v5/evolution/runs',{discovery_run_id:discoveryId});runId=packet.run_id;const c=packet.candidate||{},sources=c.source_case_ids||[];if(sources.length!==2||c.candidate_authorship!=='experimental_model_authored_candidate'||c.hardcoded_strategy_fallback_used!==false||c.production_registered!==false||c.production_active!==false||!packet.safety_replay?.passed)throw new Error('Copilot 候选合同审核拒绝');await req('POST',`/api/v1/internal-eval/d1-v5/evolution/runs/${runId}/review`,{decision:'approve',reviewer_type:'github_copilot',independent_human_review:false,note:'GitHub Copilot 非独立、非人工合同审核：确认候选由模型从本次 discovery 的两条机制匹配失败生成，无手写 fallback，作用域仅限 Expert/resource.content，不注册、不激活、不写生产。',candidate_digest:packet.candidate_digest});await req('POST',`/api/v1/internal-eval/d1-v5/evolution/runs/${runId}/qualification`);const qs=await waitEvolution(runId,['qualification_passed','qualification_failed','failed']);if(qs.status!=='qualification_passed')throw new Error('资格门禁未通过');await req('POST',`/api/v1/internal-eval/d1-v5/evolution/runs/${runId}/final`);const fs=await waitEvolution(runId,['completed','final_failed','failed']);evidence=await req('GET',`/api/v1/internal-eval/d1-v5/evolution/runs/${runId}/evidence`);out(evidence);if(fs.status!=='completed')throw new Error(`100 条终测状态：${fs.status}`);$('#progress').value=100;status('100 条 paired A/B 已完成，正在清理…')}catch(e){status('执行失败：'+e.message,true);if(runId){try{evidence=await req('GET',`/api/v1/internal-eval/d1-v5/evolution/runs/${runId}/evidence`);out({error:e.message,evidence})}catch{out({error:e.message})}}else out({error:e.message})}finally{if(runId){try{cleanup.evolution=await req('DELETE',`/api/v1/internal-eval/d1-v5/evolution/runs/${runId}?purge_artifacts=false`)}catch(e){cleanup.evolution_error=e.message}}if(discoveryId){try{cleanup.discovery=await req('DELETE',`/api/v1/internal-eval/d1-v5-discovery/batches/${discoveryId}?purge_artifact=true`)}catch(e){cleanup.discovery_error=e.message}}if(evidence)out({evidence,cleanup});if(evidence?.status==='completed')status('100 条评测完成；沙箱已清理',false);btn.disabled=false}};
</script></body></html>""",
                headers={"Cache-Control": "no-store"},
            )

        @app.get("/internal-eval/d1-precheck", response_class=HTMLResponse)
        async def d1_precheck_runtime_panel() -> HTMLResponse:
            """Small evaluation-only panel used to launch the five live pilot pairs."""

            return HTMLResponse(
                """<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><title>D1 在线预检</title>
<style>
body{font-family:system-ui,sans-serif;max-width:1040px;margin:32px auto;padding:0 18px;color:#172033}
.bar,.case{border:1px solid #d8dfeb;border-radius:12px;padding:16px;margin:12px 0;background:#fff}
input{min-width:420px;padding:9px;border:1px solid #aeb9ca;border-radius:8px}
button{padding:9px 14px;border:0;border-radius:8px;background:#1769e0;color:#fff;cursor:pointer;margin-left:8px}
button:disabled{opacity:.55;cursor:wait}.meta{color:#60708a;font-size:13px}pre{white-space:pre-wrap;word-break:break-word;background:#f6f8fb;padding:12px;border-radius:8px;max-height:420px;overflow:auto}
</style></head><body>
<h1>D1 真实模型在线预检</h1>
<p>仅评测环境使用；执行固定A/B上下文，不写正式会话、计划或业务数据库。</p>
<div class="bar"><label>评测令牌 <input id="token" type="password" autocomplete="off"></label><button id="load">加载案例</button></div>
<div id="status" class="meta"></div><div id="cases"></div>
<script>
const token=()=>document.querySelector('#token').value.trim();
const headers=()=>({'X-Accountability-Evaluation-Token':token()});
const status=t=>document.querySelector('#status').textContent=t;
document.querySelector('#load').onclick=async()=>{try{status('正在加载…');const r=await fetch('/api/v1/internal-eval/d1-precheck/cases',{headers:headers()});if(!r.ok)throw new Error(await r.text());const body=await r.json();const root=document.querySelector('#cases');root.innerHTML='';for(const item of body.items){const box=document.createElement('div');box.className='case';box.innerHTML=`<strong>${item.case_id}</strong><div class="meta">${item.case_group} · ${item.pair_order} · ${item.scenario}</div><button>Execute</button><pre hidden></pre>`;const btn=box.querySelector('button'),out=box.querySelector('pre');btn.onclick=async()=>{btn.disabled=true;out.hidden=false;out.textContent='真实模型执行中…';try{const x=await fetch(`/api/v1/internal-eval/d1-precheck/${item.case_id}/execute`,{method:'POST',headers:headers()});const data=await x.json();out.textContent=JSON.stringify(data,null,2);if(!x.ok)throw new Error(data.detail||'执行失败')}catch(e){out.textContent='执行失败：'+e.message}finally{btn.disabled=false}};root.appendChild(box)}status(`已加载 ${body.items.length} 条预检案例`)}catch(e){status('加载失败：'+e.message)}};
</script></body></html>"""
            )

        @app.get("/internal-eval/d1-ab100", response_class=HTMLResponse)
        async def d1_ab100_runtime_panel() -> HTMLResponse:
            """Evaluation-only panel for one AB100 pair with a full model I/O trace."""

            return HTMLResponse(
                """<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><title>D1 AB100 完整轨迹</title>
<style>
body{font-family:system-ui,sans-serif;max-width:1180px;margin:32px auto;padding:0 18px;color:#172033}
.bar,.case{border:1px solid #d8dfeb;border-radius:12px;padding:16px;margin:12px 0;background:#fff}
input,select{min-width:420px;padding:9px;border:1px solid #aeb9ca;border-radius:8px}
button{padding:9px 14px;border:0;border-radius:8px;background:#1769e0;color:#fff;cursor:pointer;margin-left:8px}
button:disabled{opacity:.55;cursor:wait}.meta{color:#60708a;font-size:13px;margin:8px 0}
pre{white-space:pre-wrap;word-break:break-word;background:#f6f8fb;padding:12px;border-radius:8px;max-height:680px;overflow:auto}
</style></head><body>
<h1>D1 AB100 真实模型完整轨迹</h1>
<p>仅评测环境使用；执行冻结A/B上下文，不写正式会话、学习计划或业务数据库。模型隐藏推理不导出。</p>
<div class="bar"><label>评测令牌 <input id="token" type="password" autocomplete="off"></label><button id="load">加载100条案例</button></div>
<div class="case"><label>案例 <select id="case"><option>请先加载</option></select></label><button id="execute" disabled>Execute</button><div id="status" class="meta"></div><pre id="output" hidden></pre></div>
<script>
const token=()=>document.querySelector('#token').value.trim();
const headers=()=>({'X-Accountability-Evaluation-Token':token()});
const status=t=>document.querySelector('#status').textContent=t;
const select=document.querySelector('#case'),execute=document.querySelector('#execute'),out=document.querySelector('#output');
document.querySelector('#load').onclick=async()=>{try{status('正在加载…');const r=await fetch('/api/v1/internal-eval/d1-ab100/cases',{headers:headers()});if(!r.ok)throw new Error(await r.text());const body=await r.json();select.innerHTML='';for(const item of body.items){const option=document.createElement('option');option.value=item.case_id;option.textContent=`${item.case_id} · ${item.case_group} · ${item.pair_order} · ${item.scenario}`;select.appendChild(option)}execute.disabled=false;status(`已加载 ${body.items.length} 条案例`)}catch(e){status('加载失败：'+e.message)}};
execute.onclick=async()=>{execute.disabled=true;out.hidden=false;out.textContent='真实模型A/B执行中，正在记录完整输入输出…';status('运行中');try{const r=await fetch(`/api/v1/internal-eval/d1-ab100/${select.value}/execute-trace`,{method:'POST',headers:headers()});const data=await r.json();out.textContent=JSON.stringify(data,null,2);if(!r.ok)throw new Error(data.detail||'执行失败');window.__D1_LAST_TRACE__=data;status(`执行完成；轨迹已保存：${data.artifact_file}`)}catch(e){out.textContent='执行失败：'+e.message;status('执行失败')}finally{execute.disabled=false}};
</script></body></html>"""
            )

        @app.get("/api/v1/internal-eval/accountability/cases")
        async def list_accountability_evaluation_cases(
            http_request: Request,
        ) -> dict:
            service = require_accountability_evaluation(http_request)
            return {
                "schema_version": "1.0",
                "items": [
                    spec.model_dump(mode="json")
                    for spec in service.specs.values()
                ],
            }

        @app.post(
            "/api/v1/internal-eval/accountability/{evaluation_case_id}/stream"
        )
        async def stream_accountability_evaluation_case(
            evaluation_case_id: str,
            http_request: Request,
        ) -> StreamingResponse:
            service = require_accountability_evaluation(http_request)
            user = current_user(http_request)
            if user is None:
                raise HTTPException(status_code=401, detail="评测接口必须登录")
            try:
                spec = service.spec(evaluation_case_id)
            except KeyError as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from exc
            thread_id = f"EVAL_ACC_{evaluation_case_id}_{uuid4().hex}"
            evaluation_request = ReviewCardRequest(
                operation_id=thread_id[:96],
                thread_id=thread_id[:128],
                conversation_id=thread_id[:128],
                learner_id=user.user_id,
                user_request=spec.prompt,
                available_minutes=15,
            )
            return _workflow_stream(
                evaluation_request.thread_id,
                lambda: service.execute(evaluation_case_id, evaluation_request),
                user_request=spec.prompt,
                run_use_case=service.use_case,
            )

        @app.get("/api/v1/internal-eval/accountability/runs/{thread_id}")
        async def get_accountability_evaluation_run(
            thread_id: str,
            http_request: Request,
        ) -> dict:
            service = require_accountability_evaluation(http_request)
            record = service.record(thread_id)
            if record is None:
                raise HTTPException(status_code=404, detail="评测运行不存在")
            return record

        @app.get("/api/v1/internal-eval/d1-precheck/cases")
        async def list_d1_precheck_cases(http_request: Request) -> dict:
            require_accountability_evaluation(http_request)
            return {
                "schema_version": "d1-precheck-cases-1.0",
                "items": d1_precheck_runner.cases_payload(),
            }

        @app.post(
            "/api/v1/internal-eval/d1-precheck/{evaluation_case_id}/prepare"
        )
        async def prepare_d1_precheck_pair(
            evaluation_case_id: str,
            http_request: Request,
        ) -> dict:
            require_accountability_evaluation(http_request)
            try:
                return d1_precheck_runner.prepare_payload(evaluation_case_id)
            except KeyError as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from exc
            except ValueError as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from exc

        @app.post(
            "/api/v1/internal-eval/d1-precheck/{evaluation_case_id}/validate"
        )
        async def validate_d1_precheck_pair(
            evaluation_case_id: str,
            http_request: Request,
        ) -> dict:
            require_accountability_evaluation(http_request)
            try:
                payload = await http_request.json()
                if str(payload.get("case_id") or "") != evaluation_case_id:
                    raise ValueError("receipt case_id does not match URL")
                return d1_precheck_runner.validate_payload(payload)
            except KeyError as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from exc
            except (TypeError, ValueError) as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc

        @app.post(
            "/api/v1/internal-eval/d1-precheck/{evaluation_case_id}/execute"
        )
        async def execute_d1_precheck_pair(
            evaluation_case_id: str,
            http_request: Request,
        ) -> dict:
            """Run one isolated real-model D1 pair without production writeback."""

            require_accountability_evaluation(http_request)
            user = current_user(http_request)
            if user is None:
                raise HTTPException(status_code=401, detail="评测接口必须登录")
            try:
                return await d1_precheck_execution.execute_pair(
                    evaluation_case_id,
                    learner_id=user.user_id,
                )
            except KeyError as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from exc
            except (RuntimeError, TypeError, ValueError) as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from exc

        @app.get("/api/v1/internal-eval/d1-ab100/cases")
        async def list_d1_ab100_cases(http_request: Request) -> dict:
            require_accountability_evaluation(http_request)
            return {
                "schema_version": "d1-ab100-cases-1.0",
                "items": d1_ab100_runner.cases_payload(),
            }

        @app.post(
            "/api/v1/internal-eval/d1-ab100/{evaluation_case_id}/execute-trace"
        )
        async def execute_d1_ab100_pair_with_trace(
            evaluation_case_id: str,
            http_request: Request,
        ) -> dict:
            """Run one AB100 pair and persist an internal, sanitized model I/O trace."""

            require_accountability_evaluation(http_request)
            user = current_user(http_request)
            if user is None:
                raise HTTPException(status_code=401, detail="评测接口必须登录")
            recorder = container.model_trace_recorder
            if recorder is None:
                raise HTTPException(status_code=409, detail="模型轨迹记录器不可用")
            cases = {case.case_id: case for case in d1_ab100_runner.cases}
            case = cases.get(evaluation_case_id)
            if case is None:
                raise HTTPException(status_code=404, detail="AB100评测案例不存在")
            recorder.reset()
            started_at = datetime.now(timezone.utc).isoformat()
            try:
                with recorder.capture_full():
                    result = await d1_ab100_execution.execute_pair(
                        evaluation_case_id,
                        learner_id=user.user_id,
                    )
            except (RuntimeError, TypeError, ValueError) as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from exc

            model_calls = []
            for item in recorder.items:
                value = item.model_dump(mode="json", exclude={"reasoning_text"})
                value["reasoning_text"] = "[OMITTED_NOT_EXPORTED]"
                model_calls.append(_sanitize(value))
            record = {
                "schema_version": "d1-ab100-model-io-trace-1.0",
                "trace_purpose": "formal_ab100_pair_with_developer_diagnostic_trace",
                "formal_environment_write_allowed": False,
                "provider_reasoning_exported": False,
                "case": {
                    "case_id": case.case_id,
                    "case_group": case.case_group,
                    "prompt": case.prompt,
                    "expected_task_type": case.expected_task_type,
                    "pair_order": case.pair_order,
                    "target_rule": case.target_rule,
                    "target_agent": case.target_agent,
                    "owner_step_id": case.owner_step_id,
                    "expected_rerun_step_ids": list(case.expected_rerun_step_ids),
                    "scenario": case.scenario,
                    "formal_environment_write_allowed": False,
                },
                "started_at": started_at,
                "finished_at": datetime.now(timezone.utc).isoformat(),
                "model_name": container.chat_model_name,
                "model_call_count": len(model_calls),
                "model_calls": model_calls,
                "pair_result": _sanitize(result),
            }
            d1_ab100_trace_dir.mkdir(parents=True, exist_ok=True)
            artifact = d1_ab100_trace_dir / f"{evaluation_case_id}.model_io.json"
            artifact.write_text(
                json.dumps(record, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            return {
                **record,
                "artifact_file": str(artifact.relative_to(Path(__file__).resolve().parents[3])),
            }

        @app.get("/api/v1/internal-eval/d1-v2/manifest")
        async def get_d1_v2_manifest(http_request: Request) -> dict:
            require_accountability_evaluation(http_request)
            return d1_v2_batch.manifest_payload()

        @app.get("/api/v1/internal-eval/d1-v5-discovery/manifest")
        async def get_d1_v5_discovery_manifest(http_request: Request) -> dict:
            require_accountability_evaluation(http_request)
            return d1_v5_discovery_batch.manifest_payload()

        @app.post("/api/v1/internal-eval/d1-v5-discovery/batches")
        async def start_d1_v5_discovery_batch(http_request: Request) -> dict:
            require_accountability_evaluation(http_request)
            user = current_user(http_request)
            if user is None:
                raise HTTPException(status_code=401, detail="评测接口必须登录")
            try:
                return d1_v5_discovery_batch.start(requested_by=user.user_id)
            except ValueError as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from exc

        @app.get("/api/v1/internal-eval/d1-v5-discovery/batches/{run_id}")
        async def get_d1_v5_discovery_batch(
            run_id: str,
            http_request: Request,
        ) -> dict:
            require_d1_v5_discovery_run_owner(http_request, run_id)
            return d1_v5_discovery_batch.status(run_id)

        @app.get(
            "/api/v1/internal-eval/d1-v5-discovery/batches/{run_id}/receipts"
        )
        async def get_d1_v5_discovery_receipts(
            run_id: str,
            http_request: Request,
        ) -> dict:
            require_d1_v5_discovery_run_owner(http_request, run_id)
            return d1_v5_discovery_batch.receipts(run_id)

        @app.delete("/api/v1/internal-eval/d1-v5-discovery/batches/{run_id}")
        async def cleanup_d1_v5_discovery_batch(
            run_id: str,
            http_request: Request,
            purge_artifact: bool = Query(default=True),
        ) -> dict:
            require_d1_v5_discovery_run_owner(http_request, run_id)
            return await d1_v5_discovery_batch.cleanup(
                run_id,
                purge_artifact=purge_artifact,
            )

        @app.post("/api/v1/internal-eval/d1-v5/evolution/runs")
        async def create_d1_v5_evolution_run(
            payload: D1V5ExperimentStartRequest,
            http_request: Request,
        ) -> dict:
            service = require_d1_v5_evolution_sandbox(http_request)
            try:
                return await service.create(payload.discovery_run_id)
            except KeyError as exc:
                raise HTTPException(status_code=404, detail="V5 发现证据不存在") from exc
            except (RuntimeError, ValueError) as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from exc

        @app.get("/api/v1/internal-eval/d1-v5/evolution/runs/{run_id}")
        async def get_d1_v5_evolution_run(
            run_id: str,
            http_request: Request,
        ) -> dict:
            service = require_d1_v5_evolution_sandbox(http_request)
            try:
                return service.status(run_id)
            except KeyError as exc:
                raise HTTPException(status_code=404, detail="V5 进化评测不存在") from exc

        @app.get(
            "/api/v1/internal-eval/d1-v5/evolution/runs/{run_id}/review-packet"
        )
        async def get_d1_v5_evolution_review_packet(
            run_id: str,
            http_request: Request,
        ) -> dict:
            service = require_d1_v5_evolution_sandbox(http_request)
            try:
                return service.review_packet(run_id)
            except KeyError as exc:
                raise HTTPException(status_code=404, detail="V5 进化评测不存在") from exc

        @app.post(
            "/api/v1/internal-eval/d1-v5/evolution/runs/{run_id}/review"
        )
        async def review_d1_v5_evolution_candidate(
            run_id: str,
            payload: D1V5CopilotReviewRequest,
            http_request: Request,
        ) -> dict:
            service = require_d1_v5_evolution_sandbox(http_request)
            try:
                return service.submit_review(run_id, payload)
            except KeyError as exc:
                raise HTTPException(status_code=404, detail="V5 进化评测不存在") from exc
            except ValueError as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from exc

        @app.post(
            "/api/v1/internal-eval/d1-v5/evolution/runs/{run_id}/qualification"
        )
        async def start_d1_v5_evolution_qualification(
            run_id: str,
            http_request: Request,
        ) -> dict:
            service = require_d1_v5_evolution_sandbox(http_request)
            try:
                return service.start_qualification(run_id)
            except KeyError as exc:
                raise HTTPException(status_code=404, detail="V5 进化评测不存在") from exc
            except (RuntimeError, ValueError) as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from exc

        @app.post("/api/v1/internal-eval/d1-v5/evolution/runs/{run_id}/final")
        async def start_d1_v5_evolution_final(
            run_id: str,
            http_request: Request,
        ) -> dict:
            service = require_d1_v5_evolution_sandbox(http_request)
            try:
                return service.start_final(run_id)
            except KeyError as exc:
                raise HTTPException(status_code=404, detail="V5 进化评测不存在") from exc
            except (RuntimeError, ValueError) as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from exc

        @app.post("/api/v1/internal-eval/d1-v5/evolution/runs/{run_id}/resume")
        async def resume_d1_v5_evolution_run(
            run_id: str,
            http_request: Request,
        ) -> dict:
            service = require_d1_v5_evolution_sandbox(http_request)
            try:
                return service.resume(run_id)
            except KeyError as exc:
                raise HTTPException(status_code=404, detail="V5 进化评测不存在") from exc
            except (RuntimeError, ValueError) as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from exc

        @app.get(
            "/api/v1/internal-eval/d1-v5/evolution/runs/{run_id}/evidence"
        )
        async def get_d1_v5_evolution_evidence(
            run_id: str,
            http_request: Request,
        ) -> dict:
            service = require_d1_v5_evolution_sandbox(http_request)
            try:
                return service.evidence(run_id)
            except KeyError as exc:
                raise HTTPException(status_code=404, detail="V5 进化评测不存在") from exc

        @app.delete("/api/v1/internal-eval/d1-v5/evolution/runs/{run_id}")
        async def cleanup_d1_v5_evolution_run(
            run_id: str,
            http_request: Request,
            purge_artifacts: bool = Query(default=False),
        ) -> dict:
            service = require_d1_v5_evolution_sandbox(http_request)
            return await service.cleanup(run_id, purge_artifacts=purge_artifacts)

        @app.get("/api/v1/internal-eval/d1-v2/independent-review")
        async def get_d1_v2_independent_review(http_request: Request) -> dict:
            require_accountability_evaluation(http_request)
            return d1_v2_registry_service.review_package_payload()

        @app.post("/api/v1/internal-eval/d1-v2/independent-review")
        async def submit_d1_v2_independent_review(
            http_request: Request,
            payload: D1V2IndependentReviewRequest,
        ) -> dict:
            require_accountability_evaluation(http_request)
            user = current_user(http_request)
            if user is None:
                raise HTTPException(status_code=401, detail="评测接口必须登录")
            if d1_v2_batch.has_runs():
                raise HTTPException(
                    status_code=409,
                    detail="V2 已存在运行证据，不能补录启动前独立复核",
                )
            try:
                return d1_v2_registry_service.record_human_review(
                    [item.model_dump(mode="json") for item in payload.items],
                    submitted_by=user.user_id,
                    expected_attempt_number=payload.attempt_number,
                    attestation={
                        "human_reviewer": payload.human_reviewer,
                        "independent_review": payload.independent_review,
                        "gold_labels_not_seen": payload.gold_labels_not_seen,
                        "prior_review_decisions_not_seen": (
                            payload.prior_review_decisions_not_seen
                        ),
                    },
                )
            except ValueError as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from exc

        @app.get("/api/v1/evolution/evaluation/d1-v2/overview")
        async def get_d1_v2_evaluation_overview(http_request: Request) -> dict:
            require_admin(http_request)
            return d1_v2_batch.overview()

        @app.post("/api/v1/internal-eval/d1-v2/batches")
        async def start_d1_v2_batch(
            http_request: Request,
            payload: D1V2BatchStartRequest,
        ) -> dict:
            require_accountability_evaluation(http_request)
            user = current_user(http_request)
            if user is None:
                raise HTTPException(status_code=401, detail="评测接口必须登录")
            try:
                return d1_v2_batch.start(
                    requested_by=user.user_id,
                    learner_id=user.user_id,
                    case_ids=payload.case_ids,
                    purpose=payload.purpose,
                    prerequisite_run_id=payload.prerequisite_run_id,
                )
            except ValueError as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from exc

        @app.get("/api/v1/internal-eval/d1-v2/batches")
        async def list_d1_v2_batches(http_request: Request) -> dict:
            require_accountability_evaluation(http_request)
            user = current_user(http_request)
            if user is None:
                raise HTTPException(status_code=401, detail="评测接口必须登录")
            return {
                "schema_version": "d1-v2-batch-list-1.0",
                "items": d1_v2_batch.list_runs(requested_by=user.user_id),
            }

        @app.get("/api/v1/internal-eval/d1-v2/batches/{run_id}")
        async def get_d1_v2_batch_status(
            run_id: str,
            http_request: Request,
        ) -> dict:
            require_d1_v2_run_owner(http_request, run_id)
            try:
                return d1_v2_batch.status(run_id)
            except KeyError as exc:
                raise HTTPException(status_code=404, detail="V2 批次不存在") from exc

        @app.get("/api/v1/internal-eval/d1-v2/batches/{run_id}/receipts")
        async def get_d1_v2_batch_receipts(
            run_id: str,
            http_request: Request,
        ) -> dict:
            require_d1_v2_run_owner(http_request, run_id)
            try:
                return d1_v2_batch.receipts(run_id)
            except KeyError as exc:
                raise HTTPException(status_code=404, detail="V2 批次不存在") from exc

        @app.get("/api/v1/internal-eval/d1-v2/batches/{run_id}/model-trace")
        async def get_d1_v2_single_model_trace(
            run_id: str,
            http_request: Request,
        ) -> dict:
            require_d1_v2_run_owner(http_request, run_id)
            try:
                return d1_v2_batch.single_trace(run_id)
            except KeyError as exc:
                raise HTTPException(status_code=404, detail="V2 批次不存在") from exc
            except ValueError as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from exc

        @app.get("/api/v1/internal-eval/d1-v2/batches/{run_id}/score")
        async def get_d1_v2_batch_score(
            run_id: str,
            http_request: Request,
        ) -> dict:
            require_d1_v2_run_owner(http_request, run_id)
            try:
                return d1_v2_batch.score(run_id)
            except KeyError as exc:
                raise HTTPException(status_code=404, detail="V2 批次不存在") from exc
            except ValueError as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from exc

        @app.post("/api/v1/internal-eval/d1-v2/batches/{run_id}/resume")
        async def resume_d1_v2_batch(
            run_id: str,
            http_request: Request,
        ) -> dict:
            user = require_d1_v2_run_owner(http_request, run_id)
            try:
                return d1_v2_batch.resume(
                    run_id,
                    requested_by=user.user_id,
                    learner_id=user.user_id,
                )
            except KeyError as exc:
                raise HTTPException(status_code=404, detail="V2 批次不存在") from exc
            except ValueError as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from exc

        @app.post(
            "/api/v1/internal-eval/d1-v2/batches/{run_id}/precheck-decision"
        )
        async def decide_d1_v2_precheck(
            run_id: str,
            http_request: Request,
            payload: D1V2PrecheckDecisionRequest,
        ) -> dict:
            user = require_d1_v2_run_owner(http_request, run_id)
            try:
                return d1_v2_batch.confirm_precheck(
                    run_id,
                    requested_by=user.user_id,
                    decision=payload.decision,
                    note=payload.note,
                )
            except ValueError as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from exc

        @app.post("/api/v1/internal-eval/d1-v2/batches/{run_id}/repeatability")
        async def start_d1_v2_repeatability(
            run_id: str,
            http_request: Request,
        ) -> dict:
            user = require_d1_v2_run_owner(http_request, run_id)
            try:
                return d1_v2_batch.start_repeatability(
                    run_id,
                    requested_by=user.user_id,
                    learner_id=user.user_id,
                )
            except ValueError as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from exc

        @app.post(
            "/api/v1/internal-eval/d1-v2/batches/{run_id}/repeatability/resume"
        )
        async def resume_d1_v2_repeatability(
            run_id: str,
            http_request: Request,
        ) -> dict:
            user = require_d1_v2_run_owner(http_request, run_id)
            try:
                return d1_v2_batch.resume_repeatability(
                    run_id,
                    requested_by=user.user_id,
                    learner_id=user.user_id,
                )
            except ValueError as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from exc

        @app.get("/api/v1/internal-eval/d1-v2/batches/{run_id}/blind-review")
        async def get_d1_v2_formal_blind_review(
            run_id: str,
            http_request: Request,
        ) -> dict:
            require_accountability_evaluation(http_request)
            try:
                status = d1_v2_batch.status(run_id)
            except KeyError as exc:
                raise HTTPException(status_code=404, detail="V2 批次不存在") from exc
            if status["purpose"] != "formal" or status["status"] != "completed":
                raise HTTPException(
                    status_code=409,
                    detail="正式盲审需要已完成的正式批次",
                )
            return d1_v2_registry_service.formal_review_package_payload()

        @app.post("/api/v1/internal-eval/d1-v2/batches/{run_id}/blind-review")
        async def submit_d1_v2_formal_blind_review(
            run_id: str,
            http_request: Request,
            payload: D1V2FormalBlindReviewRequest,
        ) -> dict:
            require_accountability_evaluation(http_request)
            user = current_user(http_request)
            if user is None:
                raise HTTPException(status_code=401, detail="评测接口必须登录")
            try:
                return d1_v2_batch.attach_blind_reviews(
                    run_id,
                    blind_reviews=[
                        item.model_dump(mode="json") for item in payload.items
                    ],
                    requested_by=user.user_id,
                )
            except KeyError as exc:
                raise HTTPException(status_code=404, detail="V2 批次不存在") from exc
            except ValueError as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from exc

        @app.post(
            "/api/v1/internal-eval/evolution-effect/{evaluation_case_id}/pair"
        )
        async def run_evolution_effect_pair(
            evaluation_case_id: str,
            http_request: Request,
            rule_id: str = Query(min_length=8, max_length=128),
        ) -> dict:
            """Run one no-writeback A/B pair on an identical Expert context."""

            service = require_accountability_evaluation(http_request)
            user = current_user(http_request)
            if user is None:
                raise HTTPException(status_code=401, detail="评测接口必须登录")
            thread_id = f"EVAL_PAIR_{evaluation_case_id}_{uuid4().hex}"
            try:
                spec = service.spec(evaluation_case_id)
                evaluation_request = ReviewCardRequest(
                    operation_id=thread_id[:96],
                    thread_id=thread_id[:128],
                    conversation_id=thread_id[:128],
                    learner_id=user.user_id,
                    user_request=spec.prompt,
                    available_minutes=15,
                )
                return await service.execute_evolution_pair(
                    evaluation_case_id,
                    evaluation_request,
                    rule_id=rule_id,
                )
            except KeyError as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from exc
            except (RuntimeError, ValueError) as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/api/v1/workshop/smart-papers/stream")
    async def stream_smart_paper(
        request: ReviewCardRequest, http_request: Request
    ) -> StreamingResponse:
        """Dedicated structured paper endpoint with a server-owned execution path."""

        request = scoped_review_request(request, current_user(http_request))
        try:
            constraints = validate_smart_paper_constraints(request.exam_constraints)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        await asyncio.to_thread(require_available_thread, http_request, request.thread_id)
        thread_id = request.thread_id or f"THREAD_{uuid4().hex}"
        request = request.model_copy(
            update={
                "thread_id": thread_id,
                "exam_constraints": constraints,
                "current_page": {
                    **dict(request.current_page or {}),
                    "path": "/practice/smart-paper",
                    "product_surface": "smart_paper",
                },
            }
        )
        active_thread_id = await asyncio.to_thread(
            container.review_card_use_case.claim_active_run,
            request.learner_id,
            "smart_paper",
            thread_id,
        )
        if active_thread_id != thread_id:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "smart_paper_running",
                    "message": "已有一份试卷正在后台生成，请恢复现有任务。",
                    "active_thread_id": active_thread_id,
                },
            )
        await asyncio.to_thread(
            container.review_card_use_case.mark_run_started,
            thread_id,
            request.learner_id,
            product_surface="smart_paper",
        )
        return _workflow_stream(
            thread_id,
            lambda: container.review_card_use_case.execute(
                request,
                execution_entrypoint="workshop_smart_paper",
            ),
            user_request=request.user_request,
        )

    @app.post("/api/v1/workshop/smart-papers/reviews/{thread_id}")
    async def resolve_smart_paper_review(
        thread_id: str,
        payload: SmartPaperHumanReviewAction,
        http_request: Request,
    ):
        reviewer = require_admin(http_request)
        try:
            result = await asyncio.to_thread(
                container.review_card_use_case.resolve_smart_paper_human_review,
                thread_id,
                action=payload.action,
                reviewer_id=reviewer.user_id,
                note=payload.note,
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="待复核试卷不存在") from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return public_workflow_result(result) if hasattr(result, "model_dump") else result

    @app.post("/api/v1/review-cards/stream")
    async def stream_review_card(
        request: ReviewCardRequest, http_request: Request
    ) -> StreamingResponse:
        request = scoped_review_request(request, current_user(http_request))
        # 同步 DB 读（占用检查）/ 写（run started）包 to_thread，避免阻塞主循环。
        await asyncio.to_thread(require_available_thread, http_request, request.thread_id)
        thread_id = request.thread_id or f"THREAD_{uuid4().hex}"
        request = request.model_copy(update={"thread_id": thread_id})
        product_surface = str(
            (request.current_page or {}).get("product_surface") or ""
        ).strip()
        # ``product_surface`` is a system-owned enum sent by the dedicated
        # product entry point. Open user text is deliberately not inspected.
        if product_surface == "smart_paper":
            active_thread_id = await asyncio.to_thread(
                container.review_card_use_case.claim_active_run,
                request.learner_id,
                product_surface,
                thread_id,
            )
            if active_thread_id != thread_id:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "code": "smart_paper_running",
                        "message": "已有一份试卷正在后台生成，请恢复现有任务。",
                        "active_thread_id": active_thread_id,
                    },
                )
        await asyncio.to_thread(
            container.review_card_use_case.mark_run_started,
            thread_id,
            request.learner_id,
            product_surface=(product_surface or None),
        )
        return _workflow_stream(
            thread_id,
            lambda: container.review_card_use_case.execute(request),
            user_request=request.user_request,
        )

    @app.post("/api/v1/review-cards/runs/{thread_id}/resume/stream")
    async def resume_review_card(
        thread_id: str,
        request: WorkflowResumeRequest,
        http_request: Request,
    ) -> StreamingResponse:
        await asyncio.to_thread(require_run_owner, http_request, thread_id)
        return _workflow_stream(
            thread_id,
            lambda: container.review_card_use_case.resume(thread_id, request),
            resumed=True,
        )

    @app.get("/api/v1/review-cards/runs/{thread_id}")
    async def get_review_card_run(thread_id: str, request: Request):
        state = await asyncio.to_thread(require_run_owner, request, thread_id)
        payload = safe_run_status(state)
        if payload.get("status") == "running":
            with _RUNTIME_PROGRESS_LOCK:
                bucket = _RUNTIME_PROGRESS.get(thread_id) or {}
            payload["progress_events"] = list(bucket.get("events") or [])
        return payload

    @app.post("/api/v1/review-cards/runs/{thread_id}/cancel")
    async def cancel_review_card_run(thread_id: str, request: Request):
        state = await asyncio.to_thread(require_run_owner, request, thread_id)
        if state.get("status") in {
            "completed", "failed", "interrupted", "waiting_human_review", "cancelled"
        }:
            return {"status": state.get("status"), "thread_id": thread_id}
        state = await asyncio.to_thread(
            container.review_card_use_case.request_run_cancellation,
            thread_id,
        )
        with workflow_worker_loops_lock:
            worker = workflow_worker_loops.get(thread_id)
        if worker is not None:
            worker_loop, worker_task = worker
            worker_loop.call_soon_threadsafe(worker_task.cancel)
        return {
            "status": (state or {}).get("status") or "cancellation_requested",
            "thread_id": thread_id,
        }

    @app.get("/api/v1/learners/{learner_id}/review-queue")
    async def get_review_queue(learner_id: str, request: Request, limit: int = 50):
        require_owner(request, learner_id)
        if backend_handoff is not None:
            behavior = await asyncio.to_thread(
                backend_handoff.load_learning_context, learner_id
            )
            container.review_service.ingest_question_attempts(
                learner_id=learner_id,
                attempts=behavior.get("question_attempt", []),
            )
        return container.review_service.get_queue(learner_id, limit=limit)

    @app.get("/api/v1/review-queue")
    async def get_current_user_review_queue(
        request: Request,
        limit: int = Query(default=50, ge=1, le=200),
    ):
        """Stable current-user queue; the learner-id route remains compatible."""

        user = current_user(request)
        if user is None:
            raise HTTPException(status_code=401, detail="请先登录后继续")
        if backend_handoff is not None:
            behavior = await asyncio.to_thread(
                backend_handoff.load_learning_context, user.user_id
            )
            container.review_service.ingest_question_attempts(
                learner_id=user.user_id,
                attempts=behavior.get("question_attempt", []),
            )
        return container.review_service.get_queue(user.user_id, limit=limit)

    @app.get("/api/v1/review-dashboard")
    async def get_review_dashboard(
        request: Request,
        limit: int = Query(default=50, ge=1, le=200),
        history_limit: int = Query(default=100, ge=1, le=500),
    ) -> dict:
        """Current-user review queue, KP mastery and review history."""

        user = current_user(request)
        if user is None:
            raise HTTPException(status_code=401, detail="请先登录后继续")
        details: dict[str, Any] = {
            "schema_version": "1.0",
            "learner_id": user.user_id,
            "mastery": [],
            "mastery_history": [],
            "review_states": [],
            "review_tasks": [],
        }
        if backend_handoff is not None:
            behavior = await asyncio.to_thread(
                backend_handoff.load_learning_context, user.user_id
            )
            container.review_service.ingest_question_attempts(
                learner_id=user.user_id,
                attempts=behavior.get("question_attempt", []),
            )
            details = await asyncio.to_thread(
                backend_handoff.load_review_dashboard,
                user.user_id,
                history_limit=history_limit,
            )
        queue = container.review_service.get_queue(user.user_id, limit=limit)
        mastery = list(details.get("mastery") or [])
        scores = [
            float(item["mastery_score"])
            for item in mastery
            if isinstance(item, dict) and isinstance(item.get("mastery_score"), (int, float))
        ]
        # ``review_tasks`` must be sourced from review_service (the same store
        # ``submit_attempt`` validates against); platform_backend's side-channel
        # ReviewTaskRecord rows are not submittable and previously caused 404.
        kp_names = {
            str(item.get("kp_id") or ""): str(item.get("kp_name") or "")
            for item in mastery
            if isinstance(item, dict) and str(item.get("kp_id") or "").strip()
        }
        review_tasks = []
        seen_review_task_scopes: set[tuple[str, str]] = set()
        task_mapping = (
            await asyncio.to_thread(
                backend_handoff.canonicalize_knowledge_point_ids,
                user.user_id,
                tuple(
                    delivery.task.primary_kp_id
                    for delivery in container.review_service.list_active_deliveries(
                        user.user_id
                    )
                ),
            )
            if backend_handoff is not None
            and hasattr(backend_handoff, "canonicalize_knowledge_point_ids")
            else {}
        )
        for delivery in container.review_service.list_active_deliveries(user.user_id):
            task = delivery.task
            canonical_id = task_mapping.get(task.primary_kp_id, task.primary_kp_id)
            task_scope = (canonical_id, task.review_type)
            if task_scope in seen_review_task_scopes:
                continue
            seen_review_task_scopes.add(task_scope)
            review_tasks.append({
                "review_task_id": task.review_task_id,
                "kp_id": canonical_id,
                "kp_name": kp_names.get(canonical_id, canonical_id),
                "review_type": task.review_type,
                "status": task.status,
                "scheduled_at": None,
                "created_at": None,
            })
        return {
            **details,
            "review_tasks": review_tasks,
            "queue": queue.model_dump(mode="json"),
            "summary": {
                "knowledge_point_count": len(mastery),
                "average_mastery": round(sum(scores) / len(scores), 2) if scores else None,
                "due_count": queue.due_count,
                "active_task_count": queue.active_task_count,
                "history_count": len(details.get("mastery_history") or []),
            },
        }

    @app.post("/api/v1/review-tasks/{review_task_id}/attempts")
    async def submit_review_attempt(
        review_task_id: str,
        submission: ReviewAttemptSubmission,
        request: Request,
    ):
        user = current_user(request)
        if user is not None:
            submission = submission.model_copy(update={"learner_id": user.user_id})
        try:
            return container.review_service.submit_attempt(review_task_id, submission)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except (ValueError, RuntimeError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.get("/api/v1/review-preferences")
    async def get_review_preferences(request: Request) -> dict:
        """Return the current learner's review preferences."""

        user = current_user(request)
        if user is None:
            raise HTTPException(status_code=401, detail="请先登录后继续")
        return {
            "learner_id": user.user_id,
            "daily_capacity": container.review_service.get_daily_capacity(
                user.user_id
            ),
        }

    @app.put("/api/v1/review-preferences")
    async def update_review_preferences(
        request: Request, update: ReviewPreferenceUpdateRequest
    ) -> dict:
        """Persist the learner's review preferences (e.g. fewer tasks per day)."""

        user = current_user(request)
        if user is None:
            raise HTTPException(status_code=401, detail="请先登录后继续")
        container.review_service.set_daily_capacity(
            user.user_id, update.daily_capacity
        )
        return {
            "learner_id": user.user_id,
            "daily_capacity": container.review_service.get_daily_capacity(
                user.user_id
            ),
        }

    @app.post("/api/v1/review-tasks/{review_task_id}/cancel")
    async def cancel_review_task(
        review_task_id: str, request: Request
    ) -> dict:
        """Cancel a review task; the underlying memory unit is postponed so the
        task is not immediately regenerated."""

        user = current_user(request)
        if user is None:
            raise HTTPException(status_code=401, detail="请先登录后继续")
        try:
            task = container.review_service.cancel_task(
                review_task_id, learner_id=user.user_id
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except (ValueError, RuntimeError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return task.model_dump(mode="json")

    @app.post("/api/v1/review-tasks/{review_task_id}/snooze")
    async def snooze_review_task(
        review_task_id: str,
        request: Request,
        snooze: ReviewSnoozeRequest | None = None,
    ) -> dict:
        """Delay a review task: the memory unit's next review is pushed back and
        the task returns to ``pending`` (out of the due set)."""

        user = current_user(request)
        if user is None:
            raise HTTPException(status_code=401, detail="请先登录后继续")
        try:
            task = container.review_service.snooze_task(
                review_task_id,
                learner_id=user.user_id,
                hours=snooze.hours if snooze is not None else 24,
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except (ValueError, RuntimeError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return task.model_dump(mode="json")

    @app.post("/api/v1/learners/{learner_id}/review-queue/dispatch")
    async def dispatch_due_review(
        learner_id: str,
        dispatch_request: ReviewDispatchRequest,
        request: Request,
    ):
        require_owner(request, learner_id)
        pushed = await materialize_due_review_resource(
            learner_id,
            available_minutes=dispatch_request.available_minutes,
        )
        if pushed["status"] == "empty":
            return pushed
        return pushed["result"]

    def _workflow_stream(
        thread_id: str,
        operation,
        *,
        user_request: str | None = None,
        resumed: bool = False,
        run_use_case=None,
    ) -> StreamingResponse:
        queue: asyncio.Queue[dict[str, object] | None] = asyncio.Queue()
        request_exam_workspace = current_exam_workspace()
        workflow_use_case = run_use_case or container.review_card_use_case
        # 2026-08-16: workflow 整体移入线程池独立事件循环执行，把 LangGraph
        # 每步节点转换的同步 SQLAlchemy checkpoint 写（MySQL）从 uvicorn 主
        # 循环中摘除。此前双会话并发时两个 workflow 在主循环上互相抢占，
        # SSE 帧被大量 checkpoint 写延迟，浏览器长连接超时断开。
        _main_event_loop = asyncio.get_running_loop()
        _main_thread_id = threading.get_ident()
        _sequence_lock = threading.Lock()
        _event_sequence = 0
        _agent_output_tasks: set[asyncio.Task] = set()
        # ``complete_text`` business calls are the only model boundary whose
        # raw content is itself intended to be learner-facing prose.  Keep its
        # accumulated text server-side and release only the same safe
        # projection used by committed stage output.  JSON calls, compilers
        # and provider reasoning never enter this map.
        _business_text_streams: dict[str, dict[str, str]] = {}
        # Streaming provider reasoning for the Copilot-style thought block.
        # Deltas are sanitized with the same allow-list projection as
        # business text; only per-call delivery state is tracked here.
        _reasoning_streams: dict[str, dict[str, str]] = {}
        _true_streamed_steps: set[str] = set()

        class _WorkflowCancelledInWorker(Exception):
            pass

        def _sequence_event(event: dict[str, object]) -> dict[str, object]:
            """Attach a run-local monotonic sequence for ordering and dedupe."""
            nonlocal _event_sequence
            with _sequence_lock:
                _event_sequence += 1
                sequence = _event_sequence
            return {
                **event,
                "thread_id": event.get("thread_id") or thread_id,
                "seq": event.get("seq") or sequence,
            }

        def _enqueue_public_event(event: dict[str, object]) -> None:
            """Sequence, retain and enqueue an event on the main event loop.

            Sequence numbers must be assigned at queue insertion time.  If a
            worker assigns them before ``call_soon_threadsafe``, an agent-output
            coroutine can overtake that callback and make SSE sequence numbers
            appear out of order.
            """

            public_event = _sequence_event(event)
            if public_event.get("event") not in _NON_TRACE_EVENT_TYPES:
                with _RUNTIME_PROGRESS_LOCK:
                    bucket = _RUNTIME_PROGRESS.setdefault(thread_id, {"events": []})
                    bucket["events"].append(public_event)
            queue.put_nowait(public_event)

        async def _stream_agent_output(event: dict[str, object]) -> None:
            """Reveal one safe stage result without exposing raw model deltas."""

            text = str(event.get("public_output") or "").strip()
            if not text:
                return
            base = {
                "thread_id": thread_id,
                "agent": event.get("agent"),
                "step_id": event.get("step_id"),
                # This is the validated, allow-listed stage artifact.  It is
                # deliberately separate from the live public working draft so
                # the browser can collapse the latter without losing it.
                "output_phase": "formal",
            }
            _enqueue_public_event({
                **base,
                "event": "agent_output_started",
                "ts": time.time_ns() // 1_000_000,
            })
            for delta in _public_agent_output_chunks(text):
                _enqueue_public_event({
                    **base,
                    "event": "agent_output_delta",
                    "ts": time.time_ns() // 1_000_000,
                    "delta": delta,
                })
                # Keep the safe stage summary visibly progressive instead of
                # flashing the whole result after a long model wait.
                await asyncio.sleep(0.035)
            _enqueue_public_event({
                **base,
                "event": "agent_output_committed",
                "ts": time.time_ns() // 1_000_000,
            })

        def _schedule_agent_output(event: dict[str, object]) -> None:
            def _start() -> None:
                task = asyncio.create_task(_stream_agent_output(event))
                _agent_output_tasks.add(task)
                task.add_done_callback(_agent_output_tasks.discard)

            if threading.get_ident() == _main_thread_id:
                _start()
            else:
                _main_event_loop.call_soon_threadsafe(_start)

        def _handle_business_text_event(event: dict[str, object]) -> None:
            """Convert genuine provider prose deltas into safe stage deltas."""

            if str(event.get("output_kind") or "business") != "business":
                return
            event_type = str(event.get("event") or "")
            call_id = str(event.get("call_id") or "")
            step_id = str(event.get("step_id") or event.get("agent") or "")
            agent = str(event.get("agent") or "")
            if not call_id or not step_id or not agent:
                return
            base = {
                "thread_id": thread_id,
                "agent": agent,
                "step_id": step_id,
                # Public business prose only. Provider reasoning_content,
                # structured compiler output and prompts never reach here.
                "output_phase": "working",
            }
            if event_type == "business_text_started":
                append = step_id in _true_streamed_steps
                _true_streamed_steps.add(step_id)
                _business_text_streams[call_id] = {
                    "raw": "",
                    "emitted": "",
                    "step_id": step_id,
                    "agent": agent,
                }
                _enqueue_public_event({
                    **base,
                    "event": "agent_output_started",
                    "append": append,
                    "ts": time.time_ns() // 1_000_000,
                })
                if append:
                    _enqueue_public_event({
                        **base,
                        "event": "agent_output_delta",
                        "delta": "\n\n---\n\n",
                        "ts": time.time_ns() // 1_000_000,
                    })
                return
            state = _business_text_streams.get(call_id)
            if state is None:
                return
            if event_type == "business_text_delta":
                state["raw"] = (state["raw"] + str(event.get("delta") or ""))[:24_000]
                safe = project_public_business_text(state["raw"], limit=20_000)
                # Keep a short unstable tail server-side.  It prevents an
                # unfinished <think>/internal-ID/fenced block from crossing a
                # chunk boundary before the sanitizer can recognise it, while
                # still making long answers visible during provider output.
                stable = safe[:-96] if len(safe) > 96 else ""
                emitted = state["emitted"]
                if stable.startswith(emitted) and len(stable) > len(emitted):
                    delta = stable[len(emitted):]
                    state["emitted"] = stable
                    _enqueue_public_event({
                        **base,
                        "event": "agent_output_delta",
                        "delta": delta,
                        "ts": time.time_ns() // 1_000_000,
                    })
                return
            if event_type == "business_text_completed":
                safe = project_public_business_text(state["raw"], limit=20_000)
                emitted = state["emitted"]
                if safe.startswith(emitted):
                    delta = safe[len(emitted):]
                    if delta:
                        _enqueue_public_event({
                            **base,
                            "event": "agent_output_delta",
                            "delta": delta,
                            "ts": time.time_ns() // 1_000_000,
                        })
                else:
                    _enqueue_public_event({
                        **base,
                        "event": "agent_output_replaced",
                        "public_output": safe,
                        "ts": time.time_ns() // 1_000_000,
                    })
                _enqueue_public_event({
                    **base,
                    "event": "agent_output_committed",
                    "ts": time.time_ns() // 1_000_000,
                })
                _business_text_streams.pop(call_id, None)
                return
            if event_type == "business_text_failed":
                _enqueue_public_event({
                    **base,
                    "event": "agent_output_replaced",
                    "public_output": "本次模型调用未形成可用阶段输出。",
                    "ts": time.time_ns() // 1_000_000,
                })
                _enqueue_public_event({
                    **base,
                    "event": "agent_output_committed",
                    "ts": time.time_ns() // 1_000_000,
                })
                _business_text_streams.pop(call_id, None)

        def _handle_reasoning_event(event: dict[str, object]) -> None:
            """Convert reasoning_* runtime events into agent_reasoning_* SSE events."""
            event_type = str(event.get("event") or "")
            call_id = str(event.get("call_id") or "")
            step_id = str(event.get("step_id") or event.get("agent") or "")
            agent = str(event.get("agent") or "")
            if not call_id or not step_id or not agent:
                return
            base = {
                "thread_id": thread_id,
                "agent": agent,
                "step_id": step_id,
                "output_phase": "thinking",
            }
            if event_type == "reasoning_started":
                _reasoning_streams[call_id] = {"raw": "", "emitted": ""}
                _enqueue_public_event({
                    **base,
                    "event": "agent_reasoning_started",
                    "ts": time.time_ns() // 1_000_000,
                })
                return
            state = _reasoning_streams.get(call_id)
            if state is None:
                return
            if event_type == "reasoning_delta":
                state["raw"] = (state["raw"] + str(event.get("delta") or ""))[:24_000]
                safe = project_public_business_text(state["raw"], limit=20_000)
                stable = safe[:-96] if len(safe) > 96 else ""
                emitted = state["emitted"]
                if stable.startswith(emitted) and len(stable) > len(emitted):
                    delta = stable[len(emitted):]
                    state["emitted"] = stable
                    _enqueue_public_event({
                        **base,
                        "event": "agent_reasoning_delta",
                        "delta": delta,
                        "ts": time.time_ns() // 1_000_000,
                    })
                return
            if event_type == "reasoning_committed":
                safe = project_public_business_text(state["raw"], limit=20_000)
                emitted = state["emitted"]
                if safe.startswith(emitted):
                    delta = safe[len(emitted):]
                    if delta:
                        _enqueue_public_event({
                            **base,
                            "event": "agent_reasoning_delta",
                            "delta": delta,
                            "ts": time.time_ns() // 1_000_000,
                        })
                _enqueue_public_event({
                    **base,
                    "event": "agent_reasoning_committed",
                    "ts": time.time_ns() // 1_000_000,
                })
                _reasoning_streams.pop(call_id, None)
                return

        def _forward_publish(event: dict[str, object]) -> None:
            # publish 同时维护 _RUNTIME_PROGRESS（轮询恢复用）与 SSE 队列。
            # 在 worker 线程中被 sink 调用时，事件先经 call_soon_threadsafe
            # 投递到主循环（保证 queue.put_nowait 与前端消费者同一线程），
            # 内存表仍由 worker 线程直接追加（锁保护）。
            event_type = str(event.get("event") or "")
            if event_type.startswith("business_text_"):
                if threading.get_ident() == _main_thread_id:
                    _handle_business_text_event(event)
                else:
                    try:
                        _main_event_loop.call_soon_threadsafe(
                            _handle_business_text_event, event
                        )
                    except RuntimeError:
                        pass
                return
            if event_type.startswith("reasoning_"):
                if threading.get_ident() == _main_thread_id:
                    _handle_reasoning_event(event)
                else:
                    try:
                        _main_event_loop.call_soon_threadsafe(
                            _handle_reasoning_event, event
                        )
                    except RuntimeError:
                        pass
                return
            public_event = public_runtime_event(event)
            public_event.setdefault("ts", time.time_ns() // 1_000_000)
            if public_event.get("event") == "model_delta":
                return
            if threading.get_ident() == _main_thread_id:
                _enqueue_public_event(public_event)
            else:
                try:
                    _main_event_loop.call_soon_threadsafe(
                        _enqueue_public_event, public_event
                    )
                except RuntimeError:
                    # 主循环已关闭（如服务退出），丢弃事件即可。
                    return
            if (
                public_event.get("event") == "system_output"
                and public_event.get("public_output")
            ):
                step_id = str(public_event.get("step_id") or "")
                if step_id in _true_streamed_steps:
                    # Replace the in-flight model prose with the validated,
                    # allow-listed stage projection.  This is not fake replay:
                    # the user already saw genuine provider deltas; the final
                    # replacement reflects compiler/application validation.
                    finalized = {
                        "thread_id": thread_id,
                        "agent": public_event.get("agent"),
                        "step_id": step_id,
                        "event": "agent_output_replaced",
                        "output_phase": "formal",
                        "public_output": public_event.get("public_output"),
                        "ts": time.time_ns() // 1_000_000,
                    }
                    if threading.get_ident() == _main_thread_id:
                        _enqueue_public_event(finalized)
                    else:
                        _main_event_loop.call_soon_threadsafe(
                            _enqueue_public_event, finalized
                        )
                else:
                    _schedule_agent_output(public_event)

        def publish(event: dict[str, object]) -> None:
            # 兼容调用方：主线程直接同步入队；worker 线程走线程安全转发。
            _forward_publish(event)

        def failure_event(exc: Exception) -> dict[str, object]:
            run_state = workflow_use_case.get_run_state(thread_id) or {}
            execution_id = run_state.get("execution_id")
            message = str(exc).strip()
            error_type = type(exc).__name__
            normalized = message.lower()
            failed_step = run_state.get("failed_step")
            step_match = re.search(r"步骤\s+([^（(\s]+)", message)
            if step_match and failed_step in {
                None,
                "",
                "orchestrator",
                "finalization",
            }:
                failed_step = step_match.group(1)
            persisted_code = str(run_state.get("error_code") or "").strip()
            if persisted_code:
                error_code = persisted_code
                retryable = bool(run_state.get("retryable", False))
            elif (
                "connecterror" in normalized
                or "connectionerror" in normalized
                or "readerror" in normalized
                or "writeerror" in normalized
                or "transport error" in normalized
            ):
                error_code = "model_transport_error"
                retryable = True
            elif "timeout" in normalized or "timed out" in normalized:
                if failed_step in {"knowledge", "knowledge_base_agent"} or "knowledge" in normalized:
                    error_code = "knowledge_timeout"
                elif failed_step in {"paper_blueprint", "paper_blueprint_agent"}:
                    error_code = "paper_blueprint_timeout"
                elif "model" in normalized or "transport" in normalized:
                    error_code = "model_timeout"
                else:
                    error_code = "workflow_timeout"
                retryable = True
            elif "empty" in normalized or "no content" in normalized:
                error_code = "model_empty_response"
                retryable = True
            elif "invalid structured output" in normalized or "invalid_json" in normalized:
                # 模型输出无法解析为有效 JSON（含 agent 名 knowledge_* 时
                # 不能误归类为知识检索失败）。
                error_code = "model_invalid_output"
                retryable = True
            elif failed_step in {"conversation", "persistence", "snapshot", "profile_writeback"}:
                error_code = "persistence_failed"
                retryable = True
            elif "knowledge" in normalized:
                error_code = "knowledge_step_failed"
                retryable = True
            elif (
                failed_step in {"learning_plan", "learning_plan_service"}
                and "dailytaskprogresserror" in normalized
            ):
                error_code = "daily_task_publication_failed"
                retryable = True
            elif (
                failed_step in {
                    "diagnosis",
                    "diagnosis_agent",
                    "diagnosis_long",
                    "diagnosis_short",
                }
                and (
                    "plan contract" in normalized
                    or "规划合同" in message
                    or "规划正文" in message
                    or "编译为合同" in message
                )
            ):
                error_code = "plan_compilation_failed"
                retryable = False
            elif failed_step in {"audit", "audit_agent"} or "audit decision" in normalized:
                error_code = "audit_step_failed"
                retryable = True
            elif failed_step in {
                "paper_blueprint",
                "question_pool",
                "paper_assembly",
                "paper_audit",
            }:
                error_code = "paper_generation_failed"
                retryable = True
            elif "database" in normalized or "mysql" in normalized or "持久化" in message:
                error_code = "persistence_failed"
                retryable = True
            else:
                error_code = "workflow_failed"
                retryable = False
            user_message = safe_failure_message(error_code)
            return {
                "event": "run_failed",
                "ts": time.time_ns() // 1_000_000,
                "error_type": error_type,
                "error_code": error_code,
                "retryable": retryable,
                "message": user_message,
                "user_message": user_message,
                "thread_id": thread_id,
                "execution_id": execution_id,
                "failed_step": failed_step,
                **(
                    {
                        "diagnostics": _sanitize(
                            run_state.get("failure_model_diagnostics")
                        )
                    }
                    if run_state.get("failure_model_diagnostics")
                    else {}
                ),
            }

        def _run_workflow_in_thread():
            # ContextVar 是线程隔离的：recording sink 与 exam workspace 绑定
            # 必须在 worker 线程内重做，operation 内部 emit 的事件才能落
            # 到正确的 sink（RecordingEventSink 内建 threading.Lock，跨线程
            # drain/emit 安全）。
            token = bind_recording_sink(publish, _NON_TRACE_EVENT_TYPES)
            workspace_token = None
            if request_exam_workspace is not None:
                workspace_token = bind_exam_workspace_context(request_exam_workspace)
            worker_loop = asyncio.new_event_loop()
            asyncio.set_event_loop(worker_loop)
            worker_task = worker_loop.create_task(operation())
            with workflow_worker_loops_lock:
                workflow_worker_loops[thread_id] = (worker_loop, worker_task)
            try:
                workflow_use_case.raise_if_run_cancelled(thread_id)
                try:
                    return worker_loop.run_until_complete(worker_task)
                except asyncio.CancelledError as exc:
                    raise _WorkflowCancelledInWorker() from exc
            finally:
                with workflow_worker_loops_lock:
                    workflow_worker_loops.pop(thread_id, None)
                pending = asyncio.all_tasks(worker_loop)
                for pending_task in pending:
                    pending_task.cancel()
                if pending:
                    worker_loop.run_until_complete(
                        asyncio.gather(*pending, return_exceptions=True)
                    )
                worker_loop.close()
                asyncio.set_event_loop(None)
                if workspace_token is not None:
                    reset_exam_workspace(workspace_token)
                reset_event_sink(token)

        async def run_workflow() -> None:
            # Forward a browser-safe collaboration stream while retaining only
            # small receipts used to restore a run after a page/conversation
            # switch. Full model payloads remain in server-side observability.
            try:
                await queue.put(
                    _sequence_event({
                        "event": "run_resumed" if resumed else "run_started",
                        "ts": time.time_ns() // 1_000_000,
                        "thread_id": thread_id,
                        "user_request": user_request,
                    })
                )
                # 2026-08-16: operation（container.review_card_use_case.execute）
                # 内部执行 LangGraph 图，每个节点转换都会同步写 SQLAlchemy
                # checkpoint（MySQL），且各 repository 的 save 也是同步 DB
                # 调用。直接在 uvicorn 事件循环上 await 会在并发会话时阻塞
                # SSE 转发（见 2026-08-16 并发事故：讲解/组卷同时运行互掐）。
                # 整体移入独立线程的事件循环执行（同步调用留在该线程内，
                # 完全避开主循环）；asyncio.run 在 3.10+ 允许在非主线程使用。
                result = await asyncio.to_thread(_run_workflow_in_thread)
                # The cancel endpoint may win the race immediately after the
                # worker returns. Never emit a success answer or terminal
                # completion event for a run that is already cancellation
                # requested/cancelled.
                current_state = await asyncio.to_thread(
                    workflow_use_case.get_run_state,
                    thread_id,
                )
                if (current_state or {}).get("status") in {
                    "cancellation_requested",
                    "cancelled",
                }:
                    raise _WorkflowCancelledInWorker()
                # A fast downstream step may finish before the preceding
                # public stage-result animation.  Drain all already scheduled
                # streams before the final answer starts so event order stays
                # understandable and no agent output is cut off.
                if _agent_output_tasks:
                    await asyncio.gather(*tuple(_agent_output_tasks))
                current_state = await asyncio.to_thread(
                    workflow_use_case.get_run_state,
                    thread_id,
                )
                if (current_state or {}).get("status") in {
                    "cancellation_requested",
                    "cancelled",
                }:
                    raise _WorkflowCancelledInWorker()
                result_status = getattr(result, "status", None)
                if result_status == "failed":
                    detail = (
                        getattr(result, "error_message", None)
                        or "workflow execution failed"
                    )
                    raise RuntimeError(str(detail))
                event_name = (
                    "run_interrupted"
                    if result_status == "interrupted"
                    else "run_waiting_human_review"
                    if result_status == "waiting_human_review"
                    else "run_completed"
                )
                assistant_message = workflow_result_to_markdown(result)
                streamable_answer = _public_streamable_answer(assistant_message)
                publication_status = (
                    "interrupted"
                    if event_name == "run_interrupted"
                    else "human_review"
                    if event_name == "run_waiting_human_review"
                    else "approved"
                )
                await queue.put(
                    _sequence_event({
                        "event": "answer_started",
                        "ts": time.time_ns() // 1_000_000,
                        "thread_id": thread_id,
                        "publication_status": publication_status,
                    })
                )
                for delta in _public_answer_chunks(streamable_answer):
                    current_state = await asyncio.to_thread(
                        workflow_use_case.get_run_state,
                        thread_id,
                    )
                    if (current_state or {}).get("status") in {
                        "cancellation_requested",
                        "cancelled",
                    }:
                        raise _WorkflowCancelledInWorker()
                    await queue.put(
                        _sequence_event({
                            "event": "answer_delta",
                            "ts": time.time_ns() // 1_000_000,
                            "thread_id": thread_id,
                            "delta": delta,
                            "publication_status": publication_status,
                        })
                    )
                    # Keep the learner-facing reveal perceptible without
                    # materially increasing an already long workflow.
                    await asyncio.sleep(0.008)
                await queue.put(
                    _sequence_event({
                        "event": "answer_committed",
                        "ts": time.time_ns() // 1_000_000,
                        "thread_id": thread_id,
                        "publication_status": publication_status,
                    })
                )
                current_state = await asyncio.to_thread(
                    workflow_use_case.get_run_state,
                    thread_id,
                )
                if (current_state or {}).get("status") in {
                    "cancellation_requested",
                    "cancelled",
                }:
                    raise _WorkflowCancelledInWorker()
                await queue.put(
                    _sequence_event({
                        "event": event_name,
                        "ts": time.time_ns() // 1_000_000,
                        "result": public_workflow_result(result),
                        "assistant_message": assistant_message,
                    })
                )
            except (asyncio.CancelledError, _WorkflowCancelledInWorker):
                for output_task in tuple(_agent_output_tasks):
                    output_task.cancel()
                if _agent_output_tasks:
                    await asyncio.gather(
                        *tuple(_agent_output_tasks), return_exceptions=True
                    )
                await asyncio.to_thread(
                    workflow_use_case.mark_run_cancelled,
                    thread_id,
                )
                await queue.put(_sequence_event({
                    "event": "run_cancelled",
                    "ts": time.time_ns() // 1_000_000,
                    "thread_id": thread_id,
                    "status": "cancelled",
                    "assistant_message": "已停止生成。",
                }))
            except Exception as exc:
                # failure_event 内部同步读 run_state（DB），包 to_thread。
                failure = await asyncio.to_thread(failure_event, exc)
                # mark_run_failed 内部同步写 MySQL（run_state save），包
                # to_thread 避免失败清理阶段再次阻塞主事件循环。
                await asyncio.to_thread(
                    workflow_use_case.mark_run_failed,
                    thread_id,
                    str(exc),
                    error_type=str(failure["error_type"]),
                    error_code=str(failure["error_code"]),
                    retryable=bool(failure["retryable"]),
                    failed_step=(
                        str(failure["failed_step"])
                        if failure.get("failed_step")
                        else None
                    ),
                )
                await queue.put(_sequence_event(failure))
            finally:
                with _RUNTIME_PROGRESS_LOCK:
                    _RUNTIME_PROGRESS.pop(thread_id, None)
                await queue.put(None)

        async def event_source():
            task = asyncio.create_task(run_workflow())
            workflow_tasks.add(task)
            workflow_tasks_by_thread[thread_id] = task

            def _forget_task(completed: asyncio.Task) -> None:
                workflow_tasks.discard(completed)
                if workflow_tasks_by_thread.get(thread_id) is completed:
                    workflow_tasks_by_thread.pop(thread_id, None)

            task.add_done_callback(_forget_task)
            try:
                while True:
                    event = await queue.get()
                    if event is None:
                        break
                    yield "data: " + json.dumps(event, ensure_ascii=False) + "\n\n"
            finally:
                # The workflow intentionally keeps running after an SSE disconnect.
                # Its status/result remains available through the run-state endpoint.
                pass

        return StreamingResponse(
            event_source(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    if backend_handoff is not None:
        # The production build calls the transitional business API through
        # `/api/*`. Vite removes that prefix in development, so the same mapping
        # must exist when FastAPI serves the built frontend directly. Main
        # `/api/v1/*` routes were registered above and remain authoritative.
        app.mount("/api", backend_handoff.app, name="frontend_backend_api")

    # ── SPA 前端页面路由（URL 路由改造）──────────────────
    # 练习工坊 /practice、专项特训 /practice/special-training 等前端页面路径
    # 需要返回 index.html 由 React 接管；非页面前缀（API/静态/文档）保持 404。
    # 注意：必须注册在 /api mount 之后（优先让后端 API 处理）、根 mount 之前。
    _SPA_NON_PAGE_PREFIXES = (
        "api/",
        "auth",
        "assets/",
        "design-images/",
        "assistant-character/",
        "learning-stage/",
        "textbook-covers/",
        "textbook-status-icons/",
        "acupuncture",
        "acupuncture-models/",
        "knowledge-graph/",
        "platform-assets/",
        "docs",
        "redoc",
        "openapi.json",
        "health",
        "treekg",
        "register",
        "reset-password",
        "send-code",
        "token",
        "users",
        "favicon.svg",
        "hero_word.txt",
    )

    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa_frontend_fallback(full_path: str, request: Request):
        if full_path.startswith(_SPA_NON_PAGE_PREFIXES):
            raise HTTPException(status_code=404, detail="Not Found")
        if backend_handoff is not None:
            # The SPA catch-all is registered before the legacy root mount so
            # browser pages remain owned by React.  Delegate only paths that
            # are an actual full match in the delivered business application;
            # moving the root mount earlier would swallow every SPA route.
            child_scope = dict(request.scope)
            child_scope["root_path"] = request.scope.get("root_path", "")
            child_scope["app_root_path"] = request.scope.get(
                "app_root_path", child_scope["root_path"]
            )
            for route in backend_handoff.app.routes:
                match, _ = route.matches(child_scope)
                if match == Match.FULL:
                    return _AsgiDelegateResponse(backend_handoff.app)
        if frontend_index is not None and frontend_index.is_file():
            return FileResponse(frontend_index)
        raise HTTPException(status_code=404, detail="Not Found")

    if backend_handoff is not None:
        # Keep this catch-all mount last for legacy direct business routes.
        app.mount("/", backend_handoff.app, name="frontend_backend")

    return app
