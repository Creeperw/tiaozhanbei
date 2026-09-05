from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from APP.backend.auth import get_current_user
from APP.backend.database import UserModel, get_db
from APP.backend.exam_learning_service import (
    get_learner_knowledge_point_state,
    get_node,
    get_node_knowledge_points,
    get_node_learner_summary,
    get_visible_node_learner_states,
    get_requirement_knowledge_points,
    list_nodes,
    list_tracks,
)


router = APIRouter(prefix="/exam-learning", tags=["Exam Learning"])


class LearnerStatesRequest(BaseModel):
    membership_ids: list[str] = Field(min_length=1, max_length=120)


def _not_found(exc: KeyError) -> HTTPException:
    return HTTPException(status_code=404, detail=str(exc.args[0] if exc.args else exc))


@router.get("/tracks")
def tracks(current_user: UserModel = Depends(get_current_user)):
    return list_tracks()


@router.get("/tracks/{track_id}/nodes")
def nodes(
    track_id: str,
    parent_membership_id: str | None = Query(default=None),
    current_user: UserModel = Depends(get_current_user),
):
    try:
        return list_nodes(track_id, parent_membership_id)
    except KeyError as exc:
        raise _not_found(exc) from exc


@router.get("/tracks/{track_id}/nodes/{membership_id}")
def node_detail(
    track_id: str,
    membership_id: str,
    current_user: UserModel = Depends(get_current_user),
):
    try:
        return get_node(track_id, membership_id)
    except KeyError as exc:
        raise _not_found(exc) from exc


@router.get("/tracks/{track_id}/nodes/{membership_id}/knowledge-points")
def node_knowledge_points(
    track_id: str,
    membership_id: str,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=100),
    current_user: UserModel = Depends(get_current_user),
):
    try:
        return get_node_knowledge_points(
            track_id,
            membership_id,
            offset=offset,
            limit=limit,
        )
    except KeyError as exc:
        raise _not_found(exc) from exc


@router.get("/tracks/{track_id}/nodes/{membership_id}/learner-summary")
def node_learner_summary(
    track_id: str,
    membership_id: str,
    current_user: UserModel = Depends(get_current_user),
    db=Depends(get_db),
):
    try:
        return get_node_learner_summary(
            db,
            user_id=current_user.id,
            track_id=track_id,
            membership_id=membership_id,
        )
    except KeyError as exc:
        raise _not_found(exc) from exc


@router.post("/tracks/{track_id}/nodes/learner-states")
def visible_node_learner_states(
    track_id: str,
    payload: LearnerStatesRequest,
    current_user: UserModel = Depends(get_current_user),
    db=Depends(get_db),
):
    try:
        return get_visible_node_learner_states(
            db,
            user_id=current_user.id,
            track_id=track_id,
            membership_ids=payload.membership_ids,
        )
    except KeyError as exc:
        raise _not_found(exc) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/knowledge-points/{kp_id}/learner-state")
def learner_knowledge_point_state(
    kp_id: str,
    current_user: UserModel = Depends(get_current_user),
    db=Depends(get_db),
):
    return get_learner_knowledge_point_state(
        db,
        user_id=current_user.id,
        kp_id=kp_id,
    )


@router.get("/requirements/{node_id}/knowledge-points")
def requirement_knowledge_points(
    node_id: str,
    current_user: UserModel = Depends(get_current_user),
):
    try:
        return get_requirement_knowledge_points(node_id)
    except KeyError as exc:
        raise _not_found(exc) from exc
