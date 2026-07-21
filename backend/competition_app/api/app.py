from __future__ import annotations

from pathlib import Path
import asyncio
import json
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from urllib.parse import quote
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, Field
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, StreamingResponse
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
    app.mount("/auth", StaticFiles(directory=auth_root, html=True), name="auth")
    app.mount("/demo", StaticFiles(directory=static_root, html=True), name="demo")
    app.mount("/chat", StaticFiles(directory=chat_root, html=True), name="chat")

    @app.middleware("http")
    async def authentication_boundary(request: Request, call_next):
        raw_token = request.cookies.get(SESSION_COOKIE)
        current_user = container.authentication_service.authenticate(raw_token)
        request.state.current_user = current_user
        path = request.url.path
        current_app_path = (
            path == "/"
            or path == "/demo-app"
            or path == "/health"
            or path == "/openapi.json"
            or path.startswith(
                (
                    "/api/v1/",
                    "/auth",
                    "/demo",
                    "/chat",
                    "/docs",
                    "/redoc",
                )
            )
        )
        # The delivered frontend backend owns authentication for its own routes
        # (JWT bearer).  The parent cookie boundary must not pre-empt it.
        handoff_path = backend_handoff is not None and not current_app_path
        public_path = (
            path == "/health"
            or path == "/openapi.json"
            or path.startswith(("/auth", "/docs", "/redoc"))
            or path.startswith("/api/v1/auth/")
            or handoff_path
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

    @app.get("/", include_in_schema=False)
    async def root() -> RedirectResponse:
        return RedirectResponse(url="/demo/")

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
        return {
            **behavior,
            "learner_id": user.user_id,
            "learning_task": (
                plans.learning_task.model_dump(mode="json")
                if plans is not None and plans.learning_task is not None
                else None
            ),
            "short_term_plan": (
                plans.short_term_plan.model_dump(mode="json")
                if plans is not None and plans.short_term_plan is not None
                else None
            ),
            "review_queue": queue.model_dump(mode="json"),
            "capabilities": {
                "behavior_context": backend_handoff is not None,
                "focus_tracking": backend_handoff is not None,
                "task_completion": backend_handoff is not None,
                "learning_trends": backend_handoff is not None,
                "review_feedback": True,
                "execution_graph": True,
            },
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
                await queue.put({"event": event_name, "result": _sanitize(result)})
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
        # Keep this catch-all mount last. Existing /api/v1, /demo and /chat
        # routes remain authoritative; every unmatched frontend contract route
        # is served by the delivered backend on the same host and port.
        app.mount("/", backend_handoff.app, name="frontend_backend")

    return app
