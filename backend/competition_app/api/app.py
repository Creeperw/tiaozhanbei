from __future__ import annotations

from pathlib import Path
import asyncio
import json
import re
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any, Literal
from urllib.parse import quote
from uuid import uuid4

from fastapi import FastAPI, File, HTTPException, Query, Request, UploadFile
from pydantic import BaseModel, Field
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles

from competition_app.application.container import ApplicationContainer
from competition_app.application.personalized_review_card import (
    ReviewCardRequest,
    WorkflowResumeRequest,
)
from competition_app.runtime.event_stream import bind_event_sink, reset_event_sink
from competition_app.runtime.snapshot import _sanitize
from competition_app.contracts.review import ReviewAttemptSubmission
from competition_app.contracts.auth import (
    AccountProfileUpdateRequest,
    AuthUser,
    LoginRequest,
    RegisterRequest,
)
from competition_app.repositories.auth import UsernameTakenError
from competition_app.services.auth import InvalidCredentialsError
from competition_app.services.learning_path_projection import LearningPathProjectionService
from competition_app.services.profile_readiness import ProfileReadinessService
from competition_app.services.planning_readiness import PlanningReadinessService
from competition_app.services.plan_progress import build_plan_progress
from competition_app.services.learning_monitoring import LearningMonitoringService
from competition_app.services.workshop import WorkshopKnowledgeService
from competition_app.services.qualification_papers import QualificationPaperRepository
from competition_app.application.workflow_presentation import workflow_result_to_markdown
from competition_app.api.simulated_patient_routes import router as sp_router, init_engine as sp_init_engine


SESSION_COOKIE = "competition_session"
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


class StageEvidenceRequest(BaseModel):
    requirement: str = Field(min_length=1, max_length=1000)
    task_id: str = Field(min_length=1, max_length=160)


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
    try:
        if raw_difficulty is None or isinstance(raw_difficulty, bool):
            raise ValueError("difficulty is absent")
        parsed_difficulty = float(raw_difficulty)
        if not parsed_difficulty.is_integer() or not 1 <= parsed_difficulty <= 5:
            raise ValueError("difficulty must be an integer from 1 to 5")
        difficulty = int(parsed_difficulty)
        difficulty_source = str(
            question.get("difficulty_source")
            or question.get("难度来源")
            or "source_metadata"
        ).strip()
    except (TypeError, ValueError):
        difficulty = None
        difficulty_source = None
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


