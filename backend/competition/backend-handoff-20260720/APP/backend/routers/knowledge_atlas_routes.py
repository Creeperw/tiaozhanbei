"""Authenticated FastAPI transport for the read-only Knowledge Atlas."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import FileResponse

from APP.backend.auth import get_current_user
from APP.backend.database import UserModel
from APP.backend.knowledge_atlas_service import (
    AtlasUnavailableError,
    atlas_service,
)
from APP.backend.question_index_search_service import question_index_search_service
from APP.backend.rag_core import RAGUnavailableError


router = APIRouter(prefix="/knowledge/atlas", tags=["Knowledge Atlas"])


def _unavailable(exc: Exception) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail={"code": "knowledge_atlas_unavailable", "message": str(exc)},
    )


@router.get("/status")
def get_atlas_status(current_user: UserModel = Depends(get_current_user)):
    del current_user
    return {"ok": True, **atlas_service.status()}


@router.get("/routes")
def get_atlas_routes(current_user: UserModel = Depends(get_current_user)):
    del current_user
    try:
        return {"ok": True, "routes": atlas_service.routes()}
    except AtlasUnavailableError as exc:
        raise _unavailable(exc) from exc


@router.get("/nodes")
def get_atlas_nodes(
    level: int = Query(1),
    route: str = Query("textbook_14_5"),
    lv1: str = Query(""),
    lv2: str = Query(""),
    chapter: str = Query(""),
    chapter_id: str = Query(""),
    section_id: str = Query(""),
    current_user: UserModel = Depends(get_current_user),
):
    del current_user
    try:
        return {
            "ok": True,
            **atlas_service.nodes(
                level,
                route_id=route,
                lv1=lv1,
                lv2=lv2,
                chapter=chapter,
                chapter_id=chapter_id,
                section_id=section_id,
            ),
        }
    except AtlasUnavailableError as exc:
        raise _unavailable(exc) from exc
    except (KeyError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "invalid_atlas_request", "message": str(exc).strip("'\"")},
        ) from exc


@router.get("/detail/{kp_id}")
def get_atlas_detail(
    kp_id: str,
    question_limit: int = Query(30, ge=1, le=100),
    current_user: UserModel = Depends(get_current_user),
):
    del current_user
    try:
        return {
            "ok": True,
            **atlas_service.detail(kp_id, question_limit=question_limit),
        }
    except AtlasUnavailableError as exc:
        raise _unavailable(exc) from exc
    except KeyError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "knowledge_point_not_found", "message": str(exc).strip("'\"")},
        ) from exc


@router.get("/images/{filename}")
def get_atlas_image(
    filename: str,
    current_user: UserModel = Depends(get_current_user),
):
    del current_user
    try:
        path = atlas_service.image_path(filename)
    except AtlasUnavailableError as exc:
        raise _unavailable(exc) from exc
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Atlas image not found") from exc
    return FileResponse(path, headers={"Cache-Control": "private, max-age=86400"})


@router.post("/warm", status_code=status.HTTP_202_ACCEPTED)
def warm_atlas(current_user: UserModel = Depends(get_current_user)):
    del current_user
    service_status = atlas_service.status()
    if not service_status["available"]:
        raise _unavailable(AtlasUnavailableError("; ".join(service_status["errors"])))
    return {"ok": True, "status": atlas_service.start_warm()}


@router.get("/resolve-context")
def resolve_atlas_context(
    track_id: str = Query(""),
    membership_id: str = Query(""),
    current_user: UserModel = Depends(get_current_user),
):
    del current_user
    repository = None
    if track_id and membership_id:
        try:
            from APP.backend.exam_learning_service import get_official_exam_repository

            repository = get_official_exam_repository()
        except (FileNotFoundError, RuntimeError):
            repository = None
    try:
        return {
            "ok": True,
            **atlas_service.resolve_context(
                track_id=track_id,
                membership_id=membership_id,
                exam_repository=repository,
            ),
        }
    except AtlasUnavailableError as exc:
        raise _unavailable(exc) from exc


@router.get("/questions/search")
def search_atlas_questions(
    q: str = Query("", max_length=500),
    kp_id: list[str] | None = Query(default=None),
    limit: int = Query(20, ge=1, le=100),
    mode: str = Query("semantic", pattern="^(semantic|lexical)$"),
    current_user: UserModel = Depends(get_current_user),
):
    del current_user
    try:
        if mode == "semantic":
            items = question_index_search_service.search(q, kp_ids=kp_id or (), limit=limit)
        else:
            items = atlas_service.search_questions(q, kp_ids=kp_id or (), limit=limit)
    except AtlasUnavailableError as exc:
        raise _unavailable(exc) from exc
    except RAGUnavailableError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"state": exc.state, "message": exc.message},
        ) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return {"ok": True, "mode": mode, "items": items, "total": len(items)}
