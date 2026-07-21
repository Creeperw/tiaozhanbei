from __future__ import annotations

from pathlib import Path
import asyncio
import json
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from urllib.parse import quote
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Query, Request
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
from competition_app.contracts.auth import AuthUser, LoginRequest, RegisterRequest
from competition_app.repositories.auth import UsernameTakenError
from competition_app.services.auth import InvalidCredentialsError
from competition_app.services.learning_path_projection import LearningPathProjectionService
from competition_app.services.profile_readiness import ProfileReadinessService
from competition_app.services.workshop import WorkshopKnowledgeService
from competition_app.application.workflow_presentation import workflow_result_to_markdown


SESSION_COOKIE = "competition_session"


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


def create_app(container: ApplicationContainer, *, auth_required: bool = True) -> FastAPI:
    backend_handoff = container.backend_handoff_runtime

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        if backend_handoff is not None:
            await backend_handoff.startup()
        try:
            yield
        finally:
            if backend_handoff is not None:
                await backend_handoff.shutdown()

    app = FastAPI(title="Competition App", version="0.1.0", lifespan=lifespan)
    static_root = Path(__file__).parents[1] / "static"
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
    app.mount("/auth", StaticFiles(directory=auth_root, html=True), name="auth")
    app.mount("/demo", StaticFiles(directory=static_root, html=True), name="demo")
    app.mount("/chat", StaticFiles(directory=chat_root, html=True), name="chat")

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
            or path == "/health"
            or path == "/openapi.json"
            or path.startswith(("/assets/", "/design-images/", "/assistant-character/"))
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
            or (
                path.startswith("/training/workspace/papers/")
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
                behavior = await asyncio.to_thread(
                    backend_handoff.load_learning_context, current_user.user_id
                )
                container.review_service.ingest_question_attempts(
                    learner_id=current_user.user_id,
                    attempts=behavior.get("question_attempt", []),
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
        return response

    @app.post("/api/v1/auth/login")
    async def login(request: LoginRequest):
        try:
            result, raw_token = container.authentication_service.login(request)
        except InvalidCredentialsError as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from exc
        response = JSONResponse(content=result.model_dump(mode="json"))
        set_session_cookie(response, raw_token, result.expires_at)
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
        return [
            {
                "id": row.get("message_id"),
                "role": row.get("role"),
                "content": row.get("content"),
                "timestamp": row.get("created_at"),
            }
            for row in rows
        ]

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
        plans = container.review_card_use_case.plan_repository.get_current(user.user_id)
        queue = container.review_service.get_queue(user.user_id, limit=12)
        long_term_payload = (
            plans.long_term_plan.model_dump(mode="json")
            if plans is not None and plans.long_term_plan is not None
            else None
        )
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
            "profile_readiness": profile_readiness.model_dump(mode="json"),
            "learning_path": {
                "available": long_term_payload is not None,
                "root_endpoint": "/api/v1/learning-path",
                "children_endpoint": "/api/v1/learning-path/nodes?parent_id={node_id}",
                "schema_version": "1.0",
            },
            "capabilities": {
                "behavior_context": backend_handoff is not None,
                "focus_tracking": backend_handoff is not None,
                "task_completion": backend_handoff is not None,
                "learning_trends": backend_handoff is not None,
                "review_feedback": True,
                "execution_graph": True,
            },
        }

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
        if backend_handoff is not None:
            try:
                behavior = await asyncio.to_thread(
                    backend_handoff.load_learning_context, user.user_id
                )
            except Exception:
                # The home portal remains usable while optional behavior metrics recover.
                behavior = {}
        plans = container.review_card_use_case.plan_repository.get_current(user.user_id)
        queue = container.review_service.get_queue(user.user_id, limit=12)
        sessions = container.review_card_use_case.conversation_repository.list_sessions(
            user.user_id
        )
        today_tasks: list[dict] = []
        if plans is not None and plans.learning_task is not None:
            task = plans.learning_task
            if task.status != "completed":
                today_tasks.append(
                    {
                        "task_id": task.task_id,
                        "title": task.task_content,
                        "duration": f"{task.estimated_minutes} 分钟",
                        "status": task.status,
                        "source": "daily_task",
                    }
                )
        for entry in queue.entries:
            if entry.task is None:
                continue
            today_tasks.append(
                {
                    "task_id": entry.task.review_task_id,
                    "title": entry.memory_unit.prompt_abstract,
                    "duration": f"{entry.task.estimated_minutes} 分钟",
                    "status": entry.task.status,
                    "source": "review_queue",
                }
            )
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
            "status_cards": [
                {"key": "accuracy", "value": f"{round(float(accuracy or 0) * 100)}%"},
                {"key": "completion", "value": f"{round(float(completion or 0) * 100)}%"},
            ],
            "announcements": [],
            "review_queue": queue.model_dump(mode="json"),
        }

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
        if task.status != "completed":
            now = datetime.now(timezone.utc)
            task = task.model_copy(
                update={
                    "status": "completed",
                    "version": task.version + 1,
                    "updated_at": now,
                }
            )
            repository.save_current(
                user.user_id,
                plans.model_copy(update={"learning_task": task}),
            )
        return {"learning_task": task.model_dump(mode="json")}

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
        return _sanitize(state)

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
                container.review_card_use_case.mark_run_failed(thread_id, str(exc))
                await queue.put(
                    {
                        "event": "run_failed",
                        "error_type": type(exc).__name__,
                        "message": str(exc),
                        "thread_id": thread_id,
                    }
                )
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