def create_app(container: ApplicationContainer, *, auth_required: bool = True) -> FastAPI:
    backend_handoff = container.backend_handoff_runtime
    qualification_papers = QualificationPaperRepository(
        Path(__file__).resolve().parents[1] / "data" / "qualification_papers",
        runtime_root=Path(__file__).resolve().parents[1] / "runtime" / "qualification_papers",
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        workshop_dispatcher = None
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
        try:
            yield
        finally:
            if workshop_dispatcher is not None:
                workshop_dispatcher.cancel()
                try:
                    await workshop_dispatcher
                except asyncio.CancelledError:
                    pass
            if backend_handoff is not None:
                await backend_handoff.shutdown()

    app = FastAPI(title="Competition App", version="0.1.0", lifespan=lifespan)
    static_root = Path(__file__).parents[1] / "static"
    platform_assets_root = static_root / "platform-assets"
    chat_root = Path(__file__).parents[1] / "chat_static"
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
    app.mount(
        "/platform-assets",
        StaticFiles(directory=platform_assets_root),
        name="platform_assets",
    )
    app.mount("/auth", StaticFiles(directory=auth_root, html=True), name="auth")
    app.mount("/demo", StaticFiles(directory=static_root, html=True), name="demo")
    app.mount("/chat", StaticFiles(directory=chat_root, html=True), name="chat")

    # ── 模拟病患模块 ────────────────────────────────────
    try:
        sp_init_engine(llm_timeout_seconds=120.0)
        app.include_router(sp_router)
    except Exception:
        import logging
        _logger = logging.getLogger("competition_app.simulated_patient")
        _logger.warning("模拟病患模块初始化失败", exc_info=True)

    @app.middleware("http")
    async def authentication_boundary(request: Request, call_next):
        raw_token = request.cookies.get(SESSION_COOKIE)
        current_user = container.authentication_service.authenticate(raw_token)
        request.state.current_user = current_user
        path = request.url.path
        # Mounted business routes share the main cookie identity. Their internal
        # dependency maps request.state.current_user to a domain-local user row.
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
                    "/platform-assets/",
                )
            )
            or path.startswith(("/auth", "/docs", "/redoc"))
            or path.startswith("/api/v1/auth/")
        )
        if auth_required and current_user is None and not public_path:
            if request.method == "GET" and (
                path == "/" or path == "/demo-app" or path.startswith(("/demo", "/chat"))
            ):
                return RedirectResponse(
                    url=f"/auth/?next={quote(path, safe='/')}", status_code=303
                )
            return JSONResponse(
                status_code=401,
                content={"detail": "请先登录后继续"},
            )
        response = await call_next(request)
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

                behavior = await asyncio.to_thread(
                    backend_handoff.load_learning_context, current_user.user_id
                )
                container.review_service.ingest_question_attempts(
                    learner_id=current_user.user_id,
                    attempts=behavior.get("question_attempt", []),
                )
                await asyncio.to_thread(
                    backend_handoff.load_learning_insights,
                    current_user.user_id,
                    days=30,
                    plan_context={},
                    run_automation=True,
                )
            except Exception:
                # The authoritative answer has already been committed. A later
                # context/queue read retries this idempotent projection.
                pass
        if path.startswith(("/demo", "/chat", "/auth")):
            response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
        return response

    @app.middleware("http")
    async def disable_demo_cache(request: Request, call_next):
        response = await call_next(request)
        if request.url.path.startswith(("/demo", "/chat", "/auth")):
            response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
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
            "workflow_timeout": "本次处理超时，已保存当前会话，请稍后重试。",
            "plan_compilation_failed": "学习规划未能通过结构化校验，请稍后重试。",
            "audit_step_failed": "内容审核未能完成，请稍后重试。",
            "daily_task_publication_failed": "今日任务发布未能完成，请稍后重试。",
            "paper_generation_failed": "试卷生成未能完成，请稍后重试。",
            "persistence_failed": "结果保存失败，请稍后重试。",
        }
        return messages.get(
            str(error_code or ""),
            "这次处理没有成功完成，请稍后重试。",
        )

    def safe_run_status(state: dict[str, Any]) -> dict[str, Any]:
        result = state.get("result")
        if hasattr(result, "model_dump"):
            result = result.model_dump(mode="json")
        if not isinstance(result, dict):
            result = None
        payload = {
            "status": state.get("status"),
            "thread_id": state.get("thread_id"),
            "execution_id": state.get("execution_id"),
            "task_type": state.get("task_type")
            or (result or {}).get("task_type"),
            "result": _sanitize(result) if result is not None else None,
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

    @app.get("/api/v1/textbooks/pdfs/catalog")
    async def textbook_pdf_catalog(request: Request) -> dict:
        current_user(request)
        items = container.textbook_pdf_service.books()
        return {"items": items, "total": len(items)}

    @app.get("/api/v1/textbooks/pdfs/resolve")
    async def resolve_textbook_pdf(book: str, request: Request) -> dict:
        current_user(request)
        item = container.textbook_pdf_service.resolve(book)
        return {"available": bool(item and item.get("available")), "book": item}

    @app.get("/api/v1/textbooks/pdfs/{book_id}/file")
    async def textbook_pdf_file(book_id: str, request: Request):
        current_user(request)
        item = container.textbook_pdf_service.by_id(book_id)
        path = container.textbook_pdf_service.file_path(book_id)
        if item is None or path is None:
            raise HTTPException(status_code=404, detail="该教材暂无电子版")
        return FileResponse(
            path,
            media_type="application/pdf",
            filename=path.name,
            content_disposition_type="inline",
            headers={"Cache-Control": "private, max-age=3600"},
        )

    @app.get("/api/v1/textbooks/pdfs/{book_id}/pages/{page_number}/annotations")
    async def textbook_pdf_annotations(
        book_id: str, page_number: int, request: Request
    ) -> dict:
        user = current_user(request)
        if page_number < 1 or container.textbook_pdf_service.by_id(book_id) is None:
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
        if page_number < 1 or container.textbook_pdf_service.by_id(book_id) is None:
            raise HTTPException(status_code=404, detail="教材页面不存在")
        return container.textbook_pdf_service.annotations.save_page(
            user.user_id, book_id, page_number, payload.annotations
        )

    @app.get("/api/v1/textbooks/pdfs/{book_id}/reading-state")
    async def textbook_pdf_reading_state(book_id: str, request: Request) -> dict:
        user = current_user(request)
        if container.textbook_pdf_service.by_id(book_id) is None:
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
        if container.textbook_pdf_service.by_id(book_id) is None:
            raise HTTPException(status_code=404, detail="教材不存在")
        return container.textbook_pdf_service.annotations.save_reading_state(
            user.user_id, book_id, payload.page_number, payload.zoom
        )

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
        return repository.list_sessions(user.user_id)

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

    @app.get("/demo-app", include_in_schema=False)
    async def demo_app() -> FileResponse:
        return FileResponse(static_root / "index.html")

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
            await asyncio.to_thread(backend_handoff.load_learning_context, user.user_id)
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
        days: int = Query(default=7, ge=1, le=90),
    ) -> dict:
        user = current_user(request)
        if user is None:
            raise HTTPException(status_code=401, detail="请先登录后继续")
        behavior = (
            await asyncio.to_thread(backend_handoff.load_learning_context, user.user_id)
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
        result = await asyncio.to_thread(
            backend_handoff.load_learning_insights,
            user.user_id,
            days=days,
            plan_context=current_plan_context(user.user_id),
            run_automation=run_automation,
        )
        queue = await canonical_review_queue(user.user_id)
        overview = {
            **dict(result.get("overview") or {}),
            "due_review_count": queue.due_count,
            "review_projection_source": "canonical_review_memory",
        }
        data_sources = list(result.get("data_sources") or [])
        if "canonical_review_memory" not in data_sources:
            data_sources.append("canonical_review_memory")
        return {
            **result,
            "overview": overview,
            "data_sources": data_sources,
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
        try:
            return await asyncio.to_thread(
                backend_handoff.submit_intervention_feedback,
                user.user_id,
                intervention_id,
                body.action,
                body.reason,
            )
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

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
        return await asyncio.to_thread(
            backend_handoff.run_plan_review,
            user.user_id,
            plan_context=current_plan_context(user.user_id),
            trigger_type="manual",
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
            return await asyncio.to_thread(
                backend_handoff.decide_plan_review,
                user.user_id,
                review_id,
                body.decision,
            )
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

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
            page = await asyncio.to_thread(
                LearningPathProjectionService(loader).page,
                learner_id=user.user_id,
                plan=plans.long_term_plan,
                parent_id=parent_id,
                mastery_rows=behavior.get("mastery") or [],
                offset=offset,
                limit=limit,
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
        seen: set[str] = set()
        for match in selected_kps:
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
        if not candidates:
            return None
        # Ensure variety by shuffling candidates
        import random as _random
        _random.shuffle(candidates)
        return next(
            (
                question
                for question in candidates
                if question["question_id"] not in attempted_question_ids
            ),
            candidates[0],
        )

    @app.get("/api/v1/workshop/practice/next")
    async def next_workshop_practice_question(
        request: Request,
        kp_id: str | None = Query(default=None, min_length=1, max_length=120),
        topic: str | None = Query(default=None, min_length=1, max_length=500),
        scope: str = Query(default="public", pattern="^(public|user|all)$"),
        mode: str = Query(default="objective", pattern="^(all|objective|case)$"),
    ) -> dict:
        user = current_user(request)
        runtime = require_workshop_runtime()
        if scope in {"user", "all"}:
            personal = await asyncio.to_thread(
                runtime.issue_personal_practice,
                user.user_id,
                kp_id=kp_id,
                mode=mode,
            )
            if personal.get("available") or scope == "user":
                return _sanitize_practice_question_labels(personal)

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

        has_explicit_target = bool(kp_id or str(topic or "").strip())
        latest_claim = selection_context.get("latest_active_claim")
        resume_claim = getattr(runtime, "resume_formal_practice_claim", None)
        if not has_explicit_target and isinstance(latest_claim, dict) and callable(resume_claim):
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
                    return _sanitize_practice_question_labels(resumed)

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
                return _sanitize_practice_question_labels(issued)
        except Exception:
            # Keep projected formal questions usable while the read-only bank
            # is temporarily unavailable; never reinterpret this as an empty bank.
            pass
        cached = await asyncio.to_thread(
            runtime.issue_cached_public_practice,
            user.user_id,
            kp_id=kp_id,
            mode=mode,
        )
        return _sanitize_practice_question_labels(cached)

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
        if backend_handoff is not None:
            try:
                behavior = await asyncio.to_thread(
                    backend_handoff.load_learning_context, user.user_id
                )
            except Exception:
                # The home portal remains usable while optional behavior metrics recover.
                behavior = {}
            try:
                learning_activity = await asyncio.to_thread(
                    backend_handoff.load_learning_activity_summary,
                    user.user_id,
                    days=30,
                    recent_limit=20,
                )
            except Exception:
                # Recent workshop history is supplemental to the dashboard response.
                learning_activity = {"recent_activities": []}
        daily_task_timer = await asyncio.to_thread(
            container.daily_task_refresh_service.ensure_current, user.user_id
        )
        try:
            await asyncio.to_thread(
                container.learning_plan_service.ensure_executable_daily_resources,
                user.user_id,
            )
        except Exception:
            # Legacy-task repair must not make the home portal unavailable.
            pass
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
                    for query in queries[:5]:
                        try:
                            matches = container.knowledge_backend.map.resolve_topic(
                                str(query), limit=10
                            )
                        except Exception:
                            matches = []
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
                    "duration": f"{task.estimated_minutes} 分钟",
                    "estimated_minutes": task.estimated_minutes,
                    "expected_output": task.expected_output,
                    "completion_criteria": task.completion_criteria,
                    "status": task.status,
                    "refresh_started_at": task.refresh_started_at,
                    "refresh_due_at": task.refresh_due_at,
                    "source": "daily_task",
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
        checkin_status = {"checked_in_today": False, "streak": 0, "total_checkins": 0, "calendar_days": []}
        if backend_handoff is not None:
            try:
                checkin_status = await asyncio.to_thread(
                    backend_handoff.get_checkin_status, user.user_id, days=7
                )
            except Exception:
                pass
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
            return await container.review_card_use_case.execute(request)
        except RuntimeError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/api/v1/review-cards/stream")
    async def stream_review_card(
        request: ReviewCardRequest, http_request: Request
    ) -> StreamingResponse:
        request = scoped_review_request(request, current_user(http_request))
        require_available_thread(http_request, request.thread_id)
        thread_id = request.thread_id or f"THREAD_{uuid4().hex}"
        request = request.model_copy(update={"thread_id": thread_id})
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
        require_run_owner(http_request, thread_id)
        return _workflow_stream(
            thread_id,
            lambda: container.review_card_use_case.resume(thread_id, request),
            resumed=True,
        )

    @app.get("/api/v1/review-cards/runs/{thread_id}")
    async def get_review_card_run(thread_id: str, request: Request):
        state = require_run_owner(request, thread_id)
        return safe_run_status(state)

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
        return {
            **details,
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

    @app.post("/api/v1/learners/{learner_id}/review-queue/dispatch")
    async def dispatch_due_review(
        learner_id: str,
        dispatch_request: ReviewDispatchRequest,
        request: Request,
    ):
        require_owner(request, learner_id)
        entry = container.review_service.next_dispatch_entry(learner_id)
        if entry is None:
            return {"status": "empty", "message": "当前没有等待资源的到期复习知识点。"}
        unit = entry.memory_unit
        result = await container.review_card_use_case.execute(
            ReviewCardRequest(
                learner_id=learner_id,
                user_request=(
                    "请为以下已到期知识点生成一张可立即学习的复习卡："
                    f"{unit.prompt_abstract}"
                ),
                available_minutes=dispatch_request.available_minutes,
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
            raise HTTPException(status_code=409, detail="到期资源生成意外进入追问状态")
        return result

    def _workflow_stream(
        thread_id: str,
        operation,
        *,
        user_request: str | None = None,
        resumed: bool = False,
    ) -> StreamingResponse:
        queue: asyncio.Queue[dict[str, object] | None] = asyncio.Queue()

        def failure_event(exc: Exception) -> dict[str, object]:
            run_state = container.review_card_use_case.get_run_state(thread_id) or {}
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
                "error_type": error_type,
                "error_code": error_code,
                "retryable": retryable,
                "message": user_message,
                "user_message": user_message,
                "thread_id": thread_id,
                "execution_id": execution_id,
                "failed_step": failed_step,
            }

        def publish(event: dict[str, object]) -> None:
            queue.put_nowait(event)

        async def run_workflow() -> None:
            token = bind_event_sink(publish)
            try:
                await queue.put(
                    {
                        "event": "run_resumed" if resumed else "run_started",
                        "thread_id": thread_id,
                        "user_request": user_request,
                    }
                )
                result = await operation()
                event_name = (
                    "run_interrupted"
                    if getattr(result, "status", None) == "interrupted"
                    else "run_completed"
                )
                await queue.put(
                    {
                        "event": event_name,
                        "result": _sanitize(result),
                        "assistant_message": workflow_result_to_markdown(result),
                    }
                )
            except Exception as exc:
                failure = failure_event(exc)
                container.review_card_use_case.mark_run_failed(
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
                await queue.put(failure)
            finally:
                reset_event_sink(token)
                await queue.put(None)

        async def event_source():
            task = asyncio.create_task(run_workflow())
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
        # Keep this catch-all mount last for legacy direct business routes.
        app.mount("/", backend_handoff.app, name="frontend_backend")

    return app
